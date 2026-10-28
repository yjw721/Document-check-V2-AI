# -*- coding: utf-8 -*-
"""规则与词库一键扫描管理：扫描分类、备份导出、清理。"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, HTTPException

from checkers.rule_scan import (
    scan_all as rule_scan_all,
    clean_items as rule_scan_clean,
    cleanable_ids as rule_scan_cleanable_ids,
    build_backup_text as rule_scan_backup_text,
    build_backup_csv as rule_scan_backup_csv,
)


from server.state import SCAN_STATE, TASKS, TASK_LOCK, make_task as _make_task, task_hook as _task_hook, try_acquire_scan as _try_acquire_scan, release_scan as _release_scan, SCAN_STATE as _SCAN_STATE, TASKS as _TASKS, TASK_LOCK as _TASK_LOCK

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
