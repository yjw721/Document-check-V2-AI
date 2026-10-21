# -*- coding: utf-8 -*-
"""向量库抽象层（低耦合的关键）。

核验系统与知识库之间只依赖本文件定义的 ``VectorStore`` 契约，
具体后端（Chroma / 内存 JSON / 未来的 Milvus、pgvector …）通过
``stores/__init__.py`` 的工厂按配置注入。

新增一个后端的步骤：
    1. 新建模块，实现 ``VectorStore`` 的全部抽象方法；
    2. 在 ``stores/__init__.py`` 的 ``_REGISTRY`` 里登记；
    3. 前端 / 路由无需改动（``vector_backends()`` 会自动列出）。
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence, Tuple


def l2_normalize(vec: Sequence[float]) -> List[float]:
    """余弦空间下统一归一化：让不同后端返回的相似度口径一致。"""
    s = math.sqrt(sum(float(x) * float(x) for x in vec))
    if s <= 1e-12:
        return [float(x) for x in vec]
    return [float(x) / s for x in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度；两段均为零向量时返回 0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        fx, fy = float(x), float(y)
        dot += fx * fy
        na += fx * fx
        nb += fy * fy
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    return dot / math.sqrt(na * nb)


def sanitize_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """把元数据收敛为后端可接受的基本类型。

    Chroma 不接受 ``None``，且键名不能为空；统一在此清洗，
    避免每个后端各写一遍。
    """
    out: Dict[str, Any] = {}
    for k, v in (meta or {}).items():
        key = str(k or "").strip()
        if not key or key == "None":
            continue
        if v is None:
            out[key] = ""
        elif isinstance(v, (str, int, float, bool)):
            out[key] = v
        else:
            out[key] = str(v)
    return out


class VectorStore(ABC):
    """向量库统一契约。

    所有方法都必须是**幂等**的：重复写入同一 cid 只覆盖、不追加。
    """

    #: 后端标识，写入配置与状态回显
    backend = "base"

    @abstractmethod
    def upsert(self, items: Sequence[Tuple[str, Sequence[float], Dict[str, Any]]]) -> int:
        """写入 / 覆盖向量。返回实际写入条数。

        items: ``[(chunk_id, vector, metadata), ...]``
        """

    @abstractmethod
    def delete(self, cids: Sequence[str]) -> int:
        """按 chunk_id 删除，返回删除条数。"""

    @abstractmethod
    def delete_by_doc(self, doc_ids: Sequence[str]) -> int:
        """按文档 id 批量删除该文档的全部向量。"""

    @abstractmethod
    def clear(self) -> None:
        """清空整个向量库。"""

    @abstractmethod
    def count(self) -> int:
        """向量总数。"""

    @abstractmethod
    def dim(self) -> int:
        """当前库内向量的维度；空库返回 0。"""

    @abstractmethod
    def search(self, query_vec: Sequence[float], top_k: int,
               doc_ids: Optional[Sequence[str]] = None) -> List[Tuple[str, float]]:
        """稠密检索。返回 ``[(chunk_id, 余弦相似度), ...]``（降序）。"""

    # ------------------------------------------------------------------
    # 可选能力：默认实现基于 search / 内部字典，后端可覆写以获得更好性能
    # ------------------------------------------------------------------
    def get(self, cids: Sequence[str]) -> Dict[str, List[float]]:
        """按 cid 取回原始向量（用于 MMR 去冗余）。"""
        return {}

    def stats(self) -> Dict[str, Any]:
        """后端自检信息，供状态接口回显。"""
        return {"backend": self.backend}

    def close(self) -> None:
        """释放资源（可选覆写）。"""
