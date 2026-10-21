# -*- coding: utf-8 -*-
"""本地 AI 自学习记忆：成对样本管理、学习过程流式日志。"""

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
from server.services.ai import ai_create_enabled as _ai_create_enabled
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

# --- 本地 AI 自学习记忆（人工校对成对样本：系统留存原始文档 + 用户上传修订文档） ---
@router.get("/api/ai_memory")
def api_ai_memory_get():
    """自学习总览：开关 / 配对状态 / 样本列表 / 学习产出 / 统计。"""
    return ai_memory_payload()


@router.post("/api/ai_memory/toggle")
def api_ai_memory_toggle(payload: Dict[str, Any]):
    """自学习总开关。"""
    ai_memory_set_enabled(bool(payload.get("enabled", False)))
    return {"ok": True, "enabled": ai_memory_is_enabled()}


@router.post("/api/ai_memory/pair")
async def api_ai_memory_pair(files: List[UploadFile] = File(...)):
    """上传人工修改后的修订文档，与系统留存的原始检测文档配对。"""
    if not _ai_create_enabled() or not ai_memory_is_enabled():
        return {"ok": False, "message": "本地AI自学习已关闭，请先启用"}
    if not files:
        return {"ok": False, "message": "未收到修订文档", "data": ai_memory_payload()}
    f = files[0]
    try:
        raw = await f.read()
        if len(raw) > 50 * 1024 * 1024:
            return {"ok": False, "message": "修订文档过大（>50MB）", "data": ai_memory_payload()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"读取修订文档失败：{exc}", "data": ai_memory_payload()}
    ok, msg, status = ai_memory_upload_revised(f.filename or "revised.docx", raw)
    return {"ok": ok, "message": msg, "source_status": status, "data": ai_memory_payload()}


@router.post("/api/ai_memory/diffs")
def api_ai_memory_diffs():
    """比对原始文档与修订文档文本差异，返回差异片段（错误 → 正确）供用户确认。"""
    if not _ai_create_enabled() or not ai_memory_is_enabled():
        return {"ok": False, "message": "本地AI自学习已关闭，请先启用", "diffs": []}
    ok, msg, diffs = ai_memory_compute_diffs()
    return {"ok": ok, "message": msg, "diffs": diffs}


@router.post("/api/ai_memory/samples")
def api_ai_memory_add_sample(payload: Dict[str, Any]):
    """用户确认后将配对差异片段加入本地记忆样本库（仅存差异文本，释放完整文档）。"""
    if not _ai_create_enabled() or not ai_memory_is_enabled():
        return {"ok": False, "message": "本地AI自学习已关闭，请先启用"}
    diffs = payload.get("diffs") or []
    ok, msg = ai_memory_add_sample(
        diffs,
        str(payload.get("source_doc") or "—"),
        str(payload.get("revised_doc") or "—"),
        note=str(payload.get("note") or ""),
        content=str(payload.get("content") or ""),
    )
    return {"ok": ok, "message": msg, "data": ai_memory_payload()}


# ---- 本地 AI 自学习：学习过程实时进度与日志缓冲 ----
_MLEARN_LOGS: Dict[str, List[Dict[str, Any]]] = {}
_MLEARN_STATE: Dict[str, Dict[str, Any]] = {}
_MLEARN_CANCEL: set = set()
_MAX_MLEARN_LOG_ENTRIES = 20000
# 常驻任务数上限：超出的历史任务连同日志一并回收，避免长时间运行后内存无限增长
_MAX_MLEARN_TASKS = 8

# 各阶段的起始进度百分比；推理阶段内部按已输出字符数继续缓增
_MLEARN_STAGE_PROGRESS: Dict[str, int] = {
    "start": 6, "prompt": 14, "infer": 22, "parse": 88, "validate": 95,
    "done": 100, "error": 100,
}
_MLEARN_INFER_CAP = 78          # 推理期进度上限，余量留给解析 / 校验 / 入库
_MLEARN_CHARS_PER_PCT = 24      # 推理期每输出多少字符推进 1%


