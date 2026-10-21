# -*- coding: utf-8 -*-
"""服务端全局状态与路径常量。

说明：本模块只存放「状态与基础设施」，不注册任何业务路由，
以避免与 server.routes.* 形成循环导入。
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(ROOT, "static")
CACHE_DIR = os.path.join(tempfile.gettempdir(), "doc_checker_local_cache")
REPORT_DIR = os.path.join(ROOT, "reports")
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

# 服务端工作区内存态：每个浏览器工作区（X-Workspace / 默认 default）独立一份检测结果。
# 单个工作区内：results 检测结果 / last_scan_time / scan_seconds。
_WORKSPACES: Dict[str, Dict[str, Any]] = {}
_WS_LOCK = threading.Lock()
# 规则词库扫描结果内存态（最近一次扫描，供刷新页面后继续查看 / 清理校验）
_SCAN_STATE: Dict[str, Any] = {"last": None}
# 范本解析草案内存态（按工作区隔离）：所有上传范本仅在本地内存处理（BytesIO），不落盘、零网络
_TEMPLATE_STATES: Dict[str, Dict[str, Any]] = {}
_MAX_TEMPLATE_MB = 50
_MAX_WORKSPACES = 200  # 防止局域网多用户长时间运行导致内存无限增长

# 全局扫描并发闸门：同一时刻只允许 1 个检测任务（检测 / 文件夹扫描），其余 409 排队提示
_SCAN_GATE_LOCK = threading.Lock()
_SCAN_RUNNING = False


# 对外只读引用：路由模块统一 from server.state import ...，
# 指向的仍是同一批可变容器，避免跨模块重新赋值导致状态不同步。
WORKSPACES = _WORKSPACES
SCAN_STATE = _SCAN_STATE
TEMPLATE_STATES = _TEMPLATE_STATES
TASKS = _TASKS
TASK_LOCK = _TASK_LOCK
WS_LOCK = _WS_LOCK


def workspace(wsid: str = "") -> Dict[str, Any]:
    """按工作区 id 取（或创建）内存态；空 id 归入 default 工作区。"""
    wsid = (wsid or "default").strip() or "default"
    with _WS_LOCK:
        ws = _WORKSPACES.get(wsid)
        if ws is None:
            ws = {"results": [], "last_scan_time": None, "scan_seconds": 0.0}
            _WORKSPACES[wsid] = ws
            if len(_WORKSPACES) > _MAX_WORKSPACES:
                # 超限时逐出最旧的空结果工作区，防止内存膨胀
                for old_wsid in sorted(
                    _WORKSPACES, key=lambda k: _WORKSPACES[k].get("last_scan_time") or datetime(1970, 1, 1)
                ):
                    if not _WORKSPACES[old_wsid]["results"]:
                        del _WORKSPACES[old_wsid]
                    if len(_WORKSPACES) <= _MAX_WORKSPACES:
                        break
        return ws


def template_state(wsid: str = "") -> Dict[str, Any]:
    """按工作区取范本解析草案内存态。"""
    wsid = (wsid or "default").strip() or "default"
    with _WS_LOCK:
        st = _TEMPLATE_STATES.get(wsid)
        if st is None:
            st = {"parser": None}
            _TEMPLATE_STATES[wsid] = st
            if len(_TEMPLATE_STATES) > _MAX_WORKSPACES:
                # 超限时逐出空草案，防止内存膨胀
                for old in list(_TEMPLATE_STATES):
                    if _TEMPLATE_STATES[old].get("parser") is None:
                        del _TEMPLATE_STATES[old]
                    if len(_TEMPLATE_STATES) <= _MAX_WORKSPACES:
                        break
        return st


def try_acquire_scan() -> bool:
    """尝试获取全局扫描闸门；获取失败返回 False（已有任务进行中）。"""
    global _SCAN_RUNNING
    with _SCAN_GATE_LOCK:
        if _SCAN_RUNNING:
            return False
        _SCAN_RUNNING = True
        return True


def release_scan() -> None:
    """释放全局扫描闸门。"""
    global _SCAN_RUNNING
    with _SCAN_GATE_LOCK:
        _SCAN_RUNNING = False


# ---------------------------------------------------------------------------
# 核验任务注册表（异步任务：进度 / 阶段 / 思考日志 / 取消）
# ---------------------------------------------------------------------------
_TASKS: Dict[str, Dict[str, Any]] = {}
_TASK_LOCK = threading.Lock()
_MAX_TASKS = 30
_TASK_TTL_SECONDS = 3600  # 任务最大存活时间（1小时）
_TASK_CLEANUP_INTERVAL = 300  # 清理检查间隔（5分钟）

# 阶段 key -> 中文展示名（与前端 WaitingTab 共用语义）
STAGE_NAMES = {
    "parse": "文件解析",
    "page": "页码定位",
    "format_error": "格式错误检测",
    "fluency": "语句通顺度检测",
    "wordbank": "行业词库规则匹配",
    "summary": "结果汇总",
    "ai": "AI 智能核验",
    "scan": "规则词库扫描",
}


def make_task(files: List[str], wsid: str = "default") -> str:
    """登记一个后台核验任务，返回 task_id。wsid 记录结果归属的工作区。"""
    import uuid

    tid = uuid.uuid4().hex[:12]
    now = time.time()
    with _TASK_LOCK:
        task = {
            "status": "running",
            "progress": 0.0,
            "stage": "parse",
            "stage_text": STAGE_NAMES["parse"],
            "logs": [],
            "cancel": threading.Event(),
            "error": "",
            "files": list(files),
            "wsid": wsid,
            "created_at": now,
            "last_access": now,
        }
        _TASKS[tid] = task
        # 超过上限时清理最旧的已完成任务，防止内存膨胀
        if len(_TASKS) > _MAX_TASKS:
            for old_tid in list(_TASKS):
                if _TASKS[old_tid]["status"] in ("done", "cancelled", "error"):
                    del _TASKS[old_tid]
                if len(_TASKS) <= _MAX_TASKS:
                    break
        # 定期清理过期任务
        _cleanup_expired_tasks(now)
    return tid


def _cleanup_expired_tasks(now: float) -> None:
    """清理过期任务（TTL + 仅保留最近的已完成任务）。"""
    expired = []
    for tid, task in _TASKS.items():
        if now - task.get("last_access", now) > _TASK_TTL_SECONDS:
            expired.append(tid)
    for tid in expired:
        _TASKS.pop(tid, None)
    # 如果已完成任务过多，保留最近 10 个
    done_tasks = [
        (tid, task.get("last_access", 0))
        for tid, task in _TASKS.items()
        if task["status"] in ("done", "cancelled", "error")
    ]
    if len(done_tasks) > 10:
        done_tasks.sort(key=lambda x: x[1])
        for tid, _ in done_tasks[:-10]:
            _TASKS.pop(tid, None)


def task_snapshot(tid: str) -> Optional[Dict[str, Any]]:
    """取任务快照（含进度 / 阶段 / 日志）。"""
    with _TASK_LOCK:
        t = _TASKS.get(tid)
        if not t:
            return None
        t["last_access"] = time.time()
        return {
            "status": t["status"],
            "progress": round(float(t["progress"]), 1),
            "stage": t["stage"],
            "stage_text": t["stage_text"],
            "logs": list(t["logs"]),
            "error": t["error"],
            "result": t.get("result"),
            "ai_stream": t.get("ai_stream"),
            "ai_summary": t.get("ai_summary"),
        }


def task_hook(tid: str) -> Callable[[float, str, str], bool]:
    """构造核验任务进度钩子（percent 0-100, 阶段 key, 日志文本）→ bool。"""

    def hook(percent: float, stage: str, log: str) -> bool:
        with _TASK_LOCK:
            t = _TASKS.get(tid)
            if not t:
                return False
            if t["cancel"].is_set():
                return False
            t["progress"] = float(percent)
            t["last_access"] = time.time()
            if stage:
                t["stage"] = stage
                t["stage_text"] = STAGE_NAMES.get(stage, stage)
            if log:
                logs = t["logs"]
                if not logs or logs[-1] != log:
                    logs.append(log)
                    if len(logs) > 400:
                        del logs[:-400]
        return True

    return hook


def cancel_task(tid: str) -> bool:
    """取消任务：置取消标志 + 删除该任务的上传缓存文件。"""
    with _TASK_LOCK:
        t = _TASKS.get(tid)
        if not t:
            return False
        if t["status"] == "running":
            t["cancel"].set()
            t["status"] = "cancelled"
            for fp in t["files"]:
                try:
                    if os.path.isfile(fp):
                        os.remove(fp)
                except OSError:
                    pass
    return True
