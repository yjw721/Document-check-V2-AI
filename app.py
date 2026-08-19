# -*- coding: utf-8 -*-
"""
文档低级错误检查工具 —— FastAPI 后端（离线保密版）
=========================================================================
纯本地运行：仅监听 127.0.0.1，无任何外部网络请求、无遥测、无数据上传。
前端为 static/index.html（深色玻璃拟态单页应用），所有检测逻辑复用既有
checkers / config / report 模块。

启动：python app.py   （默认 http://127.0.0.1:8501）
"""

from __future__ import annotations

import io
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from checkers.base import STATUS_PASS, STATUS_UNREADABLE, detect_file_type
from checkers.scanner import check_single, collect_folder, summarize, _is_blank
from config.config_manager import load_rules, save_rules, RuleConfig
from config.settings_manager import load_settings, save_settings, restore_defaults
from checkers.custom_rules import load_custom_rules, save_custom_rules
from checkers.wordbank import load_wordbanks, save_wordbanks, parse_entries_import
from checkers.template_parser import TemplateParser
from checkers.dictionary_manager import list_dictionaries, read_dictionary, save_dictionary
from checkers.fluency_checker import RULE_KEYS as FLUENCY_RULE_KEYS
from checkers.ai_checker import (
    ai_check_file, test_connection as ai_test_connection,
    list_refs, save_ref, delete_ref, set_ref_enabled,
)
from checkers.ai_builder import (
    build_dialogue as ai_build_dialogue, build_from_doc as ai_build_doc,
    build_from_text as ai_build_text,
)
from checkers.rule_filter import (
    filter_generated as rule_filter_generated,
    filter_rules_payload as rule_filter_rules_payload,
    filter_entries_payload as rule_filter_entries_payload,
)
from checkers.rule_scan import (
    scan_all as rule_scan_all,
    clean_items as rule_scan_clean,
    cleanable_ids as rule_scan_cleanable_ids,
    build_backup_text as rule_scan_backup_text,
    build_backup_csv as rule_scan_backup_csv,
)
from checkers.ai_memory import (
    payload as ai_memory_payload, set_enabled as ai_memory_set_enabled,
    is_enabled as ai_memory_is_enabled, learn_sample as ai_memory_learn,
    toggle_learned as ai_memory_toggle, delete_learned as ai_memory_delete,
    delete_sample as ai_memory_sample_delete, clear_all as ai_memory_clear,
    save_source_docs as ai_memory_save_source_docs,
    source_docs_status as ai_memory_source_status,
    upload_revised as ai_memory_upload_revised,
    compute_diffs as ai_memory_compute_diffs,
    add_sample as ai_memory_add_sample,
    export_learned as ai_memory_export,
)
from report.report_builder import build_report, default_report_name

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(ROOT, "static")
CACHE_DIR = os.path.join(tempfile.gettempdir(), "doc_checker_local_cache")
REPORT_DIR = os.path.join(ROOT, "reports")
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

# 服务端内存态（本地单机工具，进程内保存即可）
_STATE: Dict[str, Any] = {"results": [], "last_scan_time": None, "scan_seconds": 0.0}
# 规则词库扫描结果内存态（最近一次扫描，供刷新页面后继续查看 / 清理校验）
_SCAN_STATE: Dict[str, Any] = {"last": None}
# 范本解析草案内存态：所有上传范本仅在本地内存处理（BytesIO），不落盘、零网络
_TEMPLATE_STATE: Dict[str, Any] = {"parser": None}
_MAX_TEMPLATE_MB = 50

# ---------------------------------------------------------------------------
# 核验任务注册表（异步任务：进度 / 阶段 / 思考日志 / 取消）
# ---------------------------------------------------------------------------
_TASKS: Dict[str, Dict[str, Any]] = {}
_TASK_LOCK = threading.Lock()
_MAX_TASKS = 30

# 阶段 key -> 中文展示名（与前端 WaitingTab 共用语义）
STAGE_NAMES = {
    "parse": "文件解析",
    "page": "页码定位",
    "format_error": "格式错误检测",
    "fluency": "语句通顺度检测",
    "wordbank": "行业词库规则匹配",
    "summary": "结果汇总",
    "ai": "AI 智能核验",
    "scan": "规则词库扫描",
}


def _make_task(files: List[str]) -> str:
    """登记一个后台核验任务，返回 task_id。"""
    import uuid
    tid = uuid.uuid4().hex[:12]
    with _TASK_LOCK:
        task = {
            "status": "running",
            "progress": 0.0,
            "stage": "parse",
            "stage_text": STAGE_NAMES["parse"],
            "logs": [],
            "cancel": threading.Event(),
            "error": "",
            "files": list(files),
        }
        _TASKS[tid] = task
        # 超过上限时清理最旧的已完成任务，防止内存膨胀
        if len(_TASKS) > _MAX_TASKS:
            for old_tid in list(_TASKS):
                if _TASKS[old_tid]["status"] in ("done", "cancelled", "error"):
                    del _TASKS[old_tid]
                if len(_TASKS) <= _MAX_TASKS:
                    break
    return tid


def _task_snapshot(tid: str) -> Optional[Dict[str, Any]]:
    with _TASK_LOCK:
        t = _TASKS.get(tid)
        if not t:
            return None
        return {
            "status": t["status"],
            "progress": round(float(t["progress"]), 1),
            "stage": t["stage"],
            "stage_text": t["stage_text"],
            "logs": list(t["logs"]),
            "error": t["error"],
            "result": t.get("result"),
            "ai_stream": t.get("ai_stream"),
        }


def _task_hook(tid: str) -> Callable[[float, str, str], bool]:
    """构造核验任务进度钩子（percent 0-100, 阶段 key, 日志文本）→ bool。"""
    def hook(percent: float, stage: str, log: str) -> bool:
        with _TASK_LOCK:
            t = _TASKS.get(tid)
            if not t:
                return False
            if t["cancel"].is_set():
                return False
            t["progress"] = float(percent)
            if stage:
                t["stage"] = stage
                t["stage_text"] = STAGE_NAMES.get(stage, stage)
            if log:
                logs = t["logs"]
                if not logs or logs[-1] != log:
                    logs.append(log)
                    if len(logs) > 400:
                        del logs[:-400]
        return True
    return hook


