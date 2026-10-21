# -*- coding: utf-8 -*-
"""检测执行与公共服务层。

从原 app.py 的「序列化与工具」段整体迁出：
结果序列化、缓存统计与清理、规则摘要、检测执行、AI 核验执行、状态快照。
"""
from __future__ import annotations

import io
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException, UploadFile
from fastapi.responses import JSONResponse

from checkers.base import STATUS_PASS, STATUS_UNREADABLE, detect_file_type
from checkers.scanner import check_single, collect_folder, summarize, _is_blank
from config.config_manager import load_rules, save_rules, RuleConfig
from config.settings_manager import load_settings
from checkers.custom_rules import load_custom_rules
from checkers.wordbank import load_wordbanks
from checkers.fluency_checker import RULE_KEYS as FLUENCY_RULE_KEYS
from checkers.ai_checker import (
    ai_enabled, ai_config, run_ai_verify, merge_ai_results, ai_logs,
    clear_ai_logs, AIError,
)
from checkers import ai_activity
from checkers import activity as rule_activity
from checkers.rule_filter import filter_generated as rule_filter_generated

from server import state
from server.state import (
    ROOT, STATIC_DIR, CACHE_DIR, REPORT_DIR, STAGE_NAMES,
    WORKSPACES, SCAN_STATE, TEMPLATE_STATES, TASKS, TASK_LOCK, WS_LOCK,
    workspace, template_state, make_task, task_snapshot, task_hook,
    cancel_task, try_acquire_scan, release_scan,
)

# 序列化与工具
# ---------------------------------------------------------------------------
def serialize_result(res) -> Dict[str, Any]:
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
        "ai_status": res.ai_status,
        "ai_note": res.ai_note,
        "issue_count": res.issue_count,
        "active_issue_count": res.active_issue_count,
        "severity": res.severity_count(),
        "issues": [i.to_dict() for i in res.issues],
    }


def cache_stats() -> Dict[str, Any]:
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


def clear_cache() -> Dict[str, int]:
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


def rule_summary() -> Dict[str, str]:
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


def run_detection(items: List[tuple], append: bool = False, tid: str = "", wsid: str = "default",
                   ai_over: Optional[Dict[str, Any]] = None) -> None:
    """执行批量检测（复用 scanner.check_single，按后台设置并发与超时）。

    tid 非空时走异步任务模式：实时上报整体进度/阶段/思考日志，支持取消。
    结果写入 wsid 对应工作区，多用户彼此隔离。
    ai_over：本次任务的 AI 行为覆盖（enabled / verify_scope），不修改全局设置。
    """
    settings = load_settings()
    det = settings.get("detection", {})
    parse_set = settings.get("parse", {})
    ai_cfg = dict(settings.get("ai", {}) or {})
    if ai_over:
        for k in ("enabled", "verify_scope"):
            if ai_over.get(k) is not None:
                ai_cfg[k] = ai_over[k]
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
    hook = task_hook(tid) if tid else None

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
            if tid and TASKS.get(tid, {}).get("cancel", threading.Event()).is_set():
                return

    # ---- AI 智能核验阶段（可选：local 本地 AI / online 联网 AI）----
    if ai_enabled:
        base = done / total * 90.0
        new_results = run_ai_verify(items, new_results, ai_cfg, tid, base, total)

    if tid and TASKS.get(tid, {}).get("cancel", threading.Event()).is_set():
        return

    if auto_ignore_blank:
        new_results = [
            r for r in new_results
            if not (r.status == STATUS_PASS and r.active_issue_count == 0 and _is_blank(r))
        ]

    state = workspace(wsid)
    if append:
        state["results"] = state["results"] + new_results
    else:
        state["results"] = new_results
    state["last_scan_time"] = datetime.now()
    state["scan_seconds"] = 0.0  # 前端单独计时

    if tid:
        with TASK_LOCK:
            t = TASKS.get(tid)
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


def task_alive(tid: str) -> bool:
    """任务是否未被取消（供进度钩子内部使用）。"""
    with TASK_LOCK:
        t = TASKS.get(tid)
        return bool(t and not t["cancel"].is_set())


def run_task(tid: str, items: List[tuple], append: bool = False, wsid: str = "default",
              ai_over: Optional[Dict[str, Any]] = None) -> None:
    """后台线程包装：检测异常时任务置 error，避免前台一直等待；结束释放扫描闸门。"""
    try:
        run_detection(items, append, tid, wsid, ai_over)
    except Exception as exc:  # noqa: BLE001 - 任务级兜底
        with TASK_LOCK:
            t = TASKS.get(tid)
            if t and t["status"] == "running":
                t["status"] = "error"
                t["error"] = f"{type(exc).__name__}：{str(exc)[:120]}"
    finally:
        release_scan()


