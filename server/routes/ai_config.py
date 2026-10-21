# -*- coding: utf-8 -*-
"""AI 智能核验配置：状态、技能、模型、连通性测试、参考资料、日志。"""

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

# AI 智能核验（可选：本地 AI / 联网 AI）
# ---------------------------------------------------------------------------
@app.get("/api/ai/status")
def api_ai_status():
    return {"ai": (load_settings().get("ai") or {})}


@app.get("/api/ai/skills")
def api_ai_skills():
    """返回内置专项技能列表（前端技能开关与自定义管理的唯一来源）。"""
    from checkers.ai_checker import BUILTIN_SKILLS
    return {"builtin": BUILTIN_SKILLS}


@app.get("/api/ai/models")
def api_ai_models(base_url: Optional[str] = None):
    """获取本地 Ollama 已安装模型列表（供前端下拉选择；Ollama 未运行返回空）。
    可通过 ?base_url=... 指定临时地址（不写入 settings），便于切换模型时即时探测。"""
    import json
    import urllib.error
    import urllib.request
    ai = load_settings().get("ai") or {}
    raw = (base_url or ai.get("base_url") or "http://127.0.0.1:11434")
    base = str(raw).rstrip("/") or "http://127.0.0.1:11434"
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        names = sorted(set(str(m.get("name") or "") for m in data.get("models", []) if m.get("name")))
        return {"ok": True, "models": names, "base_url": base}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "models": [], "base_url": base, "message": f"无法连接本地 AI 服务：{exc}"}


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


@app.get("/api/ai/logs")
def api_ai_logs(limit: int = 200, offset: int = 0):
    """AI 活动日志（核验 / 生成 / 测试连接 / 自学习）。"""
    items = ai_activity.get_logs(limit=max(1, int(limit)), offset=max(0, int(offset)))
    return {"ok": True, "logs": items, "total": len(items)}


@app.post("/api/ai/logs/clear")
def api_ai_logs_clear():
    n = ai_activity.clear_logs()
    return {"ok": True, "cleared": n}


# --- 通用操作日志（规则/词库等后台保存操作） ---
@app.get("/api/activity/logs")
def api_activity_logs(module: str = "", limit: int = 200, offset: int = 0):
    """操作记录（修改项 / 操作人 / 时间）。module 可过滤，如 rules / wordbank。"""
    items = rule_activity.get_logs(
        module=module or None, limit=max(1, int(limit)), offset=max(0, int(offset)),
    )
    return {"ok": True, "logs": items, "total": len(items)}


@app.post("/api/activity/logs")
def api_activity_log_create(payload: Dict[str, Any]):
    """写入一条操作记录（修改项 / 操作人 / 时间）。"""
    rule_activity.log_activity(
        module=str(payload.get("module") or "unknown"),
        action=str(payload.get("action") or "save"),
        detail=str(payload.get("detail") or ""),
        operator=payload.get("operator"),
    )
    return {"ok": True}


@app.post("/api/activity/logs/clear")
def api_activity_logs_clear(payload: Dict[str, Any]):
    module = (payload or {}).get("module") or None
    n = rule_activity.clear_logs(module=module)
    return {"ok": True, "cleared": n}


# --- AI 规则/词库智能生成（对话式 + 文本式 + 文档式，统一入库校验与来源标记） ---