def _cancel_task(tid: str) -> bool:
    """取消任务：置取消标志 + 删除该任务的上传缓存文件。"""
    with _TASK_LOCK:
        t = _TASKS.get(tid)
        if not t:
            return False
        if t["status"] == "running":
            t["cancel"].set()
            t["status"] = "cancelled"
            for fp in t["files"]:
                try:
                    if os.path.isfile(fp):
                        os.remove(fp)
                except OSError:
                    pass
    return True

app = FastAPI(title="文档低级错误检查工具", version="2.0.0")

# 允许跨域：本工具仅监听 127.0.0.1，但内置预览面板可能将页面嵌在自有域名下
# 以异源方式加载，此时前端 fetch 相对路径会指向错误地址 / 被 CORS 拦截。
# 放开同源限制后，前端在「异源直连 127.0.0.1:8501」场景下即可正常通信。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


# ---------------------------------------------------------------------------
# 序列化与工具
# ---------------------------------------------------------------------------
def _serialize_result(res) -> Dict[str, Any]:
    return {
        "file_name": res.file_name,
        "file_path": res.file_path,
        "file_type": res.file_type,
        "file_size": res.file_size,
        "size_text": res.size_text,
        "status": res.status,
        "status_text": res.status_text,
        "error_message": res.error_message,
        "truncated": res.truncated,
        "stats": res.stats,
        "issue_count": res.issue_count,
        "active_issue_count": res.active_issue_count,
        "severity": res.severity_count(),
        "issues": [i.to_dict() for i in res.issues],
    }


def _cache_stats() -> Dict[str, Any]:
    count, size = 0, 0
    if os.path.isdir(CACHE_DIR):
        for name in os.listdir(CACHE_DIR):
            p = os.path.join(CACHE_DIR, name)
            try:
                if os.path.isfile(p):
                    count += 1
                    size += os.path.getsize(p)
            except OSError:
                continue
    return {"count": count, "size": size, "size_text": f"{size / 1024 / 1024:.2f} MB"}


def _clear_cache() -> Dict[str, int]:
    n, freed = 0, 0
    if os.path.isdir(CACHE_DIR):
        for name in os.listdir(CACHE_DIR):
            p = os.path.join(CACHE_DIR, name)
            try:
                if os.path.isfile(p):
                    freed += os.path.getsize(p)
                    os.remove(p)
                    n += 1
            except OSError:
                continue
    return {"count": n, "freed": freed}


def _rule_summary() -> Dict[str, str]:
    cfg = load_rules()
    data = cfg.data
    out: Dict[str, str] = {}
    for k, v in data.items():
        if k in ("meta", "global") or not isinstance(v, dict):
            continue
        total = len(v)
        enabled = sum(1 for r in v.values() if isinstance(r, dict) and r.get("enabled", True))
        out[k] = f"{enabled}/{total}"
    return out


def _run_detection(items: List[tuple], append: bool = False, tid: str = "") -> None:
    """执行批量检测（复用 scanner.check_single，按后台设置并发与超时）。

    tid 非空时走异步任务模式：实时上报整体进度/阶段/思考日志，支持取消。
    """
    settings = load_settings()
    det = settings.get("detection", {})
    parse_set = settings.get("parse", {})
    ai_cfg = settings.get("ai", {}) or {}
    concurrency = max(1, int(det.get("concurrency", 2)))
    parse_timeout = int(det.get("parse_timeout", 0) or 0)
    auto_ignore_blank = bool(det.get("auto_ignore_blank", False))
    opts = {
        "enable_pdf": bool(parse_set.get("enable_pdf", True)),
        "enable_legacy": bool(parse_set.get("enable_legacy", False)),
        "parse_timeout": parse_timeout,
        "auto_ignore_blank": auto_ignore_blank,
        "fluency_sensitivity": str(det.get("fluency_sensitivity", "normal") or "normal"),
    }
    config = load_rules()
    ai_enabled = bool(ai_cfg.get("enabled", False))
    total = max(len(items), 1)
    per_file: Dict[str, float] = {}
    hook = _task_hook(tid) if tid else None

    def file_hook(name: str) -> Optional[Callable[[float, str, str], bool]]:
        if not hook:
            return None
        def h(percent: float, stage: str, log: str) -> bool:
            per_file[name] = float(percent)
            overall = (sum(per_file.values()) / 100.0) / total * (90.0 if ai_enabled else 100.0)
            return hook(overall, stage, f"「{name}」{log}" if log else "")
        return h

    new_results = []
    done = 0
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = [(ex.submit(check_single, path, config, name, opts, file_hook(name)), path, name)
                for path, name in items]
        for fut, path, name in futs:
            new_results.append(fut.result())
            done += 1
            per_file[name] = 100.0
            if hook:
                hook(done / total * (90.0 if ai_enabled else 100.0), "",
                     f"「{name}」规则检测完成（{done}/{total}）")
            if tid and _TASKS.get(tid, {}).get("cancel", threading.Event()).is_set():
                return

    # ---- AI 智能核验阶段（可选：local 本地 AI / online 联网 AI）----
    if ai_enabled:
        base = done / total * 90.0
        new_results = _run_ai_verify(items, new_results, ai_cfg, tid, base, total)

    if tid and _TASKS.get(tid, {}).get("cancel", threading.Event()).is_set():
        return

    if auto_ignore_blank:
        new_results = [
            r for r in new_results
            if not (r.status == STATUS_PASS and r.active_issue_count == 0 and _is_blank(r))
        ]

    if append:
        _STATE["results"] = _STATE["results"] + new_results
    else:
        _STATE["results"] = new_results
    _STATE["last_scan_time"] = datetime.now()
    _STATE["scan_seconds"] = 0.0  # 前端单独计时

    if tid:
        with _TASK_LOCK:
            t = _TASKS.get(tid)
            if t:
                t["status"] = "done"
                t["progress"] = 100.0
                t["stage"] = "summary"
                t["stage_text"] = STAGE_NAMES["summary"]
        # 检测完成：自学习总开关开启时留存原始待检测文档（供人工修订配对学习）
        try:
            if ai_memory_is_enabled():
                ai_memory_save_source_docs(items)
        except Exception:  # noqa: BLE001 - 留存失败不影响检测
            pass


