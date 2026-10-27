# -*- coding: utf-8 -*-
"""RAG 文本层：分词 / 标题层级识别 / 重叠语义切片 / 上下文前缀。

对齐当前主流 RAG 实践的四项改进：

1. **bigram 不跨标点**：旧实现先把全文的汉字 ``findall`` 成单字序列再滑窗，
   于是「应收。账款」会造出伪词「收账」。改为先切 CJK 连续段再滑窗。
2. **标题层级栈**：旧实现只记「最后一个标题」，章节路径丢失。
   改为维护 (层级, 标题) 栈，生成 ``第三章 / 第二节 / （一）`` 完整路径，
   既用于引用溯源，也用于上下文前缀。
3. **块间重叠**：旧实现块与块之间零重叠，跨块语义断裂。
   新块承接上一块尾部 ``CHUNK_OVERLAP`` 字符（按句读边界对齐）。
4. **上下文前缀（Contextual Retrieval）**：为每块生成 ``文档名 + 章节路径``
   前缀参与向量化，显著提升块在向量空间中的可分性
   （Anthropic 2024 Contextual Retrieval 思路的零调用成本近似）。
5. **表格感知（v4）**：数值表格是核验类知识库的主力内容，专门处理三件事——
   a) 重叠只回填**完整行**且**不跨表格行**：旧实现按固定字数从块尾截 100 字，
      表格行（无句读标点）会被切在数字中间（如 ``3.06`` 截成 ``06 |``），
      且同一残片在后续每块重复出现，制造近重复噪声块；
   b) **表格续块自动补表头**：同一表格被块大小切开时，续块首部重复该表的
      表头行（含合并横幅），片段不再「只有一排裸数字、不知列含义」；
   c) **过短片段合并**：启用 ``MIN_CHUNK_CHARS``——过短的普通片段并入前块，
      孤立标题并入后块（标题必须与其内容同块）。
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# 切片参数（可被 knowledge.json 的 config 覆盖）
# ---------------------------------------------------------------------------
CHUNK_SIZE = 500           # 片段目标字符数
CHUNK_OVERLAP = 100        # 相邻片段重叠字符数
MIN_CHUNK_CHARS = 24       # 过短片段并入相邻片段
MAX_DOC_CHARS = 300_000    # 单文件参与索引的最大文本量
MAX_CHUNKS_PER_DOC = 600

# 切片结构版本：切片规则变化时递增，触发全量重建
CHUNK_SCHEMA_VERSION = 4

_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_LATIN = re.compile(r"[a-z0-9_]+")

# 表格行块定位（parse_docx 的 loc 形如「表 2 第 7 行」）与数据行日期前缀
_TBL_ROW = re.compile(r"^表\s*(\d+)\s+第\s*(\d+)\s+行$")
_DATE_PREFIX = re.compile(r"^(?:19|20)\d{2}\s*[/年.\-]")

# 形如日期/金额的行不应被误判为标题
_NOT_HEADING = re.compile(r"(?:19|20)\d{2}\s*[.\-/年]|\d+\s*[.]\s*\d+\s*[%‰]|^[\d,\s.]+$")
_MAX_HEADING_LEN = 40

# Markdown 标题：# → 1 级，## → 2 级 …（最多 4 级）
_MD_HEADING = re.compile(r"^\s*(#{1,6})\s*(.*)$")

# 标题层级规则（顺序即优先级，最先命中生效）
_HEADING_RULES: List[Tuple[int, "re.Pattern[str]"]] = [
    (1, re.compile(r"^\s*第[一二三四五六七八九十百千零〇0-9]+[章篇卷编]")),
    (1, re.compile(r"^\s*(?:附录|附件|序言|总则|前言)\s*[：:．.]?")),
    (2, re.compile(r"^\s*第[一二三四五六七八九十百千零〇0-9]+[节条]")),
    (3, re.compile(r"^\s*[（(][一二三四五六七八九十百]+[)）]")),
    (3, re.compile(r"^\s*[一二三四五六七八九十百]+、")),
    (2, re.compile(r"^\s*[0-9]+\s*[.．]\s*[0-9]+")),
    (4, re.compile(r"^\s*[0-9]+(?:\.[0-9]+)+")),
    (4, re.compile(r"^\s*[0-9]+[.、．)]")),
]

_SENT_END = ("。", "；", "！", "？", "；", "…")


def tokenize(text: str) -> List[str]:
    """中文 bigram（不跨标点）+ 拉丁词，纯标准库实现。"""
    t = (text or "").lower()
    toks: List[str] = [m.group(0) for m in _LATIN.finditer(t)]
    for run in _CJK_RUN.findall(t):
        if len(run) == 1:
            toks.append(run)
        else:
            toks.extend(run[i:i + 2] for i in range(len(run) - 1))
    return toks


def match_heading(text: str) -> Tuple[int, str]:
    """识别标题，返回 (层级, 清洗后的标题文本)；非标题返回 (0, "")。"""
    t = (text or "").strip()
    if not t or len(t) > _MAX_HEADING_LEN:
        return 0, ""
    if _NOT_HEADING.search(t):
        return 0, ""

    m = _MD_HEADING.match(t)
    if m and m.group(2).strip():
        return min(4, len(m.group(1))), m.group(2).strip()

    for lvl, pat in _HEADING_RULES:
        if pat.match(t):
            return lvl, t
    return 0, ""


def heading_level(text: str) -> int:
    """返回标题层级（1 最高）；非标题返回 0。"""
    return match_heading(text)[0]


def is_heading(text: str) -> bool:
    return heading_level(text) > 0


def _tail_snippet(text: str, n: int) -> str:
    """取文本尾部 n 个字符作为下一块的重叠——行感知、表格安全。

    - 只回填**完整行**：绝不在行中间截断（旧实现按固定字数截尾，表格行无句读
      标点，会切在数字中间，如 ``2025/07/20 | ... | 3.06`` 截出 ``06 | 3.09 |``，
      且同一残片在后续每块重复，制造近重复噪声）；
    - 末行为**表格行**时返回空：数值行的重叠没有语义增益，只有重复噪声；
    - 末行是超长散文行时退回句读对齐。
    """
    lines = [x for x in (text or "").split("\n") if x.strip()]
    if not lines:
        return ""
    if " | " in lines[-1]:
        return ""                       # 表格结尾：不回填重叠
    if len(text) <= n:
        return text.strip()
    picked: List[str] = []
    total = 0
    for ln in reversed(lines):
        if " | " in ln:                 # 只回填到最后一个完整散文行为止
            break
        if total + len(ln) + 1 > n:
            break
        picked.append(ln)
        total += len(ln) + 1
    if picked:
        return "\n".join(reversed(picked)).strip()
    tail = text[-n:]
    for sep in _SENT_END + ("\n", ". ", "; "):
        k = tail.find(sep)
        if 0 <= k < len(tail) - 1:
            return tail[k + len(sep):].strip()
    return tail.strip()


def _mk_chunk(stack: List[Tuple[int, str]], buf: List[str], loc: str) -> Dict[str, str]:
    text = "\n".join(buf).strip()
    path = " / ".join(t for _, t in stack)
    fallback = " ".join(text.split())[:40]
    return {
        "section": (path or fallback)[:120],
        "path": path[:200],
        "loc": (loc or " ".join(text.split())[:60])[:60],
        "text": text,
    }


def _is_data_row(line: str) -> bool:
    """表格数据行判定：前两列任一以日期（2025/06/30、2026年1月…）开头。"""
    cells = [c.strip() for c in line.split(" | ")]
    return any(_DATE_PREFIX.match(c) for c in cells[:2])


def _merge_tiny(chunks: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """过短片段合并（启用 ``MIN_CHUNK_CHARS``）。

    - 普通过短片段 → 并入**前块**（元数据沿用前块）；
    - 以标题开头的过短片段（孤立标题）→ 并入**后块**（标题必须与其内容同块）；
    - 合并只拼接 text，``section / path / loc`` 沿用被并入目标的元数据。
    """
    if not chunks:
        return chunks
    out: List[Dict[str, str]] = []
    pending: List[str] = []          # 待并入下一块的孤立标题 / 首部碎片
    for c in chunks:
        txt = (c.get("text") or "").strip()
        if len(txt) < MIN_CHUNK_CHARS:
            if txt:
                first = txt.split("\n", 1)[0]
                if out and match_heading(first)[0] == 0:
                    out[-1]["text"] = out[-1]["text"] + "\n" + (c.get("text") or "")
                else:
                    pending.append(c.get("text") or "")
            continue
        if pending:
            c = dict(c, text="\n".join(pending + [c.get("text") or ""]))
            pending = []
        out.append(c)
    if pending:
        if out:
            out[-1] = dict(out[-1], text=out[-1]["text"] + "\n" + "\n".join(pending))
        else:
            out = [{"section": "", "path": "", "loc": "", "text": "\n".join(pending)}]
    return out


def chunk_blocks(blocks: List[Tuple[str, str]],
                 size: int = CHUNK_SIZE,
                 overlap: int = CHUNK_OVERLAP) -> List[Dict[str, str]]:
    """段落级聚合切片（v4：表格感知）。

    - 标题行既是新块的起始，也保留在块内（充当天然标签）；
    - 块达 ``size`` 时按**行边界**切分，承接上一块尾部 ``overlap`` 字符
      （重叠只回填完整行，且不跨表格行——见 ``_tail_snippet``）；
    - 同一表格（loc「表 N 第 R 行」）被切开时，续块首部自动补该表表头行；
    - 收尾做 ``_merge_tiny``：过短片段并入相邻块，孤立标题并入其内容块；
    - 切片结构版本写入调用方的 doc["sig"]，由 engine 决定是否复用。
    """
    size = max(120, int(size or CHUNK_SIZE))
    overlap = max(0, min(int(overlap or 0), size // 3))

    chunks: List[Dict[str, str]] = []
    stack: List[Tuple[int, str]] = []
    buf: List[str] = []
    buf_len = 0
    loc0 = ""
    total_chars = 0
    pending_overlap = False   # 当前 buf 是否仅有上一块尾部的重叠片段
    buf_tid = ""              # 当前 buf 中表格行所属的表（「表 3」）；非表格行为 ""
    tbl_state: Dict[str, Dict[str, object]] = {}   # 表 id → {"hdr": [表头行], "data": bool}

    def _flush() -> None:
        nonlocal buf, buf_len, loc0, pending_overlap, buf_tid
        buf_tid = ""
        if not buf or (pending_overlap and len(buf) == 1):
            buf, buf_len, loc0, pending_overlap = [], 0, "", False
            return
        chunks.append(_mk_chunk(stack, buf, loc0))
        prev = chunks[-1]["text"]
        buf, buf_len, loc0, pending_overlap = [], 0, "", False
        if overlap > 0 and len(prev) > overlap * 2:
            tail = _tail_snippet(prev, overlap)
            if tail:
                buf, buf_len, pending_overlap = [tail], len(tail), True

    for loc, raw in blocks:
        t = (raw or "").strip()
        if not t:
            continue
        # PDF 抽取常把整页合成一个块、标题行埋在块内部；
        # 因此先按换行切行再识别标题，否则章节路径会整体退化为正文首字。
        for ln in (x.strip() for x in t.split("\n") if x.strip()):
            lvl, label = match_heading(ln)
            if lvl:
                if buf:
                    _flush()
                while stack and stack[-1][0] >= lvl:
                    stack.pop()
                stack.append((lvl, label or ln))
            total_chars += len(ln)
            if total_chars > MAX_DOC_CHARS:
                return _merge_tiny(chunks)
            m = _TBL_ROW.match(str(loc or ""))
            tid = f"表{m.group(1)}" if m else ""
            if buf_len > 0 and buf_len + len(ln) > size:
                flushed_tid = buf_tid
                _flush()
                # 同表续块：把该表表头行补到块首，片段不再是一排裸数字
                if tid and tid == flushed_tid and not buf:
                    st = tbl_state.get(tid) or {}
                    hdr = list(st.get("hdr") or [])
                    if hdr:
                        buf = hdr
                        buf_len = sum(len(h) for h in hdr) + len(hdr) - 1
                        loc0 = str(loc or "")
                        pending_overlap = False
            if not loc0:
                loc0 = str(loc or "")
            buf.append(ln)
            buf_len += len(ln)
            if tid:
                buf_tid = tid
                st = tbl_state.setdefault(tid, {"hdr": [], "data": False})
                if not st["data"]:
                    if _is_data_row(ln):
                        st["data"] = True
                    elif len(st["hdr"]) < 4:      # 上限防膨胀（横幅 + 列头即可）
                        st["hdr"].append(ln)
            else:
                buf_tid = ""
            if len(chunks) >= MAX_CHUNKS_PER_DOC:
                return _merge_tiny(chunks)

    if buf and not (pending_overlap and len(buf) == 1):
        chunks.append(_mk_chunk(stack, buf, loc0))
    return _merge_tiny(chunks)


def context_prefix(doc_name: str, section_path: str,
                   heading_hint: str = "") -> str:
    """生成片段的上下文前缀（供向量化使用）。

    对应主流 RAG 的 *contextual chunk heading*：把「文档名 / 章节路径」
    前置到块文本里再嵌入，使孤立片段（如「比例为 5%」）也能被正确召回。
    """
    parts: List[str] = []
    if doc_name:
        parts.append(f"文档：{doc_name}")
    p = (section_path or heading_hint or "").strip()
    if p:
        parts.append(f"章节：{p}")
    return ("｜".join(parts) + "\n") if parts else ""


def prune_query(q: List[str], df: Dict[str, int], n_docs: int,
                max_df_ratio: float = 0.45, keep_top: int = 3) -> List[str]:
    """查询词剪枝：剔除在多数片段中出现的低区分度词元。

    等价于检索侧的 IDF 剪枝——避免「我们」「进行」类高频词元把相关度打分拉平。
    两个安全阀，防止把查询剪成噪声：
      - 片段数 <= 4 时直接跳过（样本太少，df 不可靠）；
      - 无论如何保留 IDF 最高的 ``keep_top`` 个词元。
    """
    if not q:
        return []
    uniq = list(dict.fromkeys(q))
    if n_docs <= 4:
        return uniq
    thr = max(2, int(n_docs * max_df_ratio))
    kept = [t for t in uniq if df.get(t, 0) <= thr]
    if not kept:
        return uniq
    # 保留区分度最高的若干词元（df 越小 IDF 越高）
    top = sorted(uniq, key=lambda t: df.get(t, 0))[:keep_top]
    return list(dict.fromkeys(kept + top))
