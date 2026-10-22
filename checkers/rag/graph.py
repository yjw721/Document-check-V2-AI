# -*- coding: utf-8 -*-
"""知识库思维图谱构建器。

把知识库的分块层级（文档 → 章节路径 → 子章节）组织成一张图：

- **节点**：文档节点（``doc``）+ 章节节点（``sec``，由 ``chunk.path`` 按
  `` / `` 拆出的层级树，如 ``附件 / 1.成本法 / 1.1 重置成本法``）
- **结构边（hierarchy）**：文档 → 一级章节 → 二级章节 …，体现「思维 / 知识结构」
- **关联边（related）**：章节节点之间的**语义相似**（向量余弦）或**关键词共现**
  （无向量时降级），揭示跨章节 / 跨文档的概念关联

返回可被前端力导向布局直接消费的 ``nodes / edges / stats``。

设计约束：仅标准库、无外部依赖、零网络外发；向量不可用时自动退化为
关键词共现关联，保证图谱始终可用。
"""

from __future__ import annotations

import hashlib
from collections import defaultdict, OrderedDict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from checkers.rag import engine as _engine

SEP = " / "
# 关联边相关阈值
MAX_RELATED_PER_NODE = 3      # 每个节点最多保留的关联边数（避免视觉杂乱）
SIM_THRESHOLD = 0.55          # 向量余弦关联下限
TERM_SHARE_MIN = 2            # 关键词共现关联所需的最小共享高权重词数
TOP_TERMS = 12                # 每章节取前 N 个高 IDF 词用于共现判断
TOP_RELATED_EDGES = 90        # 关联边总数上限


def _split_path(path: str) -> List[str]:
    return [p.strip() for p in str(path).split(SEP) if p.strip()]


def _sec_id(key: str) -> str:
    return "sec:" + hashlib.md5(key.encode("utf-8")).hexdigest()[:14]


def _fetch_vecs(idx: Any, cids: Sequence[str]) -> Dict[str, Sequence[float]]:
    """按 cid 取向量；优先向量库后端，内存快照兜底。"""
    cids = [str(c) for c in cids if c]
    if not cids:
        return {}
    store = getattr(idx, "vec_store", None)
    if store is not None:
        try:
            return store.get(list(cids)) or {}
        except Exception:  # noqa: BLE001 - 后端异常时退回内存快照
            pass
    snap = getattr(idx, "vectors", None) or {}
    return {c: snap[c] for c in cids if c in snap}


def _mean_vec(vecs: Sequence[Sequence[float]]) -> Optional[List[float]]:
    vecs = [v for v in vecs if v]
    if not vecs:
        return None
    dim = len(vecs[0])
    acc = [0.0] * dim
    for v in vecs:
        if len(v) != dim:
            continue
        for i, x in enumerate(v):
            acc[i] += float(x)
    n = len(vecs)
    return [a / n for a in acc]


def _section_terms(idx: Any, cids: Sequence[str]) -> set:
    """章节的代表词：取所含分块的高 IDF 词（TF-IDF 加权），用于无向量时的共现关联。"""
    pos = [idx._cid_pos[str(c)] for c in cids if str(c) in idx._cid_pos]
    tf: Dict[str, float] = defaultdict(float)
    for p in pos:
        for t in idx.bm25.doc_tokens[p]:
            tf[t] += 1
    scored = {t: tf[t] * idx.bm25.idf.get(t, 0.0) for t in tf}
    top = sorted(scored, key=scored.get, reverse=True)[:TOP_TERMS]
    return set(top)