def _task_alive(tid: str) -> bool:
    """任务是否未被取消（供进度钩子内部使用）。"""
    with _TASK_LOCK:
        t = _TASKS.get(tid)
        return bool(t and not t["cancel"].is_set())


def _run_task(tid: str, items: List[tuple], append: bool = False) -> None:
    """后台线程包装：检测异常时任务置 error，避免前台一直等待。"""
    try:
        _run_detection(items, append, tid)
    except Exception as exc:  # noqa: BLE001 - 任务级兜底
        with _TASK_LOCK:
            t = _TASKS.get(tid)
            if t and t["status"] == "running":
                t["status"] = "error"
                t["error"] = f"{type(exc).__name__}：{str(exc)[:120]}"


def _run_ai_verify(items: List[tuple], results: List[Any], ai_cfg: Dict[str, Any],
                   tid: str = "", base_progress: float = 0.0,
                   total_items: int = 1) -> List[Any]:
    """对已检测文件追加 AI 智能核验（失败降级为提示条目，不中断整体任务）。"""
    if tid and not _task_alive(tid):
        return results
    from checkers.base import Issue, STATUS_ISSUE
    limit = load_rules().max_issues_per_file()
    ai_workers = max(1, min(2, int(ai_cfg.get("ai_workers", 1) or 1)))
    mode = ai_cfg.get("mode") or "local"
    name_of = {n: p for p, n in items}
    by_name = {r.file_name: r for r in results}
    target = [(p, n) for p, n in items
              if n in by_name and by_name[n].status != STATUS_UNREADABLE]
    hook = _task_hook(tid) if tid else None
    if hook:
        hook(base_progress + 1.0, "ai",
             f"AI 智能核验启动：{len(target)} 个文件（本地推理较慢，请耐心等待）…")

    def _one(pair):
        p, n = pair
        res = by_name[n]
        cancelled = (lambda: (tid and not _task_alive(tid))) if tid else None
        # 流式推理缓冲：本文件重置，逐 token 追加（前端轮询实时展示）
        with _TASK_LOCK:
            t0 = _TASKS.get(tid)
            if t0:
                t0["ai_stream"] = {"file": n, "chunk": 0, "total": 0,
                                   "content": "", "thinking": "",
                                   "preview": "", "history": []}

        def _tok(text: str, kind: str) -> None:
            with _TASK_LOCK:
                t = _TASKS.get(tid)
                if not t:
                    return
                s = t.get("ai_stream")
                if not s:
                    return
                key = "thinking" if kind == "thinking" else "content"
                s[key] = (s.get(key, "") + text)[-20000:]

        def _chunk(k: int, total: int, preview: str) -> None:
            with _TASK_LOCK:
                t = _TASKS.get(tid)
                if not t:
                    return
                s = t.get("ai_stream")
                if not s:
                    return
                # 归档上一段输出，供界面分段回看
                if s.get("content") or s.get("thinking"):
                    s.setdefault("history", []).append({
                        "chunk": s.get("chunk"), "total": s.get("total"),
                        "content": s.get("content", ""), "thinking": s.get("thinking", ""),
                        "preview": s.get("preview", ""),
                    })
                    s["history"] = s["history"][-12:]
                s["chunk"] = k
                s["total"] = total
                s["content"] = ""
                s["thinking"] = ""
                s["preview"] = preview

        try:
            added, note = ai_check_file(p, detect_file_type(n), res.issues,
                                        cfg=ai_cfg, limit=limit,
                                        max_files_issues=limit,
                                        cancel=cancelled,
                                        on_token=_tok, on_chunk=_chunk)
        except Exception as exc:  # noqa: BLE001 - AI 阶段异常不中断
            added, note = 0, f"AI 核验异常：{type(exc).__name__}：{str(exc)[:100]}"
        if added:
            res.status = STATUS_ISSUE
        elif note:
            res.issues.append(Issue(
                rule_key="ai_verify",
                rule_title="AI 智能核验",
                severity="low",
                location="—",
                detail=note,
                snippet="",
                suggestion="请检查本地 AI 服务是否启动、模型是否可用，或检查联网 AI 接口配置。",
                source="ai",
            ))
        return n, added, note, mode

    with ThreadPoolExecutor(max_workers=ai_workers) as ex:
        futs = [ex.submit(_one, pair) for pair in target]
        for i, fut in enumerate(futs, start=1):
            if tid and not _task_alive(tid):
                for f in futs[i-1:]:
                    f.cancel()
                break
            _n, _added, _note, _mode = fut.result()
            if hook:
                pct = base_progress + 10.0 * i / max(len(target), 1)
                if not hook(pct, "ai", f"AI 智能核验：{i}/{max(len(target), 1)} 个文件已完成"):
                    for f in futs[i:]:
                        f.cancel()
                    break
    return results


def _state_payload() -> Dict[str, Any]:
    summary = summarize(_STATE["results"])
    return {
        "results": [_serialize_result(r) for r in _STATE["results"]],
        "summary": summary,
        "last_scan_time": (_STATE["last_scan_time"].strftime("%Y-%m-%d %H:%M:%S")
                           if _STATE["last_scan_time"] else None),
        "cache": _cache_stats(),
    }


# ---------------------------------------------------------------------------
# 概览 / 状态
# ---------------------------------------------------------------------------
@app.get("/api/overview")
def api_overview():
    summary = summarize(_STATE["results"])
    cache = _cache_stats()
    status = [
        ("网络状态", "离线（零请求）"),
        ("已导入文件", str(len(_STATE["results"]))),
        ("发现问题", str(int(summary["total_issues"]))),
        ("本地缓存", f"{cache['count']} 个 / {cache['size_text']}"),
        ("上次检测", (_STATE["last_scan_time"].strftime("%m-%d %H:%M:%S")
                      if _STATE["last_scan_time"] else "尚未执行")),
    ]
    # 最近问题（按严重度取前 10 条）
    sev_weight = {"high": 3, "medium": 2, "low": 1}
    recent = []
    for fi, res in enumerate(_STATE["results"]):
        for ii, iss in enumerate(res.issues):
            if iss.state == "ignored":
                continue
            recent.append({
                "file_name": res.file_name,
                "file_type": res.file_type,
                "severity": iss.severity,
                "rule_title": iss.rule_title,
                "location": iss.location,
                "detail": iss.detail,
            })
    recent.sort(key=lambda x: sev_weight.get(x["severity"], 0), reverse=True)
    return {
        "summary": summary,
        "status": status,
        "cache": cache,
        "recent_issues": recent[:10],
        "rule_summary": _rule_summary(),
    }


