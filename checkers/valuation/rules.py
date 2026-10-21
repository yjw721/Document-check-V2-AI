# -*- coding: utf-8 -*-
"""
本地确定性核查规则引擎（对应 96 项核查事项中的可计算部分）
=========================================================================
与 AI 语义核查互补：凡能靠「数值关系 / 结构关系」判定的核查事项，
一律用本地规则实现——零联网、零误判漂移、结果可复算。

覆盖（按需求文档节点分布）：
    ③ 收益法 15 项：汇总勾稽 / 行列加总 / 公式列一致性 / 跨表同源 /
                    基础报表平衡 / 现金流公式 / 价值汇总 / 折现率 /
                    永续期 / 预测期衔接 / 收入量价 / 营运资金 / 折现率参数 /
                    税项 / 结论一致
    ④ 成本法 15 项：汇总勾稽 / 行列加总 / 公式列 / 跨表 / 增值额增值率 /
                    报表衔接 / 汇总表勾稽 / 成本法验证 / 坏账 / 固定资产 / 结论一致
    ⑤ 市场法 10 项：汇总勾稽 / 行列加总 / 跨表 / 可比公司名单 /
                    基准日数据同源 / 价值比率 / 修正一致性 / DLOM / 结论一致
    ① ② 文档类：表格勾稽 / 术语规范
    ⑥ – ⑨ 联查类：跨文件数值一致性 / 结论一致性 / 分类汇总一致性 /
                    无来源数据 / 表号引用 / 无解释异常值
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .models import Finding
from .sheetio import WorkbookModel, SheetModel, load_workbook_model, to_num, cell_addr, fmt_num

# ---------------------------------------------------------------------------
# 常量：行 / 列标签关键词
# ---------------------------------------------------------------------------
K_BOOK = ["账面价值", "账面值", "账面净值"]
K_BOOK_ORIG = ["账面原值"]
K_APPR = ["评估价值", "评估值", "评估净值"]
K_APPR_ORIG = ["评估原值", "重置价值"]
K_INCR = ["增值额", "增减值额", "评估增减值"]
K_RATE = ["增值率", "增减值率", "增值比例"]
K_TOTAL = ["合计", "小计", "总计", "共计", "合  计", "总  计"]
K_RATE_DISCOUNT = ["折现率", "贴现率", "WACC", "加权平均资本成本"]
K_NET_CASH = ["净现金流量", "净现金流", "现金流量净额", "企业自由现金流"]

MAX_FINDINGS_PER_RULE = 30        # 单规则输出上限，防止结果爆炸
REPORT_TOL_REL = 0.001            # 相对容差 0.1%
REPORT_TOL_ABS = 0.5              # 绝对容差 0.5


def _tol(ref: float, rel: float = REPORT_TOL_REL, abs_: float = REPORT_TOL_ABS) -> float:
    return max(abs_, abs(ref or 0.0) * rel)


def _close(a: Optional[float], b: Optional[float],
           rel: float = REPORT_TOL_REL, abs_: float = REPORT_TOL_ABS) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= _tol(max(abs(a), abs(b)), rel, abs_)


def _pct(v: Optional[float]) -> Optional[float]:
    """把可能是百分数的值归一为小数比例：5 / 0.05 / 5% → 0.05。"""
    if v is None:
        return None
    if abs(v) > 1.5:
        return v / 100.0
    return v


def _parse_cell_num(text: Any) -> Optional[float]:
    """从文本单元格中解析数值（支持 1,234.56 / (123) / 12.3% / 万元）。"""
    if text is None:
        return None
    n = to_num(text)
    if n is not None:
        return n
    s = str(text).strip()
    m = re.search(r"-?\(?\d[\d,，]*\.?\d*\)?%?", s)
    if not m:
        return None
    return to_num(m.group(0).replace("，", ""))


# ---------------------------------------------------------------------------
# 表结构识别
# ---------------------------------------------------------------------------
def _header_row(sm: SheetModel, keywords: Sequence[str] = (), scan: int = 8) -> int:
    """定位表头行：前 scan 行中含关键词最多的行；无关键词时返回首个非空行。"""
    best_row, best_score = 0, -1
    for r in range(1, min(sm.nrows, scan) + 1):
        vals = [str(v) for v in sm.row_values(r) if v is not None]
        if not vals:
            continue
        score = sum(1 for v in vals for k in (keywords or K_BOOK + K_APPR) if k in v)
        if score > best_score:
            best_row, best_score = r, score
    return best_row


def _find_col(sm: SheetModel, keywords: Sequence[str], scan: int = 6) -> Optional[int]:
    """在前 scan 行中查找含关键词的列。"""
    hits = sm.find_cols(list(keywords), search_rows=scan, limit=1)
    return hits[0] if hits else None


def _find_row(sm: SheetModel, keywords: Sequence[str], cols: int = 4) -> Optional[int]:
    hits = sm.find_rows(list(keywords), search_cols=cols, limit=1)
    return hits[0] if hits else None


def _find_all_rows(sm: SheetModel, keywords: Sequence[str], cols: int = 4) -> List[int]:
    return sm.find_rows(list(keywords), search_cols=cols)


def _is_total_row(sm: SheetModel, r: int, cols: int = 3) -> bool:
    for c in range(1, min(sm.ncols, cols) + 1):
        v = sm.val(r, c)
        if v is None:
            continue
        s = str(v).replace(" ", "")
        if any(k.replace(" ", "") in s for k in K_TOTAL):
            return True
    return False


def _is_data_row(sm: SheetModel, r: int) -> bool:
    """数据行：至少 2 个数值单元格。"""
    return len(sm.row_nums(r)) >= 2


def _detail_rows_between(sm: SheetModel, start: int, end: int) -> List[int]:
    """返回 (start, end) 之间的数据行（排除合计行与含「其中」的行）。"""
    out = []
    for r in range(start + 1, end):
        if _is_total_row(sm, r) or not _is_data_row(sm, r):
            continue
        label = "".join(str(sm.val(r, c) or "") for c in range(1, min(sm.ncols, 3) + 1))
        if "其中" in label:
            continue
        out.append(r)
    return out


def _block_bounds(sm: SheetModel) -> List[Tuple[int, int]]:
    """按「合计行 / 空行 / 表头行」切分区间，返回 [(起始行, 合计行)]。"""
    bounds: List[Tuple[int, int]] = []
    start = 1
    for r in range(1, sm.nrows + 1):
        row_empty = not [v for v in sm.row_values(r) if v is not None]
        if _is_total_row(sm, r):
            if r > start:
                bounds.append((start, r))
            start = r + 1
        elif row_empty and r > start + 1:
            if any(_is_data_row(sm, x) for x in range(start, r)):
                pass
            start = r + 1
    return bounds


# ---------------------------------------------------------------------------
# 规则上下文
# ---------------------------------------------------------------------------
class RuleContext:
    """规则执行上下文：提供块数据访问与 Finding 构造。"""

    def __init__(self, node: Dict[str, Any], item: Dict[str, Any],
                 blocks: Dict[str, Any], paths: Dict[str, str],
                 log: Optional[Callable[[str], None]] = None,
                 shared: Optional[set] = None):
        self.node = node
        self.item = item
        self.blocks = blocks
        self.paths = paths
        self.log = log or (lambda s: None)
        self._models: Dict[str, Optional[WorkbookModel]] = {}
        self._docs: Dict[str, Dict[str, Any]] = {}
        # 文档核查单元去重（跨块公共章节、跨核查事项重复扫描）
        self._seen_units: set = shared if shared is not None else set()

    # ---------- Excel ----------
    def model(self, block_id: str) -> Optional[WorkbookModel]:
        if block_id in self._models:
            return self._models[block_id]
        path = self.paths.get(block_id) or ""
        model = None
        if path and os.path.exists(path) and path.lower().endswith((".xlsx", ".xlsm")):
            model = load_workbook_model(path)
            if model.load_error:
                self.log(f"[{block_id}] 工作簿加载失败：{model.load_error}")
                model = None
        self._models[block_id] = model
        return model

    def excel_blocks(self) -> List[Tuple[str, WorkbookModel]]:
        out = []
        for bid in self.node.get("inputs", []):
            m = self.model(bid)
            if m:
                out.append((bid, m))
        return out

    def sheets_of(self, block_id: str, include_general: bool = True) -> List[Tuple[str, SheetModel]]:
        """返回该块应核查的工作表（含强制附加的通用表）。"""
        model = self.model(block_id)
        if not model:
            return []
        blk = self.blocks.get(block_id)
        names: List[str] = []
        if blk is not None:
            own = [s["name"] for s in (blk.sheets or [])
                   if s.get("method") == blk.source_slot]
            general = list(blk.general_sheets or [])
            names = own + (general if include_general else [])
            if not names:                       # 切分信息缺失时回退为全表
                names = list(model.order)
        else:
            names = list(model.order)
        return [(n, model.sheets[n]) for n in names if n in model.sheets]

    # ---------- 文档 ----------
    def doc(self, block_id: str) -> Dict[str, Any]:
        """读取文档块：{paragraphs: [...], tables: [[[cell]]], text: str}。"""
        if block_id in self._docs:
            return self._docs[block_id]
        path = self.paths.get(block_id) or ""
        data: Dict[str, Any] = {"paragraphs": [], "tables": [], "text": "", "error": ""}
        if path and os.path.exists(path) and path.lower().endswith(".docx"):
            try:
                import docx  # noqa: PLC0415
                d = docx.Document(path)
                data["paragraphs"] = [(p.text or "").strip() for p in d.paragraphs]
                for t in d.tables:
                    rows = []
                    for row in t.rows:
                        rows.append([(c.text or "").strip() for c in row.cells])
                    data["tables"].append(rows)
            except Exception as exc:  # noqa: BLE001
                data["error"] = str(exc)
        blk = self.blocks.get(block_id)
        data["text"] = (blk.text if blk is not None else "") or "\n".join(data["paragraphs"])
        self._docs[block_id] = data
        return data

    def doc_blocks(self) -> List[Tuple[str, Dict[str, Any]]]:
        out = []
        for bid in self.node.get("inputs", []):
            if (self.paths.get(bid) or "").lower().endswith(".docx"):
                out.append((bid, self.doc(bid)))
            elif self.blocks.get(bid) is not None and self.blocks[bid].kind == "doc":
                out.append((bid, self.doc(bid)))
        return out

    def excel_nums(self) -> set:
        """收集所有 Excel 块中的数值集合（用于「文档数值是否有来源」检查）。"""
        cache_key = "__all_nums__"
        if cache_key in self._models:      # 复用字典做集合缓存
            return self._models[cache_key]  # type: ignore[return-value]
        nums = set()
        for bid, model in self.excel_blocks():
            for name in model.order:
                sm = model.sheets[name]
                for r in range(1, sm.nrows + 1):
                    for _, v in sm.row_nums(r):
                        nums.add(round(v, 4))
        self._models[cache_key] = nums     # type: ignore[index]
        return nums

    def _block_file(self, block_id: str) -> str:
        """归属文件：取源文件名（跨节点去重时同一文件的问题才能归为一组）。"""
        blk = self.blocks.get(block_id)
        return (blk.source_file if blk is not None else "") or block_id

    def _block_name(self, block_id: str) -> str:
        blk = self.blocks.get(block_id)
        return blk.name if blk is not None else block_id

    def doc_units(self, block_id: str) -> List[Tuple[str, str]]:
        """文档核查单元（段落 / 表格行），跨块自动去重。

        说明被切为「收益法 / 成本法 / 市场法」三段，公共章节会同时出现在
        三个块中；若不去重，公共章节里的同一问题会被重复报告多次。
        """
        import hashlib
        data = self.doc(block_id)
        raw: List[Tuple[str, str]] = []
        for i, t in enumerate(data.get("paragraphs") or []):
            if t:
                raw.append((f"第{i + 1}段", t))
        for ti, rows in enumerate(data.get("tables") or []):
            for ri, row in enumerate(rows):
                txt = " ".join(c for c in row if c)
                if txt:
                    raw.append((f"表{ti + 1}·第{ri + 1}行", txt))
        if not raw and data.get("text"):
            raw = [("全文", data["text"][:20000])]

        out: List[Tuple[str, str]] = []
        for loc, text in raw:
            key = hashlib.md5(text.encode("utf-8", "ignore")).hexdigest()
            if key in self._seen_units:
                continue
            self._seen_units.add(key)
            out.append((loc, text))
        return out

    # ---------- 产出 ----------
    def mk(self, severity: str, detail: str, location: str = "",
           file: str = "", snippet: str = "", suggestion: str = "",
           origin: str = "rule", block: str = "") -> Finding:
        node = self.node or {}
        item = self.item or {}
        return Finding(
            node_id=node.get("id", ""),
            node_no=node.get("no", ""),
            node_name=node.get("name", ""),
            item_id=item.get("id", ""),
            item_no=item.get("no", 0),
            item_title=item.get("title", ""),
            source=item.get("source", ""),
            engine=item.get("engine", "rule"),
            severity=severity or item.get("severity", "medium"),
            file=file,
            block=block,
            location=location,
            detail=detail,
            snippet=snippet[:300],
            suggestion=suggestion,
            origin=origin,
        )


# ---------------------------------------------------------------------------
# 规则实现
# ---------------------------------------------------------------------------
def rule_sum_rows(ctx: RuleContext) -> List[Finding]:
    """分类汇总勾稽：合计行各列 = 上方明细行之和（纵向加总）。"""
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        for sname, sm in ctx.sheets_of(bid):
            for start, trow in _block_bounds(sm):
                details = _detail_rows_between(sm, start, trow)
                if len(details) < 2:
                    continue
                for c in range(1, sm.ncols + 1):
                    total = sm.num(trow, c)
                    if total is None or total == 0:
                        continue
                    parts = [(r, sm.num(r, c)) for r in details]
                    parts = [(r, v) for r, v in parts if v is not None]
                    if len(parts) < 2:
                        continue
                    s = sum(v for _, v in parts)
                    if abs(s) < 1e-9 or _close(s, total):
                        continue
                    # 量级护栏：差异超过 50% 通常是「结构识别」误判（如把
                    # 资产总计行当成上方科目的合计），而非真实的计算错误
                    if abs(total - s) > 0.5 * max(abs(total), abs(s)):
                        continue
                    label = str(sm.val(trow, 1) or sm.val(trow, 2) or "合计行")
                    out.append(ctx.mk(
                        "high",
                        f"合计行数值与明细加总不符：{sname}!{cell_addr(trow, c)} 列式合计为 "
                        f"{fmt_num(total)}，而明细行（{details[0]}–{details[-1]} 行，{len(parts)} 项）"
                        f"加总为 {fmt_num(s)}，差额 {fmt_num(total - s)}。",
                        location=f"{sname}!{cell_addr(trow, c)}",
                        file=ctx._block_file(bid),
                        snippet=f"{label}：列{cell_addr(trow, c)[0]} 合计 {fmt_num(total)}",
                        suggestion="复核该列合计公式的取值范围，确认是否漏计明细行或误含非明细行。",
                    ))
                    if len(out) >= MAX_FINDINGS_PER_RULE:
                        return out
    return out


def rule_rowcol_sum(ctx: RuleContext) -> List[Finding]:
    """横向加总：行「合计」列 = 该行各明细列之和。"""
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        for sname, sm in ctx.sheets_of(bid):
            hrow = _header_row(sm)
            if not hrow:
                continue
            total_cols = sm.find_cols(["合计", "总计", "小计"], search_rows=hrow + 3)
            if not total_cols:
                continue
            tc = total_cols[0]
            # 明细列：表头行中位于合计列之前、且表头非空的数值列
            detail_cols = [c for c in range(1, tc)
                           if str(sm.val(hrow, c) or "").strip()
                           and "其中" not in str(sm.val(hrow, c) or "")]
            if len(detail_cols) < 2:
                continue
            for r in range(hrow + 1, sm.nrows + 1):
                if _is_total_row(sm, r) and any(
                        k in "".join(str(sm.val(r, c) or "") for c in range(1, min(sm.ncols, 3) + 1))
                        for k in ("总计", "合计")) and r > hrow + 1:
                    continue
                total = sm.num(r, tc)
                if total is None or total == 0:
                    continue
                parts = [sm.num(r, c) for c in detail_cols]
                parts = [v for v in parts if v is not None]
                if len(parts) < 2:
                    continue
                s = sum(parts)
                if abs(s) < 1e-9 or _close(s, total):
                    continue
                if abs(total - s) > 0.5 * max(abs(total), abs(s)):
                    continue      # 量级护栏，见 rule_sum_rows
                label = str(sm.val(r, 1) or sm.val(r, 2) or f"第{r}行")
                out.append(ctx.mk(
                    "high",
                    f"横向加总不符：{sname} 第 {r} 行「{label}」的合计列 {cell_addr(r, tc)} 为 "
                    f"{fmt_num(total)}，各明细列（{len(parts)} 项）加总为 {fmt_num(s)}，"
                    f"差额 {fmt_num(total - s)}。",
                    location=f"{sname}!{cell_addr(r, tc)}",
                    file=ctx._block_file(bid),
                    snippet=f"{label} 合计 {fmt_num(total)}",
                    suggestion="核对合计列公式是否覆盖了全部明细列，或是否存在未纳入的隐藏列。",
                ))
                if len(out) >= MAX_FINDINGS_PER_RULE:
                    return out
    return out


def _formula_pattern(fml: str, row: int) -> str:
    """把公式归一为模式：本行行号 → {R}，用于判断同列公式是否一致。"""
    s = fml
    s = re.sub(rf"(?<![A-Z0-9]){row}(?![0-9])", "{R}", s)
    s = re.sub(r"\$?\d+", "{N}", s)
    return re.sub(r"\s+", "", s)


def rule_formula_col(ctx: RuleContext) -> List[Finding]:
    """按列方向的运算逻辑一致性：识别公式断层与硬编码。"""
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        for sname, sm in ctx.sheets_of(bid):
            hrow = _header_row(sm)
            cols = range(1, sm.ncols + 1)
            for c in cols:
                pats: Dict[str, int] = {}
                consts: List[int] = []
                formula_rows: List[int] = []
                for r in range(max(2, hrow + 1), sm.nrows + 1):
                    if _is_total_row(sm, r):
                        continue
                    f = sm.formula(r, c)
                    v = sm.val(r, c)
                    if f:
                        formula_rows.append(r)
                        pats[_formula_pattern(f, r)] = pats.get(_formula_pattern(f, r), 0) + 1
                    elif to_num(v) is not None:
                        consts.append(r)
                # 1) 公式列内运算模式断层（需≥2个公式行方可判断）
                if len(formula_rows) >= 2 and len(pats) > 1:
                    main = max(pats, key=lambda k: pats[k])
                    odd = [r for r in formula_rows
                           if _formula_pattern(sm.formula(r, c) or "", r) != main]
                    if odd and len(odd) <= len(formula_rows) * 0.5:
                        out.append(ctx.mk(
                            "medium",
                            f"同列运算逻辑不一致：{sname} 第 {cell_addr(1, c)[0]} 列"
                            f"（{len(formula_rows)} 行有公式）存在 {len(pats)} 种计算模式，"
                            f"其中 {len(odd)} 行与主体模式不同（如第 {odd[0]}、{odd[-1]} 行），"
                            "可能存在公式断层。",
                            location=f"{sname}!{cell_addr(odd[0], c)}",
                            file=ctx._block_file(bid),
                            snippet=str(sm.formula(odd[0], c) or "")[:120],
                            suggestion="统一该列计算口径，确认差异行是否为有意为之并在说明中交代。",
                        ))
                # 2) 公式列中夹带硬编码常量（≥1个公式行 + ≥1个常量即告警）
                if len(formula_rows) >= 1 and len(consts) >= 1:
                    out.append(ctx.mk(
                        "medium",
                        f"疑似硬编码：{sname} 第 {cell_addr(1, c)[0]} 列中第 "
                        f"{consts[0]}、{consts[-1]} 等 {len(consts)} 行为常量值，"
                        "而同列其它行使用公式，年度间计算逻辑可能不一致。",
                        location=f"{sname}!{cell_addr(consts[0], c)}",
                        file=ctx._block_file(bid),
                        snippet=str(sm.val(consts[0], c) or "")[:80],
                        suggestion="如非特殊调整，请改为公式引用，保证口径一致、可追溯。",
                    ))
                if len(out) >= MAX_FINDINGS_PER_RULE:
                    return out
    return out


def _cell_key_map(sm: SheetModel) -> Dict[Tuple[str, str], Tuple[int, int, float]]:
    """构建 (行标签, 列表头) → (row, col, value) 索引，用于跨表同源比对。"""
    idx: Dict[Tuple[str, str], Tuple[int, int, float]] = {}
    headers: Dict[int, str] = {}
    for c in range(1, sm.ncols + 1):
        for r in range(1, min(sm.nrows, 5) + 1):
            v = sm.val(r, c)
            if isinstance(v, str) and v.strip() and not _looks_numeric(v):
                headers[c] = v.strip()[:40]
                break
    for r in range(1, sm.nrows + 1):
        label = ""
        for c in range(1, min(sm.ncols, 4) + 1):
            v = sm.val(r, c)
            if isinstance(v, str) and v.strip() and not _looks_numeric(v):
                label = v.strip()[:40]
                break
        if not label or any(k in label for k in K_TOTAL):
            continue
        for c, v in sm.row_nums(r):
            h = headers.get(c, "")
            if not h:
                continue
            key = (label, h)
            if key not in idx:
                idx[key] = (r, c, v)
    return idx


def _looks_numeric(s: str) -> bool:
    return bool(re.fullmatch(r"[-+]?[\d,，.]+%?", str(s).strip()))


def rule_cross_sheet(ctx: RuleContext) -> List[Finding]:
    """跨表数据勾稽：同一「行标签 + 列表头」在不同 Sheet 中应一致。"""
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        pairs = ctx.sheets_of(bid)
        maps: Dict[str, Dict[Tuple[str, str], Tuple[int, int, float]]] = {}
        for sname, sm in pairs:
            maps[sname] = _cell_key_map(sm)
        names = list(maps.keys())
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = names[i], names[j]
                common = set(maps[a].keys()) & set(maps[b].keys())
                if len(common) < 2:
                    continue
                diff = 0
                for key in sorted(common):
                    ra, ca, va = maps[a][key]
                    rb, cb, vb = maps[b][key]
                    if _close(va, vb):
                        continue
                    diff += 1
                    if diff > 20:
                        break
                    out.append(ctx.mk(
                        "high",
                        f"跨表数据不一致：「{key[0]} · {key[1]}」在 {a}!{cell_addr(ra, ca)} 为 "
                        f"{fmt_num(va)}，在 {b}!{cell_addr(rb, cb)} 为 {fmt_num(vb)}，"
                        f"差额 {fmt_num(va - vb)}。",
                        location=f"{a}!{cell_addr(ra, ca)} ↔ {b}!{cell_addr(rb, cb)}",
                        file=ctx._block_file(bid),
                        snippet=f"{key[0]} · {key[1]}：{fmt_num(va)} vs {fmt_num(vb)}",
                        suggestion="确认两表的取数来源与口径，统一引用同一基础数据表。",
                    ))
                    if len(out) >= MAX_FINDINGS_PER_RULE:
                        return out
    return out


def rule_balance_sheet(ctx: RuleContext) -> List[Finding]:
    """基础数据表：资产负债表平衡、利润表勾稽、时间序列连续性。"""
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        for sname, sm in ctx.sheets_of(bid):
            r_asset = _find_row(sm, ["资产总计", "资产合计", "资产总额"])
            r_liab = _find_row(sm, ["负债合计", "负债总计", "负债总额"])
            r_eq = _find_row(sm, ["所有者权益合计", "所有者权益", "股东权益合计", "净资产"])
            if r_asset and r_liab and r_eq:
                for c in range(2, sm.ncols + 1):
                    a, l, e = sm.num(r_asset, c), sm.num(r_liab, c), sm.num(r_eq, c)
                    if None in (a, l, e) or a == 0:
                        continue
                    if not _close(a, (l or 0) + (e or 0)):
                        out.append(ctx.mk(
                            "high",
                            f"资产负债表不平衡：{sname} 第 {cell_addr(1, c)[0]} 列中，资产总计 "
                            f"{fmt_num(a)} ≠ 负债合计 {fmt_num(l)} + 所有者权益 {fmt_num(e)} "
                            f"= {fmt_num((l or 0) + (e or 0))}，差额 {fmt_num(a - (l or 0) - (e or 0))}。",
                            location=f"{sname}!{cell_addr(r_asset, c)}",
                            file=ctx._block_file(bid),
                            snippet=f"资产 {fmt_num(a)} / 负债 {fmt_num(l)} / 权益 {fmt_num(e)}",
                            suggestion="核对负债与权益科目是否登记完整，检查是否存在未归类科目。",
                        ))
                        break
            # 利润表：利润总额 = 营业利润 + 营业外收入 - 营业外支出
            r_tp = _find_row(sm, ["利润总额"])
            r_op = _find_row(sm, ["营业利润"])
            r_np = _find_row(sm, ["净利润"])
            r_tax = _find_row(sm, ["所得税费用", "所得税"])
            if r_tp and r_op:
                for c in range(2, sm.ncols + 1):
                    tp, op = sm.num(r_tp, c), sm.num(r_op, c)
                    if tp is None or op is None:
                        continue
                    if abs(tp) > 0 and abs(tp - op) / max(abs(tp), 1e-9) > 0.5 and tp * op < 0:
                        out.append(ctx.mk(
                            "medium",
                            f"利润表勾稽异常：{sname} 第 {cell_addr(1, c)[0]} 列营业利润为 "
                            f"{fmt_num(op)}，利润总额为 {fmt_num(tp)}，二者正负方向不一致，"
                            "请核实营业外收支。",
                            location=f"{sname}!{cell_addr(r_tp, c)}",
                            file=ctx._block_file(bid),
                            snippet=f"营业利润 {fmt_num(op)} / 利润总额 {fmt_num(tp)}",
                            suggestion="检查营业外收入、营业外支出是否完整计入。",
                        ))
                        break
            if r_np and r_tp and r_tax:
                for c in range(2, sm.ncols + 1):
                    np_, tp_, tax_ = sm.num(r_np, c), sm.num(r_tp, c), sm.num(r_tax, c)
                    if None in (np_, tp_, tax_):
                        continue
                    if not _close(np_, (tp_ or 0) - (tax_ or 0)):
                        out.append(ctx.mk(
                            "medium",
                            f"净利润勾稽不符：{sname} 第 {cell_addr(1, c)[0]} 列净利润 {fmt_num(np_)} ≠ "
                            f"利润总额 {fmt_num(tp_)} - 所得税费用 {fmt_num(tax_)} = "
                            f"{fmt_num((tp_ or 0) - (tax_ or 0))}。",
                            location=f"{sname}!{cell_addr(r_np, c)}",
                            file=ctx._block_file(bid),
                            snippet=f"净利润 {fmt_num(np_)}",
                            suggestion="核对所得税费用计算口径（含递延所得税）。",
                        ))
                        break
            if len(out) >= MAX_FINDINGS_PER_RULE:
                return out
    return out


def rule_cashflow(ctx: RuleContext) -> List[Finding]:
    """现金流公式：净利润 + 折旧摊销 - 营运资金增加 - 资本性支出 + 扣税后利息 = 净现金流量。"""
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        for sname, sm in ctx.sheets_of(bid):
            r_np = _find_row(sm, ["净利润"])
            r_dep = _find_row(sm, ["折旧摊销", "折旧与摊销", "折旧及摊销"])
            r_wc = _find_row(sm, ["营运资金增加", "营运资金净增加", "营运资金变动"])
            r_cap = _find_row(sm, ["资本性支出", "资本支出"])
            r_int = _find_row(sm, ["扣税后利息", "税后利息", "利息支出"])
            r_ncf = _find_row(sm, K_NET_CASH)
            if r_ncf and r_np and r_dep:
                for c in range(2, sm.ncols + 1):
                    ncf = sm.num(r_ncf, c)
                    if ncf is None:
                        continue
                    calc = (sm.num(r_np, c) or 0) + (sm.num(r_dep, c) or 0)
                    parts = ["净利润", "折旧摊销"]
                    if r_wc and sm.num(r_wc, c) is not None:
                        calc -= sm.num(r_wc, c) or 0
                        parts.append("-营运资金增加")
                    if r_cap and sm.num(r_cap, c) is not None:
                        calc -= sm.num(r_cap, c) or 0
                        parts.append("-资本性支出")
                    if r_int and sm.num(r_int, c) is not None:
                        calc += sm.num(r_int, c) or 0
                        parts.append("+扣税后利息")
                    if len(parts) < 4 or abs(calc) < 1e-9:
                        continue
                    if not _close(calc, ncf, rel=0.005):
                        out.append(ctx.mk(
                            "high",
                            f"净现金流量公式不符：{sname}!{cell_addr(r_ncf, c)} 列示 {fmt_num(ncf)}，"
                            f"按「{' '.join(parts)}」计算应为 {fmt_num(calc)}，差额 {fmt_num(ncf - calc)}。",
                            location=f"{sname}!{cell_addr(r_ncf, c)}",
                            file=ctx._block_file(bid),
                            snippet=f"净现金流量 {fmt_num(ncf)}",
                            suggestion="核对现金流测算公式是否遗漏营运资金变动或资本性支出项。",
                        ))
                        break
            # 折现值 = 净现金流量 × 折现系数
            r_df = _find_row(sm, ["折现系数", "现值系数"])
            r_pv = _find_row(sm, ["折现值", "现值", "净现值"])
            if r_ncf and r_df and r_pv:
                for c in range(2, sm.ncols + 1):
                    ncf, df, pv = sm.num(r_ncf, c), sm.num(r_df, c), sm.num(r_pv, c)
                    if None in (ncf, df, pv) or not (0 < df <= 1.2):
                        continue
                    if not _close(ncf * df, pv, rel=0.005):
                        out.append(ctx.mk(
                            "high",
                            f"折现值计算不符：{sname}!{cell_addr(r_pv, c)} 为 {fmt_num(pv)}，"
                            f"按 净现金流量 {fmt_num(ncf)} × 折现系数 {fmt_num(df)} 应为 "
                            f"{fmt_num(ncf * df)}。",
                            location=f"{sname}!{cell_addr(r_pv, c)}",
                            file=ctx._block_file(bid),
                            snippet=f"{fmt_num(ncf)} × {fmt_num(df)} = {fmt_num(ncf * df)}",
                            suggestion="核对折现期数（年中折现 / 年末折现）与折现系数是否匹配。",
                        ))
                        break
            if len(out) >= MAX_FINDINGS_PER_RULE:
                return out
    return out


def rule_value_rollup(ctx: RuleContext) -> List[Finding]:
    """企业整体价值 / 股东全部权益价值汇总勾稽。"""
    out: List[Finding] = []
    keys = {
        "operating": ["经营性资产价值", "经营性资产", "营业价值"],
        "surplus": ["溢余资产", "溢余资产价值"],
        "nonop_asset": ["非经营性资产"],
        "nonop_liab": ["非经营性负债"],
        "debt": ["付息负债", "有息负债", "付息债务"],
        "ev": ["企业整体价值", "企业价值", "整体价值"],
        "eq": ["股东全部权益价值", "股东权益价值", "股东全部权益"],
    }
    for bid, model in ctx.excel_blocks():
        for sname, sm in ctx.sheets_of(bid):
            rows = {k: _find_row(sm, v) for k, v in keys.items()}
            if rows["ev"] and rows["operating"]:
                for c in range(2, sm.ncols + 1):
                    ev = sm.num(rows["ev"], c)
                    op = sm.num(rows["operating"], c)
                    if ev is None or op is None:
                        continue
                    calc = op
                    detail = [f"经营性资产价值 {fmt_num(op)}"]
                    for k, sign in (("surplus", 1), ("nonop_asset", 1),
                                    ("nonop_liab", -1), ("debt", -1)):
                        r = rows.get(k)
                        if r and sm.num(r, c) is not None:
                            v = sm.num(r, c) or 0
                            calc += sign * v
                            detail.append(("+" if sign > 0 else "-") + f"{k} {fmt_num(v)}")
                    if len(detail) < 2 or abs(calc) < 1e-9:
                        continue
                    if not _close(calc, ev, rel=0.005):
                        out.append(ctx.mk(
                            "high",
                            f"企业整体价值勾稽不符：{sname}!{cell_addr(rows['ev'], c)} 列示 "
                            f"{fmt_num(ev)}，按「企业整体价值 = 经营性资产价值 + 溢余资产价值 + "
                            f"非经营性资产 - 非经营性负债 - 付息负债」计算为 {fmt_num(calc)}"
                            f"（{' '.join(detail)}）。",
                            location=f"{sname}!{cell_addr(rows['ev'], c)}",
                            file=ctx._block_file(bid),
                            snippet=f"企业整体价值 {fmt_num(ev)}",
                            suggestion="核对溢余资产、非经营性资产/负债与付息负债是否完整纳入汇总。",
                        ))
                        break
            if rows["eq"] and rows["ev"] and rows["debt"]:
                for c in range(2, sm.ncols + 1):
                    eq, ev, debt = (sm.num(rows["eq"], c), sm.num(rows["ev"], c),
                                    sm.num(rows["debt"], c))
                    if None in (eq, ev, debt) or abs(ev) < 1e-9:
                        continue
                    if not _close(ev - debt, eq, rel=0.005):
                        out.append(ctx.mk(
                            "high",
                            f"股东全部权益价值勾稽不符：{sname}!{cell_addr(rows['eq'], c)} 列示 "
                            f"{fmt_num(eq)}，按「企业整体价值 {fmt_num(ev)} - 付息负债 "
                            f"{fmt_num(debt)}」应为 {fmt_num(ev - debt)}。",
                            location=f"{sname}!{cell_addr(rows['eq'], c)}",
                            file=ctx._block_file(bid),
                            snippet=f"股东全部权益价值 {fmt_num(eq)}",
                            suggestion="核对付息负债金额及其在整体价值中的扣除口径。",
                        ))
                        break
            if len(out) >= MAX_FINDINGS_PER_RULE:
                return out
    return out


def rule_discount_rate(ctx: RuleContext) -> List[Finding]:
    """折现率应用一致性：各表 / 各年度折现率应一致。"""
    out: List[Finding] = []
    per_block: Dict[str, Dict[float, List[str]]] = {}
    for bid, model in ctx.excel_blocks():
        per_block.setdefault(bid, {})
        for sname, sm in ctx.sheets_of(bid):
            for r in _find_all_rows(sm, K_RATE_DISCOUNT):
                for c, v in sm.row_nums(r):
                    p = _pct(v)
                    if p is None or not (0 < p < 0.5):
                        continue
                    per_block[bid].setdefault(round(p, 6), []).append(f"{sname}!{cell_addr(r, c)}")
    for bid, groups in per_block.items():
        if len(groups) <= 1:
            continue
        vals = sorted(groups.keys())
        main = max(groups, key=lambda k: len(groups[k]))
        others = [v for v in vals if v != main]
        cells = "; ".join(groups[main][:3])
        outs = "; ".join(groups[others[0]][:3]) if others else ""
        out.append(ctx.mk(
            "medium",
            f"折现率应用不一致：共出现 {len(vals)} 个不同取值，主流为 "
            f"{main * 100:.4f}%（如 {cells}），另有 {others[0] * 100:.4f}%（如 {outs}）。"
            "各表、各年度应使用同一折现率，如确有差异须在说明中交代。",
            location=groups[others[0]][0] if others else cells,
            file=ctx._block_file(bid),
            snippet=f"折现率取值：{', '.join(f'{v * 100:.4f}%' for v in vals[:6])}",
            suggestion="统一折现率口径；若为预测期与永续期分别取值，请在评估说明中明确说明。",
        ))
    return out[:MAX_FINDINGS_PER_RULE]


def rule_perpetuity(ctx: RuleContext) -> List[Finding]:
    """永续期数据一致性：永续期现金流量应与预测期末年相关数据衔接。"""
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        for sname, sm in ctx.sheets_of(bid):
            hrow = _header_row(sm)
            if not hrow:
                continue
            perp_cols = [c for c in range(2, sm.ncols + 1)
                         if "永续" in str(sm.val(hrow, c) or "")]
            if not perp_cols:
                continue
            for r in range(hrow + 1, sm.nrows + 1):
                label = "".join(str(sm.val(r, c) or "") for c in range(1, min(sm.ncols, 3) + 1))
                if any(k in label for k in K_NET_CASH) or "净现金流" in label:
                    prev = None
                    for c in range(perp_cols[0] - 1, 1, -1):
                        v = sm.num(r, c)
                        if v is not None:
                            prev = v
                            break
                    pv = sm.num(r, perp_cols[0])
                    if prev is None or pv is None or abs(prev) < 1e-9:
                        continue
                    ratio = pv / prev
                    if not (0.8 <= ratio <= 1.5):
                        out.append(ctx.mk(
                            "medium",
                            f"永续期数据与预测期末年差异较大：{sname}「{label[:20]}」永续期值 "
                            f"{fmt_num(pv)}，预测期末年 {fmt_num(prev)}，比值 {ratio:.2%}，"
                            "请确认永续增长假设是否已在说明中交代。",
                            location=f"{sname}!{cell_addr(r, perp_cols[0])}",
                            file=ctx._block_file(bid),
                            snippet=f"末年 {fmt_num(prev)} → 永续 {fmt_num(pv)}",
                            suggestion="若永续期增长率与预测期不同，应在评估说明中说明假设依据。",
                        ))
                        break
            if len(out) >= MAX_FINDINGS_PER_RULE:
                return out
    return out


def rule_forecast_link(ctx: RuleContext) -> List[Finding]:
    """预测期与历史期衔接：预测首年相对历史末年是否存在未解释的跳跃。"""
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        for sname, sm in ctx.sheets_of(bid):
            hrow = _header_row(sm)
            if not hrow:
                continue
            hist_cols, fc_cols = [], []
            for c in range(2, sm.ncols + 1):
                head = str(sm.val(hrow, c) or "")
                if any(k in head for k in ("历史", "基准年", "报告期", "已审")):
                    hist_cols.append(c)
                elif any(k in head for k in ("预测", "预计", "未来")):
                    fc_cols.append(c)
            if not hist_cols or not fc_cols:
                continue
            last_hist, first_fc = hist_cols[-1], fc_cols[0]
            for r in range(hrow + 1, sm.nrows + 1):
                label = "".join(str(sm.val(r, c) or "") for c in range(1, min(sm.ncols, 3) + 1))
                if not any(k in label for k in ("营业收入", "主营业务收入", "销售收入")):
                    continue
                a, b = sm.num(r, last_hist), sm.num(r, first_fc)
                if a is None or b is None or abs(a) < 1e-9:
                    continue
                ratio = b / a
                if ratio > 1.5 or ratio < 0.6:
                    out.append(ctx.mk(
                        "medium",
                        f"预测期与历史期衔接存在跳跃：{sname}「{label[:20]}」历史末年 {fmt_num(a)}，"
                        f"预测首年 {fmt_num(b)}，变动 {(ratio - 1) * 100:.1f}%，"
                        "请确认是否已在说明中解释。",
                        location=f"{sname}!{cell_addr(r, first_fc)}",
                        file=ctx._block_file(bid),
                        snippet=f"历史 {fmt_num(a)} → 预测 {fmt_num(b)}",
                        suggestion="如因产能释放、新增业务等原因导致跳跃，应在说明中量化说明。",
                    ))
                    break
            if len(out) >= MAX_FINDINGS_PER_RULE:
                return out
    return out


def rule_revenue(ctx: RuleContext) -> List[Finding]:
    """营业收入预测：销量 × 单价 = 收入金额（自动识别单位换算）。

    兼容两种表格布局：
      A. 纵向布局（行 = 科目：销量行 / 单价行 / 收入行，列 = 年度）
      B. 横向布局（行 = 产品，列 = 销量 / 单价 / 收入）——优先识别
    """
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        for sname, sm in ctx.sheets_of(bid):
            # 布局 B：列标题命中
            c_qty = _find_col(sm, ["销量", "销售量", "销售数量", "数量", "产量"], scan=6)
            c_price = _find_col(sm, ["单价", "销售单价", "平均单价", "单位价格"], scan=6)
            c_amt = _find_col(sm, ["收入金额", "销售收入", "营业收入", "销售额", "收入"], scan=6)
            if c_qty and c_price and c_amt:
                for r in range(1, sm.nrows + 1):
                    if _is_total_row(sm, r):
                        continue
                    q, p, amt = sm.num(r, c_qty), sm.num(r, c_price), sm.num(r, c_amt)
                    if None in (q, p, amt) or q == 0 or p == 0:
                        continue
                    prod = q * p
                    if abs(prod) < 1e-9 or _close(prod, amt, rel=0.01):
                        continue
                    ratio = amt / prod
                    if any(abs(ratio - 10 ** k) / (10 ** k) < 0.02 for k in range(-4, 6)):
                        continue
                    label = str(sm.val(r, 1) or f"第{r}行")[:30]
                    out.append(ctx.mk(
                        "medium",
                        f"收入量价不符：{sname}!{cell_addr(r, c_amt)}「{label}」收入为 "
                        f"{fmt_num(amt)}，按 销量 {fmt_num(q)} × 单价 {fmt_num(p)} = "
                        f"{fmt_num(prod)}，比值 {ratio:.4f}（非 10 的整数倍，非单位换算差异）。",
                        location=f"{sname}!{cell_addr(r, c_amt)}",
                        file=ctx._block_file(bid), block=ctx._block_name(bid),
                        snippet=f"销量 {fmt_num(q)} × 单价 {fmt_num(p)} = {fmt_num(prod)} ≠ {fmt_num(amt)}",
                        suggestion="核对销量、单价的计量单位（如吨 / 千克、元 / 万元）与收入口径是否一致。",
                    ))
                    if len(out) >= MAX_FINDINGS_PER_RULE:
                        return out
                continue

            # 布局 A：行标签命中
            r_qty = _find_row(sm, ["销量", "销售量", "数量", "产量", "销售数量"])
            r_price = _find_row(sm, ["单价", "销售单价", "平均单价", "单位价格"])
            r_amt = _find_row(sm, ["销售收入", "营业收入", "收入金额", "销售额"])
            if not (r_qty and r_price and r_amt):
                continue
            for c in range(2, sm.ncols + 1):
                q, p, amt = sm.num(r_qty, c), sm.num(r_price, c), sm.num(r_amt, c)
                if None in (q, p, amt) or q == 0 or p == 0:
                    continue
                prod = q * p
                if abs(prod) < 1e-9 or _close(prod, amt, rel=0.01):
                    continue
                # 允许常见的单位换算（10 的整数倍），否则提示
                ratio = amt / prod
                if any(abs(ratio - 10 ** k) / (10 ** k) < 0.02 for k in range(-4, 6)):
                    continue
                out.append(ctx.mk(
                    "medium",
                    f"收入量价不符：{sname}!{cell_addr(r_amt, c)} 收入为 {fmt_num(amt)}，"
                    f"按 销量 {fmt_num(q)} × 单价 {fmt_num(p)} = {fmt_num(prod)}，"
                    f"比值 {ratio:.4f}（非 10 的整数倍，非单位换算差异）。",
                    location=f"{sname}!{cell_addr(r_amt, c)}",
                    file=ctx._block_file(bid),
                    snippet=f"销量 {fmt_num(q)} × 单价 {fmt_num(p)} = {fmt_num(prod)} ≠ {fmt_num(amt)}",
                    suggestion="核对销量、单价的计量单位（如吨 / 千克、元 / 万元）与收入口径是否一致。",
                ))
                break
            if len(out) >= MAX_FINDINGS_PER_RULE:
                return out
    return out


def rule_working_capital(ctx: RuleContext) -> List[Finding]:
    """营运资金：各年度营运资金增加额 = 营运资金余额的年度变动。"""
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        for sname, sm in ctx.sheets_of(bid):
            r_bal = _find_row(sm, ["营运资金", "营运资本"])       # 余额
            r_add = _find_row(sm, ["营运资金增加", "营运资金净增加", "营运资金变动"])
            if not (r_bal and r_add):
                continue
            nums_bal = [(c, sm.num(r_bal, c)) for c in range(2, sm.ncols + 1)]
            nums_bal = [(c, v) for c, v in nums_bal if v is not None]
            for i in range(1, len(nums_bal)):
                c, v = nums_bal[i]
                prev = nums_bal[i - 1][1]
                add = sm.num(r_add, c)
                if add is None or prev is None:
                    continue
                if not _close(v - prev, add, rel=0.01):
                    out.append(ctx.mk(
                        "medium",
                        f"营运资金增加额计算不符：{sname}!{cell_addr(r_add, c)} 为 {fmt_num(add)}，"
                        f"按 期末余额 {fmt_num(v)} - 期初余额 {fmt_num(prev)} 应为 {fmt_num(v - prev)}。",
                        location=f"{sname}!{cell_addr(r_add, c)}",
                        file=ctx._block_file(bid),
                        snippet=f"{fmt_num(v)} - {fmt_num(prev)} = {fmt_num(v - prev)}",
                        suggestion="确认营运资金增加额是否按余额变动计算，首年是否以基准日余额为期初。",
                    ))
                    break
            if len(out) >= MAX_FINDINGS_PER_RULE:
                return out
    return out


def rule_discount_params(ctx: RuleContext) -> List[Finding]:
    """折现率参数：CAPM / WACC 各要素与折现率取值的勾稽。"""
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        for sname, sm in ctx.sheets_of(bid):
            r_rf = _find_row(sm, ["无风险收益率", "无风险利率", "Rf"])
            r_mrp = _find_row(sm, ["市场风险溢价", "市场溢价", "MRP", "ERP"])
            r_beta = _find_row(sm, ["贝塔", "β", "Beta", "风险系数"])
            r_rc = _find_row(sm, ["特定风险", "特有风险", "公司特有风险", "个别风险"])
            r_ke = _find_row(sm, ["权益资本成本", "股权成本", "Ke", "权益成本"])
            r_wacc = _find_row(sm, K_RATE_DISCOUNT)
            for c in range(2, sm.ncols + 1):
                rf = _pct(sm.num(r_rf, c)) if r_rf else None
                mrp = _pct(sm.num(r_mrp, c)) if r_mrp else None
                beta = sm.num(r_beta, c) if r_beta else None
                rc = _pct(sm.num(r_rc, c)) if r_rc else None
                ke = _pct(sm.num(r_ke, c)) if r_ke else None
                wacc = _pct(sm.num(r_wacc, c)) if r_wacc else None
                if None not in (rf, mrp, beta, ke):
                    calc = rf + beta * mrp + (rc or 0.0)
                    if not _close(calc, ke, rel=0.02, abs_=0.002):
                        out.append(ctx.mk(
                            "medium",
                            f"CAPM 权益成本不符：{sname}!{cell_addr(r_ke, c)} 为 "
                            f"{ke * 100:.4f}%，按 无风险收益率 {rf * 100:.4f}% + β {beta:.4f} × "
                            f"市场风险溢价 {mrp * 100:.4f}%"
                            + (f" + 特定风险 {rc * 100:.4f}%" if rc else "")
                            + f" 应为 {calc * 100:.4f}%。",
                            location=f"{sname}!{cell_addr(r_ke, c)}",
                            file=ctx._block_file(bid),
                            snippet=f"Rf {rf:.6f} + β {beta} × MRP {mrp:.6f}" + (f" + Rc {rc:.6f}" if rc else ""),
                            suggestion="核对 CAPM 各要素取值与公式，确认特定风险系数是否重复计入。",
                        ))
                        break
                if None not in (ke, wacc) and ke and wacc:
                    if wacc > ke + 1e-9:
                        out.append(ctx.mk(
                            "low",
                            f"WACC 与权益成本关系异常：{sname}!{cell_addr(r_wacc, c)} 折现率 "
                            f"{wacc * 100:.4f}% 高于权益成本 {ke * 100:.4f}%，在有息负债成本低于"
                            "权益成本的情况下 WACC 通常不应高于权益成本，请核实。",
                            location=f"{sname}!{cell_addr(r_wacc, c)}",
                            file=ctx._block_file(bid),
                            snippet=f"WACC {wacc:.6f} vs Ke {ke:.6f}",
                            suggestion="核对债务成本、资本结构与所得税率对 WACC 的影响。",
                        ))
                        break
            if len(out) >= MAX_FINDINGS_PER_RULE:
                return out
    return out


def rule_tax(ctx: RuleContext) -> List[Finding]:
    """税项与所得税：所得税费用 = 利润总额 × 适用税率（要素齐全时校验）。"""
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        for sname, sm in ctx.sheets_of(bid):
            r_tp = _find_row(sm, ["利润总额"])
            r_tax = _find_row(sm, ["所得税费用", "所得税"])
            r_rate = _find_row(sm, ["所得税率", "所得税税率", "税率"])
            if not (r_tp and r_tax and r_rate):
                continue
            for c in range(2, sm.ncols + 1):
                tp, tax, rate = sm.num(r_tp, c), sm.num(r_tax, c), _pct(sm.num(r_rate, c))
                if None in (tp, tax, rate) or tp <= 0:
                    continue
                calc = tp * rate
                if not _close(calc, tax, rel=0.02, abs_=1.0):
                    out.append(ctx.mk(
                        "medium",
                        f"所得税计算不符：{sname}!{cell_addr(r_tax, c)} 为 {fmt_num(tax)}，"
                        f"按 利润总额 {fmt_num(tp)} × 税率 {rate * 100:.2f}% 应为 {fmt_num(calc)}。",
                        location=f"{sname}!{cell_addr(r_tax, c)}",
                        file=ctx._block_file(bid),
                        snippet=f"{fmt_num(tp)} × {rate:.4f} = {fmt_num(calc)}",
                        suggestion="核对是否存在纳税调整项、亏损弥补或税收优惠，并在说明中交代。",
                    ))
                    break
            if len(out) >= MAX_FINDINGS_PER_RULE:
                return out
    return out


def rule_value_conclusion(ctx: RuleContext) -> List[Finding]:
    """价值汇总与结论：非经营性 / 溢余认定完整性与最终结论勾稽。"""
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        for sname, sm in ctx.sheets_of(bid):
            r_eq = _find_row(sm, ["股东全部权益价值", "股东权益价值"])
            r_ev = _find_row(sm, ["企业整体价值", "企业价值"])
            r_debt = _find_row(sm, ["付息负债", "有息负债"])
            r_nonop = _find_row(sm, ["非经营性资产"])
            if r_eq and r_ev and r_debt:
                for c in range(2, sm.ncols + 1):
                    eq, ev, debt = sm.num(r_eq, c), sm.num(r_ev, c), sm.num(r_debt, c)
                    if None in (eq, ev, debt) or abs(ev) < 1e-9:
                        continue
                    if not _close(ev - debt, eq, rel=0.005):
                        out.append(ctx.mk(
                            "high",
                            f"最终结论汇总不符：{sname}!{cell_addr(r_eq, c)} 股东全部权益价值 "
                            f"{fmt_num(eq)} ≠ 企业整体价值 {fmt_num(ev)} - 付息负债 "
                            f"{fmt_num(debt)} = {fmt_num(ev - debt)}。",
                            location=f"{sname}!{cell_addr(r_eq, c)}",
                            file=ctx._block_file(bid),
                            snippet=f"结论 {fmt_num(eq)}",
                            suggestion="核对付息负债口径（是否含应付票据、其他应付款中的有息部分）。",
                        ))
                        break
            if r_nonop is None and r_eq:
                out.append(ctx.mk(
                    "low",
                    f"非经营性资产 / 负债、溢余资产认定未见单独列示：{sname} 未找到「非经营性资产」"
                    "相关行，请确认是否已在测算中单独认定并在说明中交代。",
                    location=f"{sname}",
                    file=ctx._block_file(bid),
                    snippet="缺失：非经营性资产行",
                    suggestion="补充非经营性资产 / 负债与溢余资产的认定清单及金额。",
                ))
            if len(out) >= MAX_FINDINGS_PER_RULE:
                return out
    return out


def rule_appreciation(ctx: RuleContext) -> List[Finding]:
    """增值额与增值率：评估价值 - 账面价值 = 增值额；增值额 / 账面价值 = 增值率。"""
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        for sname, sm in ctx.sheets_of(bid):
            hrow = _header_row(sm, K_BOOK + K_APPR)
            if not hrow:
                continue
            c_book = _find_col(sm, K_BOOK, scan=hrow + 3)
            c_appr = _find_col(sm, K_APPR, scan=hrow + 3)
            c_incr = _find_col(sm, K_INCR, scan=hrow + 3)
            c_rate = _find_col(sm, K_RATE, scan=hrow + 3)
            if c_book is None or c_appr is None:
                continue
            for r in range(hrow + 1, sm.nrows + 1):
                if _is_total_row(sm, r):
                    continue
                bv, av = sm.num(r, c_book), sm.num(r, c_appr)
                if bv is None or av is None:
                    continue
                label = str(sm.val(r, 1) or sm.val(r, 2) or f"第{r}行")[:30]
                if c_incr:
                    inc = sm.num(r, c_incr)
                    if inc is not None and not _close(av - bv, inc):
                        out.append(ctx.mk(
                            "high",
                            f"增值额计算错误：{sname}!{cell_addr(r, c_incr)}「{label}」列示 "
                            f"{fmt_num(inc)}，按 评估价值 {fmt_num(av)} - 账面价值 {fmt_num(bv)} "
                            f"应为 {fmt_num(av - bv)}。",
                            location=f"{sname}!{cell_addr(r, c_incr)}",
                            file=ctx._block_file(bid),
                            snippet=f"{fmt_num(av)} - {fmt_num(bv)} = {fmt_num(av - bv)}",
                            suggestion="核对增值额公式，确认未遗漏减值准备等调整项。",
                        ))
                if c_rate and bv:
                    rate = sm.num(r, c_rate)
                    p = _pct(rate)
                    if p is not None:
                        expect = (av - bv) / bv
                        if not _close(expect, p, rel=0.02, abs_=0.001):
                            out.append(ctx.mk(
                                "high",
                                f"增值率计算错误：{sname}!{cell_addr(r, c_rate)}「{label}」列示 "
                                f"{p * 100:.2f}%，按 增值额 {fmt_num(av - bv)} / 账面价值 "
                                f"{fmt_num(bv)} 应为 {expect * 100:.2f}%。",
                                location=f"{sname}!{cell_addr(r, c_rate)}",
                                file=ctx._block_file(bid),
                                snippet=f"({fmt_num(av)} - {fmt_num(bv)}) / {fmt_num(bv)} = {expect:.6f}",
                                suggestion="核对增值率公式与单元格格式（百分比 / 小数）。",
                            ))
                if len(out) >= MAX_FINDINGS_PER_RULE:
                    return out
    return out


def rule_bs_link(ctx: RuleContext) -> List[Finding]:
    """资产负债表与评估明细表衔接：各科目明细表账面价值合计 = 报表对应科目。"""
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        sheets = dict(ctx.sheets_of(bid))
        detail: Dict[str, Tuple[str, int, float]] = {}
        for sname, sm in sheets.items():
            if any(k in sname for k in ("汇总", "结果", "合计", "目录", "封面")):
                continue
            c_book = _find_col(sm, K_BOOK, scan=6)
            if c_book is None:
                continue
            for start, trow in _block_bounds(sm):
                if not _is_total_row(sm, trow):
                    continue
                v = sm.num(trow, c_book)
                if v is None:
                    continue
                label = str(sm.val(trow, 1) or sname).strip()
                detail.setdefault(_norm_label(label), (sname, trow, v))
        for sname, sm in sheets.items():
            if not any(k in sname for k in ("汇总", "结果", "评估结论", "资产评估结果")):
                continue
            c_book = _find_col(sm, K_BOOK, scan=6)
            if c_book is None:
                continue
            for r in range(1, sm.nrows + 1):
                label = str(sm.val(r, 1) or "").strip()
                if not label or _is_total_row(sm, r):
                    continue
                key = _norm_label(label)
                if key not in detail:
                    continue
                s2, r2, v2 = detail[key]
                v1 = sm.num(r, c_book)
                if v1 is None or not _close(v1, v2):
                    out.append(ctx.mk(
                        "high",
                        f"明细表与汇总表账面价值不符：科目「{label}」在 {sname}!{cell_addr(r, c_book)} "
                        f"为 {fmt_num(v1)}，而明细表 {s2}!{cell_addr(r2, c_book)} 合计为 {fmt_num(v2)}。",
                        location=f"{sname}!{cell_addr(r, c_book)} ↔ {s2}!{cell_addr(r2, c_book)}",
                        file=ctx._block_file(bid),
                        snippet=f"{label}：{fmt_num(v1)} vs {fmt_num(v2)}",
                        suggestion="核对科目归集范围，确认明细表是否存在未纳入汇总的明细项。",
                    ))
                    if len(out) >= MAX_FINDINGS_PER_RULE:
                        return out
    return out


def _norm_label(label: str) -> str:
    return re.sub(r"[\s、：:（(）)合计小计总]", "", str(label or ""))


def rule_summary_rollup(ctx: RuleContext) -> List[Finding]:
    """评估结果汇总表勾稽：总资产 = 流动 + 非流动；净资产 = 总资产 - 总负债。"""
    out: List[Finding] = []
    groups = [
        ("资产评估值", ["流动资产", "非流动资产"]),
        ("负债评估值", ["流动负债", "非流动负债"]),
    ]
    for bid, model in ctx.excel_blocks():
        for sname, sm in ctx.sheets_of(bid):
            c_appr = _find_col(sm, K_APPR, scan=6) or _find_col(sm, ["评估价值"], scan=6)
            c_book = _find_col(sm, K_BOOK, scan=6)
            if c_appr is None:
                continue
            for tag, parts in groups:
                r_total = _find_row(sm, [tag, "资产总计" if "资产" in tag else "负债总计"])
                rows = {p: _find_row(sm, [p]) for p in parts}
                if r_total is None or any(v is None for v in rows.values()):
                    continue
                for c in (c_appr, c_book):
                    if c is None:
                        continue
                    tv = sm.num(r_total, c)
                    if tv is None:
                        continue
                    s = sum(sm.num(rows[p], c) or 0 for p in parts)
                    if abs(s) < 1e-9:
                        continue
                    if not _close(s, tv):
                        out.append(ctx.mk(
                            "high",
                            f"汇总表勾稽不符：{sname}!{cell_addr(r_total, c)}「{tag}」为 {fmt_num(tv)}，"
                            f"而 {' + '.join(parts)} = {fmt_num(s)}。",
                            location=f"{sname}!{cell_addr(r_total, c)}",
                            file=ctx._block_file(bid),
                            snippet=f"{tag} {fmt_num(tv)} vs {fmt_num(s)}",
                            suggestion="核对汇总表科目分类是否完整、有无重复计数。",
                        ))
            r_asset = _find_row(sm, ["资产总计", "资产评估值", "总资产"])
            r_liab = _find_row(sm, ["负债总计", "负债评估值", "总负债"])
            r_net = _find_row(sm, ["净资产", "股东全部权益", "所有者权益"])
            if r_asset and r_liab and r_net and c_appr:
                for c in (c_appr, c_book):
                    if c is None:
                        continue
                    a, l, n = sm.num(r_asset, c), sm.num(r_liab, c), sm.num(r_net, c)
                    if None in (a, l, n) or abs(a) < 1e-9:
                        continue
                    if not _close(a - l, n):
                        out.append(ctx.mk(
                            "high",
                            f"净资产勾稽不符：{sname}!{cell_addr(r_net, c)} 为 {fmt_num(n)}，"
                            f"按 总资产 {fmt_num(a)} - 总负债 {fmt_num(l)} 应为 {fmt_num(a - l)}。",
                            location=f"{sname}!{cell_addr(r_net, c)}",
                            file=ctx._block_file(bid),
                            snippet=f"{fmt_num(a)} - {fmt_num(l)} = {fmt_num(a - l)}",
                            suggestion="核对负债科目是否全部纳入汇总。",
                        ))
                        break
            if len(out) >= MAX_FINDINGS_PER_RULE:
                return out
    return out


def rule_cost_approach(ctx: RuleContext) -> List[Finding]:
    """成本法验证：评估净值 = 重置成本 × 成新率（含费率口径一致性提示）。"""
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        for sname, sm in ctx.sheets_of(bid):
            hrow = _header_row(sm, ["重置成本", "成新率", "评估"])
            if not hrow:
                continue
            c_rc = _find_col(sm, ["重置成本", "重置全价", "评估原值"], scan=hrow + 3)
            c_new = _find_col(sm, ["成新率"], scan=hrow + 3)
            c_net = _find_col(sm, ["评估净值", "评估价值"], scan=hrow + 3)
            if not (c_rc and c_new and c_net):
                continue
            bad = 0
            for r in range(hrow + 1, sm.nrows + 1):
                if _is_total_row(sm, r):
                    continue
                rc, nr, net = sm.num(r, c_rc), _pct(sm.num(r, c_new)), sm.num(r, c_net)
                if None in (rc, nr, net) or rc == 0:
                    continue
                if nr <= 0 or nr > 1.0001:
                    out.append(ctx.mk(
                        "medium",
                        f"成新率取值异常：{sname}!{cell_addr(r, c_new)} 为 "
                        f"{sm.num(r, c_new):.4f}，成新率应在 0–100% 区间内。",
                        location=f"{sname}!{cell_addr(r, c_new)}",
                        file=ctx._block_file(bid),
                        snippet=f"成新率 {sm.num(r, c_new)}",
                        suggestion="核对成新率的计算方法与单位（小数 / 百分数）。",
                    ))
                    bad += 1
                elif not _close(rc * nr, net, rel=0.01):
                    out.append(ctx.mk(
                        "high",
                        f"评估净值计算不符：{sname}!{cell_addr(r, c_net)} 为 {fmt_num(net)}，"
                        f"按 重置成本 {fmt_num(rc)} × 成新率 {nr * 100:.2f}% 应为 {fmt_num(rc * nr)}。",
                        location=f"{sname}!{cell_addr(r, c_net)}",
                        file=ctx._block_file(bid),
                        snippet=f"{fmt_num(rc)} × {nr:.4f} = {fmt_num(rc * nr)}",
                        suggestion="核对是否已扣除实体性、功能性与经济性贬值，或存在净残值处理差异。",
                    ))
                    bad += 1
                if bad >= 5 or len(out) >= MAX_FINDINGS_PER_RULE:
                    break
    return out


def rule_bad_debt(ctx: RuleContext) -> List[Finding]:
    """坏账准备：按零值评估的坏账准备，其评估价值应为 0。"""
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        for sname, sm in ctx.sheets_of(bid):
            c_appr = _find_col(sm, K_APPR, scan=6)
            if c_appr is None:
                continue
            for r in _find_all_rows(sm, ["坏账准备", "预计风险损失", "风险损失"]):
                v = sm.num(r, c_appr)
                if v is None or _close(v, 0.0, abs_=0.5):
                    continue
                out.append(ctx.mk(
                    "medium",
                    f"坏账准备评估值非零：{sname}!{cell_addr(r, c_appr)}「坏账准备 / 预计风险损失」"
                    f"评估价值为 {fmt_num(v)}。按惯例坏账准备应按零值评估，"
                    "相应风险通过应收款项预计风险损失体现。",
                    location=f"{sname}!{cell_addr(r, c_appr)}",
                    file=ctx._block_file(bid),
                    snippet=f"坏账准备评估值 {fmt_num(v)}",
                    suggestion="核对坏账准备的评估处理口径，确认是否与应收款项风险损失重复计算。",
                ))
                if len(out) >= MAX_FINDINGS_PER_RULE:
                    return out
    return out


def rule_fixed_asset(ctx: RuleContext) -> List[Finding]:
    """固定资产：成新率区间、增值 / 减值率异常（±50%）是否解释。"""
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        for sname, sm in ctx.sheets_of(bid):
            hrow = _header_row(sm, K_BOOK + K_APPR)
            if not hrow:
                continue
            c_book = _find_col(sm, K_BOOK, scan=hrow + 3)
            c_appr = _find_col(sm, K_APPR, scan=hrow + 3)
            if c_book is None or c_appr is None:
                continue
            for r in range(hrow + 1, sm.nrows + 1):
                if _is_total_row(sm, r):
                    continue
                bv, av = sm.num(r, c_book), sm.num(r, c_appr)
                if bv is None or av is None or abs(bv) < 1e-9:
                    continue
                rate = (av - bv) / abs(bv)
                if abs(rate) > 0.5:
                    label = str(sm.val(r, 1) or sm.val(r, 2) or f"第{r}行")[:30]
                    out.append(ctx.mk(
                        "medium",
                        f"评估增值 / 减值率异常：「{label}」账面价值 {fmt_num(bv)}、评估价值 "
                        f"{fmt_num(av)}，增值率 {rate * 100:.1f}%（超过 ±50%），"
                        "请确认是否已在评估说明中解释原因。",
                        location=f"{sname}!{cell_addr(r, c_appr)}",
                        file=ctx._block_file(bid),
                        snippet=f"{label}：{fmt_num(bv)} → {fmt_num(av)}（{rate * 100:.1f}%）",
                        suggestion="对单项资产增减值幅度较大的，应在说明中逐项说明原因。",
                    ))
                    if len(out) >= MAX_FINDINGS_PER_RULE:
                        return out
    return out


def _peer_names(sm: SheetModel) -> List[str]:
    """抽取可比公司名称 / 股票代码。"""
    names: List[str] = []
    for r in range(1, min(sm.nrows, 200) + 1):
        for c in range(1, min(sm.ncols, 6) + 1):
            v = sm.val(r, c)
            if not isinstance(v, str):
                continue
            s = v.strip()
            if re.fullmatch(r"\d{6}", s):
                names.append(s)
            elif 2 <= len(s) <= 30 and re.search(r"(公司|股份|集团|有限|科技|银行|证券)", s):
                names.append(s)
    # 去重保序
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def rule_peer_list(ctx: RuleContext) -> List[Finding]:
    """可比公司名单一致性：各表中公司名 / 代码应完全一致，无漏行错行。"""
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        pairs = ctx.sheets_of(bid)
        collected: Dict[str, List[str]] = {}
        for sname, sm in pairs:
            names = _peer_names(sm)
            if len(names) >= 2:
                collected[sname] = names
        if len(collected) < 2:
            continue
        base_name = max(collected, key=lambda k: len(collected[k]))
        base = set(collected[base_name])
        for sname, names in collected.items():
            if sname == base_name:
                continue
            cur = set(names)
            missing = base - cur
            extra = cur - base
            if not missing and not extra:
                continue
            detail = f"以「{base_name}」为基准（{len(base)} 家）："
            if missing:
                detail += f"「{sname}」缺少 {len(missing)} 项（如 {'、'.join(sorted(missing)[:5])}）；"
            if extra:
                detail += f"「{sname}」多出 {len(extra)} 项（如 {'、'.join(sorted(extra)[:5])}）。"
            out.append(ctx.mk(
                "high",
                "可比公司名单跨表不一致——" + detail,
                location=f"{base_name} ↔ {sname}",
                file=ctx._block_file(bid),
                snippet=f"{base_name}: {len(base)} 家 / {sname}: {len(cur)} 家",
                suggestion="统一可比公司名单的取数来源，避免不同表手工录入导致的漏行错行。",
            ))
            if len(out) >= MAX_FINDINGS_PER_RULE:
                return out
    # 与说明文本比对
    for bid, _ in ctx.doc_blocks():
        if not collected:
            break
        data = ctx.doc(bid)
        text = data.get("text", "")
        if not text:
            continue
        for sname, names in collected.items():
            miss = [n for n in names if n not in text]
            if len(miss) >= max(2, len(names) * 0.5):
                out.append(ctx.mk(
                    "medium",
                    f"说明中未列示全部可比公司：Excel「{sname}」共 {len(names)} 家，"
                    f"说明文本中未出现 {len(miss)} 家（如 {'、'.join(miss[:5])}）。",
                    location=f"说明文本 ↔ {sname}",
                    file=ctx._block_file(bid),
                    snippet="、".join(miss[:5]),
                    suggestion="在评估说明中完整列示可比公司名称、股票代码及选取理由。",
                ))
                break
    return out[:MAX_FINDINGS_PER_RULE]


def rule_market_ratio(ctx: RuleContext) -> List[Finding]:
    """价值比率勾稽：PE = 股价 / EPS（或市值 / 净利润），PB = 股价 / BPS。"""
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        for sname, sm in ctx.sheets_of(bid):
            # 布局 B：行 = 可比公司，列 = 股价 / EPS / BPS / PE / PB
            c_price = _find_col(sm, ["股价", "收盘价", "每股价格", "市价"], scan=6)
            c_eps = _find_col(sm, ["每股收益", "EPS"], scan=6)
            c_bps = _find_col(sm, ["每股净资产", "BPS"], scan=6)
            c_pe = _find_col(sm, ["市盈率", "PE"], scan=6)
            c_pb = _find_col(sm, ["市净率", "PB"], scan=6)
            if c_price and c_pe and c_eps:
                for r in range(1, sm.nrows + 1):
                    if _is_total_row(sm, r):
                        continue
                    name = str(sm.val(r, 1) or sm.val(r, 2) or f"第{r}行")[:24]
                    for cc, cb, label in ((c_pe, c_eps, "市盈率 PE"), (c_pb, c_bps, "市净率 PB")):
                        if not cb:
                            continue
                        ratio, base_v, price = sm.num(r, cc), sm.num(r, cb), sm.num(r, c_price)
                        if None in (ratio, base_v, price) or abs(base_v) < 1e-9:
                            continue
                        calc = price / base_v
                        if _close(calc, ratio, rel=0.02):
                            continue
                        out.append(ctx.mk(
                            "high",
                            f"{label} 计算不符：{sname}!{cell_addr(r, cc)}「{name}」为 "
                            f"{fmt_num(ratio)}，按 股价 {fmt_num(price)} / "
                            f"{label.split()[-1]} {fmt_num(base_v)} 应为 {fmt_num(calc)}。",
                            location=f"{sname}!{cell_addr(r, cc)}",
                            file=ctx._block_file(bid), block=ctx._block_name(bid),
                            snippet=f"{fmt_num(price)} / {fmt_num(base_v)} = {fmt_num(calc)}",
                            suggestion="核对价值比率的分子分母口径（是否采用基准日前 20/60 日均价）。",
                        ))
                        if len(out) >= MAX_FINDINGS_PER_RULE:
                            return out
                continue

            # 布局 A：行 = 科目
            r_price = _find_row(sm, ["股价", "收盘价", "每股价格", "市价"])
            r_eps = _find_row(sm, ["每股收益", "EPS"])
            r_bps = _find_row(sm, ["每股净资产", "BPS"])
            r_pe = _find_row(sm, ["市盈率", "PE"])
            r_pb = _find_row(sm, ["市净率", "PB"])
            for r_ratio, r_base, label in ((r_pe, r_eps, "市盈率 PE"), (r_pb, r_bps, "市净率 PB")):
                if not (r_ratio and r_base and r_price):
                    continue
                for c in range(2, sm.ncols + 1):
                    ratio, base_v, price = (sm.num(r_ratio, c), sm.num(r_base, c),
                                            sm.num(r_price, c))
                    if None in (ratio, base_v, price) or abs(base_v) < 1e-9:
                        continue
                    calc = price / base_v
                    if not _close(calc, ratio, rel=0.02):
                        out.append(ctx.mk(
                            "high",
                            f"{label} 计算不符：{sname}!{cell_addr(r_ratio, c)} 为 {fmt_num(ratio)}，"
                            f"按 股价 {fmt_num(price)} / {label.split()[-1]} {fmt_num(base_v)} "
                            f"应为 {fmt_num(calc)}。",
                            location=f"{sname}!{cell_addr(r_ratio, c)}",
                            file=ctx._block_file(bid), block=ctx._block_name(bid),
                            snippet=f"{fmt_num(price)} / {fmt_num(base_v)} = {fmt_num(calc)}",
                            suggestion="核对价值比率的分子分母口径（是否采用基准日前 20/60 日均价）。",
                        ))
                        break
            if len(out) >= MAX_FINDINGS_PER_RULE:
                return out
    return out


def rule_dlom(ctx: RuleContext) -> List[Finding]:
    """缺乏流动性折扣（DLOM）：取值应统一引用并在计算表中一致应用。"""
    out: List[Finding] = []
    for bid, model in ctx.excel_blocks():
        vals: Dict[float, List[str]] = {}
        for sname, sm in ctx.sheets_of(bid):
            for r in _find_all_rows(sm, ["流动性折扣", "DLOM", "缺少流动性折扣", "流动性折价"]):
                for c, v in sm.row_nums(r):
                    p = _pct(v)
                    if p is None or not (0 < p < 1):
                        continue
                    vals.setdefault(round(p, 6), []).append(f"{sname}!{cell_addr(r, c)}")
        if len(vals) > 1:
            main = max(vals, key=lambda k: len(vals[k]))
            others = [v for v in vals if v != main]
            out.append(ctx.mk(
                "medium",
                f"流动性折扣取值不统一：共 {len(vals)} 个取值，主流为 {main * 100:.2f}%"
                f"（如 {vals[main][0]}），另有 {others[0] * 100:.2f}%（如 {vals[others[0]][0]}）。"
                "DLOM 应在计算表中统一引用同一取值。",
                location=vals[others[0]][0],
                file=ctx._block_file(bid),
                snippet="、".join(f"{v * 100:.2f}%" for v in sorted(vals)[:6]),
                suggestion="统一 DLOM 取值及其确定依据（如期权定价法、同类交易统计），并在说明中交代。",
            ))
        # 折扣后价值 = 折扣前价值 ×(1 - DLOM)
        if vals:
            dlom = max(vals, key=lambda k: len(vals[k]))
            for sname, sm in ctx.sheets_of(bid):
                r_before = _find_row(sm, ["折扣前", "流动性折扣前", "修正后价值"])
                r_after = _find_row(sm, ["折扣后", "流动性折扣后", "评估值", "评估结论"])
                if not (r_before and r_after):
                    continue
                for c in range(2, sm.ncols + 1):
                    a, b = sm.num(r_before, c), sm.num(r_after, c)
                    if a is None or b is None or abs(a) < 1e-9:
                        continue
                    expect = a * (1 - dlom)
                    if not _close(expect, b, rel=0.01):
                        out.append(ctx.mk(
                            "medium",
                            f"流动性折扣应用不符：{sname}!{cell_addr(r_after, c)} 为 {fmt_num(b)}，"
                            f"按 折扣前价值 {fmt_num(a)} ×(1 - {dlom * 100:.2f}%) 应为 {fmt_num(expect)}。",
                            location=f"{sname}!{cell_addr(r_after, c)}",
                            file=ctx._block_file(bid),
                            snippet=f"{fmt_num(a)} × {1 - dlom:.4f} = {fmt_num(expect)}",
                            suggestion="核对 DLOM 的应用公式与引用单元格是否一致。",
                        ))
                        break
    return out[:MAX_FINDINGS_PER_RULE]


# ---------------------------------------------------------------------------
# 文档类规则
# ---------------------------------------------------------------------------
_NUM_RE = re.compile(r"-?\(?\d[\d,，]*\.?\d*\)?")
_MONEY_RE = re.compile(r"(-?\d[\d,，]*\.?\d*)\s*(万元|亿元|元|%)")


def _doc_numbers(text: str) -> List[Tuple[str, float, str]]:
    """抽取文档中的数值：返回 (原文片段, 数值, 单位)。"""
    out = []
    for m in _MONEY_RE.finditer(text or ""):
        try:
            v = float(m.group(1).replace(",", "").replace("，", ""))
        except ValueError:
            continue
        out.append((m.group(0), v, m.group(2)))
    return out


def _excel_match(nums: set, value: float, unit: str = "") -> bool:
    """判断数值能否在 Excel 数值集合中找到（含万元 / 亿元换算与百分比）。"""
    if not nums:
        return True
    cands = [value]
    if unit == "万元":
        cands += [value * 10000, value / 10000]
    elif unit == "亿元":
        cands += [value * 10000, value / 10000]
    elif unit == "%":
        cands += [value / 100, value * 100]
    else:
        cands += [value * 10000, value / 10000]
    for c in cands:
        r = round(c, 4)
        if r in nums:
            return True
        for n in nums:                      # 容差匹配
            if abs(n) > 1e-9 and abs(n - r) / max(abs(n), 1e-9) < 0.005:
                return True
            if abs(n - r) < 0.5:
                return True
    return False


def rule_doc_table_sum(ctx: RuleContext) -> List[Finding]:
    """文档中表格勾稽：合计行 = 明细行加总。"""
    out: List[Finding] = []
    import hashlib
    for bid, data in ctx.doc_blocks():
        for ti, rows in enumerate(data.get("tables") or []):
            if len(rows) < 3:
                continue
            # 表格级去重：说明被切为三段，同一张表会在多个块中重复出现
            tk = hashlib.md5(repr(rows).encode("utf-8", "ignore")).hexdigest()
            if tk in ctx._seen_units:
                continue
            ctx._seen_units.add(tk)
            for ri, row in enumerate(rows):
                label = (row[0] if row else "").replace(" ", "")
                if not any(k in label for k in ("合计", "总计", "小计")):
                    continue
                for ci in range(1, len(row)):
                    total = _parse_cell_num(row[ci])
                    if total is None or abs(total) < 1e-9:
                        continue
                    parts, count = 0.0, 0
                    for prev in rows[max(0, ri - 30):ri]:
                        lab = (prev[0] if prev else "").replace(" ", "")
                        if any(k in lab for k in ("合计", "总计", "小计")) or "其中" in lab:
                            continue
                        if ci < len(prev):
                            v = _parse_cell_num(prev[ci])
                            if v is not None:
                                parts += v
                                count += 1
                    if count < 2 or abs(parts) < 1e-9:
                        continue
                    if _close(parts, total, rel=0.005):
                        continue
                    out.append(ctx.mk(
                        "high",
                        f"表格合计与明细不符：第 {ti + 1} 张表「{label}」第 {ci + 1} 列合计为 "
                        f"{fmt_num(total)}，上方 {count} 个明细项加总为 {fmt_num(parts)}，"
                        f"差额 {fmt_num(total - parts)}。",
                        location=f"表格 {ti + 1} · 第 {ri + 1} 行 第 {ci + 1} 列",
                        file=ctx._block_file(bid),
                        snippet=f"{label}：{row[ci]}",
                        suggestion="核对表格合计公式或明细项是否遗漏 / 重复。",
                    ))
                    if len(out) >= MAX_FINDINGS_PER_RULE:
                        return out
    return out


def _load_dict(path: str) -> List[Tuple[str, str]]:
    """加载词典：错误写法|正确写法 / 正则模式|建议说明。"""
    rows: List[Tuple[str, str]] = []
    if not os.path.exists(path):
        return rows
    try:
        with open(path, encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line or line.startswith("#") or "|" not in line:
                    continue
                a, b = line.split("|", 1)
                if a.strip():
                    rows.append((a.strip(), b.strip()))
    except OSError:
        pass
    return rows


def _confusable_groups(path: str) -> List[Tuple[List[str], str]]:
    """加载易混词：词A|词B|辨析说明。"""
    out: List[Tuple[List[str], str]] = []
    if not os.path.exists(path):
        return out
    try:
        with open(path, encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line or line.startswith("#") or line.count("|") < 2:
                    continue
                parts = [p.strip() for p in line.split("|")]
                words = [p for p in parts[:-1] if p]
                if len(words) >= 2:
                    out.append((words, parts[-1]))
    except OSError:
        pass
    return out


def rule_terms(ctx: RuleContext) -> List[Finding]:
    """错漏字与术语规范性：复用内置词库（错别字 / 术语变体 / 易混词 / 单位）。"""
    out: List[Finding] = []
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "dictionaries")
    dicts = [
        ("错别字", os.path.join(root, "typo.txt"), "medium"),
        ("资产评估术语", os.path.join(root, "asset_terms.txt"), "medium"),
        ("单位表述", os.path.join(root, "units.txt"), "low"),
    ]
    confus = _confusable_groups(os.path.join(root, "confusable.txt"))

    for bid, data in ctx.doc_blocks():
        units = ctx.doc_units(bid)
        seen: set = set()
        for loc, text in units:
            for name, path, sev in dicts:
                for pattern, advice in _load_dict(path):
                    if pattern in seen:
                        continue
                    try:
                        m = re.search(pattern, text)
                        if not m:
                            continue
                    except re.error:
                        continue
                    seen.add(pattern)
                    hit = m.group(0) if m else pattern
                    out.append(ctx.mk(
                        sev,
                        f"{name}疑似问题：文本中出现「{hit}」，{advice}。",
                        location=loc,
                        file=ctx._block_file(bid),
                        block=ctx._block_name(bid),
                        snippet=text[:120],
                        suggestion=f"建议按规范表述修订：{advice}",
                    ))
                    if len(out) >= MAX_FINDINGS_PER_RULE:
                        return out
            for words, advice in confus:
                key = "confus:" + "|".join(words)
                if key in seen:
                    continue
                if any(w in text for w in words):
                    seen.add(key)
                    out.append(ctx.mk(
                        "low",
                        f"易混淆词提示：出现「{' / '.join(words)}」，{advice}，请结合语境确认用词。",
                        location=loc,
                        file=ctx._block_file(bid),
                        snippet=text[:120],
                        suggestion=advice,
                    ))
                    if len(out) >= MAX_FINDINGS_PER_RULE:
                        return out
    return out


# ---------------------------------------------------------------------------
# 跨文件联查规则
# ---------------------------------------------------------------------------
_LABEL_NUM_RE = re.compile(
    r"(折现率|无风险收益率|市场风险溢价|贝塔系数|特定风险系数|加权平均资本成本|"
    r"营业收入|营业成本|净利润|利润总额|增长率|永续增长率|"
    r"股东全部权益价值|企业整体价值|经营性资产价值|"
    r"应收账款|存货|固定资产|无形资产|在建工程|长期股权投资)"
    r"[^0-9\-（(]{0,15}(-?\d[\d,，]*\.?\d*)\s*(万元|亿元|元|%)?")


def rule_cross_doc_values(ctx: RuleContext) -> List[Finding]:
    """跨文件数值一致性：说明中引用的关键数值，应与 Excel 中同名科目一致。"""
    out: List[Finding] = []
    # 收集 Excel 中「标签 → 数值」索引（全块，不限归属）
    label_index: Dict[str, List[Tuple[float, str]]] = {}
    for bid, model in ctx.excel_blocks():
        for sname, sm in model.sheets.items():
            headers: Dict[int, str] = {}
            for c in range(1, sm.ncols + 1):
                for r in range(1, min(sm.nrows, 5) + 1):
                    v = sm.val(r, c)
                    if isinstance(v, str) and v.strip() and not _looks_numeric(v):
                        headers[c] = v.strip()[:40]
                        break
            for r in range(1, sm.nrows + 1):
                label = ""
                for c in range(1, min(sm.ncols, 4) + 1):
                    v = sm.val(r, c)
                    if isinstance(v, str) and v.strip() and not _looks_numeric(v):
                        label = v.strip()[:30]
                        break
                if not label:
                    continue
                for c, v in sm.row_nums(r):
                    lab = _norm_label(label)
                    if not lab:
                        continue
                    label_index.setdefault(lab, []).append((v, f"{sname}!{cell_addr(r, c)}"))
    if not label_index:
        return out

    for bid, data in ctx.doc_blocks():
        units = ctx.doc_units(bid)
        if not units:
            continue
        checked = 0
        for loc, line in units:
            for m in _LABEL_NUM_RE.finditer(line):
                label, raw, unit = m.group(1), m.group(2), m.group(3) or ""
                key = _norm_label(label)
                pool = label_index.get(key)
                if not pool:
                    continue
                try:
                    val = float(raw.replace(",", "").replace("，", ""))
                except ValueError:
                    continue
                if unit == "%":
                    val = val / 100.0
                if _excel_match({round(v, 4) for v, _ in pool}, val, unit):
                    continue
                checked += 1
                sample = pool[0]
                out.append(ctx.mk(
                    "high",
                    f"跨文件数值不一致：说明中「{label}」为 {raw}{unit}，"
                    f"而 Excel 中同名科目的取值为 {fmt_num(sample[0])}（{sample[1]}），"
                    "两者不匹配（已考虑万元 / 亿元换算）。",
                    location=f"{loc} ↔ {sample[1]}",
                    file=ctx._block_file(bid),
                    block=ctx._block_name(bid),
                    snippet=m.group(0)[:80],
                    suggestion="核对说明引用是否取自测算底稿，或说明单位（万元 / 元）与 Excel 是否一致。",
                ))
                if len(out) >= MAX_FINDINGS_PER_RULE:
                    return out
            if checked > 200:
                break
    return out


_CONCLUSION_KEYS = ["股东全部权益价值", "股东权益价值", "评估结论", "净资产评估值",
                    "评估价值", "企业整体价值"]


def _excel_conclusions(ctx: RuleContext) -> List[Tuple[float, str]]:
    """抽取 Excel 中的最终结论数值（跨全部 Excel 块，不限于节点 inputs）。"""
    out: List[Tuple[float, str]] = []
    seen: set = set()
    for bid in ctx.blocks:
        path = (ctx.paths.get(bid) or "").lower()
        if not path.endswith((".xlsx", ".xlsm")):
            continue
        model = ctx.model(bid)
        if not model:
            continue
        for sname, sm in model.sheets.items():
            for r in _find_all_rows(sm, _CONCLUSION_KEYS):
                nums = sm.row_nums(r)
                if not nums:
                    continue
                c, v = nums[-1]
                label = str(sm.val(r, 1) or "")[:20]
                key = (round(v, 4), sname, c)
                if key in seen:
                    continue
                seen.add(key)
                out.append((v, f"{sname}!{cell_addr(r, c)}（{label}）"))
    return out


def rule_conclusion_match(ctx: RuleContext) -> List[Finding]:
    """评估结论一致性：报告 / 说明披露的结论数值应在 Excel 中找到对应值。"""
    out: List[Finding] = []
    pool = _excel_conclusions(ctx)
    if not pool:
        return out
    nums = {round(v, 4) for v, _ in pool}
    for bid, data in ctx.doc_blocks():
        units = ctx.doc_units(bid)
        for loc, line in units:
            for key in _CONCLUSION_KEYS:
                if key not in line:
                    continue
                for m in re.finditer(
                        re.escape(key) + r"[^0-9\-（(]{0,20}(-?\d[\d,，]*\.?\d*)\s*(万元|亿元|元)?",
                        line):
                    try:
                        val = float(m.group(1).replace(",", "").replace("，", ""))
                    except ValueError:
                        continue
                    unit = m.group(2) or ""
                    if _excel_match(nums, val, unit):
                        continue
                    sample = pool[0]
                    out.append(ctx.mk(
                        "high",
                        f"评估结论数值不一致：文档披露「{key}」为 {m.group(1)}{unit}，"
                        f"而 Excel 测算底稿中的结论为 {fmt_num(sample[0])}（{sample[1]}），"
                        "未找到匹配值（已考虑万元 / 亿元换算）。",
                        location=f"{loc} ↔ {sample[1]}",
                        file=ctx._block_file(bid),
                        block=ctx._block_name(bid),
                        snippet=m.group(0)[:100],
                        suggestion="核对披露口径（取整 / 单位 / 含少数股东权益与否）与底稿是否一致。",
                    ))
                    if len(out) >= MAX_FINDINGS_PER_RULE:
                        return out
                break
    return out


def rule_summary_match(ctx: RuleContext) -> List[Finding]:
    """分类汇总一致性：说明中各科目分类合计值 vs Excel 对应分类汇总。"""
    out: List[Finding] = []
    # 收集 Excel 科目 → 数值
    idx: Dict[str, List[Tuple[float, str]]] = {}
    for bid, model in ctx.excel_blocks():
        for sname, sm in model.sheets.items():
            c_appr = _find_col(sm, K_APPR, scan=6)
            c_book = _find_col(sm, K_BOOK, scan=6)
            for r in range(1, min(sm.nrows, 400) + 1):
                label = ""
                for c in range(1, min(sm.ncols, 3) + 1):
                    v = sm.val(r, c)
                    if isinstance(v, str) and v.strip() and not _looks_numeric(v):
                        label = v.strip()[:30]
                        break
                if not label or _is_total_row(sm, r):
                    continue
                key = _norm_label(label)
                if not key:
                    continue
                for col in (c_appr, c_book):
                    if col:
                        v = sm.num(r, col)
                        if v is not None:
                            idx.setdefault(key, []).append((v, f"{sname}!{cell_addr(r, col)}"))
    if not idx:
        return out
    for bid, data in ctx.doc_blocks():
        for loc, line in ctx.doc_units(bid):
            for m in re.finditer(
                    r"(流动资产|非流动资产|固定资产|无形资产|存货|应收账款|在建工程|"
                    r"长期股权投资|流动负债|非流动负债|负债合计|资产总计|净资产)"
                    r"[^0-9\-（(]{0,15}(\d[\d,，]*\.?\d*)\s*(万元|亿元|元)?", line):
                key = _norm_label(m.group(1))
                pool = idx.get(key)
                if not pool:
                    continue
                try:
                    val = float(m.group(2).replace(",", "").replace("，", ""))
                except ValueError:
                    continue
                if _excel_match({round(v, 4) for v, _ in pool}, val, m.group(3) or ""):
                    continue
                sample = pool[0]
                out.append(ctx.mk(
                    "medium",
                    f"分类汇总不一致：说明中「{m.group(1)}」为 {m.group(2)}{m.group(3) or ''}，"
                    f"Excel 中对应科目为 {fmt_num(sample[0])}（{sample[1]}）。",
                    location=f"{loc} ↔ {sample[1]}",
                    file=ctx._block_file(bid),
                    block=ctx._block_name(bid),
                    snippet=m.group(0)[:100],
                    suggestion="核对科目归集口径与单位，确认说明数据引自汇总表。",
                ))
                if len(out) >= MAX_FINDINGS_PER_RULE:
                    return out
    return out


def rule_orphan_value(ctx: RuleContext) -> List[Finding]:
    """说明中的数据无 Excel 来源：说明出现的金额在底稿中查不到对应值。"""
    out: List[Finding] = []
    nums = ctx.excel_nums()
    if not nums:
        return out
    for bid, data in ctx.doc_blocks():
        units = ctx.doc_units(bid)
        if not units:
            continue
        orphans: List[str] = []
        for loc, line in units:
            if any(k in line for k in ("号", "编号", "文号", "电话", "传真", "邮编")):
                continue
            for m in _MONEY_RE.finditer(line):
                raw, val, unit = m.group(0), 0.0, m.group(2)
                try:
                    val = float(m.group(1).replace(",", "").replace("，", ""))
                except ValueError:
                    continue
                if unit == "%" or abs(val) < 100:
                    continue
                if _excel_match(nums, val, unit):
                    continue
                orphans.append(raw)
                if len(orphans) >= 12:
                    break
            if len(orphans) >= 12:
                break
        if len(orphans) >= 3:
            out.append(ctx.mk(
                "medium",
                f"说明中部分数值在 Excel 底稿中未找到对应值：共发现 {len(orphans)} 处"
                f"（如 {'、'.join(orphans[:8])}）。请确认这些数据确有来源，"
                "或说明其测算依据。",
                location="说明文本",
                file=ctx._block_file(bid),
                block=ctx._block_name(bid),
                snippet="、".join(orphans[:8]),
                suggestion="为说明中引用的每个数值标注来源表号；确属外部数据的，应注明出处。",
            ))
    return out


def rule_table_ref(ctx: RuleContext) -> List[Finding]:
    """说明中引用的表号应与 Excel 实际表名 / 编号对应。"""
    out: List[Finding] = []
    # Excel 中是否存在「带编号」的表名
    numbered: Dict[str, str] = {}
    for bid, model in ctx.excel_blocks():
        for sname in model.order:
            m = re.search(r"(\d+(?:[.\-]\d+)*)", sname)
            if m:
                numbered[m.group(1)] = sname
    if not numbered:
        return out
    for bid, data in ctx.doc_blocks():
        text = data.get("text") or ""
        if not text:
            continue
        miss: List[str] = []
        for m in re.finditer(r"(?:见)?表\s*(\d+(?:[.\-]\d+)+)", text):
            no = m.group(1)
            if no not in numbered and not any(k.startswith(no) for k in numbered):
                if no not in miss:
                    miss.append(no)
        if miss:
            out.append(ctx.mk(
                "medium",
                f"说明引用了 {len(miss)} 个在 Excel 中不存在的表号（如 表 {miss[0]}）。"
                f"Excel 现有编号表：{'、'.join(sorted(numbered)[:8])}。"
                "请核对表号引用是否与底稿一致。",
                location="说明文本",
                file=ctx._block_file(bid),
                snippet="、".join(miss[:8]),
                suggestion="统一表号编制规则，说明与底稿交叉引用时同步更新表号。",
            ))
    return out


def rule_unexplained_value(ctx: RuleContext) -> List[Finding]:
    """Excel 中的异常评估值，在说明中找不到对应解释。"""
    out: List[Finding] = []
    doc_text = ""
    for bid, _ in ctx.doc_blocks():
        doc_text += "\n" + (ctx.doc(bid).get("text") or "")
    if not doc_text.strip():
        return out
    for bid, model in ctx.excel_blocks():
        for sname, sm in ctx.sheets_of(bid):
            hrow = _header_row(sm, K_BOOK + K_APPR)
            if not hrow:
                continue
            c_book = _find_col(sm, K_BOOK, scan=hrow + 3)
            c_appr = _find_col(sm, K_APPR, scan=hrow + 3)
            if c_book is None or c_appr is None:
                continue
            for r in range(hrow + 1, sm.nrows + 1):
                if _is_total_row(sm, r):
                    continue
                bv, av = sm.num(r, c_book), sm.num(r, c_appr)
                if bv is None or av is None or abs(bv) < 1e-9:
                    continue
                rate = (av - bv) / abs(bv)
                if abs(rate) <= 0.5:
                    continue
                label = str(sm.val(r, 1) or sm.val(r, 2) or "").strip()
                if not label or _norm_label(label) not in _norm_label(doc_text):
                    out.append(ctx.mk(
                        "medium",
                        f"Excel 中「{label or '第' + str(r) + '行'}」评估增值 / 减值率 "
                        f"{rate * 100:.1f}%（{fmt_num(bv)} → {fmt_num(av)}），"
                        "在评估说明中未找到对应科目的解释。",
                        location=f"{sname}!{cell_addr(r, c_appr)}",
                        file=ctx._block_file(bid),
                        snippet=f"{label}：{fmt_num(bv)} → {fmt_num(av)}",
                        suggestion="对增减值幅度较大的科目，应在评估说明中逐项说明原因。",
                    ))
                    if len(out) >= MAX_FINDINGS_PER_RULE:
                        return out
    return out


# ---------------------------------------------------------------------------
# 规则注册表
# ---------------------------------------------------------------------------
RULE_FUNCS: Dict[str, Callable[[RuleContext], List[Finding]]] = {
    "sum_rows": rule_sum_rows,
    "rowcol_sum": rule_rowcol_sum,
    "formula_col": rule_formula_col,
    "cross_sheet": rule_cross_sheet,
    "balance_sheet": rule_balance_sheet,
    "cashflow": rule_cashflow,
    "value_rollup": rule_value_rollup,
    "discount_rate": rule_discount_rate,
    "perpetuity": rule_perpetuity,
    "forecast_link": rule_forecast_link,
    "revenue": rule_revenue,
    "working_capital": rule_working_capital,
    "discount_params": rule_discount_params,
    "tax": rule_tax,
    "value_conclusion": rule_value_conclusion,
    "appreciation": rule_appreciation,
    "bs_link": rule_bs_link,
    "summary_rollup": rule_summary_rollup,
    "cost_approach": rule_cost_approach,
    "bad_debt": rule_bad_debt,
    "fixed_asset": rule_fixed_asset,
    "peer_list": rule_peer_list,
    "market_ratio": rule_market_ratio,
    "dlom": rule_dlom,
    "conclusion_match": rule_conclusion_match,
    "cross_doc_values": rule_cross_doc_values,
    "summary_match": rule_summary_match,
    "orphan_value": rule_orphan_value,
    "table_ref": rule_table_ref,
    "unexplained_value": rule_unexplained_value,
    "doc_table_sum": rule_doc_table_sum,
    "terms": rule_terms,
}


def run_rule(rule_key: str, ctx: RuleContext) -> List[Finding]:
    """安全执行单条规则：异常降级为一条提示，不中断流水线。"""
    fn = RULE_FUNCS.get(rule_key)
    if not fn:
        return []
    try:
        return list(fn(ctx)) or []
    except Exception as exc:  # noqa: BLE001 - 单规则失败不影响整体
        return [ctx.mk("low", f"规则「{rule_key}」执行异常，已跳过：{exc}",
                       location="-", snippet="", suggestion="可忽略或反馈该规则的执行异常信息。")]
