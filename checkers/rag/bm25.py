# -*- coding: utf-8 -*-
"""稀疏检索层：BM25（Okapi）索引。

与旧实现的关键差异：

- 旧 ``_build_bm25`` 每次检索都对全库重新分词，且 ``_bm25_score`` 对每个
  片段再重建一次词频表 —— 单次查询 O(总词元数)，片段越多越慢。
- 本模块**建表一次**（词频表 + IDF + 平均长度），查询阶段只做 O(查询词元) 打分，
  配合 engine 层的索引缓存，可支撑核验阶段「逐片段检索」而不产生额外开销。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

from checkers.rag.text import tokenize

K1 = 1.5
B = 0.75


class BM25Index:
    """面向「一次性建表、多次查询」的 BM25 索引。"""

    # doc_tokens 仅供 MMR / PRF 取词使用；打分走倒排，不遍历全库
    __slots__ = ("n", "doc_tokens", "doc_counts", "df", "idf", "avg", "cid", "inv")

    def __init__(self, texts: Sequence[str],
                 tokens_list: Optional[Sequence[Sequence[str]]] = None,
                 cids: Optional[Sequence[str]] = None) -> None:
        toks: List[List[str]] = []
        for i, t in enumerate(texts):
            if tokens_list is not None and i < len(tokens_list) and tokens_list[i]:
                toks.append(list(tokens_list[i]))
            else:
                toks.append(tokenize(t or ""))
        self.doc_tokens = toks
        self.cid = list(cids or [""] * len(toks))

        df: Dict[str, int] = {}
        counts: List[Dict[str, int]] = []
        inv: Dict[str, List[int]] = {}
        for i, tl in enumerate(toks):
            c: Dict[str, int] = {}
            for t in tl:
                c[t] = c.get(t, 0) + 1
            counts.append(c)
            for t in c:
                df[t] = df.get(t, 0) + 1
                inv.setdefault(t, []).append(i)
        self.doc_counts = counts
        self.df = df
        self.inv = inv
        self.n = max(len(toks), 1)
        self.avg = (sum(len(t) for t in toks) / self.n) if toks else 0.0
        self.idf = {t: math.log(1 + (self.n - f + 0.5) / (f + 0.5))
                    for t, f in df.items()}

    # ------------------------------------------------------------------
    def _norm(self, i: int) -> float:
        dl = len(self.doc_tokens[i])
        return K1 * ((1 - B) + B * dl / self.avg) if self.avg > 0 else 1.0

    def scores(self, q: Sequence[str]) -> List[float]:
        """对全库打分。借助倒排表只累加命中查询词元的文档，复杂度 O(命中数)。"""
        out = [0.0] * self.n
        acc: Dict[int, float] = {}
        for t in dict.fromkeys(q):
            idf_t = self.idf.get(t)
            if not idf_t:
                continue
            for i in self.inv.get(t, ()):
                f = self.doc_counts[i][t]
                denom = self._norm(i)
                acc[i] = acc.get(i, 0.0) + idf_t * (f * (K1 + 1)) / (f + denom)
        for i, s in acc.items():
            out[i] = s
        return out

    def top(self, q: Sequence[str], k: int = 20) -> List[int]:
        """返回得分最高的 k 个片段下标（升序无关，按得分降序）。"""
        sc = self.scores(q)
        idx = [i for i in range(self.n) if sc[i] > 0]
        idx.sort(key=lambda i: sc[i], reverse=True)
        return idx[:max(1, k)]

    def top_with_scores(self, q: Sequence[str], k: int = 20) -> List[tuple]:
        sc = self.scores(q)
        idx = [i for i in range(self.n) if sc[i] > 0]
        idx.sort(key=lambda i: sc[i], reverse=True)
        return [(i, sc[i]) for i in idx[:max(1, k)]]