@app.get("/api/files")
def api_files():
    return {"results": [_serialize_result(r) for r in _STATE["results"]]}


@app.get("/api/issues")
def api_issues():
    out = []
    for fi, res in enumerate(_STATE["results"]):
        for ii, iss in enumerate(res.issues):
            d = iss.to_dict()
            d.update({"file_index": fi, "issue_index": ii, "file_name": res.file_name,
                      "file_type": res.file_type})
            out.append(d)
    return {"issues": out}


@app.post("/api/issue_state")
def api_issue_state(payload: Dict[str, Any]):
    fi, ii, stt = payload.get("file_index"), payload.get("issue_index"), payload.get("state")
    try:
        _STATE["results"][fi].issues[ii].state = stt
    except Exception:
        raise HTTPException(400, "无效的条目索引")
    return {"ok": True}


# ---------------------------------------------------------------------------
# 导入与检测
# ---------------------------------------------------------------------------
@app.post("/api/upload")
async def api_upload(files: List[UploadFile] = File(...)):
    stamp = datetime.now().strftime("%H%M%S")
    items = []
    for idx, uf in enumerate(files):
        safe = os.path.basename(uf.filename or f"file_{idx}")
        dst = os.path.join(CACHE_DIR, f"{stamp}_{idx:03d}_{safe}")
        try:
            content = await uf.read()
            with open(dst, "wb") as fp:
                fp.write(content)
            items.append((dst, safe))
        except OSError:
            continue
    if not items:
        raise HTTPException(400, "没有可写入的合法文件")
    tid = _make_task([dst for dst, _ in items])
    threading.Thread(target=_run_task, args=(tid, items, False), daemon=True).start()
    return {"ok": True, "task_id": tid}


@app.post("/api/scan_folder")
def api_scan_folder(payload: Dict[str, Any]):
    folder = payload.get("folder", "")
    recursive = bool(payload.get("recursive", True))
    if not folder or not os.path.isdir(folder):
        raise HTTPException(400, "文件夹不存在或无访问权限")
    paths = collect_folder(folder, recursive=recursive)
    items = [(p, os.path.basename(p)) for p in paths]
    if not items:
        raise HTTPException(400, "该文件夹下未找到支持的 Office 文件")
    tid = _make_task([])
    threading.Thread(target=_run_task, args=(tid, items, False), daemon=True).start()
    return {"ok": True, "task_id": tid}


@app.get("/api/task/{tid}")
def api_task(tid: str):
    """查询核验任务状态：进度 / 阶段 / 思考日志（轮询用）。"""
    snap = _task_snapshot(tid)
    if snap is None:
        raise HTTPException(404, "任务不存在或已过期")
    return snap


@app.post("/api/task/{tid}/cancel")
def api_task_cancel(tid: str):
    """取消核验任务：终止检测、清空该任务的中间缓存文件。"""
    if not _cancel_task(tid):
        raise HTTPException(404, "任务不存在或已过期")
    return {"ok": True}


@app.post("/api/clear_data")
def api_clear_data():
    _STATE["results"] = []
    _STATE["last_scan_time"] = None
    _STATE["scan_seconds"] = 0.0
    return {"ok": True}


@app.post("/api/clear_cache")
def api_clear_cache():
    return _clear_cache()


# ---------------------------------------------------------------------------
# 规则 / 自定义规则 / 词库
# ---------------------------------------------------------------------------
@app.get("/api/rules")
def api_rules():
    return load_rules().data


@app.post("/api/rules")
def api_save_rules(payload: Dict[str, Any]):
    ok = save_rules(RuleConfig(payload))
    if not ok:
        raise HTTPException(500, "规则保存失败")
    return {"ok": True}


@app.post("/api/rules/restore")
def api_restore_rules():
    # 复用 settings_manager 的相同原子写回逻辑：直接删除 rules.json 让其重建默认
    import shutil
    rp = os.path.join(ROOT, "config", "rules.json")
    try:
        if os.path.exists(rp):
            os.remove(rp)
    except OSError:
        pass
    return {"ok": True, "data": load_rules().data}


# ---------------------------------------------------------------------------
# 内置词库（dictionaries/*.txt，与 textnorm 检测项一一对应）
# ---------------------------------------------------------------------------
def _rule_titles() -> Dict[str, str]:
    cfg = load_rules()
    out: Dict[str, str] = {}
    for kind in ("word", "excel", "textnorm"):
        for key, rule in cfg.rule_items(kind):
            if isinstance(rule, dict):
                out[key] = str(rule.get("title", key))
    return out


@app.get("/api/dictionaries")
def api_dictionaries():
    return {"files": list_dictionaries(_rule_titles())}


@app.get("/api/dictionaries/{name}")
def api_dictionary_get(name: str):
    content = read_dictionary(name)
    if content is None:
        raise HTTPException(404, "词库不存在或名称非法")
    return {"name": name, "content": content}


@app.post("/api/dictionaries/{name}")
def api_dictionary_save(name: str, payload: Dict[str, Any]):
    content = payload.get("content")
    if not isinstance(content, str):
        raise HTTPException(400, "content 必须为字符串")
    if not save_dictionary(name, content):
        raise HTTPException(400, "词库保存失败（名称非法 / 内容超限 / 写入错误）")
    return {"ok": True}


@app.get("/api/custom_rules")
def api_custom_rules():
    return load_custom_rules()


@app.post("/api/custom_rules")
def api_save_custom_rules(payload: Dict[str, Any]):
    """保存自定义规则：入库前置校验（所有渠道统一），无效规则过滤并记录日志。"""
    cleaned, dropped = rule_filter_rules_payload(payload, channel="manual_save")
    if not save_custom_rules(cleaned):
        raise HTTPException(500, "自定义规则保存失败")
    return {"ok": True, "filtered_rules": len(dropped), "rejected": dropped}


@app.get("/api/wordbanks")
def api_wordbanks():
    return load_wordbanks()


@app.post("/api/wordbanks")
def api_save_wordbanks(payload: Dict[str, Any]):
    """保存自定义词库：入库前置校验，无效词条过滤并记录日志。"""
    cleaned, dropped = rule_filter_entries_payload(payload, channel="manual_save")
    if not save_wordbanks(cleaned):
        raise HTTPException(500, "词库保存失败")
    return {"ok": True, "filtered_entries": len(dropped), "rejected": dropped}


