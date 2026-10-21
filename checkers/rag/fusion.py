# -*- coding: utf-8 -*-
"""融合层：RRF 混合融合 / MMR 去冗余 / 相关性阈值。

为什么换掉旧的加权融合
----------------------
旧实现把 BM25 与余弦分别做 ``_norm01``（min-max）后按固定权重相加：

- min-max 对离群值极敏感：只要有一个片段 BM25 分数极高，其余全部被压到 0 附近；
- 两路分数量纲不同、分布不同，固定权重 ``0.45`` 缺乏依据；
- 未命中稠密路时 ``cos`` 全 0，``_norm01`` 退化为全 0.5，反而稀释了稀疏路。

**RRF（Reciprocal Rank Fusion）** 只用名次不用分数，天然免疫量纲与离群问题，
是 Elasticsearch / Pinecone / Weaviate / LlamaIndex 等当前默认的混合检索融合方式。
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Tuple

RRF_K = 60


def rrf(rank_lists: Iterable[Sequence[int]], k: int = RRF_K) -> List[Tuple[int, float]]:
    """多路由名次融合，返回 [(idx, rrf_score), ...]（降序）。"""
    acc: Dict[int, float] = {}
    for lst in rank_lists:
        for rank, idx in enumerate(lst, start=1):
            acc[idx] = acc.get(idx, 0.0) + 1.0 / (k + rank)
    return sorted(acc.items(), key=lambda x: x[1], reverse=True)


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


def mmr(cands: Sequence[Tuple[int, float]],
        token_sets: Sequence[set],
        vecs: Sequence[Sequence[float]] = (),
        top_k: int = 3,
        lam: float = 0.72,
        w_vec: float = 0.5) -> List[int]:
    """最大边际相关性重排：在相关性与多样性之间取平衡。

    ``lam=1`` 退化为纯按融合分排序；``lam`` 越小越强调与已选片段的差异。
    相似度用「词元 Jaccard + 向量余弦」的加权（无向量时只用前者）。
    """
    if not cands:
        return []
    top_k = max(1, int(top_k))
    if len(cands) <= top_k:
        return [i for i, _ in cands]

    max_score = max(s for _, s in cands) or 1.0
    norm = {i: (s / max_score) for i, s in cands}
    picked: List[int] = []
    rest = list(cands)

    while rest and len(picked) < top_k:
        best_i, best_v = -1, -1e9
        for pos, (i, _s) in enumerate(rest):
            if i in picked:
                continue
            rel = norm.get(i, 0.0)
            red = 0.0
            if picked:
                red = max(_sim(i, j, token_sets, vecs, w_vec) for j in picked)
            v = lam * rel - (1.0 - lam) * red
            if v > best_v:
                best_v, best_i, best_pos = v, i, pos
        picked.append(best_i)
        rest = [(i, s) for (i, s) in rest if i != best_i]
    return picked


def _sim(i: int, j: int, token_sets: Sequence[set],
         vecs: Sequence[Sequence[float]], w_vec: float) -> float:
    s = 0.0
    try:
        s += _jaccard(token_sets[i], token_sets[j])
    except (IndexError, TypeError):
        pass
    if vecs and w_vec > 0:
        try:
            vi, vj = vecs[i], vecs[j]
            if vi and vj:
                from checkers.rag.vectors import cos
                s = (1 - w_vec) * s + w_vec * max(0.0, cos(vi, vj))
        except (IndexError, TypeError):
            pass
    return s


def filter_by_relation(cands: Sequence[Tuple[int, float]],
                       top_k: int,
                       min_ratio: float = 0.28) -> List[Tuple[int, float]]:
    """相关性闸门：丢弃远低于首位的噪声片段，避免污染提示词。"""
    if not cands:
        return []
    head = cands[0][1]
    if head <= 0:
        return []
    floor = head * max(0.0, min_ratio)
    kept = [c for c in cands if c[1] >= floor]
    return kept[:max(1, top_k)]
