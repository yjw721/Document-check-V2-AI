# -*- coding: utf-8 -*-
"""向量库后端工厂。

配置驱动：``config/knowledge/knowledge.json`` 的 ``config.vector_backend``
取值 ``chroma``（默认）或 ``memory``。Chroma 不可用时自动降级为 memory，
并在 ``vector_backends()`` / ``kb_status()`` 中回显原因，界面上可感知。

核验链路只依赖 ``get_store()`` 返回的 ``VectorStore``，不感知具体后端。
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional

from checkers.rag.stores.base import VectorStore  # noqa: F401  - 契约对外暴露

DEFAULT_BACKEND = "chroma"

_LOCK = threading.RLock()
_CURRENT: Dict[str, Any] = {"key": "", "store": None, "error": ""}


def _registry() -> Dict[str, Any]:
    """延迟导入，避免未安装 chromadb 时影响模块加载。"""
    from checkers.rag.stores.memory_store import MemoryStore
    reg: Dict[str, Any] = {"memory": MemoryStore}
    try:
        from checkers.rag.stores.chroma_store import ChromaStore
        reg["chroma"] = ChromaStore
    except Exception:  # noqa: BLE001 - 缺依赖时只提供内存后端
        pass
    return reg


def _kb_dir() -> str:
    from checkers.rag.paths import KB_DIR
    return KB_DIR


def get_store(backend: Optional[str] = None,
              path: Optional[str] = None,
              force: bool = False) -> VectorStore:
    """按配置返回向量库实例（同参数复用同一连接）。

    ``path`` 显式给出后端持久化目录（Chroma）或 JSON 快照路径（memory）；
    省略时落到 ``config/knowledge/`` 下，便于测试隔离。
    """
    want = str(backend or DEFAULT_BACKEND).strip().lower() or DEFAULT_BACKEND
    chroma_path = path or os.path.join(_kb_dir(), "chroma")
    memory_path = os.path.join(_kb_dir(), "knowledge.json")
    key = f"{want}|{chroma_path}"
    with _LOCK:
        if not force and _CURRENT.get("store") is not None and _CURRENT.get("key") == key:
            return _CURRENT["store"]

        reg = _registry()
        err = ""
        store: Optional[VectorStore] = None
        cls = reg.get(want)
        if cls is None:
            err = f"未知向量后端：{want}"
            cls = reg["memory"]
            want = "memory"
        try:
            if want == "chroma":
                store = cls(path=chroma_path)
                if not store.available:
                    err = store.last_error or "Chroma 不可用"
                    store = reg["memory"](path=memory_path)
            else:
                store = cls(path=memory_path)
        except Exception as exc:  # noqa: BLE001 - 兜底：任何异常都退回内存后端
            err = f"{want} 初始化异常：{exc}"
        if store is None:
            store = reg["memory"](path=memory_path)

        _CURRENT.update(key=key, store=store, error=err)
        return store


def reset_store() -> None:
    """释放并清空当前后端实例（重建索引 / 切换后端时调用）。"""
    with _LOCK:
        store = _CURRENT.get("store")
        if store is not None:
            try:
                store.close()
            except Exception:  # noqa: BLE001
                pass
        _CURRENT.update(key="", store=None, error="")


def current_error() -> str:
    """返回后端降级原因（空串 = 未降级）。"""
    return str(_CURRENT.get("error") or "")


def vector_backends() -> List[Dict[str, Any]]:
    """列出可选后端及其可用性，供前端下拉展示。"""
    reg = _registry()
    out: List[Dict[str, Any]] = []
    labels = {
        "chroma": ("Chroma（推荐）", "本地持久化向量库，适合万级片段以内"),
        "memory": ("内存 / JSON", "零依赖兜底，向量随 knowledge.json 落盘"),
    }
    for bid in ("chroma", "memory"):
        if bid not in reg:
            continue
        label, note = labels.get(bid, (bid, ""))
        available = True
        error = ""
        if bid == "chroma":
            try:
                probe = reg[bid](path=os.path.join(_kb_dir(), "chroma"))
                available = probe.available
                error = probe.last_error
            except Exception as exc:  # noqa: BLE001
                available, error = False, str(exc)
        out.append({"id": bid, "label": label, "note": note,
                    "available": available, "error": error})
    return out
