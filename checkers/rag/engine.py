# -*- coding: utf-8 -*-
"""本地知识库（RAG）编排层：持久化 / 增量索引 / 检索 / 问答 / 核验联动。

数据目录：config/knowledge/
    knowledge.json   文档元数据 + 分块文本 + 目录树 + 权限（唯一事实来源）
    files/           原始文件（按 doc_id 存储，供来源跳转下载）
    chroma/          Chroma 向量库持久化目录（默认后端）

相对旧实现的核心升级
--------------------
1. **增量索引**：按文件内容哈希 + 切片配置指纹判断，未变更文档不再重嵌；
2. **分批嵌入**：每批 24 条请求 Ollama，失败自动折半重试（旧版一次性全量请求
   必然超时 → 静默降级 BM25，向量检索形同虚设）；
3. **索引缓存**：BM25 词频表 / IDF 常驻内存，按文件 mtime+size 失效；
4. **RRF + MMR**：替换 min-max 加权融合，消除离群值失真并去除冗余片段；
5. **上下文前缀**：嵌入文本前置「文档名 + 章节路径」（Contextual Retrieval）；
6. **逐片段检索**：核验链路按每个待核验片段各自检索后融合，取代「全文首 2000 字」；
7. **可插拔向量库**：向量不再写进 knowledge.json，改由 ``stores`` 后端承载，
   默认 Chroma 持久化，缺依赖时自动降级为内存后端；
8. **多格式 + OCR**：解析统一走 ``parsers``，支持 PDF/Word/Excel/图片 OCR。

设计约束：无嵌入模型时自动退化为纯 BM25、无 OCR 引擎时跳过扫描件、零网络外发。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from checkers.rag.paths import FILES_DIR, KB_DIR, KB_PATH
from checkers.rag.bm25 import BM25Index
from checkers.rag.fusion import filter_by_relation, mmr, rrf
from checkers.rag.prompts import audit_answer, build_rag_messages
from checkers.rag.text import (
    CHUNK_OVERLAP,
    CHUNK_SCHEMA_VERSION,
    CHUNK_SIZE,
    MAX_CHUNKS_PER_DOC,
    MAX_DOC_CHARS,
    chunk_blocks,
    context_prefix,
    heading_level,
    is_heading,
    prune_query,
    tokenize,
)
from checkers.rag.vectors import (
    cos,
    embed_batch,
    embed_many,
    embed_query,
    embed_status,
    resolve_embed_model,
)

_LOCK = threading.RLock()
DEFAULT_EMBED_MODEL = "bge-m3"
MAX_FILE_MB = 30
MAX_PROMPT_CTX = 5000          # 问答注入知识库片段的字符上限

# 检索候选池大小：各路先取候选，再交给 RRF / MMR
_SPARSE_POOL = 40
_DENSE_POOL = 40
# 核验链路：最多参与检索的待核验片段数（防止超大文件拖慢）
_VERIFY_MAX_CHUNKS = 12
# 核验链路：单次注入知识库的字符上限
_VERIFY_CTX_BUDGET = 6000
# 稠密检索的绝对相关性下限：低于该余弦值视为无关，不注入提示词
MIN_COS = 0.45
# 向量上限：纯 JSON 存储，超过该规模后不再生成向量（自动退回 BM25），
# 避免 knowledge.json 膨胀到数百 MB 拖慢启动（需更大规模时再引入向量库）
MAX_VECTORS = 4000

DEFAULT_CONFIG: Dict[str, Any] = {
    "chunk_size": CHUNK_SIZE,
    "chunk_overlap": CHUNK_OVERLAP,
    # 向量库后端：chroma（默认，本地持久化） / memory（零依赖兜底）
    "vector_backend": "chroma",
    "collection": "docverify_kb",
    # OCR：扫描件 PDF 与图片文件需要；无引擎时自动跳过并给出提示
    "ocr_enabled": True,
    "ocr_engine": "auto",           # auto | rapidocr | paddleocr | tesseract
}

#: 待嵌入项：``(chunk_id, 嵌入文本, 元数据)``
PendingItem = Tuple[str, str, Dict[str, Any]]

#: 根目录 id（与 ``tree.ROOT_ID`` 保持一致，此处冗余定义以避免模块级循环导入）
ROOT_ID = ""


def _ai_settings() -> Dict[str, Any]:
    """读取 AI 配置组；读取失败时返回空字典，保证检索链路不中断。"""
    try:
        from config.settings_manager import load_settings
        return dict(load_settings().get("ai") or {})
    except Exception:  # noqa: BLE001
        return {}


def _kb_config_raw(kb: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """知识库配置（含后端 / OCR 设置），带默认值兜底。"""
    cfg = dict(DEFAULT_CONFIG)
    data = kb if kb is not None else load_kb()
    if isinstance(data.get("config"), dict):
        cfg.update({k: v for k, v in data["config"].items() if k in DEFAULT_CONFIG})
    return cfg


def _store():
    """返回当前向量库后端实例（按配置注入，失败自动降级）。"""
    try:
        from checkers.rag import stores
        return stores.get_store(_kb_config_raw().get("vector_backend"),
                                path=os.path.join(KB_DIR, "chroma"))
    except Exception:  # noqa: BLE001 - 后端不可用时返回 None，检索退化为 BM25
        return None


# ---------------------------------------------------------------------------
# 兼容旧签名的薄封装（旧代码 / 测试可能直接调用）
# ---------------------------------------------------------------------------
def _chunk_blocks(blocks: List[Tuple[str, str]], size: int = CHUNK_SIZE) -> List[Dict[str, str]]:
    return chunk_blocks(blocks, size)


def _is_heading(text: str) -> bool:
    return is_heading(text)


def _build_bm25(chunks: Sequence[Dict[str, Any]]) -> Tuple[List[List[str]], Dict[str, float], float]:
    texts = [c.get("text") or "" for c in chunks]
    paths = [c.get("path") or c.get("section") or "" for c in chunks]
    idx = BM25Index(texts, tokens_list=[tokenize(p) + tokenize(t)
                                        for p, t in zip(paths, texts)])
    return idx.doc_tokens, idx.idf, idx.avg


def _bm25_score(q: Sequence[str], doc_tokens: Sequence[str], idf: Dict[str, float],
                avg: float, k1: float = 1.5, b: float = 0.75) -> float:
    if not doc_tokens:
        return 0.0
    counts: Dict[str, int] = {}
    for t in doc_tokens:
        counts[t] = counts.get(t, 0) + 1
    dl = len(doc_tokens)
    denom = k1 * ((1 - b) + b * dl / avg) if avg > 0 else 1.0
    s = 0.0
    for t in set(q):
        f = counts.get(t, 0)
        if f:
            s += idf.get(t, 0.0) * (f * (k1 + 1)) / (f + denom)
    return s


def _embed_texts(texts: List[str], ai_cfg: Optional[Dict[str, Any]] = None
                 ) -> Optional[List[List[float]]]:
    """兼容旧入口：内部改为分批嵌入。"""
    return embed_batch(texts, ai_cfg)


def _norm01(vals: List[float]) -> List[float]:
    if not vals:
        return []
    mx, mn = max(vals), min(vals)
    r = mx - mn
    if r <= 1e-9:
        return [0.5] * len(vals)
    return [(x - mn) / r for x in vals]


def _cos(a: Sequence[float], b: Sequence[float]) -> float:
    return cos(a, b)


def _clip(s: str, n: int = 120) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


# ---------------------------------------------------------------------------
# 文本提取（沿用原有解析能力，仅整理结构）
# ---------------------------------------------------------------------------
def _decode_text(raw: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "utf-16", "gb18030"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_blocks(name: str, raw: bytes,
                    kb: Optional[Dict[str, Any]] = None) -> List[Tuple[str, str]]:
    """按扩展名提取 [(位置, 文本), ...]。

    具体格式解析已下沉到 ``parsers`` 包（支持 PDF/Word/Excel/纯文本/图片 OCR），
    本层只负责把诊断信息剥离，保持调用方签名不变。
    """
    blocks, _diag = _extract_blocks_with_diag(name, raw, kb)
    return blocks


def _extract_blocks_with_diag(name: str, raw: bytes,
                               kb: Optional[Dict[str, Any]] = None
                               ) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
    """解析文件并同时返回诊断信息（页数 / OCR 命中 / 抽字数等）。"""
    from checkers.rag import parsers

    cfg = _kb_config_raw(kb)
    return parsers.extract_blocks(
        name, raw,
        ocr=bool(cfg.get("ocr_enabled", True)),
        ocr_engine=str(cfg.get("ocr_engine") or "auto"),
    )


# ---------------------------------------------------------------------------
# 持久化（v2 → v3 迁移）
# ---------------------------------------------------------------------------
def _empty_kb() -> Dict[str, Any]:
    return {
        "version": 3,
        "updated_at": "",
        "embed_model": "none",
        "config": dict(DEFAULT_CONFIG),
        "docs": [],
        "folders": [],
        "permissions": {},
        "vectors": {},          # 仅 memory 后端使用；chroma 自带持久化
    }


def _migrate(data: Dict[str, Any]) -> Dict[str, Any]:
    """v2 → v3：补齐章节路径与上下文前缀，移除冗余持久化的 tokens。

    同时为任何版本补齐目录树 / 权限节点，使旧库升级后可直接用树形目录。
    """
    data.setdefault("folders", [])
    data.setdefault("permissions", {})
    if int(data.get("version") or 2) >= 3:
        return data
    cfg = dict(DEFAULT_CONFIG)
    if isinstance(data.get("config"), dict):
        cfg.update({k: v for k, v in data["config"].items() if k in cfg})
    data["config"] = cfg
    for d in data.get("docs") or []:
        if not isinstance(d, dict):
            continue
        name = str(d.get("name") or "")
        for c in d.get("chunks") or []:
            if not isinstance(c, dict):
                continue
            sec = str(c.get("section") or "")
            c["path"] = str(c.get("path") or sec)[:200]
            c["ctx"] = context_prefix(name, c["path"])
            c.pop("tokens", None)      # tokens 由加载期重建，不再落盘
        d.setdefault("sig", "")
        d.setdefault("hash", "")
    data["version"] = 3
    return data


def load_kb(path: str = "") -> Dict[str, Any]:
    """载入知识库 JSON。

    路径在**调用时**解析（而非导入时绑定默认参数），
    便于测试注入临时目录、未来支持多知识库实例。
    """
    path = path or KB_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("docs"), list):
            data.setdefault("vectors", {})
            data.setdefault("embed_model", "none")
            return _migrate(data)
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return _empty_kb()


def save_kb(kb: Dict[str, Any], path: str = "") -> bool:
    path = path or KB_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with _LOCK:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(kb, f, ensure_ascii=False)
            os.replace(tmp, path)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 内存索引（带缓存）
# ---------------------------------------------------------------------------
class _Index:
    """扁平化检索视图：一次构建，多次查询。"""

    __slots__ = ("raw", "cids", "texts", "sections", "paths", "locs",
                 "doc_of", "chunk_map", "vectors", "bm25", "token_sets",
                 "vec_store", "_cid_pos")

    def __init__(self, kb: Dict[str, Any]) -> None:
        self.raw = kb
        self.cids: List[str] = []
        self.texts: List[str] = []
        self.sections: List[str] = []
        self.paths: List[str] = []
        self.locs: List[str] = []
        self.doc_of: Dict[str, Dict[str, Any]] = {}
        self.chunk_map: Dict[str, Dict[str, Any]] = {}

        for d in kb.get("docs") or []:
            if not isinstance(d, dict):
                continue
            for c in d.get("chunks") or []:
                if not isinstance(c, dict):
                    continue
                cid = str(c.get("cid") or "")
                self.cids.append(cid)
                self.texts.append(str(c.get("text") or ""))
                self.paths.append(str(c.get("path") or c.get("section") or "")[:200])
                self.sections.append(str(c.get("section") or "")[:120])
                self.locs.append(str(c.get("loc") or "")[:60])
                self.doc_of[cid] = d
                self.chunk_map[cid] = c

        # 仅 memory 后端把向量放在 knowledge.json；chroma 后端此字段为空
        self.vectors: Dict[str, Sequence[float]] = kb.get("vectors") or {}
        self.vec_store = _store()
        self._cid_pos: Dict[str, int] = {c: i for i, c in enumerate(self.cids)}
        self.bm25 = BM25Index(
            self.texts,
            tokens_list=[tokenize(p) + tokenize(t)
                         for p, t in zip(self.paths, self.texts)],
            cids=self.cids,
        )
        self.token_sets = [set(t) for t in self.bm25.doc_tokens]

    @property
    def size(self) -> int:
        return len(self.cids)

    def vecs(self) -> List[Sequence[float]]:
        """全库向量（仅 memory 后端可用；chroma 不落 JSON）。"""
        return [self.vectors.get(c) or () for c in self.cids]

    def dense_search(self, qv: Sequence[float], pool: int,
                     doc_ids: Optional[Sequence[str]] = None
                     ) -> List[Tuple[int, float]]:
        """稠密检索 → ``[(位置下标, 余弦)]``。

        位置下标用于与 BM25 名次列表一起参与 RRF 融合，故必须映射回 cids 次序。
        """
        if not qv or self.vec_store is None:
            return []
        try:
            hits = self.vec_store.search(qv, pool, doc_ids)
        except Exception:  # noqa: BLE001 - 后端异常时该路降级为空
            return []
        out: List[Tuple[int, float]] = []
        for cid, s in hits or []:
            i = self._cid_pos.get(str(cid))
            if i is not None:
                out.append((i, float(s)))
        return out

    def has_vectors(self) -> bool:
        """是否存在可用向量（内存快照或向量库后端）。"""
        if self.vectors:
            return True
        if self.vec_store is None:
            return False
        try:
            return int(self.vec_store.count()) > 0
        except Exception:  # noqa: BLE001
            return False

    def vecs_for(self, cand_idxs: Sequence[int]) -> Dict[int, Sequence[float]]:
        """取候选片段的向量，返回 ``{片段下标: 向量}``（供 MMR 去冗余）。

        只取候选而非全库 —— chroma 后端下这一点决定检索是否还够快。
        """
        if not cand_idxs:
            return {}
        if self.vectors:
            out: Dict[int, Sequence[float]] = {}
            for i in cand_idxs:
                v = self.vectors.get(self.cids[i])
                if v:
                    out[i] = v
            return out
        if self.vec_store is None:
            return {}
        wanted = [self.cids[i] for i in cand_idxs]
        try:
            got = self.vec_store.get(wanted) or {}
        except Exception:  # noqa: BLE001
            return {}
        res: Dict[int, Sequence[float]] = {}
        for i, cid in zip(cand_idxs, wanted):
            v = got.get(cid)
            if v:
                res[i] = v
        return res


_CACHE: Dict[str, Any] = {"sig": "", "index": None}


def _signature(path: str = "") -> str:
    path = path or KB_PATH
    try:
        st = os.stat(path)
        return f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return f"missing:{time.time()}"


def load_index(force: bool = False) -> _Index:
    """返回内存索引；文件未变化则直接复用缓存。"""
    sig = _signature()
    cached = _CACHE.get("index")
    if not force and cached is not None and _CACHE.get("sig") == sig:
        return cached
    with _LOCK:
        if force or _CACHE.get("index") is None or _CACHE.get("sig") != sig:
            _CACHE["index"] = _Index(load_kb())
            _CACHE["sig"] = _signature()
        return _CACHE["index"]


def invalidate() -> None:
    """索引变更后调用，强制下次重新载入。"""
    with _LOCK:
        _CACHE["index"] = None
        _CACHE["sig"] = ""


# ---------------------------------------------------------------------------
# 查询处理
# ---------------------------------------------------------------------------
def _rank_desc(scores: Sequence[float], pool: int = _SPARSE_POOL,
               min_score: float = 0.0) -> List[int]:
    idx = [i for i, s in enumerate(scores) if s > min_score]
    idx.sort(key=lambda i: scores[i], reverse=True)
    return idx[:pool]


def _filter(rank: Sequence[int], allow: Optional[set]) -> List[int]:
    """把某路检索结果限制在允许的片段集合内（目录过滤）。"""
    if allow is None:
        return list(rank)
    return [i for i in rank if i in allow]


def _gate(fused: List[Tuple[int, float]], cos_scores: Sequence[float],
          dense_used: bool, top_k: int, min_ratio: float = 0.28
          ) -> List[Tuple[int, float]]:
    """相关性闸门：先按相对分数过滤，稠密可用时再叠加绝对下限。

    中文 bigram 的 BM25 极易产生「伪相关」（如「估基」「子计」），
    只靠相对名次会把噪声片段一并注入提示词，反而诱发误判。
    实测：相关片段余弦 0.58~0.71，无关片段 0.34~0.37，
    故取 ``MIN_COS`` 作为绝对下限，宁可不注入也不注入噪声。
    """
    if not fused:
        return []
    # 无稠密路时 BM25 伪相关较多，收紧相对阈值；有稠密路时用绝对下限更稳
    floor = fused[0][1] * (min_ratio if dense_used else 0.5)
    out: List[Tuple[int, float]] = []
    for i, s in fused:
        if s < floor:
            continue
        if dense_used and (cos_scores[i] if i < len(cos_scores) else 0.0) < MIN_COS:
            continue
        out.append((i, s))
    return out[:max(int(top_k) * 4, int(top_k) + 4)]


def _dense_enabled(ai_cfg: Optional[Dict[str, Any]]) -> bool:
    return bool(ai_cfg and ai_cfg.get("embed_enabled", True))


def _prf_terms(idx: _Index, q: Sequence[str], k: int = 6) -> List[str]:
    """伪相关反馈：取首轮命中片段中 IDF 权重最高且不在查询里的词元。"""
    sc = idx.bm25.scores(q)
    if not sc:
        return []
    ranked = sorted(range(len(sc)), key=lambda i: sc[i], reverse=True)[:2]
    qset = set(q)
    cand: Dict[str, float] = {}
    for i in ranked:
        if sc[i] <= 0:
            continue
        toks = idx.bm25.doc_tokens[i]
        counts: Dict[str, int] = {}
        for t in toks:
            counts[t] = counts.get(t, 0) + 1
        for t in counts:
            if t in qset:
                continue
            w = idx.bm25.idf.get(t, 0.0) * counts[t]
            if w > cand.get(t, 0.0):
                cand[t] = w
    return [t for t, _ in sorted(cand.items(), key=lambda x: x[1], reverse=True)[:k]]


def _prepare_query(idx: _Index, query: str, prf: bool = True) -> List[str]:
    """分词 → IDF 剪枝（去高频无区分度词元）→ 伪相关反馈扩展。"""
    q_raw = tokenize(query or "")
    if not q_raw:
        return []
    q = prune_query(q_raw, idx.bm25.df, idx.bm25.n)
    if not prf or idx.size < 6:
        return q or q_raw
    return list(dict.fromkeys(list(q) + _prf_terms(idx, q)))


# ---------------------------------------------------------------------------
# 索引构建
# ---------------------------------------------------------------------------
def _doc_signature(cfg: Dict[str, Any]) -> str:
    """切片配置指纹：结构版本 + 块大小 + 重叠。"""
    return f"v{CHUNK_SCHEMA_VERSION}:{cfg.get('chunk_size')}:{cfg.get('chunk_overlap')}"


def _current_config(kb: Dict[str, Any]) -> Tuple[Dict[str, Any], int, int]:
    cfg = dict(DEFAULT_CONFIG)
    if isinstance(kb.get("config"), dict):
        cfg.update({k: v for k, v in kb["config"].items() if k in DEFAULT_CONFIG})
    size = max(120, min(4000, int(cfg.get("chunk_size") or CHUNK_SIZE)))
    overlap = max(0, min(int(cfg.get("chunk_overlap") or 0), size // 3))
    cfg["chunk_size"], cfg["chunk_overlap"] = size, overlap
    kb["config"] = cfg
    return cfg, size, overlap


def _build_chunk_entries(did: str, name: str,
                         chunks: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, c in enumerate(chunks):
        text = c.get("text", "")
        path = (c.get("path") or c.get("section") or "").strip()
        out.append({
            "cid": "c_" + hashlib.md5(
                f"{did}:{i}:{text[:80]}".encode("utf-8")).hexdigest()[:12],
            "section": c.get("section", "")[:120],
            "path": path[:200],
            "loc": c.get("loc", "")[:60],
            "text": text,
            "ctx": context_prefix(name, path),
        })
    return out


def _embed_pending(pending: Sequence[PendingItem], ai: Dict[str, Any],
                   vectors: Dict[str, Any]) -> Tuple[str, int]:
    """分批嵌入待处理片段，写入当前向量后端。

    ``pending``: ``[(chunk_id, 待嵌入文本, 元数据), ...]``
    返回 ``(生效的嵌入模型名, 向量维度)``；不可用时为 ``("none", 0)``。
    """
    if not pending or not _dense_enabled(ai):
        return "none", 0
    if len(pending) > MAX_VECTORS:      # 超规模：不生成向量，退回 BM25
        return "none", 0
    model, ok = resolve_embed_model(ai)
    if not ok:
        return "none", 0
    embs = embed_batch([p[1] for p in pending], ai)
    if not embs or len(embs) != len(pending):
        return "none", 0

    items = [(str(p[0]), v, p[2]) for p, v in zip(pending, embs)]
    store = _store()
    if store is not None:
        try:
            store.upsert(items)
        except Exception:  # noqa: BLE001 - 后端写入失败时退回内存快照
            for cid, v, _m in items:
                vectors[cid] = v
    else:
        for cid, v, _m in items:
            vectors[cid] = v
    return model, len(embs[0])


def index_docs(files: List[Tuple[str, bytes]], folder: str = ROOT_ID
               ) -> Tuple[int, List[Dict[str, Any]], List[str]]:
    """批量入库。``files: [(原始文件名, 字节内容), ...]``。

    返回 ``(成功数, 文档信息, 失败说明)``；文档信息里带 ``diag`` 解析诊断
    （页数 / OCR 页数 / 抽字数）与 ``folder`` 归属。

    增量策略：内容哈希与切片配置均未变化且已有分块的文档直接复用，
    仅对新增 / 变更文档重新切片并嵌入，避免全库重嵌。
    """
    kb = load_kb()
    cfg, size, overlap = _current_config(kb)
    sig = _doc_signature(cfg)
    docs: List[Dict[str, Any]] = list(kb.get("docs") or [])
    vectors: Dict[str, Any] = dict(kb.get("vectors") or {})

    added, docs_meta, errors = 0, [], []
    pending: List[PendingItem] = []          # (cid, 待嵌入文本, 元数据)
    changed = False
    store = _store()

    for name, raw in files:
        name = os.path.basename((name or "").strip())
        try:
            if not name:
                raise ValueError("文件名不能为空")
            if not raw:
                raise ValueError("文件内容为空")
            if len(raw) > MAX_FILE_MB * 1024 * 1024:
                raise ValueError(f"超过 {MAX_FILE_MB}MB 大小限制")

            did = "d_" + hashlib.md5(name.encode("utf-8")).hexdigest()[:10]
            existing = next((d for d in docs if d.get("id") == did), None)
            content_hash = hashlib.sha1(raw).hexdigest()[:16]

            # 命中：内容未变 + 切片配置未变 + 已有分块 + 目录未变 → 直接复用
            if (existing and existing.get("hash") == content_hash
                    and existing.get("sig") == sig and existing.get("chunks")
                    and str(existing.get("folder") or ROOT_ID) == str(folder or ROOT_ID)):
                added += 1
                docs_meta.append({
                    "id": did, "name": name,
                    "chars": sum(len(c.get("text") or "") for c in existing["chunks"]),
                    "chunks": len(existing["chunks"]), "reused": True,
                    "folder": str(existing.get("folder") or ROOT_ID),
                })
                continue

            blocks, diag = _extract_blocks_with_diag(name, raw, kb)
            if not blocks:
                raise ValueError("未能从文件中提取到有效文本")
            chunks = chunk_blocks(blocks, size, overlap)
            if not chunks:
                raise ValueError("文本过短，未生成索引分块")

            storage = f"{did}{os.path.splitext(name)[1].lower() or '.txt'}"
            os.makedirs(FILES_DIR, exist_ok=True)
            with open(os.path.join(FILES_DIR, storage), "wb") as f:
                f.write(raw)

            # 清理旧分块向量后再换入新分块
            old_cids = [str(c.get("cid") or "") for c in ((existing or {}).get("chunks") or [])
                        if c.get("cid")]
            for c in old_cids:
                vectors.pop(c, None)
            if store is not None and old_cids:
                try:
                    store.delete(old_cids)
                except Exception:  # noqa: BLE001 - 后端删除失败不阻断入库
                    pass
            docs = [d for d in docs if d.get("id") != did]

            entries = _build_chunk_entries(did, name, chunks)
            docs.append({
                "id": did,
                "name": name,
                "storage": storage,
                "size": len(raw),
                "added_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "hash": content_hash,
                "sig": sig,
                "folder": str(folder or ROOT_ID),
                "chunks": entries,
            })
            for e in entries:
                pending.append((e["cid"], (e.get("ctx") or "") + (e.get("text") or ""),
                                {"doc_id": did, "file": name,
                                 "section": (e.get("section") or "")[:120],
                                 "path": (e.get("path") or "")[:200],
                                 "loc": (e.get("loc") or "")[:60]}))
            added += 1
            changed = True
            docs_meta.append({
                "id": did, "name": name, "diag": diag,
                "chars": sum(len(c["text"]) for c in entries),
                "chunks": len(entries), "reused": False,
                "folder": str(folder or ROOT_ID),
            })
        except Exception as exc:  # noqa: BLE001 - 单文件失败不中断批量
            errors.append(f"{name}：{exc}")

    kb["docs"] = docs
    if changed:
        ai = _ai_settings()
        # 向量缺失回填：整库无向量时补齐（如用户后来才装嵌入模型 / 换了后端）
        if not vectors and (store is None or _store_count(store) == 0):
            for d in docs:
                for c in (d.get("chunks") or []):
                    if c.get("cid"):
                        pending.append((
                            str(c["cid"]),
                            (c.get("ctx") or "") + (c.get("text") or ""),
                            {"doc_id": str(d.get("id") or ""),
                             "file": str(d.get("name") or ""),
                             "section": str(c.get("section") or "")[:120],
                             "path": str(c.get("path") or "")[:200],
                            "loc": str(c.get("loc") or "")[:60]},
                        ))
        model, dim = _embed_pending(pending, ai, vectors)
        kb["embed_model"] = model
        kb["embed_dim"] = dim
        kb["embed_capped"] = len(pending) > MAX_VECTORS
        # chroma 自带持久化，不再把向量写进 knowledge.json（避免文件膨胀）
        kb["vectors"] = vectors if _is_memory_backend() else {}
        kb["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_kb(kb)
        invalidate()

    return added, docs_meta, errors


def _store_count(store) -> int:
    try:
        return int(store.count())
    except Exception:  # noqa: BLE001
        return 0


def _is_memory_backend() -> bool:
    """当前是否使用内存 / JSON 后端（决定向量是否写回 knowledge.json）。"""
    try:
        from checkers.rag.stores.memory_store import MemoryStore
        return isinstance(_store(), MemoryStore)
    except Exception:  # noqa: BLE001
        return True


def reindex_doc(doc_id: str, ai_cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """单文档重新切片并重嵌向量（文档更新后使用）。

    与 ``rebuild_index`` 的区别：只重建指定文档，不触碰库内其他文档，
    成本低、可高频执行。
    """
    kb = load_kb()
    cfg, size, overlap = _current_config(kb)
    sig = _doc_signature(cfg)
    doc = next((d for d in (kb.get("docs") or [])
                if str(d.get("id")) == str(doc_id)), None)
    if doc is None:
        return {"ok": False, "message": "文档不存在"}

    name = str(doc.get("name") or "")
    storage = str(doc.get("storage") or "")
    p = os.path.join(FILES_DIR, os.path.basename(storage)) if storage else ""
    if not name or not (p and os.path.isfile(p)):
        return {"ok": False, "message": "原文文件缺失，无法重建（请重新上传该文档）"}

    try:
        with open(p, "rb") as f:
            raw = f.read()
        blocks, diag = _extract_blocks_with_diag(name, raw, kb)
        chunks = chunk_blocks(blocks, size, overlap)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"解析失败：{exc}"}
    if not chunks:
        return {"ok": False, "message": "文本过短，未生成索引分块"}

    old_cids = [str(c.get("cid") or "") for c in (doc.get("chunks") or []) if c.get("cid")]
    store = _store()
    if store is not None and old_cids:
        try:
            store.delete(old_cids)
        except Exception:  # noqa: BLE001
            pass
    vectors = dict(kb.get("vectors") or {})
    for c in old_cids:
        vectors.pop(c, None)

    entries = _build_chunk_entries(str(doc.get("id") or ""), name, chunks)
    doc["chunks"] = entries
    doc["hash"] = hashlib.sha1(raw).hexdigest()[:16]
    doc["sig"] = sig
    doc["size"] = len(raw)

    ai = ai_cfg if ai_cfg is not None else _ai_settings()
    pending: List[PendingItem] = [
        (e["cid"], (e.get("ctx") or "") + (e.get("text") or ""),
         {"doc_id": str(doc.get("id") or ""), "file": name,
          "section": (e.get("section") or "")[:120],
          "path": (e.get("path") or "")[:200],
          "loc": (e.get("loc") or "")[:60]})
        for e in entries
    ]
    model, dim = _embed_pending(pending, ai, vectors)
    kb["embed_model"] = model
    kb["embed_dim"] = dim
    kb["vectors"] = vectors if _is_memory_backend() else {}
    kb["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_kb(kb)
    invalidate()
    # 刷盘：Chroma 持久化后端需 close() 才会把向量 checkpoint 到磁盘，
    # 否则进程退出后向量仅停留在内存客户端，新进程读取会得到 0 向量。
    try:
        from checkers.rag import stores
        stores.reset_store()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "id": doc.get("id", ""), "name": name,
            "chunks": len(entries), "embed_model": model, "diag": diag}


def rebuild_index(ai_cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """强制重建：按已存原文重新切片并全量重嵌（切片参数变更后使用）。"""
    kb = load_kb()
    _cfg, size, overlap = _current_config(kb)
    sig = _doc_signature(_cfg)
    docs = list(kb.get("docs") or [])
    pending: List[PendingItem] = []
    rebuilt, skipped, reembedded = 0, 0, 0

    store = _store()
    if store is not None:
        try:
            store.clear()          # 全量重建：先清空，杜绝孤儿向量
        except Exception:  # noqa: BLE001
            pass

    for d in docs:
        name = str(d.get("name") or "")
        storage = str(d.get("storage") or "")
        p = os.path.join(FILES_DIR, os.path.basename(storage)) if storage else ""
        if not name or not (p and os.path.isfile(p)):
            # 原文丢失（如数据恢复场景）：改用已有分块的文本重嵌，
            # 不再重新切片 —— 检索语义不变，只是拿不到更细的颗粒度。
            keep = [c for c in (d.get("chunks") or []) if c.get("cid")]
            if not keep:
                skipped += 1
                continue
            for c in keep:
                pending.append((str(c.get("cid") or ""),
                                (c.get("ctx") or "") + (c.get("text") or ""),
                                {"doc_id": str(d.get("id") or ""), "file": name,
                                 "section": str(c.get("section") or "")[:120],
                                 "path": str(c.get("path") or "")[:200],
                                 "loc": str(c.get("loc") or "")[:60]}))
            reembedded += 1
            continue
        try:
            with open(p, "rb") as f:
                raw = f.read()
            blocks, _diag = _extract_blocks_with_diag(name, raw, kb)
            chunks = chunk_blocks(blocks, size, overlap)
        except Exception:  # noqa: BLE001 - 单文档重建失败跳过
            skipped += 1
            continue
        if not chunks:
            skipped += 1
            continue
        entries = _build_chunk_entries(str(d.get("id") or ""), name, chunks)
        d["chunks"] = entries
        d["hash"] = hashlib.sha1(raw).hexdigest()[:16]
        d["sig"] = sig
        d["size"] = len(raw)
        for e in entries:
            pending.append((e["cid"], (e.get("ctx") or "") + (e.get("text") or ""),
                            {"doc_id": str(d.get("id") or ""), "file": name,
                             "section": (e.get("section") or "")[:120],
                             "path": (e.get("path") or "")[:200],
                             "loc": (e.get("loc") or "")[:60]}))
        rebuilt += 1

    if ai_cfg is None:
        ai_cfg = _ai_settings()

    vectors: Dict[str, Any] = {}
    model, dim = _embed_pending(pending, ai_cfg, vectors)
    kb["embed_model"] = model
    kb["embed_dim"] = dim
    kb["embed_capped"] = len(pending) > MAX_VECTORS
    kb["vectors"] = vectors if _is_memory_backend() else {}
    kb["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_kb(kb)
    invalidate()
    # 刷盘：Chroma 持久化后端需 close() 才会把向量 checkpoint 到磁盘，
    # 否则进程退出后向量仅停留在内存客户端，新进程读取会得到 0 向量。
    try:
        from checkers.rag import stores
        stores.reset_store()
    except Exception:  # noqa: BLE001
        pass
    st = store.stats() if store is not None else {"backend": "memory"}
    return {"ok": True, "rebuilt": rebuilt, "reembedded": reembedded,
            "skipped": skipped, "chunks": len(pending),
            "embed_model": kb["embed_model"],
            "embed_capped": kb["embed_capped"], "store": st}


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------
def _hits_to_out(idx: _Index, fused: List[Tuple[int, float]], picked: List[int],
                 bm: Sequence[float], cos_scores: Sequence[float],
                 dense_used: bool) -> List[Dict[str, Any]]:
    fmap = dict(fused)
    out: List[Dict[str, Any]] = []
    for rank, i in enumerate(picked, 1):
        cid = idx.cids[i]
        d = idx.doc_of.get(cid) or {}
        c = idx.chunk_map.get(cid) or {}
        out.append({
            "chunk_id": cid,
            "doc_id": d.get("id", ""),
            "file": d.get("name", ""),
            "section": c.get("section") or d.get("name", ""),
            "path": c.get("path") or c.get("section") or "",
            "loc": c.get("loc", ""),
            "text": c.get("text", ""),
            "score": round(float(fmap.get(i, 0.0)), 4),
            "bm25": round(float(bm[i]), 4) if bm else 0.0,
            "cos": round(float(cos_scores[i]), 4) if (dense_used and cos_scores) else 0.0,
            "rank": rank,
            "mode": "hybrid" if dense_used else "bm25",
        })
    return out


def retrieve(query: str, ai_cfg: Optional[Dict[str, Any]] = None,
             top_k: int = 3, doc_ids: Optional[Sequence[str]] = None,
             prf: bool = True) -> List[Dict[str, Any]]:
    """混合检索（BM25 必用 + 可用时叠加稠密向量），RRF 融合 + MMR 去冗余。

    输出保持旧契约：chunk_id / doc_id / file / section / loc / text / score，
    额外补充 path / bm25 / cos / mode 便于溯源与诊断。
    """
    if ai_cfg is None:
        ai_cfg = _ai_settings()
    idx = load_index()
    if idx.size == 0:
        return []

    # 目录过滤：限定检索范围的片段下标集合
    allow: Optional[set] = None
    if doc_ids:
        wanted = {str(d) for d in doc_ids if str(d)}
        allow = {i for i, cid in enumerate(idx.cids)
                 if str((idx.doc_of.get(cid) or {}).get("id") or "") in wanted}
        if not allow:
            return []

    q = _prepare_query(idx, query, prf=prf)
    if not q:
        return []

    bm = idx.bm25.scores(q)
    cos_scores: List[float] = []
    dense_used = False
    if _dense_enabled(ai_cfg) and idx.has_vectors():
        qv = embed_query(query or "", ai_cfg)
        # 向量维度必须与入库时一致，否则余弦无意义（换过嵌入模型需重建索引）
        dim = int(idx.raw.get("embed_dim") or 0)
        if qv and (not dim or len(qv) == dim):
            dense_hits = idx.dense_search(qv, _DENSE_POOL, doc_ids)
            if dense_hits:
                dense_used = True
                cos_scores = [0.0] * idx.size
                for i, s in dense_hits:
                    cos_scores[i] = s

    ranks: List[List[int]] = [_filter(_rank_desc(bm, _SPARSE_POOL), allow)]
    if dense_used:
        ranks.append(_filter(_rank_desc(cos_scores, _DENSE_POOL, MIN_COS), allow))

    fused = rrf(ranks)
    kept = _gate(fused, cos_scores, dense_used, top_k)
    picked = mmr(kept, idx.token_sets,
                 idx.vecs_for([i for i, _ in kept]) if dense_used else None,
                 top_k=top_k)
    return _hits_to_out(idx, fused, picked, bm, cos_scores, dense_used)


def retrieve_for_verify(chunk_texts: Sequence[str],
                        top_k: int = 3,
                        dense: bool = False,
                        ai_cfg: Optional[Dict[str, Any]] = None,
                        doc_ids: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """核验链路专用检索：对每个待核验片段各检一次，再 RRF 融合。

    旧实现用「文件开头 2000 字」作为唯一查询 —— 与逐段核验的内容脱节，
    长文档后半部分几乎拿不到相关知识。改为多路查询 + 名次融合，
    代价仅是一次内存 BM25 打分（索引已缓存，毫秒级）。

    ``doc_ids`` 可把检索范围限定在某个目录子树内（按目录核验时使用）。
    """
    if ai_cfg is None:
        ai_cfg = _ai_settings()
    idx = load_index()
    if idx.size == 0:
        return []

    # 目录过滤：先算出允许的片段集合，再对各路结果做交集
    allow: Optional[set] = None
    if doc_ids:
        wanted = {str(d) for d in doc_ids if str(d)}
        allow = {i for i, cid in enumerate(idx.cids)
                 if str((idx.doc_of.get(cid) or {}).get("id") or "") in wanted}
        if not allow:
            return []

    ranks: List[List[int]] = []
    bm = [0.0] * idx.size          # 跨片段聚合的 BM25 分（仅用于诊断展示）
    hit_any = False
    for t in list(chunk_texts)[:_VERIFY_MAX_CHUNKS]:
        q = _prepare_query(idx, t[:1500], prf=False)
        if not q:
            continue
        sc = idx.bm25.scores(q)
        if any(sc):
            hit_any = True
            for i, s in enumerate(sc):
                bm[i] += s
        ranks.append(_filter(idx.bm25.top(q, 12), allow))

    cos_scores: List[float] = []
    if not hit_any:
        # 退化：整文合并作为单一查询
        q = _prepare_query(idx, "\n".join(list(chunk_texts)[:8])[:2000], prf=False)
        if not q:
            return []
        ranks = [_filter(idx.bm25.top(q, 12), allow)]
        bm = idx.bm25.scores(q)

    dense_used = False
    if dense and ai_cfg and idx.has_vectors() and _dense_enabled(ai_cfg):
        # 所有待核验片段一次性批量嵌入，只发一批请求（而非每片段一次）
        segs = [(t or "")[:1500] for t in list(chunk_texts)[:_VERIFY_MAX_CHUNKS]]
        seg_vecs = embed_many(segs, ai_cfg) if segs else []
        dim_ok = [v for v in seg_vecs if v]
        if dim_ok:
            cos_scores = [0.0] * idx.size
            for v in dim_ok:
                for i, s in idx.dense_search(v, _DENSE_POOL, doc_ids):
                    if s > cos_scores[i]:
                        cos_scores[i] = s
            dense_used = any(cos_scores)

    if dense_used:
        ranks.append(_filter(_rank_desc(cos_scores, _DENSE_POOL, MIN_COS), allow))

    fused = rrf(ranks)
    kept = _gate(fused, cos_scores, dense_used, top_k)
    picked = mmr(kept, idx.token_sets,
                 idx.vecs_for([i for i, _ in kept]) if dense_used else None,
                 top_k=top_k)
    return _hits_to_out(idx, fused, picked, bm, cos_scores, dense_used)


def pack_hits(hits: Sequence[Dict[str, Any]],
              budget: int = MAX_PROMPT_CTX) -> Tuple[str, List[Dict[str, Any]]]:
    """按相关性顺序打包上下文，返回 (拼接文本, 引用清单)。

    refs 条目可直接下发给前端作为「知识依据」展示，并支持跳转原文核对。
    """
    ctx: List[str] = []
    refs: List[Dict[str, Any]] = []
    used = 0
    for h in hits:
        path = h.get("path") or h.get("section") or ""
        head = h.get("file", "") + (f" / {path}" if path else "")
        part = f"【{head}】\n{h.get('text', '')}"
        if used + len(part) > budget:
            break
        ctx.append(part)
        used += len(part)
        refs.append({
            "file": h.get("file", ""),
            "section": h.get("section", ""),
            "path": path,
            "loc": h.get("loc", ""),
            "doc_id": h.get("doc_id", ""),
            "score": h.get("score", 0),
            "snippet": _clip(h.get("text", ""), 160),
        })
    return "\n\n".join(ctx), refs


# ---------------------------------------------------------------------------
# 问答
# ---------------------------------------------------------------------------
def ask(question: str, ai_cfg: Optional[Dict[str, Any]] = None,
        top_k: int = 3, doc_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """检索 → 打包上下文 → 生成带来源标注与可信度的回答。

    新增 ``doc_ids``：可把本次问答限定在某个目录子树内（按目录检索）。
    返回值在旧契约（answer / sources / embed_used / mode / elapsed）之上，
    追加 ``audit``（是否 grounded、可信度、告警）供界面提示复核。
    """
    t0 = time.time()
    try:
        from checkers.ai_checker import _call_ollama, _call_openai
    except Exception:  # noqa: BLE001
        _call_ollama = _call_openai = None  # type: ignore[assignment]

    hits: List[Dict[str, Any]] = []
    err = ""
    try:
        hits = retrieve(question, ai_cfg, top_k, doc_ids)
    except Exception as exc:  # noqa: BLE001 - 检索失败降级提示
        err = f"知识库检索失败：{exc}"

    if not hits:
        payload = {
            "answer": "知识库为空或内容过少，暂无法回答。请先在「本地知识库」中上传文档并重新索引。",
            "sources": [], "embed_used": False,
            "mode": "none", "elapsed": round(time.time() - t0, 2),
            "audit": {"grounded": False, "refused": True, "citations": [],
                      "unknown": [], "confidence": 0.0,
                      "warnings": ["未命中任何知识库资料"]},
        }
        if err:
            payload["error"] = err
        return payload

    ctx, refs = pack_hits(hits, MAX_PROMPT_CTX)
    cfg = dict(ai_cfg or {})
    embed_used = any(h.get("mode") == "hybrid" for h in hits)
    max_cos = max([float(h.get("cos") or 0.0) for h in hits] or [0.0])

    answer = ""
    try:
        if _call_ollama is None:
            raise RuntimeError("AI 调用层不可用")
        msgs = build_rag_messages(question, ctx)
        if (cfg.get("mode") or "local") == "local":
            answer = _call_ollama(cfg, msgs, think=False)
        else:
            answer = _call_openai(cfg, msgs)
    except Exception as exc:  # noqa: BLE001 - 生成失败降级提示
        err = str(exc)
        answer = "（AI 回答失败：" + err + "）"

    try:
        from checkers import ai_activity
        ai_activity.log_event("rag", model=str(cfg.get("model") or ""),
                              detail=f"问答「{_clip(question, 30)}」命中 {len(hits)} 段"
                                     + (f"，失败：{err}" if err else ""),
                              ok=not bool(err))
    except Exception:  # noqa: BLE001 - 日志失败不影响主流程
        pass

    payload = {
        "answer": answer,
        "sources": refs,
        "embed_used": embed_used,
        "embed_model": load_index().raw.get("embed_model") or "none",
        "mode": (hits[0].get("mode") or "bm25") if hits else "none",
        "elapsed": round(time.time() - t0, 2),
        "audit": audit_answer(answer, refs, max_cos),
    }
    if err:
        payload["error"] = err
    return payload


# ---------------------------------------------------------------------------
# 管理：状态 / 配置 / 删除 / 清空 / 原文
# ---------------------------------------------------------------------------
def kb_status() -> Dict[str, Any]:
    """知识库概览（前端列表用，不含 chunk 全文）。"""
    idx = load_index()
    kb = idx.raw
    docs = []
    total_chunks = 0
    from checkers.rag.tree import path_of

    for d in kb.get("docs", []):
        if not isinstance(d, dict):
            continue
        chunks = d.get("chunks") or []
        total_chunks += len(chunks)
        folder = str(d.get("folder") or ROOT_ID)
        docs.append({
            "id": d.get("id"),
            "name": d.get("name"),
            "size": int(d.get("size") or 0),
            "chars": sum(len(c.get("text") or "") for c in chunks),
            "chunks": len(chunks),
            "added_at": d.get("added_at", ""),
            "folder": folder,
            "path": path_of(kb, folder),
        })
    cfg = _kb_config_raw(kb)
    st = embed_status(_ai_settings())
    store = _store()
    store_stats = store.stats() if store is not None else {"backend": "memory"}
    from checkers.rag import parsers
    pst = parsers.parser_status(str(cfg.get("ocr_engine") or "auto"))
    return {
        "ok": True,
        "docs": docs,
        "total_chunks": total_chunks,
        "embed_model": kb.get("embed_model") or "none",
        "embed_resolved_model": str(st.get("embed_model") or ""),
        "embed_resolved": bool(st.get("embed_resolved")),
        "embed_dim": int(kb.get("embed_dim") or 0),
        "embed_available": bool(kb.get("vectors")) or bool(store_stats.get("vectors")),
        "embed_capped": bool(kb.get("embed_capped")),
        "updated_at": kb.get("updated_at", ""),
        "config": {
            "chunk_size": int(cfg.get("chunk_size") or CHUNK_SIZE),
            "chunk_overlap": int(cfg.get("chunk_overlap") or CHUNK_OVERLAP),
            "vector_backend": str(cfg.get("vector_backend") or "chroma"),
            "collection": str(cfg.get("collection") or "docverify_kb"),
            "ocr_enabled": bool(cfg.get("ocr_enabled", True)),
            "ocr_engine": str(cfg.get("ocr_engine") or "auto"),
        },
        "store": store_stats,
        "store_error": _store_error(),
        "backends": _backend_options(),
        "parsers": pst,
    }


def _store_error() -> str:
    try:
        from checkers.rag import stores
        return stores.current_error()
    except Exception:  # noqa: BLE001
        return ""


def _backend_options() -> List[Dict[str, Any]]:
    try:
        from checkers.rag import stores
        return stores.vector_backends()
    except Exception:  # noqa: BLE001
        return [{"id": "memory", "label": "内存 / JSON", "note": "",
                 "available": True, "error": ""}]


def kb_config() -> Dict[str, Any]:
    return {"ok": True, "config": _kb_config_raw()}


def save_kb_config(patch: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """更新知识库配置（切片参数 / 向量后端 / OCR）。

    切片参数变更在下次入库或重建索引时生效；切换向量后端会重置后端实例，
    下次入库时按新后端写入（已有向量需执行一次「重建索引」迁移）。
    """
    kb = load_kb()
    cfg = _kb_config_raw(kb)
    data = patch or {}

    for k in ("chunk_size", "chunk_overlap"):
        v = data.get(k)
        if v is not None:
            try:
                cfg[k] = int(v)
            except (TypeError, ValueError):
                pass

    for k in ("vector_backend", "collection", "ocr_engine"):
        v = data.get(k)
        if v is not None:
            cfg[k] = str(v).strip()[:64]

    if data.get("ocr_enabled") is not None:
        cfg["ocr_enabled"] = bool(data.get("ocr_enabled"))

    cfg["chunk_size"] = max(120, min(4000, int(cfg.get("chunk_size") or CHUNK_SIZE)))
    cfg["chunk_overlap"] = max(0, min(int(cfg.get("chunk_overlap") or 0),
                                      cfg["chunk_size"] // 3))
    cfg["vector_backend"] = str(cfg.get("vector_backend") or "chroma").lower()
    if cfg["vector_backend"] not in ("chroma", "memory"):
        cfg["vector_backend"] = "memory"

    kb["config"] = cfg
    kb["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_kb(kb)

    try:
        from checkers.rag import stores
        stores.reset_store()        # 后端可能已切换，强制重建连接
    except Exception:  # noqa: BLE001
        pass
    invalidate()
    return {"ok": True, "config": cfg}


def delete_docs(ids: List[str]) -> Tuple[int, List[str]]:
    kb = load_kb()
    ids = [str(i) for i in (ids or [])]
    removed, names = 0, []
    rest: List[Dict[str, Any]] = []
    gone: List[str] = []
    for d in kb.get("docs", []):
        if d.get("id") in ids:
            removed += 1
            names.append(d.get("name", ""))
            gone.extend(str(c.get("cid") or "") for c in (d.get("chunks") or [])
                        if c.get("cid"))
            storage = d.get("storage")
            if storage:
                p = os.path.join(FILES_DIR, os.path.basename(str(storage)))
                try:
                    if os.path.isfile(p):
                        os.remove(p)
                except OSError:
                    pass
        else:
            rest.append(d)
    if removed:
        kb["docs"] = rest
        cids = {c.get("cid") for d in rest for c in (d.get("chunks") or [])
                if c.get("cid")}
        kb["vectors"] = {k: v for k, v in (kb.get("vectors") or {}).items()
                         if k in cids}
        store = _store()
        if store is not None and gone:
            try:
                store.delete_by_doc(list(ids))     # 优先按文档 id 整批删
            except Exception:  # noqa: BLE001 - 失败再按 chunk id 兜底
                try:
                    store.delete(gone)
                except Exception:  # noqa: BLE001
                    pass
        kb["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_kb(kb)
        invalidate()
    return removed, names


def clear_kb() -> None:
    store = _store()
    if store is not None:
        try:
            store.clear()
        except Exception:  # noqa: BLE001
            pass
    save_kb(_empty_kb())
    try:
        if os.path.isdir(FILES_DIR):
            for n in os.listdir(FILES_DIR):
                try:
                    os.remove(os.path.join(FILES_DIR, n))
                except OSError:
                    pass
    except OSError:
        pass
    invalidate()


def doc_file(doc_id: str) -> Optional[str]:
    """返回 doc_id 的原始文件路径；不存在返回 None。"""
    for d in load_kb().get("docs", []):
        if d.get("id") == doc_id:
            storage = d.get("storage")
            if storage:
                p = os.path.join(FILES_DIR, os.path.basename(str(storage)))
                return p if os.path.isfile(p) else None
    return None


def search_debug(query: str, ai_cfg: Optional[Dict[str, Any]] = None,
                 top_k: int = 5, doc_ids: Optional[Sequence[str]] = None
                 ) -> Dict[str, Any]:
    """仅检索、不生成：返回各路得分与融合结果，供前端诊断相关性。"""
    if ai_cfg is None:
        ai_cfg = _ai_settings()
    idx = load_index()
    if idx.size == 0:
        return {"ok": True, "hits": [], "stats": {"chunks": 0, "docs": 0,
                                                  "vectors": 0},
                "mode": "none", "query_terms": []}

    allow: Optional[set] = None
    if doc_ids:
        wanted = {str(d) for d in doc_ids if str(d)}
        allow = {i for i, cid in enumerate(idx.cids)
                 if str((idx.doc_of.get(cid) or {}).get("id") or "") in wanted}
        if not allow:
            return {"ok": True, "hits": [],
                    "stats": {"chunks": idx.size,
                              "docs": len(idx.raw.get("docs") or []),
                              "vectors": idx.vectors and len(idx.vectors) or 0},
                    "mode": "none", "query_terms": []}

    q_raw = tokenize(query or "")
    q = prune_query(q_raw, idx.bm25.df, idx.bm25.n)
    bm = idx.bm25.scores(q or q_raw)

    cos_scores: List[float] = []
    dense_used = False
    if _dense_enabled(ai_cfg) and idx.has_vectors():
        qv = embed_query(query or "", ai_cfg)
        dim = int(idx.raw.get("embed_dim") or 0)
        if qv and (not dim or len(qv) == dim):
            dense_hits = idx.dense_search(qv, _DENSE_POOL, doc_ids)
            if dense_hits:
                dense_used = True
                cos_scores = [0.0] * idx.size
                for i, s in dense_hits:
                    cos_scores[i] = s

    ranks: List[List[int]] = [_filter(_rank_desc(bm, _SPARSE_POOL), allow)]
    if dense_used:
        ranks.append(_filter(_rank_desc(cos_scores, _DENSE_POOL), allow))

    fused = rrf(ranks)
    kept = filter_by_relation(fused, max(int(top_k), 1))
    picked = mmr(kept, idx.token_sets,
                 idx.vecs_for([i for i, _ in kept]) if dense_used else None,
                 top_k=top_k)

    hits = []
    fmap = dict(fused)
    for rank, i in enumerate(picked, 1):
        cid = idx.cids[i]
        d = idx.doc_of.get(cid) or {}
        c = idx.chunk_map.get(cid) or {}
        hits.append({
            "rank": rank,
            "chunk_id": cid,
            "doc_id": d.get("id", ""),
            "file": d.get("name", ""),
            "section": c.get("section", ""),
            "path": c.get("path", ""),
            "loc": c.get("loc", ""),
            "rrf": round(float(fmap.get(i, 0.0)), 5),
            "bm25": round(float(bm[i]), 4),
            "cos": round(float(cos_scores[i]), 4) if dense_used else 0.0,
            "text": c.get("text", "")[:400],
        })
    return {
        "ok": True,
        "hits": hits,
        "mode": "hybrid" if dense_used else "bm25",
        "query_terms": (q or q_raw)[:24],
        "stats": {"chunks": idx.size,
                  "docs": len(idx.raw.get("docs") or []),
                  "vectors": _vector_total(idx)},
    }


def _vector_total(idx: _Index) -> int:
    """当前向量总数（兼容内存快照与向量库后端）。"""
    if idx.vectors:
        return len(idx.vectors)
    if idx.vec_store is None:
        return 0
    try:
        return int(idx.vec_store.count())
    except Exception:  # noqa: BLE001
        return 0
