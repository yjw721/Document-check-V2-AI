# -*- coding: utf-8 -*-
"""通用操作日志：记录规则/词库等后台关键保存操作（供页面底部「操作记录」查看）。

落盘为 append-only JSONL（logs/activity.jsonl），同时缓存于内存，避免高频 IO。
每条记录：{ ts, module, action, operator, ok, detail }
  - module：规则域，如 rules / wordbank
  - action：操作类型，如 save_group
  - operator：操作人（无登录体系时取设置中的角色，如 admin / user）
  - detail：修改项摘要（中文可读）
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from config.settings_manager import load_settings

LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "activity.jsonl",
)
_lock = threading.Lock()
_cache: List[Dict[str, Any]] = []
_loaded = False


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        if os.path.exists(LOG_PATH):
            with open(LOG_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        _cache.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        pass


def _operator() -> str:
    try:
        return str(load_settings().get("role", "admin") or "admin")
    except Exception:  # noqa: BLE001
        return "admin"


def log_activity(module: str, action: str, detail: str = "",
                 operator: Optional[str] = None, ok: bool = True) -> None:
    """追加一条操作记录。operator 缺省时取当前设置角色。"""
    _ensure_loaded()
    entry: Dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "module": module or "unknown",
        "action": action or "save",
        "operator": operator or _operator(),
        "ok": bool(ok),
        "detail": detail or "",
    }
    with _lock:
        _cache.append(entry)
        try:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass


def get_logs(module: Optional[str] = None, limit: int = 200, offset: int = 0) -> List[Dict[str, Any]]:
    _ensure_loaded()
    with _lock:
        items = list(_cache)
    if module:
        items = [it for it in items if it.get("module") == module]
    items.reverse()
    return items[offset: offset + limit]


def clear_logs(module: Optional[str] = None) -> int:
    """清空日志；module 给定时仅清除该模块记录。返回清除条数。"""
    global _cache, _loaded
    with _lock:
        if module:
            kept = [it for it in _cache if it.get("module") != module]
            removed = len(_cache) - len(kept)
            _cache = kept
        else:
            removed = len(_cache)
            _cache = []
        _loaded = True
        try:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            with open(LOG_PATH, "w", encoding="utf-8") as f:
                for it in _cache:
                    f.write(json.dumps(it, ensure_ascii=False) + "\n")
        except OSError:
            pass
    return removed
