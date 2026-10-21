# -*- coding: utf-8 -*-
"""规则与词库一键扫描管理：扫描分类、备份导出、清理。"""

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
from reporting.report_builder import build_report, default_report_name
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
    WORKSPACES as _WORKSPACES,
    SCAN_STATE as _SCAN_STATE,
    TEMPLATE_STATES as _TEMPLATE_STATES,
    TASKS as _TASKS,
    TASK_LOCK as _TASK_LOCK,
    WS_LOCK as _WS_LOCK,
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

# 规则与词库一键扫描管理（扫描分类 / 备份导出 / 清理）
# ---------------------------------------------------------------------------
@router.post("/api/scan/start")
def api_scan_start():
    """启动规则词库后台扫描任务（进度经 /api/task/{tid} 轮询）。"""
    if not _try_acquire_scan():
        raise HTTPException(409, "已有扫描任务正在进行，请稍后再试")
    tid = _make_task([])
    try:
        threading.Thread(target=_run_scan_task, args=(tid,), daemon=True).start()
    except Exception:
        _release_scan()
        raise
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
    finally:
        _release_scan()


@router.get("/api/scan/last")
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


@router.post("/api/scan/export")
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


@router.post("/api/scan/clean")
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