@app.post("/api/wordbanks/import")
def api_wordbanks_import(payload: Dict[str, Any]):
    """批量解析词条文本（CSV/TXT），返回解析后的 Entry 列表。"""
    raw = payload.get("text", "")
    return {"entries": parse_entries_import(raw)}


# ---------------------------------------------------------------------------
# 规则与词库一键扫描管理（扫描分类 / 备份导出 / 清理）
# ---------------------------------------------------------------------------
@app.post("/api/scan/start")
def api_scan_start():
    """启动规则词库后台扫描任务（进度经 /api/task/{tid} 轮询）。"""
    tid = _make_task([])
    threading.Thread(target=_run_scan_task, args=(tid,), daemon=True).start()
    return {"ok": True, "task_id": tid}


def _run_scan_task(tid: str) -> None:
    """后台执行规则词库扫描：进度写入任务，结果写入任务与内存态。"""
    try:
        hook = _task_hook(tid)
        result = rule_scan_all(hook)
        with _TASK_LOCK:
            t = _TASKS.get(tid)
            if not t:
                return
            t["status"] = "done"
            t["progress"] = 100.0
            t["stage_text"] = "扫描完成"
            t["result"] = result
        _SCAN_STATE["last"] = result
    except Exception as exc:  # noqa: BLE001 - 扫描失败任务置 error
        with _TASK_LOCK:
            t = _TASKS.get(tid)
            if t and t["status"] == "running":
                t["status"] = "error"
                t["error"] = f"{type(exc).__name__}：{str(exc)[:120]}"


@app.get("/api/scan/last")
def api_scan_last():
    """最近一次扫描结果（刷新页面后仍可查看）。"""
    return {"ok": True, "result": _SCAN_STATE.get("last")}


