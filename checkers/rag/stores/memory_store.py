# -*- coding: utf-8 -*-
"""内存 / JSON 向量后端。

定位：**零依赖兜底后端**。
在没有安装 Chroma 的内网离线机上，知识库依然可用（退化为纯 BM25 +
可选的 JSON 稠密向量），保证「装不上依赖 ≠ 功能不可用」。

注意：向量以 JSON 明文落盘，规模上千条后文件会明显膨胀，
仅适合小库或临时环境；正式部署请使用 ``chroma`` 后端。
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

from checkers.rag.stores.base import VectorStore, cosine, l2_normalize, sanitize_meta


class MemoryStore(VectorStore):
    """进程内字典 + JSON 快照的向量后端。"""

    backend = "memory"

    def __init__(self, path: str = "", vectors: Optional[Dict[str, Any]] = None) -> None:
        self.path = path or ""
        self._lock = threading.RLock()
        self._data: Dict[str, List[float]] = {}
        self._meta: Dict[str, Dict[str, Any]] = {}
        for cid, vec in (vectors or {}).items():
            if isinstance(vec, (list, tuple)) and vec:
                self._data[str(cid)] = [float(x) for x in vec]

    # ------------------------------------------------------------------
    def upsert(self, items: Sequence[Tuple[str, Sequence[float], Dict[str, Any]]]) -> int:
        n = 0
        with self._lock:
            for cid, vec, meta in items:
                if not cid or not vec:
                    continue
                self._data[str(cid)] = l2_normalize(vec)
                self._meta[str(cid)] = sanitize_meta(meta)
                n += 1
        return n

    def delete(self, cids: Sequence[str]) -> int:
        n = 0
        with self._lock:
            for cid in cids or []:
                if str(cid) in self._data:
                    self._data.pop(str(cid), None)
                    self._meta.pop(str(cid), None)
                    n += 1
        return n

    def delete_by_doc(self, doc_ids: Sequence[str]) -> int:
        wanted = {str(d) for d in doc_ids or []}
        if not wanted:
            return 0
        n = 0
        with self._lock:
            for cid in list(self._data):
                m = self._meta.get(cid) or {}
                if str(m.get("doc_id") or "") in wanted:
                    self._data.pop(cid, None)
                    self._meta.pop(cid, None)
                    n += 1
        return n

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._meta.clear()

    def count(self) -> int:
        return len(self._data)

    def dim(self) -> int:
        with self._lock:
            for v in self._data.values():
                return len(v)
        return 0

    def search(self, query_vec: Sequence[float], top_k: int,
               doc_ids: Optional[Sequence[str]] = None) -> List[Tuple[str, float]]:
        if not query_vec:
            return []
        wanted = {str(d) for d in doc_ids} if doc_ids else None
        q = l2_normalize(query_vec)
        out: List[Tuple[str, float]] = []
        with self._lock:
            items = list(self._data.items())
        for cid, vec in items:
            if wanted is not None and str((self._meta.get(cid) or {}).get("doc_id") or "") not in wanted:
                continue
            if len(vec) != len(q):
                continue
            out.append((cid, cosine(q, vec)))
        out.sort(key=lambda x: x[1], reverse=True)
        return out[:max(1, int(top_k))]

    def get(self, cids: Sequence[str]) -> Dict[str, List[float]]:
        with self._lock:
            return {str(c): list(self._data[str(c)]) for c in cids or [] if str(c) in self._data}

    def dump(self) -> Dict[str, Any]:
        """导出为可 JSON 序列化的快照（写入 knowledge.json）。"""
        with self._lock:
            return {k: list(v) for k, v in self._data.items()}

    def stats(self) -> Dict[str, Any]:
        return {"backend": self.backend, "path": self.path, "vectors": self.count(),
                "dim": self.dim(), "persistent": bool(self.path)}
