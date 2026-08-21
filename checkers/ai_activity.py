# -*- coding: utf-8 -*-
"""AI 活动日志：记录核验 / 生成 / 测试连接 / 自学习等关键事件（供「AI 配置 → AI 日志」查看）。

落盘为 append-only JSONL（logs/ai_activity.jsonl），同时缓存于内存，避免高频 IO。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List

LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "ai_activity.jsonl",
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


def log_event(event: str, model: str = "", detail: str = "", ok: bool = True,
              extra: Dict[str, Any] | None = None) -> None:
    """追加一条 AI 活动记录。event ∈ verify/build_dialogue/build_text/build_doc/test/learn。"""
    _ensure_loaded()
    entry: Dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "event": event,
        "model": model or "",
        "ok": bool(ok),
        "detail": detail or "",
    }
    if extra:
        entry.update(extra)
    with _lock:
        _cache.append(entry)
        try:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass


def get_logs(limit: int = 200, offset: int = 0) -> List[Dict[str, Any]]:
    _ensure_loaded()
    with _lock:
        items = list(_cache)
    items.reverse()
    return items[offset: offset + limit]


def clear_logs() -> int:
    global _cache, _loaded
    with _lock:
        n = len(_cache)
        _cache = []
        _loaded = True
        try:
            if os.path.exists(LOG_PATH):
                os.remove(LOG_PATH)
        except OSError:
            pass
    return n
