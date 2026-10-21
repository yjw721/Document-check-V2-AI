# -*- coding: utf-8 -*-
"""
本地知识库（RAG 检索问答）引擎 —— 纯本地、零新增依赖
===============================================================
流程：
    1. 上传 .docx / .pdf / .txt / .md / .csv / .xlsx → 提取分块（保留章节 / 位置元数据）
    2. 索引：BM25（中文 bigram + 拉丁词）+ 可选 Ollama 向量（/api/embed）
    3. 检索：BM25 与稠密向量加权融合，TopK 输出（附原文片段与来源）
    4. 问答：复用 AI 调用层（ai_checker._call_ollama / _call_openai）生成回答

设计约束：
    - 仅标准库，不引入 numpy / FAISS / Chroma（规模达数万 chunk 后再升级向量库）
    - 无 Ollama 嵌入模型时自动退化为 BM25 关键词检索（功能不中断）
    - 仅在本地读写 config/knowledge/，零网络外发

数据目录：config/knowledge/
    knowledge.json   索引与向量（doc → chunks → tokens / vectors）
    files/           原始文件（按 doc_id 存储，供来源跳转下载）
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

KB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "config", "knowledge")
KB_PATH = os.path.join(KB_DIR, "knowledge.json")
FILES_DIR = os.path.join(KB_DIR, "files")

_LOCK = threading.Lock()
DEFAULT_EMBED_MODEL = "bge-m3"
MAX_FILE_MB = 30
MAX_DOC_CHARS = 300_000        # 单文件参与索引的最大文本量
CHUNK_SIZE = 500               # 段落合并目标长度
MAX_CHUNKS_PER_DOC = 600
MAX_PROMPT_CTX = 5000          # 问答注入知识库片段的字符上限

# 中文大标题启发式：第X章 / 第X节 / 数字编号 / 中文序数编号
_HEADING_RE = re.compile(
    r"^(\s*第[一二三四五六七八九十百零〇0-9]+[章节篇部分卷]"
    r"|[0-9]+[.、．]"
    r"|（[一二三四五六七八九十]+）"
    r"|[一二三四五六七八九十]+、)"
)
_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN = re.compile(r"[a-z0-9_]+")


def _clip(s: str, n: int = 120) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------
def _empty_kb() -> Dict[str, Any]:
    return {"version": 2, "updated_at": "", "embed_model": "none", "docs": [], "vectors": {}}


def load_kb(path: str = KB_PATH) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("docs"), list):
            data.setdefault("vectors", {})
            data.setdefault("embed_model", "none")
            return data
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return _empty_kb()


def save_kb(kb: Dict[str, Any], path: str = KB_PATH) -> bool:
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


def kb_status() -> Dict[str, Any]:
    """知识库概览（前端列表用，不含 chunk 全文）。"""
    kb = load_kb()
    docs = []
    total_chunks = 0
    for d in kb.get("docs", []):
        chunks = d.get("chunks") or []
        total_chunks += len(chunks)
        docs.append({
            "id": d.get("id"),
            "name": d.get("name"),
            "size": int(d.get("size") or 0),
            "chars": sum(len(c.get("text") or "") for c in chunks),
            "chunks": len(chunks),
            "added_at": d.get("added_at", ""),
        })
    return {
        "ok": True,
        "docs": docs,
        "total_chunks": total_chunks,
        "embed_model": kb.get("embed_model") or "none",
        "embed_available": bool(kb.get("vectors")),
        "updated_at": kb.get("updated_at", ""),
    }


# ---------------------------------------------------------------------------
# 分词：中文按字符 bigram + 拉丁词（无需 jieba 等第三方依赖）
# ---------------------------------------------------------------------------
def tokenize(text: str) -> List[str]:
    text = (text or "").lower()
    toks: List[str] = [m.group(0) for m in _LATIN.finditer(text)]
    chars = _CJK.findall(text)
    if len(chars) >= 2:
        toks.extend(chars[i] + chars[i + 1] for i in range(len(chars) - 1))
    elif len(chars) == 1:
        toks.append(chars[0])
    return toks


# ---------------------------------------------------------------------------
# 文本提取（复用各单位解析能力，全部标准库 + 既有依赖读取内存字节）
# ---------------------------------------------------------------------------
def _decode_text(raw: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "utf-16", "gb18030"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_blocks(name: str, raw: bytes) -> List[Tuple[str, str]]:
    """按扩展名提取 [(位置, 文本), ...]。"""
    ext = os.path.splitext(name)[1].lower()
    if ext in (".txt", ".md", ".csv"):
        text = _decode_text(raw).replace("\r\n", "\n").replace("\r", "\n")
        return [(f"第 {i + 1} 行", ln.strip()) for i, ln in enumerate(text.split("\n"))
                if ln.strip() and (ext != ".csv" or "|" not in ln[:1])]
    if ext == ".docx":
        from docx import Document
        doc = Document(io.BytesIO(raw))
        blocks: List[Tuple[str, str]] = [
            (f"第 {i} 段", p.text.strip()) for i, p in enumerate(doc.paragraphs, 1) if p.text.strip()
        ]
        for ti, table in enumerate(doc.tables, 1):
            for ri, row in enumerate(table.rows, 1):
                t = " | ".join(c.text.strip() for c in row.cells if c.text.strip())
                if len(t) >= 2:
                    blocks.append((f"表 {ti} 第 {ri} 行", t))
        return blocks
    if ext == ".pdf":
        from pypdf import PdfReader
        rd = PdfReader(io.BytesIO(raw))
        out = []
        for pi in range(len(rd.pages)):
            try:
                t = (rd.pages[pi].extract_text() or "").strip()
            except Exception:  # noqa: BLE001 - 单页失败跳过
                t = ""
            if len(t) >= 2:
                out.append((f"第 {pi + 1} 页", t))
        return out
    if ext == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        blocks = []
        try:
            for ws in wb.worksheets:
                for ri, row in enumerate(ws.iter_rows(values_only=True), 1):
                    cells = [str(v).strip() for v in row if v is not None and str(v).strip()]
                    t = " | ".join(cells)
                    if len(t) >= 2:
                        blocks.append((f"「{ws.title}」第 {ri} 行", t))
        finally:
            wb.close()
        return blocks
    raise ValueError("不支持的文件类型（支持 .docx / .pdf / .txt / .md / .csv / .xlsx）")


def _is_heading(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and len(t) <= 40 and bool(_HEADING_RE.match(t))


def _chunk_blocks(blocks: List[Tuple[str, str]], size: int = CHUNK_SIZE) -> List[Dict[str, str]]:
    """段落级聚合分块：遇标题行另起新块，块上限 size 字符。"""
    chunks: List[Dict[str, str]] = []
    section = ""
    buf: List[str] = []
    buf_len = 0

    def flush() -> None:
        nonlocal buf, buf_len, section
        if buf:
            text = "\n".join(buf)
            chunks.append({"section": section or text[:40], "loc": (buf[0] or "")[:60], "text": text})
            buf, buf_len = [], 0

    total_chars = 0
    for loc, text in blocks:
        if _is_heading(text):
            flush()
            section = text
        text = text.strip()
        if not text:
            continue
        total_chars += len(text)
        if total_chars > MAX_DOC_CHARS:
            break
        if buf_len + len(text) > size and buf:
            flush()
        buf.append(text)
        buf_len += len(text)
        if len(chunks) >= MAX_CHUNKS_PER_DOC:
            break
    flush()
    return chunks


# ---------------------------------------------------------------------------
# BM25（中文 bigram 词元），纯 stdlib 实现
# ---------------------------------------------------------------------------
def _build_bm25(chunks: List[Dict[str, Any]]) -> Tuple[List[List[str]], Dict[str, float], float]:
    doc_tokens: List[List[str]] = [list(c.get("tokens") or tokenize(c.get("text") or ""))
                                   for c in chunks]
    df: Dict[str, int] = {}
    for toks in doc_tokens:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    n = max(len(chunks), 1)
    avg = sum(len(t) for t in doc_tokens) / n
    idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
    return doc_tokens, idf, avg


def _bm25_score(q: List[str], doc_tokens: List[str], idf: Dict[str, float],
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
            s += idf.get(t, 0.0) * (f * (k1 + 1)) / (f + k1 * denom)
    return s


# ---------------------------------------------------------------------------
# 稠密向量：Ollama /api/embed（批量）；失败返回 None → 自动退化为 BM25
# ---------------------------------------------------------------------------
def _embed_texts(texts: List[str], ai_cfg: Optional[Dict[str, Any]] = None) -> Optional[List[List[float]]]:
    if not texts:
        return None
    base = str((ai_cfg or {}).get("base_url") or "http://127.0.0.1:11434").rstrip("/")
    model = str((ai_cfg or {}).get("embed_model") or DEFAULT_EMBED_MODEL).strip()
    if not model:
        return None
    try:
        data = json.dumps({"model": model, "input": texts}).encode("utf-8")
        req = urllib.request.Request(base + "/api/embed", data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            js = json.loads(resp.read().decode("utf-8", errors="replace"))
        embs = js.get("embeddings")
        if not isinstance(embs, list) or len(embs) != len(texts):
            return None
        for v in embs:
            if not isinstance(v, list):
                return None
        return embs
    except Exception:  # noqa: BLE001 - 嵌入失败降级 BM25
        return None


def _cos(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0


def _norm01(vals: List[float]) -> List[float]:
    if not vals:
        return []
    mx, mn = max(vals), min(vals)
    r = mx - mn
    if r <= 1e-9:
        return [0.5] * len(vals)
    return [(x - mn) / r for x in vals]


# ---------------------------------------------------------------------------
# 采入口：上传 → 分块 → 索引 → 重嵌入
# ---------------------------------------------------------------------------
def index_docs(files: List[Tuple[str, bytes]]) -> Tuple[int, List[Dict[str, Any]], List[str]]:
    """files: [(原始文件名, 字节内容), ...]。返回 (成功数, 文档信息, 失败说明)。"""
    kb = load_kb()
    added, docs_meta, errors = 0, [], []
    changed = False

    for name, raw in files:
        name = os.path.basename((name or "").strip())
        try:
            if not name:
                raise ValueError("文件名不能为空")
            if len(raw) > MAX_FILE_MB * 1024 * 1024:
                raise ValueError(f"超过 {MAX_FILE_MB}MB 大小限制")
            blocks = _extract_blocks(name, raw)
            if not blocks:
                raise ValueError("未能从文件中提取到有效文本")
            chunks = _chunk_blocks(blocks)
            if not chunks:
                raise ValueError("文本过短，未生成索引分块")

            did = "d_" + hashlib.md5(name.encode("utf-8")).hexdigest()[:10]
            storage = f"{did}{os.path.splitext(name)[1].lower() or '.txt'}"
            os.makedirs(FILES_DIR, exist_ok=True)
            with open(os.path.join(FILES_DIR, storage), "wb") as f:
                f.write(raw)

            kb["docs"] = [d for d in kb.get("docs", []) if d.get("id") != did]
            doc: Dict[str, Any] = {
                "id": did,
                "name": name,
                "storage": storage,
                "size": len(raw),
                "added_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "chunks": [{
                    "cid": "c_" + hashlib.md5(f"{did}:{i}:{c['text'][:80]}".encode("utf-8")).hexdigest()[:12],
                    "section": c["section"][:120],
                    "loc": c["loc"][:60],
                    "text": c["text"],
                } for i, c in enumerate(chunks)],
            }
            for c in doc["chunks"]:
                c["tokens"] = tokenize(c["text"])
            kb["docs"].append(doc)
            added += 1
            changed = True
            docs_meta.append({
                "id": did, "name": name,
                "chars": sum(len(c["text"]) for c in doc["chunks"]),
                "chunks": len(doc["chunks"]),
            })
        except Exception as exc:  # noqa: BLE001 - 单文件失败不中断批量
            errors.append(f"{name}：{exc}")

    if changed:
        from config.settings_manager import load_settings
        ai = (load_settings().get("ai") or {}).copy()
        all_texts: List[str] = []
        cids: List[str] = []
        for d in kb.get("docs", []):
            for c in d.get("chunks", []):
                all_texts.append(c["text"])
                cids.append(c["cid"])
        vectors: Dict[str, List[float]] = {}
        embed_model = "none"
        if ai.get("embed_enabled", True) and all_texts:
            embs = _embed_texts(all_texts, ai)
            if embs:
                for ci, cid in enumerate(cids):
                    vectors[cid] = embs[ci]
                embed_model = str(ai.get("embed_model") or DEFAULT_EMBED_MODEL)
        kb["vectors"] = vectors
        kb["embed_model"] = embed_model
        kb["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_kb(kb)

    return added, docs_meta, errors


# ---------------------------------------------------------------------------
# 检索：BM25（必用） + 稠密（可用时融合），输出 TopK 带来源
# ---------------------------------------------------------------------------
def retrieve(query: str, ai_cfg: Optional[Dict[str, Any]] = None,
             top_k: int = 3) -> List[Dict[str, Any]]:
    kb = load_kb()
    chunks: List[Dict[str, Any]] = [c for d in kb.get("docs", []) for c in d.get("chunks", [])]
    if not chunks:
        return []
    doc_of: Dict[str, Dict[str, Any]] = \
        {c["cid"]: d for d in kb.get("docs", []) for c in d.get("chunks", [])}
    q = tokenize(query)
    if not q:
        return []

    doc_tokens, idf, avg = _build_bm25(chunks)
    bm = [_bm25_score(q, dt, idf, avg) for dt in doc_tokens]

    # 稠密检索（可选）：有向量库且启用嵌入时融合
    want_dense = bool(ai_cfg and ai_cfg.get("embed_enabled", True)) and bool(kb.get("vectors"))
    qv = None
    if want_dense:
        _emb = _embed_texts([query], ai_cfg)
        qv = _emb[0] if _emb else None
    embed_used = qv is not None
    cos = [0.0] * len(chunks)
    if qv is not None:
        for i, c in enumerate(chunks):
            v = kb["vectors"].get(c["cid"])
            if v:
                cos[i] = _cos(qv, v)

    bn = _norm01(bm)
    cn = _norm01(cos)
    w_d = 0.45 if embed_used else 0.0
    scored = sorted(
        ((w_d * cn[i] + (1 - w_d) * bn[i], chunks[i]) for i in range(len(chunks))),
        key=lambda x: x[0], reverse=True,
    )
    out: List[Dict[str, Any]] = []
    for s, c in scored[:top_k]:
        d = doc_of.get(c["cid"]) or {}
        out.append({
            "chunk_id": c["cid"],
            "doc_id": d.get("id", ""),
            "file": d.get("name", ""),
            "section": c.get("section") or d.get("name", ""),
            "loc": c.get("loc", ""),
            "text": c.get("text", ""),
            "score": round(float(s), 4),
        })
    return out


# ---------------------------------------------------------------------------
# 问答：检索 → 拼上下文 → 复用 AI 调用层生成回答（附来源）
# ---------------------------------------------------------------------------
def ask(question: str, ai_cfg: Optional[Dict[str, Any]] = None,
        top_k: int = 3) -> Dict[str, Any]:
    t0 = time.time()
    try:
        from checkers.ai_checker import _call_ollama, _call_openai
    except Exception:  # noqa: BLE001
        _call_ollama = _call_openai = None

    hits = retrieve(question, ai_cfg, top_k)
    if not hits:
        return {
            "answer": "知识库为空或内容过少，暂无法回答。请先在「本地知识库」中上传文档并重新索引。",
            "sources": [], "embed_used": False, "elapsed": time.time() - t0,
        }

    ctx = ""
    used = 0
    for h in hits:
        part = f"【{h['file']} / {h['section']}】\n{h['text']}"
        if used + len(part) > MAX_PROMPT_CTX:
            break
        ctx += part + "\n\n"
        used += len(part)

    source_meta = [{
        "file": h["file"], "section": h["section"], "loc": h["loc"],
        "snippet": _clip(h["text"], 160), "score": h["score"], "doc_id": h["doc_id"],
    } for h in hits]

    sys_prompt = (
        "你是一个严谨的本地知识库问答助手。仅依据下方【知识库资料】回答用户问题，"
        "不得编造资料中不存在的信息。引用具体内容时在句末标注 [来源：文件名 / 章节]。"
        "若资料不足以回答，请如实说明“知识库资料中未找到相关内容”。"
    )
    user = f"【知识库资料】\n{ctx}\n【用户问题】\n{question}"
    cfg = dict(ai_cfg or {})
    embed_used = bool(hits and kb_status().get("embed_available") and cfg.get("embed_enabled", True))

    answer = ""
    err = ""
    try:
        if _call_ollama is None:
            raise RuntimeError("AI 调用层不可用")
        mode = (cfg.get("mode") or "local")
        if mode == "local":
            answer = _call_ollama(cfg, [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user},
            ], think=False)
        else:
            answer = _call_openai(cfg, [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user},
            ])
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

    return {
        "answer": answer,
        "sources": source_meta,
        "embed_used": embed_used,
        "embed_model": kb_status().get("embed_model"),
        "elapsed": round(time.time() - t0, 2),
    }


# ---------------------------------------------------------------------------
# 管理：删除 / 清空 / 文件下载解析
# ---------------------------------------------------------------------------
def delete_docs(ids: List[str]) -> Tuple[int, List[str]]:
    kb = load_kb()
    ids = [str(i) for i in (ids or [])]
    removed, names = 0, []
    rest = []
    for d in kb.get("docs", []):
        if d.get("id") in ids:
            removed += 1
            names.append(d.get("name", ""))
            storage = d.get("storage")
            if storage:
                try:
                    p = os.path.join(FILES_DIR, os.path.basename(storage))
                    if os.path.isfile(p):
                        os.remove(p)
                except OSError:
                    pass
        else:
            rest.append(d)
    if removed:
        kb["docs"] = rest
        cids = [c["cid"] for d in rest for c in d.get("chunks", [])]
        kb["vectors"] = {k: v for k, v in kb["vectors"].items() if k in cids}
        kb["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_kb(kb)
    return removed, names


def clear_kb() -> None:
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


def doc_file(doc_id: str) -> Optional[str]:
    """返回 doc_id 的原始文件路径；不存在返回 None。"""
    kb = load_kb()
    for d in kb.get("docs", []):
        if d.get("id") == doc_id:
            storage = d.get("storage")
            if storage:
                p = os.path.join(FILES_DIR, os.path.basename(storage))
                return p if os.path.isfile(p) else None
    return None