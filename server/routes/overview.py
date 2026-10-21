# -*- coding: utf-8 -*-
"""总览 / 状态查询：概览统计、文件列表、问题列表、问题状态变更。"""

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

# 概览 / 状态
# ---------------------------------------------------------------------------
@router.get("/api/overview")
def api_overview(ws: str = Header("", alias="X-Workspace")):
    state = _workspace(ws)
    summary = summarize(state["results"])
    cache = _cache_stats()
    status = [
        ("网络状态", "离线（零请求）"),
        ("已导入文件", str(len(state["results"]))),
        ("发现问题", str(int(summary["total_issues"]))),
        ("本地缓存", f"{cache['count']} 个 / {cache['size_text']}"),
        ("上次检测", (state["last_scan_time"].strftime("%m-%d %H:%M:%S")
                      if state["last_scan_time"] else "尚未执行")),
    ]
    # 最近问题（按严重度取前 10 条）
    sev_weight = {"high": 3, "medium": 2, "low": 1}
    recent = []
    for fi, res in enumerate(state["results"]):
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


@router.get("/api/files")
def api_files(ws: str = Header("", alias="X-Workspace")):
    return {"results": [_serialize_result(r) for r in _workspace(ws)["results"]]}


@router.get("/api/issues")
def api_issues(ws: str = Header("", alias="X-Workspace")):
    out = []
    results = _workspace(ws)["results"]
    for fi, res in enumerate(results):
        for ii, iss in enumerate(res.issues):
            d = iss.to_dict()
            d.update({"file_index": fi, "issue_index": ii, "file_name": res.file_name,
                      "file_type": res.file_type})
            out.append(d)
    return {"issues": out}


@router.post("/api/issue_state")
def api_issue_state(payload: Dict[str, Any], ws: str = Header("", alias="X-Workspace")):
    fi, ii, stt = payload.get("file_index"), payload.get("issue_index"), payload.get("state")
    try:
        _workspace(ws)["results"][fi].issues[ii].state = stt
    except Exception:
        raise HTTPException(400, "无效的条目索引")
    return {"ok": True}


# ---------------------------------------------------------------------------