def _scan_items_by_ids(ids: List[str]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """按 item_id 从最新扫描结果中取条目；ids 为空时取全部待清理项。"""
    result = rule_scan_all()
    index = {it["item_id"]: it for it in result["items"]}
    if not ids:
        ids = rule_scan_cleanable_ids(result)
    items = [index[i] for i in ids if i in index]
    return items, result


@app.post("/api/scan/export")
def api_scan_export(payload: Dict[str, Any]):
    """导出待清理数据备份（txt / csv）；ids 为空时导出全部无效 + 重复非保留项。"""
    from fastapi.responses import Response
    fmt = (payload.get("format") or "txt").lower()
    ids = payload.get("ids") or []
    items, _result = _scan_items_by_ids(ids)
    if not items:
        raise HTTPException(400, "没有可导出的条目（当前无无效或重复项）")
    if fmt == "csv":
        content = "\ufeff" + rule_scan_backup_csv(items)
        media = "text/csv; charset=utf-8"
        ext = "csv"
    else:
        content = rule_scan_backup_text(items, title="手动备份导出")
        media = "text/plain; charset=utf-8"
        ext = "txt"
    fname = f"scan_backup_{time.strftime('%Y%m%d_%H%M%S')}.{ext}"
    return Response(
        content=content, media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/api/scan/clean")
def api_scan_clean(payload: Dict[str, Any]):
    """按 item_id 清理无效 / 重复条目；ids 为空时清理全部待清理项。

    安全红线：normal 与重复保留项一律拒绝；记忆样本不触碰；
    清理前自动备份到 reports/scan_backups/。
    """
    ids = payload.get("ids") or []
    ok, msg, info = rule_scan_clean(ids)
    if not ok:
        raise HTTPException(400, msg)
    _SCAN_STATE["last"] = None  # 数据已变化，前端将重新扫描刷新
    return {"ok": True, "message": msg, **info}


# ---------------------------------------------------------------------------
# 范本导入生成规则
# ---------------------------------------------------------------------------
@app.post("/api/template/upload")
async def api_template_upload(files: List[UploadFile] = File(...), category: str = Form("general")):
    """上传外部基准文件（.docx/.pdf/.txt/.csv/.scel，批量）并解析生成规则 / 词库草案。

    category：外部基准类别（general/industry/asset/practice/correction/forbidden/official）。
    离线安全：文件内容仅在本地内存（BytesIO）中处理，不写入磁盘、不外传。
    """
    parser = TemplateParser()
    inputs = []
    for idx, uf in enumerate(files):
        name = os.path.basename(uf.filename or f"file_{idx}")
        ext = os.path.splitext(name)[1].lower()
        if ext not in (".docx", ".pdf", ".txt", ".csv", ".scel"):
            continue
        content = await uf.read()
        if len(content) > _MAX_TEMPLATE_MB * 1024 * 1024:
            continue
        inputs.append((io.BytesIO(content), name))
    if not inputs:
        raise HTTPException(400, "没有可解析的基准文件（支持 .docx / .pdf / .txt / .csv / .scel，单个不超过 50MB）")
    draft = parser.parse_files(inputs, category=category)
    _TEMPLATE_STATE["parser"] = parser
    return draft


@app.get("/api/template/draft")
def api_template_draft():
    """获取当前范本解析草案（无草案时返回空结构）。"""
    p = _TEMPLATE_STATE.get("parser")
    if not p:
        return {"docs": [], "rules": [], "entries": [], "conflicts": [], "references": []}
    return p.draft()


@app.post("/api/template/select")
def api_template_select(payload: Dict[str, Any]):
    """更新草案选中状态（rule_ids / entry_ids 未列出的项视为取消选择）。"""
    p = _TEMPLATE_STATE.get("parser")
    if not p:
        raise HTTPException(400, "当前没有范本解析草案，请先上传范本")
    p.set_selected(payload.get("rule_ids", []), payload.get("entry_ids", []))
    return {"ok": True}


@app.post("/api/template/import")
def api_template_import(payload: Dict[str, Any]):
    """选择性确认导入：仅追加选中项到自定义规则 / 自定义词库，绝不覆盖原有配置。"""
    p = _TEMPLATE_STATE.get("parser")
    if not p:
        raise HTTPException(400, "当前没有范本解析草案，请先上传范本")
    rule_ids = payload.get("rule_ids") or []
    entry_ids = payload.get("entry_ids") or []
    if not rule_ids and not entry_ids:
        raise HTTPException(400, "未选择任何规则或词条，请先勾选要导入的内容")
    built = p.build_import(rule_ids, entry_ids)

    imported_rules = imported_entries = 0
    filtered_rules = filtered_entries = 0
    rejected = []
    from checkers.rule_filter import build_existing, filter_rules, filter_entries
    existing = build_existing()
    if built["rule_count"]:
        data = load_custom_rules()
        # 范本解析渠道：入库前置校验（仅过滤新导入组，不触碰库内既有规则）
        group = built["rule_group"]
        kept, dropped = filter_rules(group.get("rules", []), existing, "template_import")
        group["rules"] = kept
        if not group["rules"]:
            built["rule_count"] = 0
        else:
            data.setdefault("groups", []).append(group)
            if not save_custom_rules(data):
                raise HTTPException(500, "自定义规则写入失败")
        imported_rules = len(kept)
        filtered_rules = len(dropped)
        rejected.extend(dropped)
        existing["patterns"].update(
            (str(r.get("match_mode") or "keyword"), str(r.get("pattern") or "").strip())
            for r in kept if str(r.get("pattern") or "").strip())
    if built["entry_count"]:
        data = load_wordbanks()
        group = built["entry_group"]
        kept, dropped = filter_entries(group.get("entries", []), existing, "template_import")
        group["entries"] = kept
        if not group["entries"]:
            built["entry_count"] = 0
        else:
            data.setdefault("groups", []).append(group)
            if not save_wordbanks(data):
                raise HTTPException(500, "自定义词库写入失败")
        imported_entries = len(kept)
        filtered_entries = len(dropped)
        rejected.extend(dropped)

    return {"ok": True, "imported_rules": imported_rules, "imported_entries": imported_entries,
            "filtered_rules": filtered_rules, "filtered_entries": filtered_entries,
            "rejected": rejected}


@app.post("/api/template/clear")
def api_template_clear():
    """清空当前范本解析草案。"""
    _TEMPLATE_STATE["parser"] = None
    return {"ok": True}


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------
@app.get("/api/settings")
def api_settings():
    return load_settings()


@app.post("/api/settings")
def api_save_settings(payload: Dict[str, Any]):
    if not save_settings(payload):
        raise HTTPException(500, "设置保存失败")
    return {"ok": True}


@app.post("/api/settings/restore")
def api_restore_settings():
    return restore_defaults()


# ---------------------------------------------------------------------------
# AI 智能核验（可选：本地 AI / 联网 AI）
# ---------------------------------------------------------------------------
@app.get("/api/ai/status")
def api_ai_status():
    return {"ai": (load_settings().get("ai") or {})}


@app.get("/api/ai/models")
def api_ai_models():
    """获取本地 Ollama 已安装模型列表（供前端下拉选择；Ollama 未运行返回空）。"""
    import json
    import urllib.error
    import urllib.request
    ai = load_settings().get("ai") or {}
    base = str(ai.get("base_url") or "http://127.0.0.1:11434").rstrip("/")
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        names = sorted(str(m.get("name") or "") for m in data.get("models", []) if m.get("name"))
        return {"ok": True, "models": names}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "models": [], "message": f"无法连接本地 AI 服务：{exc}"}


@app.post("/api/ai/test")
def api_ai_test(payload: Dict[str, Any]):
    """测试 AI 连接（mode/base_url/api_key/model 传入即覆盖设置）。"""
    cfg = {**(load_settings().get("ai") or {}), **{k: v for k, v in payload.items()
                                                    if k in ("mode", "base_url", "api_key", "model", "timeout")}}
    ok, msg = ai_test_connection(cfg)
    return {"ok": ok, "message": msg}


# --- 参考资料（标准/词汇/规范，AI 核验时自动携带） ---
@app.get("/api/ai/refs")
def api_ai_refs():
    return {"refs": list_refs()}


@app.post("/api/ai/refs/upload")
async def api_ai_refs_upload(files: List[UploadFile] = File(...)):
    """上传参考资料（multipart files，支持 .txt/.md/.csv/.docx/.pdf）。"""
    if not files:
        return {"ok": False, "message": "未收到文件"}
    results, errors = [], []
    for f in files:
        try:
            raw = await f.read()
            if len(raw) > 5 * 1024 * 1024:
                errors.append(f"{f.filename}：文件过大（>5MB）")
                continue
            results.append(save_ref(f.filename or "ref.txt", raw))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{f.filename}：{exc}")
    return {"ok": bool(results), "message": f"已保存 {len(results)} 个参考资料"
            + (f"，失败 {len(errors)} 个：" + "；".join(errors) if errors else ""),
            "refs": list_refs()}


@app.post("/api/ai/refs/toggle")
def api_ai_refs_toggle(payload: Dict[str, Any]):
    name = str(payload.get("name") or "")
    ok = set_ref_enabled(name, bool(payload.get("enabled", True)))
    return {"ok": ok, "refs": list_refs()}


@app.post("/api/ai/refs/delete")
def api_ai_refs_delete(payload: Dict[str, Any]):
    name = str(payload.get("name") or "")
    existed = delete_ref(name)
    return {"ok": existed, "refs": list_refs()}


# --- AI 规则/词库智能生成（对话式 + 文本式 + 文档式，统一入库校验与来源标记） ---
def _ai_create_enabled() -> bool:
    """全局总开关「启用本地AI智能生成&自学习」（settings.ai.create_enabled）。"""
    return bool((load_settings().get("ai") or {}).get("create_enabled", True))


def _mark_source(result: Dict[str, Any], source: str, label: str) -> None:
    """给生成结果打来源标记（对话创建 / 文本创建），保存时写入规则词库。"""
    for r in result.get("rules") or []:
        r.setdefault("source", source)
        r.setdefault("tag", label)
    for w in result.get("wordbanks") or []:
        for e in w.get("entries") or []:
            e.setdefault("source", source)
            e.setdefault("tag", label)


@app.post("/api/ai/build/dialogue")
def api_ai_build_dialogue(payload: Dict[str, Any]):
    """对话式创建：自然语言描述需求 → 生成词库分组 + 规则（入库前置校验）。"""
    if not _ai_create_enabled():
        return {"ok": False, "message": "AI 智能生成已关闭（后台设置总开关），可手动编写或导入规则",
                "result": {"wordbanks": [], "rules": []}}
    ai = load_settings().get("ai") or {}
    ok, msg, result = ai_build_dialogue(str(payload.get("text") or ""), ai)
    filter_stat = rule_filter_generated(result, channel="ai_build") if ok else {}
    if ok:
        _mark_source(result, "ai_dialogue", "AI对话创建")
    return {"ok": ok, "message": msg, "result": result, "filter": filter_stat}


@app.post("/api/ai/build/text")
def api_ai_build_text(payload: Dict[str, Any]):
    """文本式创建：粘贴准则/规范/范本文本 → AI 读取批量生成词库与规则（入库前置校验）。"""
    if not _ai_create_enabled():
        return {"ok": False, "message": "AI 智能生成已关闭（后台设置总开关），可手动编写或导入规则",
                "result": {"wordbanks": [], "rules": []}}
    ai = load_settings().get("ai") or {}
    ok, msg, result = ai_build_text(str(payload.get("text") or ""), ai)
    filter_stat = rule_filter_generated(result, channel="ai_build") if ok else {}
    if ok:
        _mark_source(result, "ai_text", "AI文本创建")
    return {"ok": ok, "message": msg, "result": result, "filter": filter_stat}


@app.post("/api/ai/build/doc")
async def api_ai_build_doc(files: List[UploadFile] = File(...)):
    """文档式创建：上传文档（.txt/.md/.csv/.docx/.pdf）→ AI 阅读提取词库与规则。"""
    if not _ai_create_enabled():
        return {"ok": False, "message": "AI 智能生成已关闭（后台设置总开关），可手动编写或导入规则",
                "result": {"wordbanks": [], "rules": []}}
    if not files:
        return {"ok": False, "message": "未收到文件", "result": {"wordbanks": [], "rules": []}}
    f = files[0]
    try:
        raw = await f.read()
        if len(raw) > 10 * 1024 * 1024:
            return {"ok": False, "message": "文件过大（>10MB）", "result": {"wordbanks": [], "rules": []}}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"读取文件失败：{exc}", "result": {"wordbanks": [], "rules": []}}
    ai = load_settings().get("ai") or {}
    ok, msg, result = ai_build_doc(raw, f.filename or "doc.txt", ai)
    filter_stat = rule_filter_generated(result, channel="ai_build") if ok else {}
    if ok:
        _mark_source(result, "ai_text", "AI文本创建")
    return {"ok": ok, "message": msg, "result": result, "filter": filter_stat}


