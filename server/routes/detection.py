# -*- coding: utf-8 -*-
"""导入与检测：文件上传、文件夹扫描、异步任务查询与取消。"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, UploadFile, HTTPException, Header

from checkers.scanner import collect_folder


from server import state
from server.state import CACHE_DIR, workspace as _workspace, make_task as _make_task, task_snapshot as _task_snapshot, cancel_task as _cancel_task, try_acquire_scan as _try_acquire_scan, release_scan as _release_scan
from server.services.detection import clear_cache as _clear_cache, run_task as _run_task

router = APIRouter()

# 导入与检测
# ---------------------------------------------------------------------------
@router.post("/api/upload")
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


@router.post("/api/scan_folder")
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


@router.get("/api/task/{tid}")
def api_task(tid: str):
    """查询核验任务状态：进度 / 阶段 / 思考日志（轮询用）。"""
    snap = _task_snapshot(tid)
    if snap is None:
        raise HTTPException(404, "任务不存在或已过期")
    return snap


@router.post("/api/task/{tid}/cancel")
def api_task_cancel(tid: str):
    """取消核验任务：终止检测、清空该任务的中间缓存文件。"""
    if not _cancel_task(tid):
        raise HTTPException(404, "任务不存在或已过期")
    return {"ok": True}


@router.post("/api/clear_data")
def api_clear_data(ws: str = Header("", alias="X-Workspace")):
    state = _workspace(ws)
    state["results"] = []
    state["last_scan_time"] = None
    state["scan_seconds"] = 0.0
    return {"ok": True}


@router.post("/api/clear_cache")
def api_clear_cache():
    return _clear_cache()


# ---------------------------------------------------------------------------
