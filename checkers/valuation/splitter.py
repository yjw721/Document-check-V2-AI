# -*- coding: utf-8 -*-
"""
阶段二 · 文件智能切分
=========================================================================
对应需求文档「阶段二 · 智能切分详解」：

    4. AI 判断 Excel 各 Sheet 与三种方法的关联性并切分
       ⚠️ 每块均强制附加通用表
    5. AI 切分评估说明，按收益法 / 成本法 / 市场法拆为 3 段
    ·  评估报告全文保持完整，不切分

产出 7 个数据块（B1–B7）：
    B1 收益法底稿块   B2 成本法底稿块   B3 市场法底稿块
    B4 说明_收益法     B5 说明_成本法     B6 说明_市场法
    B7 评估报告全文

设计要点：
    * 关键词规则兜底：AI 不可用 / 调用失败时自动降级为本地关键词判定；
    * 「通用表强制附加」：基础财务报表等公共依赖随每个底稿块下发，
      保证跨表勾稽检查有参照锚点；
    * 评估说明按「章节 → 段落」两级判定方法归属，公共章节（说明一 / 说明二 /
      说明五等）自动附加到每个方法段，避免联查上下文缺失。
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from .ai_client import ai_enabled, chat, parse_json_obj
from .models import Block, METHOD_CN
from .sheetio import load_workbook_model

# ---------------------------------------------------------------------------
# 方法归属关键词（本地兜底规则）
# ---------------------------------------------------------------------------
SHEET_KEYWORDS: Dict[str, List[str]] = {
    "income": [
        "收益法", "收益", "现金流", "现金流量", "折现", "折现率", "净现金流",
        "营业收入预测", "收入预测", "成本预测", "费用预测", "营运资金",
        "永续", "WACC", "CAPM", "beta", "β", "贝塔", "折现系数",
        "企业自由现金流", "股权自由现金流", "息税前", "EBIT", "EBITDA",
        "净利润预测", "利润预测", "溢余资产", "非经营性",
    ],
    "cost": [
        "资产基础法", "成本法", "重置成本", "成新率", "账面价值", "账面原值",
        "评估原值", "评估净值", "增值额", "增值率", "贬值", "构筑物",
        "机器设备", "电子设备", "车辆", "房屋建筑物", "在建工程", "土地使用",
        "无形资产", "存货", "应收账款", "其他应收款", "预付", "应付",
        "明细表", "汇总表", "评估结果汇总", "清查", "评估明细",
    ],
    "market": [
        "市场法", "可比公司", "可比交易", "价值比率", "市盈率", "市净率",
        "市销率", "PE", "PB", "PS", "EV/EBITDA", "流动性折扣", "DLOM",
        "修正系数", "修正", "交易案例", "股价", "每股", "EPS", "BPS", "市值",
    ],
}

# 通用表（公共依赖）：每个底稿块强制附加
GENERAL_KEYWORDS: List[str] = [
    "通用", "基础数据", "基础表", "历史", "资产负债表", "利润表", "财务报表",
    "财务数据", "基准日", "参数", "假设", "取价", "汇总", "说明", "封面",
    "目录", "index", "cover", "评估结论汇总", "结果汇总",
]

# 说明文本：方法关键词（段落 / 章节级判定）
TEXT_KEYWORDS: Dict[str, List[str]] = {
    "income": [
        "收益法", "收益额", "折现", "折现率", "净现金流量", "现金流",
        "营业收入预测", "永续", "WACC", "CAPM", "β", "贝塔", "风险溢价",
        "经营性资产", "溢余资产", "非经营性资产", "非经营性负债",
        "企业整体价值", "股东全部权益价值", "收益期限", "预测期",
        "营运资金", "资本性支出", "折旧摊销",
    ],
    "cost": [
        "资产基础法", "成本法", "重置成本", "成新率", "账面价值", "账面原值",
        "评估原值", "评估净值", "增值额", "增值率", "实体性贬值",
        "功能性贬值", "经济性贬值", "机器设备", "房屋建筑物", "构筑物",
        "在建工程", "无形资产", "存货", "应收账款", "坏账", "评估明细",
    ],
    "market": [
        "市场法", "可比公司", "可比交易", "价值比率", "市盈率", "市净率",
        "市销率", "PE", "PB", "PS", "EV/EBITDA", "流动性折扣", "DLOM",
        "修正系数", "交易案例", "股价", "市值", "每股",
    ],
}

# 说明公共章节（附加到每个方法段）
COMMON_KEYWORDS: List[str] = [
    "评估对象", "评估范围", "资产核实", "评估结论", "评估假设", "特别事项",
    "评估依据", "评估目的", "评估基准日", "委托人", "被评估单位", "评估程序",
    "评估方法", "评估结论及分析", "评估技术说明",
]

_HEADING_RE = re.compile(
    r"^\s*(?:"
    r"第[一二三四五六七八九十百]+[章节篇部分]|"
    r"说明[一二三四五六七八九十]|"
    r"[一二三四五六七八九十]+[、．.]|"
    r"\d+(?:\.\d+)*[ 、．.]|"
    r"附件\s*\d*|"
    r"[（(][一二三四五六七八九十]+[)）]"
    r")"
)


def _score(text: str, keywords: List[str]) -> int:
    """关键词命中计分（大小写不敏感；英文关键词按词边界匹配）。"""
    if not text:
        return 0
    low = text.lower()
    score = 0
    for kw in keywords:
        k = kw.lower()
        if re.fullmatch(r"[a-z/]+", k):
            if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", low):
                score += 1
        elif k in low:
            score += 1
    return score


# ---------------------------------------------------------------------------
# Excel：Sheet 关联性判定
# ---------------------------------------------------------------------------
def classify_sheet_by_rule(name: str, preview: str) -> Tuple[str, str]:
    """本地关键词判定 Sheet 归属，返回 (method, reason)。"""
    text = f"{name} {preview}"
    scores = {m: _score(text, kws) for m, kws in SHEET_KEYWORDS.items()}
    gen = _score(f"{name} {preview[:400]}", GENERAL_KEYWORDS)
    best = max(scores, key=lambda k: scores[k])
    if scores[best] == 0:
        return ("general" if gen else "general",
                "未命中方法关键词，按通用表处理")
    if gen >= max(4, scores[best]):
        return "general", f"通用表特征命中 {gen} 项，作为公共依赖附加"
    return best, f"命中「{METHOD_CN.get(best, best)}」关键词 {scores[best]} 项"


def classify_sheets_by_ai(model, ai_cfg: Dict[str, Any],
                          log: Optional[Callable[[str], None]] = None
                          ) -> Optional[Dict[str, Dict[str, str]]]:
    """调用 AI 判定 Sheet 与三法的关联性。

    返回 {sheet_name: {"method": ..., "reason": ...}}；不可用 / 失败返回 None。
    """
    if not ai_enabled(ai_cfg):
        return None
    lines: List[str] = []
    for name in model.order:
        sm = model.sheets.get(name)
        if not sm:
            continue
        preview = sm.text(max_rows=8, max_cols=12).replace("\n", " ")
        lines.append(f"- {name}（{sm.nrows} 行 × {sm.ncols} 列）预览：{preview[:300]}")
    if not lines:
        return None

    system = (
        "你是资产评估项目负责人。下面是某评估测算 Excel 的全部工作表清单与内容预览。"
        "请判断每张表主要服务于哪种评估方法。"
        "只能输出 JSON，格式：\n"
        '{"sheets":[{"name":"表名","method":"income|cost|market|general","reason":"一句话理由"}]}\n'
        "取值说明：income=收益法，cost=成本法/资产基础法，market=市场法，"
        "general=通用/公共表（历史财务报表、基础数据、参数、汇总、封面目录等，"
        "不属于任何一种方法、但被多个方法引用）。"
        "严禁输出 JSON 之外的任何文字。"
    )
    user = "工作表清单：\n" + "\n".join(lines)
    try:
        raw = chat(ai_cfg, system, user, num_predict=2048)
        data = parse_json_obj(raw)
        rows = data.get("sheets")
        if not isinstance(rows, list) or not rows:
            return None
        out: Dict[str, Dict[str, str]] = {}
        for r in rows:
            if not isinstance(r, dict):
                continue
            name = str(r.get("name") or "").strip()
            method = str(r.get("method") or "").strip().lower()
            if name and method in ("income", "cost", "market", "general"):
                out[name] = {"method": method,
                             "reason": str(r.get("reason") or "")[:200]}
        if log:
            log(f"AI 完成 {len(out)} 张工作表的关联性判定")
        return out or None
    except Exception as exc:  # noqa: BLE001 - AI 失败降级为规则判定
        if log:
            log(f"AI 切分不可用（{exc}），已降级为关键词规则判定")
        return None


def split_workbook(slot: str, path: str, file_name: str,
                   ai_cfg: Dict[str, Any] = None,
                   manual: Optional[Dict[str, str]] = None,
                   log: Optional[Callable[[str], None]] = None
                   ) -> Dict[str, Any]:
    """切分一个测算表：返回各方法 sheet 归属与通用表。"""
    model = load_workbook_model(path)
    out: Dict[str, Any] = {
        "slot": slot, "file": file_name, "error": model.load_error,
        "sheets": [], "general": [], "by_method": {"income": [], "cost": [], "market": []},
        "engine": "rule",
    }
    if model.load_error:
        return out

    ai_map = classify_sheets_by_ai(model, ai_cfg or {}, log)
    if ai_map:
        out["engine"] = "ai"

    for name in model.order:
        sm = model.sheets[name]
        preview = sm.text(max_rows=10, max_cols=12)
        if manual and name in manual:
            method = manual[name]
            reason = "人工指定"
            engine = "manual"
        elif ai_map and name in ai_map:
            method = ai_map[name]["method"]
            reason = ai_map[name]["reason"]
            engine = "ai"
        else:
            method, reason = classify_sheet_by_rule(name, preview)
            engine = "rule"
        out["sheets"].append({
            "name": name, "method": method, "reason": reason, "engine": engine,
            "nrows": sm.nrows, "ncols": sm.ncols, "state": sm.state,
        })
        if method == "general":
            out["general"].append(name)
        else:
            out["by_method"][method].append(name)
    return out


# ---------------------------------------------------------------------------
# 评估说明：按三法拆段
# ---------------------------------------------------------------------------
def _iter_block_items(doc):
    """按文档顺序遍历段落与表格（保持原始顺序）。"""
    from docx.document import Document as _Doc  # noqa: PLC0415
    from docx.oxml.table import CT_Tbl  # noqa: PLC0415
    from docx.oxml.text.paragraph import CT_P  # noqa: PLC0415
    from docx.table import Table  # noqa: PLC0415
    from docx.text.paragraph import Paragraph  # noqa: PLC0415

    body = doc.element.body
    for child in body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, doc)
        elif isinstance(child, CT_Tbl):
            yield Table(child, doc)


def _table_text(table) -> str:
    rows = []
    try:
        for row in table.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            rows.append(" | ".join(cells))
    except Exception:  # noqa: BLE001
        return ""
    return "\n".join(rows)


def _is_heading(p) -> bool:
    style = ""
    try:
        style = str(p.style.name or "")
    except Exception:  # noqa: BLE001
        pass
    if "Heading" in style or "标题" in style:
        return True
    text = (p.text or "").strip()
    if not text or len(text) > 60:
        return False
    return bool(_HEADING_RE.match(text))


def split_statement(path: str, file_name: str) -> Dict[str, Any]:
    """把评估说明按三法拆为 3 段，并抽取公共段。"""
    out: Dict[str, Any] = {
        "file": file_name, "error": "", "engine": "rule",
        "sections": [], "paras": 0,
    }
    try:
        import docx  # noqa: PLC0415
        doc = docx.Document(path)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"评估说明解析失败：{exc}"
        return out

    # 第一遍：顺序收集文本单元
    units: List[Dict[str, Any]] = []
    for item in _iter_block_items(doc):
        if hasattr(item, "rows"):      # 表格
            text = _table_text(item)
            units.append({"type": "table", "text": text, "heading": False})
        else:
            text = (item.text or "").strip()
            if not text:
                continue
            units.append({"type": "para", "text": text, "heading": _is_heading(item)})

    # 第二遍：按标题切章，章内累计方法分数
    sections: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for u in units:
        if u["heading"] or cur is None:
            cur = {"title": u["text"][:80] if u["heading"] else "（正文开头）",
                   "units": [], "scores": {"income": 0, "cost": 0, "market": 0},
                   "common": 0}
            sections.append(cur)
        cur["units"].append(u)
        t = u["text"]
        for m, kws in TEXT_KEYWORDS.items():
            cur["scores"][m] += _score(t, kws)
        cur["common"] += _score(t, COMMON_KEYWORDS)

    # 第三遍：判定每章归属
    parts: Dict[str, List[str]] = {"income": [], "cost": [], "market": [], "common": []}
    for sec in sections:
        scores = sec["scores"]
        best = max(scores, key=lambda k: scores[k])
        total = sum(scores.values())
        method = "common"
        if total > 0 and scores[best] >= max(3, sec["common"]):
            method = best
        elif total > 0 and scores[best] > 0 and sec["common"] == 0:
            method = best
        sec["method"] = method
        sec["score"] = scores
        body = "\n".join(u["text"] for u in sec["units"] if u["text"])
        if body:
            parts[method].append(body)
            out["sections"].append({
                "title": sec["title"], "method": method,
                "score": scores, "chars": len(body),
            })

    out["paras"] = len(units)
    out["texts"] = {
        "income": "\n\n".join(parts["income"]),
        "cost": "\n\n".join(parts["cost"]),
        "market": "\n\n".join(parts["market"]),
        "common": "\n\n".join(parts["common"]),
    }
    out["chars"] = {k: len(v) for k, v in out["texts"].items()}
    return out


def split_report(path: str, file_name: str) -> Dict[str, Any]:
    """评估报告全文：保持完整，不切分。"""
    out: Dict[str, Any] = {"file": file_name, "error": "", "text": "", "chars": 0, "tables": 0}
    try:
        import docx  # noqa: PLC0415
        doc = docx.Document(path)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"评估报告解析失败：{exc}"
        return out
    buf: List[str] = []
    for item in _iter_block_items(doc):
        if hasattr(item, "rows"):
            out["tables"] += 1
            t = _table_text(item)
            if t:
                buf.append(t)
        else:
            text = (item.text or "").strip()
            if text:
                buf.append(text)
    out["text"] = "\n".join(buf)
    out["chars"] = len(out["text"])
    return out


# ---------------------------------------------------------------------------
# 组装 7 个数据块
# ---------------------------------------------------------------------------
def build_blocks(cleaned: Dict[str, Dict[str, str]],
                 split_results: Dict[str, Any],
                 statement_split: Dict[str, Any],
                 report_split: Dict[str, Any]) -> List[Block]:
    """根据切分结果组装 B1–B7 数据块。

    cleaned: {slot: {"name":..., "path":...}}
    split_results: {slot: split_workbook 结果}
    """
    blocks: List[Block] = []

    # B1 / B2 / B3：三法底稿块（含强制附加的通用表）
    for bid, slot in (("B1", "income"), ("B2", "cost"), ("B3", "market")):
        info = cleaned.get(slot) or {}
        sr = split_results.get(slot) or {}
        own = list(sr.get("by_method", {}).get(slot, []))
        general = list(sr.get("general") or [])
        blocks.append(Block(
            block_id=bid,
            name={"B1": "收益法底稿块", "B2": "成本法底稿块", "B3": "市场法底稿块"}[bid],
            kind="excel",
            source_slot=slot,
            source_file=info.get("name", ""),
            sheets=[s for s in sr.get("sheets", [])],
            general_sheets=general,
            stats={
                "own_sheets": own,
                "general_sheets": general,
                "file": info.get("name", ""),
                "path": info.get("path", ""),
                "engine": sr.get("engine", "rule"),
                "error": sr.get("error", ""),
            },
        ))

    # B4 / B5 / B6：说明三段（公共段自动附加）
    texts = (statement_split or {}).get("texts") or {}
    common = texts.get("common", "")
    for bid, key in (("B4", "income"), ("B5", "cost"), ("B6", "market")):
        body = texts.get(key, "")
        merged = (body + ("\n\n" + common if common else "")).strip()
        blocks.append(Block(
            block_id=bid,
            name={"B4": "说明_收益法", "B5": "说明_成本法", "B6": "说明_市场法"}[bid],
            kind="doc",
            source_slot="statement",
            source_file=(cleaned.get("statement") or {}).get("name", ""),
            text=merged,
            stats={
                "own_chars": len(body),
                "common_chars": len(common),
                "sections": [s for s in (statement_split or {}).get("sections", [])
                             if s.get("method") == key],
            },
        ))

    # B7：报告全文（不切分）
    blocks.append(Block(
        block_id="B7",
        name="评估报告全文",
        kind="doc",
        source_slot="report",
        source_file=(cleaned.get("report") or {}).get("name", ""),
        text=(report_split or {}).get("text", ""),
        stats={
            "chars": (report_split or {}).get("chars", 0),
            "tables": (report_split or {}).get("tables", 0),
        },
    ))
    return blocks