# --- 本地 AI 自学习记忆（人工校对成对样本：系统留存原始文档 + 用户上传修订文档） ---
@app.get("/api/ai_memory")
def api_ai_memory_get():
    """自学习总览：开关 / 配对状态 / 样本列表 / 学习产出 / 统计。"""
    return ai_memory_payload()


@app.post("/api/ai_memory/toggle")
def api_ai_memory_toggle(payload: Dict[str, Any]):
    """自学习总开关。"""
    ai_memory_set_enabled(bool(payload.get("enabled", False)))
    return {"ok": True, "enabled": ai_memory_is_enabled()}


@app.post("/api/ai_memory/pair")
async def api_ai_memory_pair(files: List[UploadFile] = File(...)):
    """上传人工修改后的修订文档，与系统留存的原始检测文档配对。"""
    if not _ai_create_enabled() or not ai_memory_is_enabled():
        return {"ok": False, "message": "本地AI自学习已关闭，请先启用"}
    if not files:
        return {"ok": False, "message": "未收到修订文档", "data": ai_memory_payload()}
    f = files[0]
    try:
        raw = await f.read()
        if len(raw) > 50 * 1024 * 1024:
            return {"ok": False, "message": "修订文档过大（>50MB）", "data": ai_memory_payload()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"读取修订文档失败：{exc}", "data": ai_memory_payload()}
    ok, msg, status = ai_memory_upload_revised(f.filename or "revised.docx", raw)
    return {"ok": ok, "message": msg, "source_status": status, "data": ai_memory_payload()}


@app.post("/api/ai_memory/diffs")
def api_ai_memory_diffs():
    """比对原始文档与修订文档文本差异，返回差异片段（错误 → 正确）供用户确认。"""
    if not _ai_create_enabled() or not ai_memory_is_enabled():
        return {"ok": False, "message": "本地AI自学习已关闭，请先启用", "diffs": []}
    ok, msg, diffs = ai_memory_compute_diffs()
    return {"ok": ok, "message": msg, "diffs": diffs}


@app.post("/api/ai_memory/samples")
def api_ai_memory_add_sample(payload: Dict[str, Any]):
    """用户确认后将配对差异片段加入本地记忆样本库（仅存差异文本，释放完整文档）。"""
    if not _ai_create_enabled() or not ai_memory_is_enabled():
        return {"ok": False, "message": "本地AI自学习已关闭，请先启用"}
    diffs = payload.get("diffs") or []
    ok, msg = ai_memory_add_sample(
        diffs,
        str(payload.get("source_doc") or "—"),
        str(payload.get("revised_doc") or "—"),
        note=str(payload.get("note") or ""),
        content=str(payload.get("content") or ""),
    )
    return {"ok": ok, "message": msg, "data": ai_memory_payload()}


@app.post("/api/ai_memory/samples/{sid}/learn")
def api_ai_memory_learn(sid: str):
    """对成对样本执行本地学习（差异片段 → 词条 + 校验规则）。

    后台线程执行并实时写入推理过程日志（token 流 + 阶段），
    前端通过 /api/ai_memory/samples/{sid}/learn/logs 轮询展示。
    """
    if not _ai_create_enabled():
        return {"ok": False, "message": "AI 智能生成已关闭（后台设置总开关）",
                "stats": {}, "data": ai_memory_payload()}
    ai = load_settings().get("ai") or {}
    from checkers.ai_memory import learn_logs as _mlogs
    from checkers.ai_memory import learn_sample as _mlearn

    def runner():
        try:
            ok, msg, stats = _mlearn(
                sid, ai,
                on_log=lambda stage, info: _mlog(sid, stage, str(info.get("text", ""))),
                on_token=lambda text, kind: _mlog(sid, "token" if kind == "content" else "thinking", text),
                auto_log=False,
            )
            with _TASK_LOCK:
                st = _MLEARN_STATE.setdefault(sid, {})
                st.update({"running": False, "done": True, "ok": ok,
                           "message": msg, "stats": stats})
        except Exception as exc:  # noqa: BLE001 - 后台线程兜底
            with _TASK_LOCK:
                st = _MLEARN_STATE.setdefault(sid, {})
                st.update({"running": False, "done": True, "ok": False,
                           "message": f"{type(exc).__name__}：{str(exc)[:120]}", "stats": {}})

    with _TASK_LOCK:
        _MLEARN_STATE[sid] = {"running": True, "done": False, "ok": False,
                              "message": "", "stats": {}}
        _MLEARN_LOGS[sid] = []
    threading.Thread(target=runner, daemon=True).start()
    return {"ok": True, "running": True, "message": "学习任务已启动（本地推理过程实时展示）"}


