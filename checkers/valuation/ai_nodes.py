# -*- coding: utf-8 -*-
"""
阶段三 · AI 核查节点执行
=========================================================================
对 96 项核查事项中「需语义判断」的部分（engine = ai / hybrid 的 AI 部分），
逐项生成提示词并调用 AI 执行。

设计要点：
    * 提示词直接由核查事项的「核查要点 + 来源提示词」构成，逐条可追溯；
    * 输入为切分后的数据块（上下文聚焦单一方法，通用表已强制附加）；
    * 长文本按 max_chars 分段，单段失败降级为提示条目，不中断节点；
    * 解析结果带 quote 回检（quote 未出现在原文中的条目直接丢弃），
      从机制上压制模型幻觉造成的误报。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from .ai_client import ai_enabled, chat, parse_json_list, AiError
from .models import Finding
from .rules import RuleContext

# ---------------------------------------------------------------------------
# 系统提示词
# ---------------------------------------------------------------------------
_SYS_TEMPLATE = """你是资深资产评估复核专家，正在执行「{node_name}」节点的第 {item_no} 项核查事项。

【核查事项】{item_title}
【核查要点】{item_desc}
【来源提示词】{source}

核查纪律（务必遵守，宁可漏报，不可误报）：
1. 只能报告【材料内容】中能够直接举证的问题，严禁臆测、严禁引入材料之外的信息；
2. 每条问题的 quote 必须是材料中逐字出现的原句 / 原表格数值，不得改写、不得拼接；
3. 数值类问题必须在 detail 中写出计算过程（如「分项 80+120=200，但合计写为 300」）；
4. 同一处问题只报告一次，选取最具代表性的 quote；
5. 若本段材料未发现该项核查事项的问题，返回空数组 []，不要为了凑数而报告；
6. severity 取 high / medium / low，把握不足时取 low。

返回严格 JSON 数组（不要输出任何其它文字），每条格式：
[{{"location":"位置（如 Sheet名!C12 或 第12段）","quote":"材料中的原文","issue":"问题简述",
"detail":"举证说明","suggestion":"整改建议","severity":"high|medium|low"}}]
"""

_USER_TEMPLATE = """【材料内容】（{block_names}）

{content}

请仅针对上述【核查事项】输出 JSON 数组。"""


def _chunk_lines(text: str, max_chars: int) -> List[str]:
    """按行切分长文本，尽量在空行 / 表边界处断开。"""
    if len(text) <= max_chars:
        return [text]
    chunks: List[str] = []
    buf: List[str] = []
    size = 0
    for line in text.split("\n"):
        if size + len(line) > max_chars and buf:
            chunks.append("\n".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def build_block_text(ctx: RuleContext, block_id: str, max_chars: int = 0) -> str:
    """取得数据块的可核查文本（Excel 块含强制附加的通用表）。"""
    blk = ctx.blocks.get(block_id)
    if blk is None:
        return ""
    if blk.kind == "excel":
        model = ctx.model(block_id)
        if not model:
            return ""
        names = [n for n, _ in ctx.sheets_of(block_id)]
        if not names:
            names = list(model.order)
        text = model.text(sheets=names, max_rows=400, max_cols=40)
    else:
        text = blk.text or ""
    if max_chars and len(text) > max_chars:
        text = text[:max_chars]
    return text


def _quote_hit(quote: str, content: str) -> bool:
    """回检 quote 是否逐字出现在材料中（去空白后比较，容忍换行差异）。"""
    if not quote:
        return False
    q = re.sub(r"\s+", "", str(quote))
    c = re.sub(r"\s+", "", str(content))
    return bool(q) and q in c


def run_ai_item(ctx: RuleContext, ai_cfg: Dict[str, Any],
                log: Optional[Callable[[str], None]] = None,
                on_token: Optional[Callable[[str, str], None]] = None
                ) -> List[Finding]:
    """执行单个核查事项的 AI 核查，返回 Finding 列表。"""
    log = log or (lambda s: None)
    item = ctx.item or {}
    node = ctx.node or {}
    if not ai_enabled(ai_cfg):
        return []

    system = _SYS_TEMPLATE.format(
        node_name=node.get("name", ""),
        item_no=item.get("no", 0),
        item_title=item.get("title", ""),
        item_desc=item.get("desc", ""),
        source=item.get("source", ""),
    )

    max_chars = int(ai_cfg.get("max_chars") or 3000)
    max_requests = int(ai_cfg.get("max_requests") or 10)
    if max_chars < 1500:
        max_chars = 3000

    # 组装材料：该节点的全部输入块
    contents: List[str] = []
    names: List[str] = []
    for bid in node.get("inputs", []):
        text = build_block_text(ctx, bid)
        if text.strip():
            contents.append(f"『{bid} {(ctx.blocks.get(bid).name if ctx.blocks.get(bid) else '')}』\n{text}")
            names.append(bid)
    if not contents:
        return []

    full = "\n\n".join(contents)
    chunks = _chunk_lines(full, max_chars)
    if len(chunks) > max_requests:
        log(f"[{item.get('id')}] 内容过长，仅核查前 {max_requests} 段（共 {len(chunks)} 段）")
        chunks = chunks[:max_requests]

    out: List[Finding] = []
    seen: set = set()
    for i, chunk in enumerate(chunks, 1):
        user = _USER_TEMPLATE.format(block_names="、".join(names), content=chunk)
        try:
            raw = chat(ai_cfg, system, user, on_token=on_token)
        except AiError as exc:
            log(f"[{item.get('id')}] 第 {i}/{len(chunks)} 段 AI 调用失败：{exc}")
            continue
        rows = parse_json_list(raw)
        if not rows:
            continue
        for row in rows:
            quote = str(row.get("quote") or "").strip()
            detail = str(row.get("detail") or row.get("issue") or "").strip()
            if not detail:
                continue
            if quote and not _quote_hit(quote, chunk):
                continue                       # 引用不实 → 判为幻觉，丢弃
            key = re.sub(r"\s+", "", (row.get("location") or "") + detail)[:150]
            if key in seen:
                continue
            seen.add(key)
            sev = str(row.get("severity") or "medium").lower()
            if sev not in ("high", "medium", "low"):
                sev = str(item.get("severity") or "medium")
            out.append(ctx.mk(
                sev,
                detail,
                location=str(row.get("location") or "-")[:120],
                file="、".join(names),
                snippet=quote,
                suggestion=str(row.get("suggestion") or ""),
                origin="ai",
            ))
    if out:
        log(f"[{item.get('id')}] AI 核查命中 {len(out)} 条")
    return out