def _mlearn_pct(stage: str, chars: int = 0) -> int:
    """按阶段（+ 推理期输出量）估算学习进度百分比。"""
    base = _MLEARN_STAGE_PROGRESS.get(stage, 0)
    if stage == "infer":
        return min(_MLEARN_INFER_CAP, base + max(0, int(chars)) // _MLEARN_CHARS_PER_PCT)
    if stage == "done":
        return 100
    return base


def _mprogress(sid: str, stage: str, *, chars: Optional[int] = None,
               retry: Optional[bool] = None) -> None:
    """推进学习任务的阶段 / 进度 / 输出字数（供前端渲染进度条与实时指标）。"""
    with _TASK_LOCK:
        st = _MLEARN_STATE.setdefault(sid, {})
        out = st.get("chars", 0) if chars is None else int(chars)
        st["chars"] = out
        if stage not in ("done", "error"):
            st["stage"] = stage
        if retry is not None:
            st["retry"] = bool(retry)
        pct = _mlearn_pct(stage, out)
        if stage == "done":
            st["progress"] = 100
        elif stage == "error":
            st["progress"] = max(int(st.get("progress") or 0), 1)
        else:
            # 进度只增不减，且未完成前封顶 99%
            st["progress"] = max(int(st.get("progress") or 0), min(pct, 99))


def _mtrim_tasks(keep: str = "") -> None:
    """按启动顺序回收多余的学习任务日志与状态（保留 keep 指定的 sid）。"""
    with _TASK_LOCK:
        excess = [k for k in _MLEARN_STATE.keys() if k != keep]
        for k in excess[: max(0, len(excess) - _MAX_MLEARN_TASKS + 1)]:
            _MLEARN_STATE.pop(k, None)
            _MLEARN_LOGS.pop(k, None)


def _mlog(sid: str, kind: str, text: str) -> None:
    """写入学习过程日志缓冲（线程安全，带超长截断）。

    kind 取值与 AI 生成统一：stage / token / thinking / error；
    阶段日志文本带 `[stage]` 前缀，供前端解析阶段流。
    """
    with _TASK_LOCK:
        buf = _MLEARN_LOGS.setdefault(sid, [])
        if len(buf) >= _MAX_MLEARN_LOG_ENTRIES:
            buf.append({"ts": time.strftime("%H:%M:%S"), "kind": "stage",
                        "text": "（日志超长，已截断后续输出）"})
            return
        buf.append({"ts": time.strftime("%H:%M:%S"), "kind": kind, "text": text})


def _mstage(sid: str, stage: str, text: str) -> None:
    """写入一条阶段日志并推进对应进度。"""
    _mlog(sid, "stage", f"[{stage}] {text}")
    if "重试" in text:
        _mprogress(sid, stage, retry=True)
    else:
        _mprogress(sid, stage)


@router.post("/api/ai_memory/samples/{sid}/learn")
def api_ai_memory_learn(sid: str):
    """对成对样本执行本地学习（差异片段 → 词条 + 校验规则）。

    后台线程执行并实时写入推理过程日志（token 流 + 阶段 + 进度百分比），
    前端通过 /api/ai_memory/samples/{sid}/learn/logs 轮询展示。
    """
    if not _ai_create_enabled():
        return {"ok": False, "message": "AI 智能生成已关闭（后台设置总开关）",
                "stats": {}, "data": ai_memory_payload()}
    ai = load_settings().get("ai") or {}
    from checkers.ai_memory import LearnAborted, learn_sample as _mlearn

    def runner():
        chars = 0

        def on_log(stage: str, info: Dict[str, Any]) -> None:
            txt = str(info.get("text", ""))
            if stage == "error":
                _mlog(sid, "error", txt)
                _mprogress(sid, "error")
            else:
                _mstage(sid, stage, txt)

        def on_token(text: str, kind: str) -> None:
            nonlocal chars
            if sid in _MLEARN_CANCEL:
                raise LearnAborted()
            _mlog(sid, "token" if kind == "content" else "thinking", text)
            if kind == "content":
                chars += len(text)
                _mprogress(sid, "infer", chars=chars)

        try:
            ok, msg, stats = _mlearn(sid, ai, on_log=on_log, on_token=on_token,
                                     auto_log=False)
            _mprogress(sid, "done")
            with _TASK_LOCK:
                st = _MLEARN_STATE.setdefault(sid, {})
                st.update({"running": False, "done": True, "ok": ok,
                           "message": msg, "stats": stats})
        except LearnAborted:
            _mstage(sid, "done", "已中止学习任务")
            with _TASK_LOCK:
                st = _MLEARN_STATE.setdefault(sid, {})
                st.update({"running": False, "done": True, "ok": False,
                           "cancelled": True, "message": "已中止学习", "stats": {}})
        except Exception as exc:  # noqa: BLE001 - 后台线程兜底
            _mprogress(sid, "error")
            with _TASK_LOCK:
                st = _MLEARN_STATE.setdefault(sid, {})
                st.update({"running": False, "done": True, "ok": False,
                           "message": f"{type(exc).__name__}：{str(exc)[:120]}", "stats": {}})
        finally:
            _MLEARN_CANCEL.discard(sid)
            with _TASK_LOCK:
                st = _MLEARN_STATE.get(sid)
                if st is not None and not st.get("finished_at"):
                    st["finished_at"] = time.time()

    with _TASK_LOCK:
        _MLEARN_STATE[sid] = {"running": True, "done": False, "ok": False,
                              "message": "", "stats": {}, "stage": "",
                              "progress": 0, "chars": 0, "retry": False,
                              "started_at": time.time()}
        _MLEARN_LOGS[sid] = []
    threading.Thread(target=runner, daemon=True).start()
    _mtrim_tasks(keep=sid)
    return {"ok": True, "running": True, "message": "学习任务已启动（本地推理过程实时展示）"}


@router.post("/api/ai_memory/samples/{sid}/learn/cancel")
def api_ai_memory_learn_cancel(sid: str):
    """中止学习任务：下一次 token 回调即中断流式推理，释放本地算力。"""
    if sid not in _MLEARN_STATE:
        return {"ok": False, "message": "学习任务不存在或已结束"}
    _MLEARN_CANCEL.add(sid)
    with _TASK_LOCK:
        st = _MLEARN_STATE.get(sid)
        if st and st.get("running"):
            st["message"] = "中止指令已下发，正在停止本地推理…"
    return {"ok": True, "message": "中止指令已下发，正在停止本地推理…"}


@router.get("/api/ai_memory/samples/{sid}/learn/logs")
def api_ai_memory_learn_logs(sid: str, since: int = 0):
    """轮询本地学习推理过程：阶段日志 + 模型 token 流 + 运行状态 + 实时进度。

    since：增量游标，与 build 日志通道同构；传上次返回的 offset 只取新增日志。
    elapsed_sec / chars_per_sec 在查询时按 started_at 实时计算，
    避免后台线程频繁写入带来的开销。
    """
    with _TASK_LOCK:
        buf = _MLEARN_LOGS.get(sid) or []
        total = len(buf)
        logs = list(buf[since:]) if 0 < since <= total else list(buf)
        st = dict(_MLEARN_STATE.get(sid, {"running": False, "done": False}))
    started = float(st.get("started_at") or 0)
    if started:
        elapsed = 0.0 if st.get("done") else max(0.0, time.time() - started)
        if st.get("done"):
            last = st.get("finished_at")
            elapsed = max(0.0, (last or time.time()) - started)
        st["elapsed_sec"] = round(elapsed, 1)
        chars = int(st.get("chars") or 0)
        st["chars_per_sec"] = round(chars / elapsed, 1) if elapsed > 1 else 0
    else:
        st["elapsed_sec"] = 0
        st["chars_per_sec"] = 0
    return {"ok": True, "logs": logs, "offset": total, "state": st}


@router.post("/api/ai_memory/samples/{sid}/toggle")
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


@router.delete("/api/ai_memory/samples/{sid}")
def api_ai_memory_sample_delete(sid: str):
    """单条删除记忆样本（不影响其已学习产出的规则词条）。"""
    if not ai_memory_sample_delete(sid):
        return {"ok": False, "message": "样本不存在或已被删除"}
    return {"ok": True, "data": ai_memory_payload()}


@router.post("/api/ai_memory/learned/{lid}/toggle")
def api_ai_memory_learned_toggle(lid: str, payload: Dict[str, Any]):
    """启用 / 禁用学习产出的词库条目或校验规则（同步影响检测）。"""
    if not ai_memory_toggle(lid, bool(payload.get("enabled", True))):
        return {"ok": False, "message": "学习产出不存在或已被删除"}
    return {"ok": True, "data": ai_memory_payload()}


@router.delete("/api/ai_memory/learned/{lid}")
def api_ai_memory_learned_delete(lid: str):
    """删除学习产出（从词库 / 自定义规则中移除，不影响其它数据）。"""
    if not ai_memory_delete(lid):
        return {"ok": False, "message": "学习产出不存在或已被删除"}
    return {"ok": True, "data": ai_memory_payload()}


@router.post("/api/ai_memory/clear")
def api_ai_memory_clear():
    """批量清空本地学习记忆（仅学习生成的数据，不动用户手动导入编写的规则词库）。"""
    removed = ai_memory_clear()
    return {"ok": True, "removed": removed, "data": ai_memory_payload()}


@router.get("/api/ai_memory/export")
def api_ai_memory_export(format: str = "txt", kind: str = "wordbanks"):
    """导出自学习产出的词库/规则文件（csv / txt，用于备份迁移）。"""
    from fastapi.responses import Response
    ok, msg, fname, content = ai_memory_export(format, kind)
    if not ok:
        raise HTTPException(400, msg)
    media = "text/csv; charset=utf-8" if format == "csv" else "text/plain; charset=utf-8"
    return Response(
        content="\ufeff" + content if format == "csv" else content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ---------------------------------------------------------------------------
