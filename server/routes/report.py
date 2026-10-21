# -*- coding: utf-8 -*-
"""检测报告导出。"""

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


@router.post("/api/report")
def api_report(payload: Dict[str, Any], ws: str = Header("", alias="X-Workspace")):
    state = _workspace(ws)
    if not state["results"]:
        raise HTTPException(400, "暂无检测结果，无法生成报告")
    operator = payload.get("operator", "")
    org = payload.get("org", "")
    include_cover = bool(payload.get("include_cover", True))
    detail_columns = payload.get("detail_columns")
    mode = payload.get("report_filter", "all") or "all"
    filtered = _filter_results_for_report(state["results"], mode)
    if mode == "fluency" and not filtered:
        raise HTTPException(400, "当前检测结果中未发现语句通顺类问题，无法生成筛选报告")
    summary = summarize(filtered)
    # AI 核验配置与提示词记录：随报告归档，使 AI 审核的问题与提示与本地规则问题同源可溯
    ai_review: Dict[str, Any] = {}
    try:
        from checkers.ai_checker import build_prompt_record
        ai_review = build_prompt_record(load_settings().get("ai") or {})
    except Exception:  # noqa: BLE001 - 记录失败不影响报告生成
        ai_review = {}
    name = default_report_name()
    out_path = os.path.join(REPORT_DIR, name)
    build_report(
        filtered, summary, out_path,
        rule_summary=_rule_summary(),
        operator=operator, org=org,
        include_cover=include_cover, detail_columns=detail_columns,
        ai_review=ai_review,
    )
    return FileResponse(
        out_path, filename=name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


# ---------------------------------------------------------------------------
