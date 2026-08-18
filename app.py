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
from checkers.ai_builder import build_dialogue as ai_build_dialogue, build_from_doc as ai_build_doc
from checkers.ai_memory import (
    payload as ai_memory_payload, set_enabled as ai_memory_set_enabled,
    is_enabled as ai_memory_is_enabled, learn_sample as ai_memory_learn,
    toggle_learned as ai_memory_toggle, delete_learned as ai_memory_delete,
    clear_all as ai_memory_clear,
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
        try:
            added, note = ai_check_file(p, detect_file_type(n), res.issues,
                                        cfg=ai_cfg, limit=limit,
                                        max_files_issues=limit,
                                        cancel=cancelled)
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
    if not save_custom_rules(payload):
        raise HTTPException(500, "自定义规则保存失败")
    return {"ok": True}


@app.get("/api/wordbanks")
def api_wordbanks():
    return load_wordbanks()


@app.post("/api/wordbanks")
def api_save_wordbanks(payload: Dict[str, Any]):
    if not save_wordbanks(payload):
        raise HTTPException(500, "词库保存失败")
    return {"ok": True}


@app.post("/api/wordbanks/import")
def api_wordbanks_import(payload: Dict[str, Any]):
    """批量解析词条文本（CSV/TXT），返回解析后的 Entry 列表。"""
    raw = payload.get("text", "")
    return {"entries": parse_entries_import(raw)}


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
    if built["rule_count"]:
        data = load_custom_rules()
        data.setdefault("groups", []).append(built["rule_group"])
        if not save_custom_rules(data):
            raise HTTPException(500, "自定义规则写入失败")
        imported_rules = built["rule_count"]
    if built["entry_count"]:
        data = load_wordbanks()
        data.setdefault("groups", []).append(built["entry_group"])
        if not save_wordbanks(data):
            raise HTTPException(500, "自定义词库写入失败")
        imported_entries = built["entry_count"]

    return {"ok": True, "imported_rules": imported_rules, "imported_entries": imported_entries}


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


# --- AI 规则/词库生成（对话式 + 文档自建） ---
@app.post("/api/ai/build/dialogue")
def api_ai_build_dialogue(payload: Dict[str, Any]):
    """自然语言描述需求 → 生成词库分组 + 规则。"""
    ai = load_settings().get("ai") or {}
    ok, msg, result = ai_build_dialogue(str(payload.get("text") or ""), ai)
    return {"ok": ok, "message": msg, "result": result}


@app.post("/api/ai/build/doc")
async def api_ai_build_doc(files: List[UploadFile] = File(...)):
    """上传文档（.txt/.md/.csv/.docx/.pdf）→ AI 阅读提取词库与规则。"""
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
    return {"ok": ok, "message": msg, "result": result}


# --- 本地 AI 自学习记忆（全程离线，仅学习人工确认样本） ---
@app.get("/api/ai_memory")
def api_ai_memory_get():
    """自学习总览：开关 / 样本列表 / 学习产出 / 统计。"""
    return ai_memory_payload()


@app.post("/api/ai_memory/toggle")
def api_ai_memory_toggle(payload: Dict[str, Any]):
    """自学习总开关。"""
    ai_memory_set_enabled(bool(payload.get("enabled", False)))
    return {"ok": True, "enabled": ai_memory_is_enabled()}


@app.post("/api/ai_memory/samples")
def api_ai_memory_add_sample(payload: Dict[str, Any]):
    """添加人工确认正确的学习样本（AI 不自动采集，仅用户主动提交）。"""
    if not ai_memory_is_enabled():
        return {"ok": False, "message": "本地AI自学习已关闭，请先在总开关中启用"}
    content = str(payload.get("content") or "").strip()
    if not content:
        return {"ok": False, "message": "样本内容不能为空"}
    if len(content) > 2000:
        return {"ok": False, "message": "样本内容过长（上限 2000 字），请截取关键片段"}
    import checkers.ai_memory as _m
    data = _m.load_samples()
    data.setdefault("samples", []).append({
        "id": _m._gen_id("s"),
        "content": content,
        "source": str(payload.get("source") or "")[:120],
        "note": str(payload.get("note") or "")[:200],
        "status": "pending",
        "enabled": True,
        "learned_at": None,
        "result_count": 0,
        "error": "",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    _m.save_samples(data)
    return {"ok": True, "message": "样本已加入本地学习库", "data": ai_memory_payload()}


@app.post("/api/ai_memory/samples/{sid}/learn")
def api_ai_memory_learn(sid: str):
    """对人工确认样本执行本地学习（提炼标准表述 → 词库条目 + 校验规则）。"""
    ai = load_settings().get("ai") or {}
    ok, msg, stats = ai_memory_learn(sid, ai)
    return {"ok": ok, "message": msg, "stats": stats, "data": ai_memory_payload()}


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
    """删除样本（不影响其已学习产出的规则词条，产出在下方列表中管理）。"""
    import checkers.ai_memory as _m
    data = _m.load_samples()
    data["samples"] = [s for s in data.get("samples", []) if s.get("id") != sid]
    _m.save_samples(data)
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