_MLEARN_LOGS: Dict[str, List[Dict[str, Any]]] = {}
_MLEARN_STATE: Dict[str, Dict[str, Any]] = {}


def _mlog(sid: str, kind: str, text: str) -> None:
    """写入学习过程日志缓冲（线程安全，带超长截断）。"""
    with _TASK_LOCK:
        buf = _MLEARN_LOGS.setdefault(sid, [])
        if len(buf) >= 20000:
            return
        buf.append({"ts": time.strftime("%H:%M:%S"), "kind": kind, "text": text})


@app.get("/api/ai_memory/samples/{sid}/learn/logs")
def api_ai_memory_learn_logs(sid: str):
    """轮询本地学习推理过程：阶段日志 + 模型 token 流 + 运行状态。"""
    with _TASK_LOCK:
        logs = list(_MLEARN_LOGS.get(sid, []))
        st = dict(_MLEARN_STATE.get(sid, {"running": False, "done": False}))
    return {"ok": True, "logs": logs, "state": st}


@app.post("/api/ai_memory/samples/{sid}/toggle")
def api_ai_memory_sample_toggle(sid: str, payload: Dict[str, Any]):
    """启用 / 禁用样本（禁用的样本不再用于学习）。"""
    import checkers.ai_memory as _m
    data = _m.load_samples()
    s = next((x for x in data.get("samples", []) if x.get("id") == sid), None)
    if not s:
        return {"ok": False, "message": "样本不存在或已被删除"}
    s["enabled"] = bool(payload.get("enabled", True))
    _m.save_samples(data)
    return {"ok": True, "data": ai_memory_payload()}


@app.delete("/api/ai_memory/samples/{sid}")
def api_ai_memory_sample_delete(sid: str):
    """单条删除记忆样本（不影响其已学习产出的规则词条）。"""
    if not ai_memory_sample_delete(sid):
        return {"ok": False, "message": "样本不存在或已被删除"}
    return {"ok": True, "data": ai_memory_payload()}


@app.post("/api/ai_memory/learned/{lid}/toggle")
def api_ai_memory_learned_toggle(lid: str, payload: Dict[str, Any]):
    """启用 / 禁用学习产出的词库条目或校验规则（同步影响检测）。"""
    if not ai_memory_toggle(lid, bool(payload.get("enabled", True))):
        return {"ok": False, "message": "学习产出不存在或已被删除"}
    return {"ok": True, "data": ai_memory_payload()}


@app.delete("/api/ai_memory/learned/{lid}")
def api_ai_memory_learned_delete(lid: str):
    """删除学习产出（从词库 / 自定义规则中移除，不影响其它数据）。"""
    if not ai_memory_delete(lid):
        return {"ok": False, "message": "学习产出不存在或已被删除"}
    return {"ok": True, "data": ai_memory_payload()}


@app.post("/api/ai_memory/clear")
def api_ai_memory_clear():
    """批量清空本地学习记忆（仅学习生成的数据，不动用户手动导入编写的规则词库）。"""
    removed = ai_memory_clear()
    return {"ok": True, "removed": removed, "data": ai_memory_payload()}


@app.get("/api/ai_memory/export")
def api_ai_memory_export(format: str = "txt", kind: str = "wordbanks"):
    """导出自学习产出的词库/规则文件（csv / txt，用于备份迁移）。"""
    from fastapi.responses import Response
    ok, msg, fname, content = ai_memory_export(format, kind)
    if not ok:
        raise HTTPException(400, msg)
    media = "text/csv; charset=utf-8" if format == "csv" else "text/plain; charset=utf-8"
    return Response(
        content="\ufeff" + content if format == "csv" else content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ---------------------------------------------------------------------------
# 报告导出
# ---------------------------------------------------------------------------
def _filter_results_for_report(results: List[Any], mode: str) -> List[Any]:
    """按导出范围过滤检测结果（不修改内存中的原始结果）。

    mode:
        all      全部问题（默认）
        fluency  仅语句通顺类问题（fl_logic / fl_incomplete / fl_order /
                 fl_repeat / fl_conj / fl_mixed）
    """
    if mode != "fluency":
        return results
    out = []
    for res in results:
        kept = [i for i in res.issues if i.rule_key in FLUENCY_RULE_KEYS]
        if kept:
            import copy as _copy
            r2 = _copy.copy(res)
            r2.issues = kept
            out.append(r2)
    return out


@app.post("/api/report")
def api_report(payload: Dict[str, Any]):
    if not _STATE["results"]:
        raise HTTPException(400, "暂无检测结果，无法生成报告")
    operator = payload.get("operator", "")
    org = payload.get("org", "")
    include_cover = bool(payload.get("include_cover", True))
    detail_columns = payload.get("detail_columns")
    mode = payload.get("report_filter", "all") or "all"
    filtered = _filter_results_for_report(_STATE["results"], mode)
    if mode == "fluency" and not filtered:
        raise HTTPException(400, "当前检测结果中未发现语句通顺类问题，无法生成筛选报告")
    summary = summarize(filtered)
    name = default_report_name()
    out_path = os.path.join(REPORT_DIR, name)
    build_report(
        filtered, summary, out_path,
        rule_summary=_rule_summary(),
        operator=operator, org=org,
        include_cover=include_cover, detail_columns=detail_columns,
    )
    return FileResponse(
        out_path, filename=name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


# ---------------------------------------------------------------------------
# 静态资源（含 index.html）
# ---------------------------------------------------------------------------
if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8501)
