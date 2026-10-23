# -*- coding: utf-8 -*-
"""Chroma 向量后端（默认后端）。

选型理由：
- **本地优先**：``PersistentClient`` 直接落盘 DuckDB + HNSW，无需起服务；
- **中文友好**：嵌入向量由 Ollama（默认 ``qwen3-embedding`` / ``bge-m3``）产出，
  与中文语料训练的多语言模型天然适配，无内置英文分词偏差；
- **零运维**：单进程内嵌，随核验系统启停，离线内网可直接部署；
- **可替换**：实现的是 ``stores.base.VectorStore`` 契约，换 Milvus / pgvector
  只需新增一个后端模块并登记，核验链路代码零改动。

注意：Chroma 的 ``hnsw:space=cosine`` 下返回的是**余弦距离**（1 - cos），
本模块统一换算回余弦相似度，与内存后端口径保持一致。
"""

from __future__ import annotations

import os
import re
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

from checkers.rag.stores.base import VectorStore, l2_normalize, sanitize_meta

_DEFAULT_COLLECTION = "docverify_kb"
# Chroma ≥1.0 对集合名的硬性约束：3~512 位，仅 [a-zA-Z0-9._-]，首尾必须为字母或数字
_VALID_COLLECTION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,510}[A-Za-z0-9]$")


def _as_list(v: Any) -> List[Any]:
    """把 Chroma 返回值收敛为 list。

    Chroma ≥1.0 的 ``get`` / ``query`` 会返回 numpy 数组，
    直接写 ``x or []`` 会触发「数组真值歧义」异常并被上层 except 吞掉，
    表现为「向量明明有，却读不出来」——故统一在此转换。
    """
    if v is None:
        return []
    if isinstance(v, list):
        return v
    try:
        return list(v)
    except TypeError:
        return []


def sanitize_collection(name: str) -> str:
    """把任意集合名收敛为 Chroma 可接受的形态；非法时回退默认值。"""
    n = str(name or "").strip()
    if _VALID_COLLECTION.match(n):
        return n
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", n).strip("._-")
    if len(cleaned) < 3:                       # 过短：拼上前缀补足
        cleaned = (_DEFAULT_COLLECTION + "_" + cleaned).strip("._-")
    if len(cleaned) > 512:
        cleaned = cleaned[:512].rstrip("._-")
    return cleaned if _VALID_COLLECTION.match(cleaned) else _DEFAULT_COLLECTION


