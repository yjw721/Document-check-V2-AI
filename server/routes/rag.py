# -*- coding: utf-8 -*-
"""本地知识库（RAG 检索问答）。"""

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

# 本地知识库（RAG 检索问答）
# ---------------------------------------------------------------------------
@app.get("/api/rag/status")
def api_rag_status():
    return rag_kb_status()


@app.post("/api/rag/upload")
async def api_rag_upload(files: List[UploadFile] = File(...)):
    """上传文档并入知识库索引（支持 .docx / .pdf / .txt / .md / .csv / .xlsx）。"""
    items: List[tuple] = []
    for uf in files:
        name = os.path.basename(uf.filename or "file")
        try:
            content = await uf.read()
        except OSError:
            continue
        if not content:
            continue
        items.append((name, content))
    if not items:
        raise HTTPException(400, "没有可上传的合法文件")
    added, docs, errors = rag_index_docs(items)
    if added == 0 and not docs:
        raise HTTPException(400, errors[0] if errors else "文件导入失败")
    return {"ok": True if added else False, "added": added, "docs": docs, "errors": errors}


@app.post("/api/rag/ask")
def api_rag_ask(payload: Dict[str, Any]):
    """基于本地知识库检索并生成回答（返回答案与来源片段）。"""
    question = str(payload.get("question") or "").strip()
    if len(question) < 2:
        raise HTTPException(400, "请输入要查询的问题")
    try:
        top_k = max(1, min(8, int(payload.get("top_k") or 3)))
    except (TypeError, ValueError):
        top_k = 3
    ai = (load_settings().get("ai") or {}).copy()
    if not ai.get("enabled"):
        raise HTTPException(400, "知识库问答需要 AI 支持：请先在「AI 配置」启用 AI 并测试连接")
    res = rag_ask(question, ai, top_k)
    return {"ok": True, **res}


@app.post("/api/rag/delete")
def api_rag_delete(payload: Dict[str, Any]):
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "请提供要删除的文档 id")
    removed, names = rag_delete_docs(ids)
    return {"ok": True, "removed": removed, "names": names}


@app.post("/api/rag/clear")
def api_rag_clear():
    rag_clear()
    return {"ok": True}


@app.get("/api/rag/file/{doc_id}")
def api_rag_file(doc_id: str):
    """下载知识库中的原始文档（来源跳转）。"""
    p = rag_doc_file(doc_id)
    if not p:
        raise HTTPException(404, "文档不存在")
    base = os.path.basename(p)
    ext = os.path.splitext(base)[1].lower()
    media = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".csv": "text/csv; charset=utf-8",
    }.get(ext, "application/octet-stream")
    return FileResponse(p, filename=base, media_type=media)


# ---------------------------------------------------------------------------
