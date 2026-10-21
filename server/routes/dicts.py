# -*- coding: utf-8 -*-
"""内置词库（dictionaries/*.txt）读写。"""

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


@router.get("/api/dictionaries")
def api_dictionaries():
    return {"files": list_dictionaries(_rule_titles())}


@router.get("/api/dictionaries/{name}")
def api_dictionary_get(name: str):
    content = read_dictionary(name)
    if content is None:
        raise HTTPException(404, "词库不存在或名称非法")
    return {"name": name, "content": content}


@router.post("/api/dictionaries/{name}")
def api_dictionary_save(name: str, payload: Dict[str, Any]):
    content = payload.get("content")
    if not isinstance(content, str):
        raise HTTPException(400, "content 必须为字符串")
    if not save_dictionary(name, content):
        raise HTTPException(400, "词库保存失败（名称非法 / 内容超限 / 写入错误）")
    return {"ok": True}


@router.get("/api/custom_rules")
def api_custom_rules():
    return load_custom_rules()


@router.post("/api/custom_rules")
def api_save_custom_rules(payload: Dict[str, Any]):
    """保存自定义规则：入库前置校验（所有渠道统一），无效规则过滤并记录日志。"""
    cleaned, dropped = rule_filter_rules_payload(payload, channel="manual_save")
    if not save_custom_rules(cleaned):
        raise HTTPException(500, "自定义规则保存失败")
    return {"ok": True, "filtered_rules": len(dropped), "rejected": dropped}


@router.get("/api/wordbanks")
def api_wordbanks():
    return load_wordbanks()


@router.post("/api/wordbanks")
def api_save_wordbanks(payload: Dict[str, Any]):
    """保存自定义词库：入库前置校验，无效词条过滤并记录日志。"""
    cleaned, dropped = rule_filter_entries_payload(payload, channel="manual_save")
    if not save_wordbanks(cleaned):
        raise HTTPException(500, "词库保存失败")
    return {"ok": True, "filtered_entries": len(dropped), "rejected": dropped}


@router.post("/api/wordbanks/import")
def api_wordbanks_import(payload: Dict[str, Any]):
    """批量解析词条文本（CSV/TXT），返回解析后的 Entry 列表。"""
    raw = payload.get("text", "")
    return {"entries": parse_entries_import(raw)}


# ---------------------------------------------------------------------------