def build_graph(folder: str = "", include_sub: bool = True) -> Dict[str, Any]:
    """构建思维图谱。

    参数：
        folder:      限定到某目录（空 = 全库）
        include_sub: 是否包含子目录（仅 folder 非空时有效）

    返回：``{"ok", "nodes", "edges", "stats", "message?"}``
    """
    kb = _engine.load_kb()
    idx = _engine.load_index()

    # 1) 收集文档（可选按目录过滤）
    if folder:
        from checkers.rag.tree import docs_in_subtree
        allowed = set(docs_in_subtree(kb, folder))
        docs = [d for d in (kb.get("docs") or []) if d and str(d.get("id")) in allowed]
    else:
        docs = [d for d in (kb.get("docs") or []) if d]

    if not docs:
        return {
            "ok": True, "nodes": [], "edges": [],
            "stats": {"docs": 0, "sections": 0, "related": 0,
                      "has_vectors": idx.has_vectors(), "message": "知识库为空"},
        }

    # 2) 章节聚合
    sec: "OrderedDict[str, dict]" = OrderedDict()
    doc_nodes: List[dict] = []

    for d in docs:
        did = str(d.get("id"))
        dname = str(d.get("name") or f"文档 {did}")
        chunks = d.get("chunks") or []
        doc_nodes.append({
            "id": f"doc:{did}", "type": "doc", "label": dname,
            "name": dname, "doc_id": did, "full_path": dname, "level": 0,
            "chunks": len(chunks), "size": int(d.get("size") or 0),
            "sample": "", "rel_count": 0,
        })
        for c in chunks:
            path = str(c.get("path") or c.get("section") or "").strip()
            if not path:
                continue
            cid = str(c.get("cid") or "")
            parts = _split_path(path)
            # 各级层级章节节点
            for depth in range(1, len(parts) + 1):
                key = SEP.join(parts[:depth])
                node = sec.get(key)
                if node is None:
                    node = sec[key] = {
                        "id": _sec_id(key), "type": "sec", "key": key,
                        "label": parts[depth - 1], "full_path": key,
                        "level": depth, "parts": parts[:depth],
                        "doc_ids": set(), "cids": [], "sample": "",
                        "rel_count": 0,
                    }
                node["doc_ids"].add(did)
                if cid:
                    node["cids"].append(cid)
                if not node["sample"]:
                    node["sample"] = str(c.get("text") or "")[:160]

    # 3) 结构边（hierarchy）
    edges: List[dict] = []
    seen_edges: set = set()

    def _add(kind: str, s: str, t: str, weight: float = 1.0,
             meta: Optional[dict] = None) -> None:
        if s == t:
            return
        # 无向去重：统一按 id 排序
        a, b = (s, t) if s < t else (t, s)
        ek = f"{a}|{b}|{kind}"
        if ek in seen_edges:
            return
        seen_edges.add(ek)
        e: dict = {"source": a, "target": b, "kind": kind, "weight": round(weight, 4)}
        if meta:
            e.update(meta)
        edges.append(e)

    for s in sec.values():
        if s["level"] > 1:
            parent_key = SEP.join(s["parts"][:-1])
            _add("hierarchy", s["id"], sec[parent_key]["id"])
        else:
            # 顶级章节挂到它出现的每个文档节点下
            for did in s["doc_ids"]:
                _add("hierarchy", s["id"], f"doc:{did}")

    # 4) 关联边（related）
    sec_list = list(sec.values())
    has_vec = idx.has_vectors()
    related: List[Tuple[str, str, float]] = []

    if has_vec:
        means: Dict[str, Optional[List[float]]] = {}
        for s in sec_list:
            means[s["id"]] = _mean_vec(_fetch_vecs(idx, s["cids"]).values())
        for i in range(len(sec_list)):
            for j in range(i + 1, len(sec_list)):
                vi, vj = means[sec_list[i]["id"]], means[sec_list[j]["id"]]
                if not vi or not vj:
                    continue
                sim = _engine.cos(vi, vj)
                if sim >= SIM_THRESHOLD:
                    related.append((sec_list[i]["id"], sec_list[j]["id"], sim))
    else:
        terms: Dict[str, set] = {s["id"]: _section_terms(idx, s["cids"]) for s in sec_list}
        for i in range(len(sec_list)):
            for j in range(i + 1, len(sec_list)):
                shared = terms[sec_list[i]["id"]] & terms[sec_list[j]["id"]]
                if len(shared) >= TERM_SHARE_MIN:
                    related.append((sec_list[i]["id"], sec_list[j]["id"],
                                   min(1.0, len(shared) / TOP_TERMS + 0.3)))

    # 每节点保留最相关的若干条，避免画面拥堵
    by_node: Dict[str, List[Tuple[str, str, float]]] = defaultdict(list)
    for a, b, w in related:
        by_node[a].append((a, b, w))
        by_node[b].append((a, b, w))
    related_kept: List[Tuple[str, str, float]] = []
    for lst in by_node.values():
        lst.sort(key=lambda x: x[2], reverse=True)
        for a, b, w in lst[:MAX_RELATED_PER_NODE]:
            related_kept.append((a, b, w))
    # 去重 + 限量
    seen = set()
    related_final: List[Tuple[str, str, float]] = []
    for a, b, w in sorted(related_kept, key=lambda x: x[2], reverse=True):
        ek = (a, b) if a < b else (b, a)
        if ek in seen:
            continue
        seen.add(ek)
        related_final.append((a, b, w))
    related_final = related_final[:TOP_RELATED_EDGES]

    for a, b, w in related_final:
        _add("related", a, b, w)

    # 统计关联度，用于前端节点强调
    rel_deg: Dict[str, int] = defaultdict(int)
    for e in edges:
        if e["kind"] == "related":
            rel_deg[e["source"]] += 1
            rel_deg[e["target"]] += 1
    for e in edges:
        if e["kind"] == "related":
            e["weight"] = round(float(e.get("weight") or 0.0), 4)

    # 5) 组装节点（set 转 list，便于 JSON 序列化）
    nodes: List[dict] = []
    for n in doc_nodes + list(sec.values()):
        n = dict(n)
        if "chunks" not in n:
            n["chunks"] = len(n.get("cids") or [])
        n["doc_ids"] = sorted(n.get("doc_ids") or [])
        n["rel_count"] = rel_deg.get(n["id"], 0)
        n.pop("cids", None)
        n.pop("parts", None)
        n.pop("key", None)
        nodes.append(n)

    stats = {
        "docs": len(doc_nodes),
        "sections": len(sec_list),
        "related": sum(1 for e in edges if e["kind"] == "related"),
        "hierarchy": sum(1 for e in edges if e["kind"] == "hierarchy"),
        "has_vectors": has_vec,
        "nodes": len(nodes),
        "edges": len(edges),
    }
    return {"ok": True, "nodes": nodes, "edges": edges, "stats": stats}