class ChromaStore(VectorStore):
    """基于 ``chromadb.PersistentClient`` 的持久化向量库。"""

    backend = "chroma"

    def __init__(self, path: str = "", collection: str = _DEFAULT_COLLECTION) -> None:
        self.path = path or ""
        self.collection_name = sanitize_collection(collection)
        self._lock = threading.RLock()
        self._client = None
        self._col = None
        self._err = ""

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------
    def _ensure(self):
        """惰性建连；失败时缓存错误，避免每次检索重复抛栈。"""
        if self._col is not None:
            return self._col
        with self._lock:
            if self._col is not None:
                return self._col
            try:
                import chromadb  # type: ignore
                from chromadb.config import Settings as ChromaSettings  # type: ignore
            except Exception as exc:  # noqa: BLE001 - 未安装依赖
                self._err = f"chromadb 未安装或不可用：{exc}"
                return None
            try:
                os.makedirs(self.path, exist_ok=True)
                # 不同版本的 Settings 支持的参数不一致，逐档降级尝试
                for kwargs in ({"anonymized_telemetry": False, "is_persistent": True},
                               {"anonymized_telemetry": False},
                               {}):
                    try:
                        self._client = chromadb.PersistentClient(
                            path=self.path, settings=ChromaSettings(**kwargs))
                        break
                    except (TypeError, ValueError):
                        continue
                if self._client is None:
                    self._client = chromadb.PersistentClient(path=self.path)
                self._col = self._client.get_or_create_collection(
                    name=self.collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
                self._err = ""
            except Exception as exc:  # noqa: BLE001 - 建连失败降级
                self._err = f"Chroma 初始化失败：{exc}"
                self._col = None
            return self._col

    @property
    def available(self) -> bool:
        return self._ensure() is not None

    @property
    def last_error(self) -> str:
        if self._col is not None:
            return ""
        self._ensure()
        return self._err

    def _reset(self) -> None:
        """连接失效后清空缓存，下次调用自动重连。"""
        with self._lock:
            self._client = None
            self._col = None

    # ------------------------------------------------------------------
    # 契约实现
    # ------------------------------------------------------------------
    def upsert(self, items: Sequence[Tuple[str, Sequence[float], Dict[str, Any]]]) -> int:
        items = [it for it in items or [] if it and it[0] and it[1]]
        if not items:
            return 0
        col = self._ensure()
        if col is None:
            return 0
        ids = [str(i[0]) for i in items]
        embs = [l2_normalize(i[1]) for i in items]
        metas = [sanitize_meta(i[2]) for i in items]
        # Chroma 单批过大易超时，按 256 分片提交
        n = 0
        step = 256
        for s in range(0, len(ids), step):
            try:
                col.upsert(ids=ids[s:s + step], embeddings=embs[s:s + step],
                           metadatas=metas[s:s + step])
                n += len(ids[s:s + step])
            except Exception:  # noqa: BLE001 - 单批失败不影响已写入部分
                self._reset()
                return n
        return n

    def delete(self, cids: Sequence[str]) -> int:
        cids = [str(c) for c in cids or [] if str(c)]
        if not cids:
            return 0
        col = self._ensure()
        if col is None:
            return 0
        before = self.count()
        try:
            col.delete(ids=cids)
        except Exception:  # noqa: BLE001
            self._reset()
            return 0
        return max(0, before - self.count())

    def delete_by_doc(self, doc_ids: Sequence[str]) -> int:
        doc_ids = [str(d) for d in doc_ids or [] if str(d)]
        if not doc_ids:
            return 0
        col = self._ensure()
        if col is None:
            return 0
        before = self.count()
        try:
            col.delete(where={"doc_id": {"$in": doc_ids}})
        except Exception:  # noqa: BLE001 - where 语法差异时退回逐条取 id 删除
            self._reset()
            col = self._ensure()
            if col is None:
                return 0
            try:
                got = col.get(where={"doc_id": {"$in": doc_ids}})
                ids = [str(i) for i in (got.get("ids") or [])]
                if ids:
                    col.delete(ids=ids)
            except Exception:  # noqa: BLE001
                self._reset()
                return 0
        return max(0, before - self.count())

    def clear(self) -> None:
        col = self._ensure()
        if col is None:
            return
        try:
            got = col.get()
            ids = [str(i) for i in (got.get("ids") or [])]
            if ids:
                col.delete(ids=ids)
        except Exception:  # noqa: BLE001
            self._reset()

    def count(self) -> int:
        col = self._ensure()
        if col is None:
            return 0
        try:
            return int(col.count())
        except Exception:  # noqa: BLE001
            self._reset()
            return 0

    def dim(self) -> int:
        col = self._ensure()
        if col is None:
            return 0
        try:
            got = col.get(limit=1, include=["embeddings"])
            for v in _as_list((got or {}).get("embeddings")):
                return len(v)
        except Exception:  # noqa: BLE001
            self._reset()
        return 0

    def search(self, query_vec: Sequence[float], top_k: int,
               doc_ids: Optional[Sequence[str]] = None) -> List[Tuple[str, float]]:
        if not query_vec:
            return []
        col = self._ensure()
        if col is None:
            return []
        k = max(1, int(top_k))
        where = None
        if doc_ids:
            ids = [str(d) for d in doc_ids if str(d)]
            if ids:
                where = {"doc_id": {"$in": ids}}
        try:
            res = col.query(query_embeddings=[l2_normalize(query_vec)],
                            n_results=k, where=where, include=["distances"])
        except Exception:  # noqa: BLE001 - 查询失败时视为该路不可用
            self._reset()
            return []
        ids_nested = _as_list(res.get("ids") if isinstance(res, dict) else None)
        dis_nested = _as_list(res.get("distances") if isinstance(res, dict) else None)
        if not ids_nested:
            return []
        ids = _as_list(ids_nested[0])
        dis = _as_list(dis_nested[0]) if dis_nested else []
        out: List[Tuple[str, float]] = []
        for cid, d in zip(ids, dis):
            try:
                # cosine 空间：distance = 1 - cos
                out.append((str(cid), max(0.0, 1.0 - float(d))))
            except (TypeError, ValueError):
                continue
        return out

    def get(self, cids: Sequence[str]) -> Dict[str, List[float]]:
        cids = [str(c) for c in cids or [] if str(c)]
        if not cids:
            return {}
        col = self._ensure()
        if col is None:
            return {}
        try:
            got = col.get(ids=cids, include=["embeddings"])
        except Exception:  # noqa: BLE001
            self._reset()
            return {}
        ids = _as_list(got.get("ids"))
        embs = _as_list(got.get("embeddings"))
        return {str(i): [float(x) for x in v] for i, v in zip(ids, embs) if v is not None}

    def stats(self) -> Dict[str, Any]:
        ok = self.available
        return {"backend": self.backend, "path": self.path,
                "collection": self.collection_name, "available": ok,
                "vectors": self.count() if ok else 0,
                "dim": self.dim() if ok else 0,
                "error": "" if ok else self.last_error}

    def close(self) -> None:
        """释放 PersistentClient 及其 SQLite 连接（Windows 下删除目录前必须调用）。"""
        with self._lock:
            client = self._client
            self._client = None
            self._col = None
        if client is None:
            return
        try:
            client.close()
            return
        except Exception:  # noqa: BLE001 - 旧版本无 close()，走兼容分支
            pass
        try:
            system = getattr(client, "_system", None)
            if system is not None:
                system.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            from chromadb.api.client import SharedSystemClient  # type: ignore
            SharedSystemClient.clear_system_cache()
        except Exception:  # noqa: BLE001
            pass
