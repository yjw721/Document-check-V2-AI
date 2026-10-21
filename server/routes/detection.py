# -*- coding: utf-8 -*-
"""导入与检测：文件上传、文件夹扫描、异步任务查询与取消。"""

from __future__ import annotations

import hmac
import io
import itertools
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, File, Form, UploadFile, HTTPException, Request, Header
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
    AI_RULE_KEY, AI_RULE_TITLE,
)
from checkers import ai_activity
from checkers import activity as rule_activity
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
from checkers.valuation.valuation_api import router as valuation_router
from checkers.rag_store import (
    kb_status as rag_kb_status, index_docs as rag_index_docs,
    ask as rag_ask, delete_docs as rag_delete_docs,
    clear_kb as rag_clear, doc_file as rag_doc_file,
)


from server import state
from server.state import (
    ROOT, STATIC_DIR, CACHE_DIR, REPORT_DIR, STAGE_NAMES,
    WORKSPACES, SCAN_STATE, TEMPLATE_STATES, TASKS, TASK_LOCK, WS_LOCK,
    workspace as _workspace,
    template_state as _template_state,
    make_task as _make_task,
    task_snapshot as _task_snapshot,
    task_hook as _task_hook,
    cancel_task as _cancel_task,
    try_acquire_scan as _try_acquire_scan,
    release_scan as _release_scan,
)
from server.services.detection import (
    serialize_result as _serialize_result,
    cache_stats as _cache_stats,
    clear_cache as _clear_cache,
    rule_summary as _rule_summary,
    run_detection as _run_detection,
    run_task as _run_task,
    task_alive as _task_alive,
    state_payload as _state_payload,
)

router = APIRouter()

# 导入与检测
# ---------------------------------------------------------------------------
@app.post("/api/upload")
async def api_upload(files: List[UploadFile] = File(...),
                     ws: str = Header("", alias="X-Workspace"),
                     ai_enabled: Optional[str] = Form(None),
                     ai_scope: Optional[str] = Form(None)):
    if not _try_acquire_scan():
        raise HTTPException(409, "已有检测任务正在进行，请稍后再试")
    try:
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
        wsid = (ws or "default").strip() or "default"
        tid = _make_task([dst for dst, _ in items], wsid)
        ai_over = _task_ai_over(ai_enabled, ai_scope)
    except Exception:
        _release_scan()
        raise
    threading.Thread(target=_run_task, args=(tid, items, False, wsid, ai_over), daemon=True).start()
    return {"ok": True, "task_id": tid}


@app.post("/api/scan_folder")
def api_scan_folder(payload: Dict[str, Any], ws: str = Header("", alias="X-Workspace")):
    if not _try_acquire_scan():
        raise HTTPException(409, "已有检测任务正在进行，请稍后再试")
    try:
        folder = payload.get("folder", "")
        recursive = bool(payload.get("recursive", True))
        if not folder or not os.path.isdir(folder):
            raise HTTPException(400, "文件夹不存在或无访问权限")
        paths = collect_folder(folder, recursive=recursive)
        items = [(p, os.path.basename(p)) for p in paths]
        if not items:
            raise HTTPException(400, "该文件夹下未找到支持的 Office 文件")
        wsid = (ws or "default").strip() or "default"
        tid = _make_task([], wsid)
        ai_over = _task_ai_over(payload.get("ai_enabled"), payload.get("ai_scope"))
    except Exception:
        _release_scan()
        raise
    threading.Thread(target=_run_task, args=(tid, items, False, wsid, ai_over), daemon=True).start()
    return {"ok": True, "task_id": tid}


def _task_ai_over(enabled, scope) -> Optional[Dict[str, Any]]:
    """把本次任务的 AI 覆盖参数归一为 ai_cfg 覆盖字典（值为 None 时不覆盖）。"""
    out: Dict[str, Any] = {}
    if enabled in ("1", "on", "true", True, 1):
        out["enabled"] = True
    elif enabled in ("0", "off", "false", False, 0):
        out["enabled"] = False
    if scope in ("all", "issues"):
        out["verify_scope"] = scope
    return out or None


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
def api_clear_data(ws: str = Header("", alias="X-Workspace")):
    state = _workspace(ws)
    state["results"] = []
    state["last_scan_time"] = None
    state["scan_seconds"] = 0.0
    return {"ok": True}


@app.post("/api/clear_cache")
def api_clear_cache():
    return _clear_cache()


# ---------------------------------------------------------------------------
