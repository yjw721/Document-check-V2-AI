# -*- coding: utf-8 -*-
"""知识库对外服务门面（低耦合契约层）。

**核验流程只依赖本模块**，不直接接触 engine / stores / parsers 的内部结构。
这样做的好处：

- 换向量库、换 OCR 引擎、改切片策略，核验链路零改动；
- 门面内部自带降级：任何异常都被吞掉并返回空结果，
  知识库故障**永远不会**拖垮核验主流程；
- 返回的是规范化数据结构（``KbRef``），而不是裸 dict，
  字段语义稳定，前端与报告层可以放心消费。

典型用法（核验链路）::

    from checkers.rag.service import context_for_verification

    ctx, refs = context_for_verification(chunk_texts)
    if refs:
        issue.evidence = format_evidence(refs)

典型用法（问答）::

    from checkers.rag.service import answer
    result = answer("应收账款坏账准备计提比例是多少")
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# 数据契约
# ---------------------------------------------------------------------------
@dataclass
class KbRef:
    """一条知识库引用（可溯源）。"""

    doc_id: str = ""
    file: str = ""              # 来源文件名
    section: str = ""           # 所属小节标题
    path: str = ""              # 完整章节路径，如「附件 / 26.评估对象」
    loc: str = ""               # 位置标识，如「第 3 页」「第 12 段」
    score: float = 0.0          # 融合分
    cos: float = 0.0            # 语义相似度（未启用稠密路时为 0）
    bm25: float = 0.0           # 关键词分
    rank: int = 0
    snippet: str = ""           # 片段摘要
    mode: str = ""              # hybrid / bm25

    @property
    def cite(self) -> str:
        """人可读出处，如「规范.md / 第三章 应收账款评估 / 3.1 账龄分析法」"""
        parts = [p for p in (self.file, self.path or self.section) if p]
        out = " / ".join(parts)
        if self.loc:
            out += f"（{self.loc}）"
        return out

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["cite"] = self.cite
        return d


def format_evidence(refs: Sequence[KbRef], limit: int = 3) -> str:
    """把引用清单格式化为「知识依据」文本，挂在核验问题上。"""
    items = list(refs or [])[:max(1, int(limit))]
    if not items:
        return ""
    return "；".join(r.cite for r in items if r.cite)


# ---------------------------------------------------------------------------
# 能力查询
# ---------------------------------------------------------------------------
def is_enabled(ai_cfg: Optional[Dict[str, Any]] = None) -> bool:
    """知识库联动总开关是否打开。"""
    try:
        cfg = ai_cfg if ai_cfg is not None else _ai_settings()
        return bool(cfg.get("kb_enabled"))
    except Exception:  # noqa: BLE001
        return False


def _ai_settings() -> Dict[str, Any]:
    try:
        from config.settings_manager import load_settings
        return dict(load_settings().get("ai") or {})
    except Exception:  # noqa: BLE001
        return {}


def _doc_ids_of_folder(folder: Optional[str]) -> Optional[List[str]]:
    """目录 id → 该子树下的文档 id 列表；根目录或空值返回 None（不过滤）。"""
    if not folder:
        return None
    try:
        from checkers.rag.engine import load_kb
        from checkers.rag.tree import docs_in_subtree
        return docs_in_subtree(load_kb(), str(folder))
    except Exception:  # noqa: BLE001 - 目录解析失败时退化为不限制范围
        return None


def _to_ref(h: Dict[str, Any]) -> KbRef:
    return KbRef(
        doc_id=str(h.get("doc_id") or ""),
        file=str(h.get("file") or ""),
        section=str(h.get("section") or ""),
        path=str(h.get("path") or ""),
        loc=str(h.get("loc") or ""),
        score=float(h.get("score") or 0.0),
        cos=float(h.get("cos") or 0.0),
        bm25=float(h.get("bm25") or 0.0),
        rank=int(h.get("rank") or 0),
        snippet=str(h.get("snippet") or ""),
        mode=str(h.get("mode") or ""),
    )


# ---------------------------------------------------------------------------
# 核验联动接口
# ---------------------------------------------------------------------------
def retrieve_for_verification(text, top_k: int = 3,
                              dense: Optional[bool] = None,
                              folder: Optional[str] = None,
                              ai_cfg: Optional[Dict[str, Any]] = None
                              ) -> List[KbRef]:
    """核验用检索：传入待核验文本（整段或片段列表），返回带出处的引用。

    - ``text`` 支持 ``str``（自动按段落切分）或 ``Sequence[str]``；
    - ``dense`` 省略时沿用 AI 配置里的 ``kb_dense_in_verify``；
    - ``folder`` 可把范围限定到某个目录子树；
    - 任何异常都返回空列表，绝不影响核验主流程。
    """
    if isinstance(text, str):
        segs = [p.strip() for p in str(text or "").split("\n") if len(p.strip()) >= 2]
    else:
        segs = [str(t or "").strip() for t in (text or []) if str(t or "").strip()]
    if not segs:
        return []

    try:
        from checkers.rag import engine as _engine
        cfg = dict(ai_cfg if ai_cfg is not None else _ai_settings())
        if not cfg.get("kb_enabled"):
            return []
        use_dense = bool(cfg.get("kb_dense_in_verify")) if dense is None else bool(dense)
        k = max(1, min(8, int(cfg.get("kb_top_k") or top_k or 3)))
        hits = _engine.retrieve_for_verify(
            segs, top_k=k, dense=use_dense, ai_cfg=cfg,
            doc_ids=_doc_ids_of_folder(folder),
        )
        return [_to_ref(h) for h in hits]
    except Exception:  # noqa: BLE001
        return []


def context_for_verification(text, top_k: int = 3, budget: int = 6000,
                             dense: Optional[bool] = None,
                             folder: Optional[str] = None,
                             ai_cfg: Optional[Dict[str, Any]] = None
                             ) -> tuple:
    """一步拿到「可直接塞进提示词的上下文」+「引用清单」。

    返回 ``(context_text, refs)``；未命中时为 ``("", [])``。
    这是核验流程最常用的入口。
    """
    refs = retrieve_for_verification(text, top_k=top_k, dense=dense,
                                     folder=folder, ai_cfg=ai_cfg)
    if not refs:
        return "", []
    try:
        from checkers.rag import engine as _engine
        ctx, packed = _engine.pack_hits([r.to_dict() for r in refs], budget)
        out = [KbRef(**{k: v for k, v in p.items() if k in _FIELDS})
               for p in packed]
        return ctx, (out or refs)
    except Exception:  # noqa: BLE001
        return "", refs


#: ``KbRef`` 的可反序列化字段（过滤掉 ``cite`` 之类的派生属性）
_FIELDS = {"doc_id", "file", "section", "path", "loc",
           "score", "cos", "bm25", "rank", "snippet", "mode"}


# ---------------------------------------------------------------------------
# 问答接口
# ---------------------------------------------------------------------------
def answer(question: str, top_k: int = 3,
           folder: Optional[str] = None,
           ai_cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """带溯源的问答。

    返回 ``{ok, answer, refs, citations, grounded, confidence, warnings, mode, elapsed}``。
    与 ``engine.ask`` 的区别：这里返回**规范化引用对象**，且不会抛异常。
    """
    empty = {"ok": True, "answer": "", "refs": [], "citations": [],
             "grounded": False, "confidence": 0.0, "warnings": [],
             "mode": "none", "elapsed": 0.0}
    q = str(question or "").strip()
    if len(q) < 2:
        return empty
    try:
        from checkers.rag import engine as _engine
        cfg = dict(ai_cfg if ai_cfg is not None else _ai_settings())
        res = _engine.ask(q, cfg, max(1, min(8, int(top_k or 3))),
                          _doc_ids_of_folder(folder))
    except Exception:  # noqa: BLE001
        return empty

    srcs = res.get("sources") or []
    refs: List[KbRef] = []
    for s in srcs:
        refs.append(KbRef(
            doc_id=str(s.get("doc_id") or ""),
            file=str(s.get("file") or ""),
            section=str(s.get("section") or ""),
            path=str(s.get("path") or ""),
            loc=str(s.get("loc") or ""),
            score=float(s.get("score") or 0.0),
            snippet=str(s.get("snippet") or ""),
        ))
    audit = res.get("audit") or {}
    return {
        "ok": True,
        "answer": str(res.get("answer") or ""),
        "refs": [r.to_dict() for r in refs],
        "citations": audit.get("citations") or [],
        "grounded": bool(audit.get("grounded")),
        "confidence": float(audit.get("confidence") or 0.0),
        "warnings": list(audit.get("warnings") or []),
        "mode": str(res.get("mode") or "none"),
        "elapsed": float(res.get("elapsed") or 0.0),
    }


# ---------------------------------------------------------------------------
# 目录 / 权限（供核验界面按目录调用）
# ---------------------------------------------------------------------------
def tree() -> Dict[str, Any]:
    """目录树（异常时返回只有根节点的空树）。"""
    try:
        from checkers.rag.tree import build_tree
        return build_tree()
    except Exception:  # noqa: BLE001
        return {"id": "", "name": "根目录", "path": "根目录",
                "folders": [], "docs": [], "folder_count": 0, "doc_count": 0}


def permissions() -> Dict[str, Any]:
    """当前角色与能力矩阵。"""
    try:
        from checkers.rag.permissions import permissions_payload
        return permissions_payload()
    except Exception:  # noqa: BLE001
        return {"ok": False, "role": "operator", "role_label": "核验操作员",
                "caps": {}, "roles": [], "cap_defs": []}


__all__ = ["KbRef", "retrieve_for_verification", "context_for_verification",
           "format_evidence", "answer", "is_enabled", "tree", "permissions"]