def run_ai_verify(items: List[tuple], results: List[Any], ai_cfg: Dict[str, Any],
                   tid: str = "", base_progress: float = 0.0,
                   total_items: int = 1) -> List[Any]:
    """对已检测文件追加 AI 智能核验。

    - verify_scope=issues 时仅核验已有基础问题的文件
    - 每文件结果写入 res.ai_status / res.ai_note（界面徽标 + 任务日志）
    - 失败默认不写入问题列表（fail_as_issue=False），避免「AI 链接失败」
      以伪问题形式污染统计、报告与最近问题；仅当 fail_as_issue=True 时保留旧行为
    """
    if tid and not task_alive(tid):
        return results
    from checkers.base import Issue, STATUS_UNREADABLE, STATUS_ISSUE
    limit = load_rules().max_issues_per_file()
    rate = ai_cfg.get("rate_limit") or {}
    ai_workers = max(1, min(3, int(rate.get("concurrency", 1) or 1)))
    if ai_workers < 1:
        ai_workers = max(1, int(ai_cfg.get("ai_workers", 1) or 1))
    scope = str(ai_cfg.get("verify_scope", "all") or "all")
    fail_as_issue = bool(ai_cfg.get("fail_as_issue", False))
    mode = ai_cfg.get("mode") or "local"
    name_of = {n: p for p, n in items}
    by_name = {r.file_name: r for r in results}
    target = [(p, n) for p, n in items
              if n in by_name and by_name[n].status != STATUS_UNREADABLE]
    if scope == "issues":
        target = [(p, n) for p, n in target if len(by_name[n].issues) > 0]
    hook = task_hook(tid) if tid else None
    if hook:
        hook(base_progress + 1.0, "ai",
             f"AI 智能核验启动：{len(target)} 个文件"
             f"（范围：{'仅命中问题文件' if scope == 'issues' else '全部可解析文件'}；"
             f"本地推理较慢，请耐心等待）…")

    summary = {"files": 0, "hit": 0, "ok": 0, "skip": 0, "fail": 0, "partial": 0}

    def _one(pair):
        p, n = pair
        res = by_name[n]
        cancelled = (lambda: (tid and not task_alive(tid))) if tid else None
        # 流式推理缓冲：本文件重置，逐 token 追加（前端轮询实时展示）
        with TASK_LOCK:
            t0 = TASKS.get(tid)
            if t0:
                t0["ai_stream"] = {"file": n, "chunk": 0, "total": 0,
                                   "content": "", "thinking": "",
                                   "preview": "", "history": []}

        def _tok(text: str, kind: str) -> None:
            with TASK_LOCK:
                t = TASKS.get(tid)
                if not t:
                    return
                s = t.get("ai_stream")
                if not s:
                    return
                key = "thinking" if kind == "thinking" else "content"
                s[key] = (s.get(key, "") + text)[-20000:]

        def _chunk(k: int, total: int, preview: str) -> None:
            with TASK_LOCK:
                t = TASKS.get(tid)
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

        # 归一化为稳定的文件级状态与说明（不再向问题列表写入伪条目）
        note = (note or "").strip()
        if added:
            res.status = STATUS_ISSUE
            status = "ok"
            txt = f"AI 核验完成：命中 {added} 条问题"
        elif note.startswith("AI 核验执行失败") or note.startswith("AI 核验异常"):
            status = "fail"
            txt = note or "AI 核验失败"
        elif note.startswith("部分分段失败"):
            status = "partial"
            txt = note + f"（已命中 {added} 条）"
        elif note.startswith("任务已取消"):
            status = "skip"
            txt = note
        elif note.startswith("未能提取") or note.startswith("AI 核验未启用") or ("跳过" in note):
            status = "skip"
            txt = note
        else:
            status = "ok"
            txt = note or "AI 核验完成"
        res.ai_status = status
        res.ai_note = txt

        # 旧行为兼容：收到明确指示时才把失败作为低级别问题写入结果
        if fail_as_issue and not added and status in ("fail", "partial") and txt:
            res.issues.append(Issue(
                rule_key=AI_RULE_KEY,
                rule_title=AI_RULE_TITLE,
                severity="low",
                location="—",
                detail=txt,
                snippet="",
                suggestion="请增大「单次超时」/「每段字数上限」，或确认本地 AI 服务（Ollama）已启动、模型已就绪。",
                source="ai",
            ))
        return n, added, status, txt

    with ThreadPoolExecutor(max_workers=ai_workers) as ex:
        futs = [ex.submit(_one, pair) for pair in target]
        for i, fut in enumerate(futs, start=1):
            if tid and not task_alive(tid):
                for f in futs[i-1:]:
                    f.cancel()
                break
            _n, _added, _status, _txt = fut.result()
            summary["files"] += 1
            if _added:
                summary["hit"] += 1
            summary[_status] = summary.get(_status, 0) + 1
            if hook:
                pct = base_progress + 10.0 * i / max(len(target), 1)
                log = f"AI 智能核验：{i}/{max(len(target), 1)} 个文件已完成"
                if _status in ("fail", "partial"):
                    log += f"｜{_n}：{_txt[:80]}"
                if not hook(pct, "ai", log):
                    for f in futs[i:]:
                        f.cancel()
                    break

    if tid:
        with TASK_LOCK:
            t = TASKS.get(tid)
            if t:
                t["ai_summary"] = summary
    return results


def state_payload(wsid: str = "default") -> Dict[str, Any]:
    state = workspace(wsid)
    summary = summarize(state["results"])
    return {
        "results": [serialize_result(r) for r in state["results"]],
        "summary": summary,
        "last_scan_time": (state["last_scan_time"].strftime("%Y-%m-%d %H:%M:%S")
                           if state["last_scan_time"] else None),
        "cache": cache_stats(),
    }


# ---------------------------------------------------------------------------
