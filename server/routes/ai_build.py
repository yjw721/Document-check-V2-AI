# -*- coding: utf-8 -*-
"""AI 规则 / 词库智能生成：对话式 / 文本式 / 文档式，含流式推理日志。"""

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
from server.services.ai import (
    ai_create_enabled as _ai_create_enabled,
    mark_source as _mark_source,
    BUILD_SOURCES as _BUILD_SOURCES,
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

# ---- AI 规则/词库智能生成：对话式 / 文本式 / 文档式（流式展示推理过程） ----
_BUILD_LOGS: Dict[str, List[Dict[str, Any]]] = {}
_BUILD_STATE: Dict[str, Dict[str, Any]] = {}
# 被用户中止的生成任务
_BUILD_CANCEL: set = set()
_MAX_BUILD_LOG_ENTRIES = 20000
# 常驻任务数上限：超出的历史任务连同日志一并回收，避免长时间运行后内存无限增长
_MAX_BUILD_TASKS = 6


def _trim_build_tasks(keep: str = "") -> None:
    """按启动顺序回收多余的生成任务日志与状态（保留 keep 指定的 bid）。"""
    with _TASK_LOCK:
        excess = [k for k in _BUILD_STATE.keys() if k != keep]
        for k in excess[: max(0, len(excess) - _MAX_BUILD_TASKS + 1)]:
            _BUILD_STATE.pop(k, None)
            _BUILD_LOGS.pop(k, None)


def _blog(bid: str, kind: str, text: str) -> None:
    """写入 AI 生成过程日志缓冲（线程安全，带超长截断）。"""
    buf = _BUILD_LOGS.setdefault(bid, [])
    if len(buf) >= _MAX_BUILD_LOG_ENTRIES:
        buf.append({"ts": time.strftime("%H:%M:%S"), "kind": "stage",
                    "text": "（日志超长，已截断后续输出）"})
        return
    buf.append({"ts": time.strftime("%H:%M:%S"), "kind": kind, "text": text})


def _build_runner(bid: str, kind: str, text: str, raw: bytes, filename: str) -> None:
    """后台执行生成并实时写入推理过程日志（token 流 + 阶段）。

    中止：用户调用 /api/ai/build/cancel 后，token 回调抛出 BuildCancelled，
    中断底层流式读取，任务以 cancelled 状态结束而非「失败」。
    """
    from checkers.ai_builder import BuildAborted, run_build as _brun
    ai = load_settings().get("ai") or {}

    def on_log(stage: str, info: Dict[str, Any]) -> None:
        txt = str(info.get("text", ""))
        if stage == "error":
            _blog(bid, "error", txt)
        else:
            _blog(bid, "stage", f"[{stage}] {txt}")

    def on_token(t: str, k: str) -> None:
        if bid in _BUILD_CANCEL:
            raise BuildAborted()
        _blog(bid, "token" if k == "content" else "thinking", t)

    try:
        ok, msg, result = _brun(kind, text=text, raw=raw, filename=filename,
                                ai=ai, on_log=on_log, on_token=on_token)
        filter_stat: Dict[str, Any] = {}
        if ok:
            _blog(bid, "stage", "[validate] 入库前置校验（过滤无效/重复规则）…")
            filter_stat = rule_filter_generated(result, channel="ai_build")
            src, label = _BUILD_SOURCES.get(kind, ("ai_build", "AI生成"))
            _mark_source(result, src, label)
            _blog(bid, "stage", f"[done] 生成完成：词库 {len(result['wordbanks'])} 组、"
                               f"规则 {len(result['rules'])} 条")
        else:
            _blog(bid, "stage", f"[done] 生成结束：{msg}")
        with _TASK_LOCK:
            st = _BUILD_STATE.setdefault(bid, {})
            st.update({"running": False, "done": True, "ok": ok,
                       "message": msg, "result": result, "filter": filter_stat})
    except BuildAborted:
        _blog(bid, "stage", "[done] 已中止生成任务")
        with _TASK_LOCK:
            st = _BUILD_STATE.setdefault(bid, {})
            st.update({"running": False, "done": True, "ok": False, "cancelled": True,
                       "message": "已中止生成", "result": None, "filter": None})
    except Exception as exc:  # noqa: BLE001 - 后台线程兜底
        with _TASK_LOCK:
            st = _BUILD_STATE.setdefault(bid, {})
            st.update({"running": False, "done": True, "ok": False,
                       "message": f"{type(exc).__name__}：{str(exc)[:200]}",
                       "result": None, "filter": None})
    finally:
        _BUILD_CANCEL.discard(bid)


_BUILD_SEQ = itertools.count(1)


def _new_build_bid() -> str:
    """生成任务 ID：毫秒时间 + 自增序号，避免同一毫秒内重复启动造成任务串台。"""
    return "b%d_%d" % (int(time.time() * 1000), next(_BUILD_SEQ))


def _start_build(kind: str, text: str = "", raw: bytes = b"", filename: str = "") -> str:
    """登记并后台启动一个生成任务，返回 bid。"""
    bid = _new_build_bid()
    with _TASK_LOCK:
        _BUILD_STATE[bid] = {"running": True, "done": False, "ok": False,
                             "message": "", "result": None, "filter": None}
        _BUILD_LOGS[bid] = []
    threading.Thread(target=_build_runner, args=(bid, kind, text, raw, filename),
                     daemon=True).start()
    _trim_build_tasks(keep=bid)
    return bid


@router.post("/api/ai/build/dialogue")
def api_ai_build_dialogue(payload: Dict[str, Any]):
    """对话式创建：自然语言描述需求 → 生成词库分组 + 规则（流式展示推理过程）。"""
    if not _ai_create_enabled():
        return {"ok": False, "message": "AI 智能生成已关闭（后台设置总开关），可手动编写或导入规则"}
    text = str(payload.get("text") or "").strip()
    if not text:
        return {"ok": False, "message": "请先描述你的规则/词库需求"}
    bid = _start_build("dialogue", text=text)
    return {"ok": True, "running": True, "bid": bid,
            "message": "生成任务已启动（本地推理过程实时展示）"}


@router.post("/api/ai/build/text")
def api_ai_build_text(payload: Dict[str, Any]):
    """文本式创建：粘贴准则/规范/范本文本 → AI 读取批量生成词库与规则（流式展示）。"""
    if not _ai_create_enabled():
        return {"ok": False, "message": "AI 智能生成已关闭（后台设置总开关），可手动编写或导入规则"}
    text = str(payload.get("text") or "").strip()
    if not text:
        return {"ok": False, "message": "请先粘贴准则/规范/范本文本"}
    bid = _start_build("text", text=text)
    return {"ok": True, "running": True, "bid": bid,
            "message": "生成任务已启动（本地推理过程实时展示）"}


@router.post("/api/ai/build/doc")
async def api_ai_build_doc(files: List[UploadFile] = File(...)):
    """文档式创建：上传文档（.txt/.md/.csv/.docx/.pdf）→ AI 阅读提取词库与规则（流式展示）。"""
    if not _ai_create_enabled():
        return {"ok": False, "message": "AI 智能生成已关闭（后台设置总开关），可手动编写或导入规则"}
    if not files:
        return {"ok": False, "message": "未收到文件"}
    f = files[0]
    try:
        raw = await f.read()
        if len(raw) > 10 * 1024 * 1024:
            return {"ok": False, "message": "文件过大（>10MB）"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"读取文件失败：{exc}"}
    bid = _start_build("doc", raw=raw, filename=f.filename or "doc.txt")
    return {"ok": True, "running": True, "bid": bid,
            "message": "生成任务已启动（本地推理过程实时展示）"}


@router.post("/api/ai/build/cancel")
def api_ai_build_cancel(payload: Dict[str, Any]):
    """中止生成任务：下一次 token 回调即中断流式推理，释放本地算力。"""
    bid = str((payload or {}).get("bid") or "")
    if not bid or bid not in _BUILD_STATE:
        return {"ok": False, "message": "任务不存在或已结束"}
    _BUILD_CANCEL.add(bid)
    with _TASK_LOCK:
        st = _BUILD_STATE.get(bid)
        if st and st.get("running"):
            st.update({"message": "中止指令已下发，正在停止本地推理…"})
    return {"ok": True, "message": "中止指令已下发，正在停止本地推理…"}


@router.get("/api/ai/build/logs")
def api_ai_build_logs(bid: str = "", since: int = 0):
    """轮询获取生成过程日志与运行状态（前端实时展示思考过程）。

    since：增量游标，传入上次返回的 offset 则只返回新增日志，
    避免长时间推理时每轮都全量回传历史日志（原实现为 O(n²) 传输与解析）。
    """
    with _TASK_LOCK:
        buf = _BUILD_LOGS.get(bid) or []
        total = len(buf)
        logs = list(buf[since:]) if 0 < since <= total else list(buf)
        state = dict(_BUILD_STATE.get(bid, {"running": False, "done": False}))
    return {
        "logs": logs,
        "offset": total,
        "state": state,
    }
