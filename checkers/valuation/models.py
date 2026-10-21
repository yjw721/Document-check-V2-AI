# -*- coding: utf-8 -*-
"""
资产评估报告智能核查 —— 公共数据模型
=========================================================================
对应需求《AI核查报告需求点提取》：
    5 份输入附件 → 4 个阶段闭环 → 9 大核查节点（96 项核查事项）→ 汇总复核输出

本模块定义：
    - 5 个附件槽位（SLOTS）
    - 7 个数据块（BLOCKS，切分产物）
    - Finding：单条核查发现（可追溯到节点 / 核查事项 / 来源提示词）
    - NodeRun：单个核查节点的执行结果

保密说明：纯本地计算，无任何网络访问。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 阶段一 · 5 个输入附件槽位
# ---------------------------------------------------------------------------
SLOTS: Dict[str, Dict[str, Any]] = {
    "report": {
        "key": "report",
        "no": 1,
        "name": "评估报告（全文）",
        "kind": "doc",
        "need": True,
        "usage": "节点①单查、节点⑥与说明联查",
        "ext": (".docx",),
    },
    "statement": {
        "key": "statement",
        "no": 2,
        "name": "评估说明",
        "kind": "doc",
        "need": True,
        "usage": "切分为收益法/成本法/市场法 3 段后使用",
        "ext": (".docx",),
    },
    "income": {
        "key": "income",
        "no": 3,
        "name": "收益法测算表",
        "kind": "excel",
        "need": False,
        "usage": "切分为收益法底稿块，节点③⑦使用",
        "ext": (".xlsx", ".xlsm"),
    },
    "cost": {
        "key": "cost",
        "no": 4,
        "name": "成本法测算表",
        "kind": "excel",
        "need": False,
        "usage": "切分为成本法底稿块，节点④⑧使用",
        "ext": (".xlsx", ".xlsm"),
    },
    "market": {
        "key": "market",
        "no": 5,
        "name": "市场法测算表",
        "kind": "excel",
        "need": False,
        "usage": "切分为市场法底稿块，节点⑤⑨使用",
        "ext": (".xlsx", ".xlsm"),
    },
}

# ---------------------------------------------------------------------------
# 阶段二 · 切分后的 7 个数据块
# ---------------------------------------------------------------------------
BLOCKS: Dict[str, Dict[str, Any]] = {
    "B1": {"id": "B1", "name": "收益法底稿块", "kind": "excel", "from": "income",
           "serve": "③ 收益法表核查 · ⑦ 说明↔收益法联查"},
    "B2": {"id": "B2", "name": "成本法底稿块", "kind": "excel", "from": "cost",
           "serve": "④ 成本法表核查 · ⑧ 说明↔成本法联查"},
    "B3": {"id": "B3", "name": "市场法底稿块", "kind": "excel", "from": "market",
           "serve": "⑤ 市场法表核查 · ⑨ 说明↔市场法联查"},
    "B4": {"id": "B4", "name": "说明_收益法", "kind": "doc", "from": "statement",
           "serve": "② 评估说明核查 · ⑦ 联查"},
    "B5": {"id": "B5", "name": "说明_成本法", "kind": "doc", "from": "statement",
           "serve": "② 评估说明核查 · ⑧ 联查"},
    "B6": {"id": "B6", "name": "说明_市场法", "kind": "doc", "from": "statement",
           "serve": "② 评估说明核查 · ⑨ 联查"},
    "B7": {"id": "B7", "name": "评估报告全文", "kind": "doc", "from": "report",
           "serve": "① 评估报告核查 · ⑥ 报告↔说明联查"},
}

# 方法归属（切分与说明分段共用）
METHODS = ("income", "cost", "market")
METHOD_CN = {"income": "收益法", "cost": "成本法", "market": "市场法"}

# 阶段定义（与需求文档四阶段一致）
STAGES = [
    {"id": "S1", "no": "Ⅰ", "name": "文件上传与预处理", "color": "#0288D1"},
    {"id": "S2", "no": "Ⅱ", "name": "文件智能切分", "color": "#006064"},
    {"id": "S3", "no": "Ⅲ", "name": "9 大核查节点", "color": "#2E7D32"},
    {"id": "S4", "no": "Ⅳ", "name": "汇总与复核", "color": "#C62828"},
]


def new_id(prefix: str = "f") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Finding:
    """单条核查发现：可追溯到节点 / 核查事项 / 来源提示词。"""

    node_id: str = ""            # N1 .. N9
    node_no: str = ""            # ① .. ⑨
    node_name: str = ""          # 评估报告核查
    item_id: str = ""            # N1-01
    item_no: int = 0
    item_title: str = ""         # 错漏字与术语规范性
    source: str = ""             # 来源提示词，如「提示词1-第二步」
    engine: str = "rule"         # rule / ai / hybrid
    severity: str = "medium"     # high / medium / low
    file: str = ""               # 所属文件（源文件名，用于跨节点去重归组）
    block: str = ""              # 所属数据块（B1–B7 名称）
    location: str = ""           # 具体位置：Sheet!C12 / 第 12 页 · 第 3 段
    detail: str = ""             # 问题说明（含计算过程）
    snippet: str = ""            # 原文片段
    suggestion: str = ""         # 整改建议
    state: str = "normal"        # normal / ignored / checked
    duplicate_of: str = ""       # 去重：指向保留的主发现 id
    merged_count: int = 1        # 去重合并的条数
    origin: str = ""             # 产出来源：rule / ai / review
    id: str = field(default_factory=lambda: new_id("f"))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def dedup_key(self) -> str:
        """去重键：同一问题（同文件 + 同位置 + 同问题性质）。

        不含 item_id —— 需求要求「同一问题被多个节点同时发现时自动去重合并」，
        例如同一处数值不一致被③④⑤ 或 ⑥⑦⑧⑨ 重复命中，最终只保留一条。
        """
        return "|".join([
            self.file,
            _norm_loc(self.location),
            _norm_detail(self.detail)[:120],
        ])


def _norm_loc(loc: str) -> str:
    return "".join(str(loc or "").split()).lower()


def _norm_detail(text: str) -> str:
    """归一化描述文本用于去重比对：去掉数字精度差异与空白。"""
    import re
    s = str(text or "")
    s = re.sub(r"\d+(?:\.\d+)?%?", "", s)   # 去掉数值，聚焦「问题性质」
    return re.sub(r"\s+", "", s)


@dataclass
class NodeRun:
    """单个核查节点的执行结果。"""

    node_id: str
    node_no: str
    node_name: str
    node_type: str = "single"       # single / cross
    inputs: List[str] = field(default_factory=list)
    status: str = "pending"         # pending / running / done / skipped / failed
    item_total: int = 0
    item_done: int = 0
    rule_count: int = 0             # 规则引擎命中
    ai_count: int = 0               # AI 命中
    findings: List[Finding] = field(default_factory=list)
    message: str = ""
    seconds: float = 0.0

    def to_dict(self, slim: bool = True) -> Dict[str, Any]:
        """slim=True 时 findings 只保留摘要（避免与最终清单重复、控制载荷）。"""
        d = asdict(self)
        d.pop("findings", None)
        if slim:
            d["finding_brief"] = [{
                "id": f.id, "severity": f.severity, "origin": f.origin,
                "location": f.location, "detail": (f.detail or "")[:160],
                "item_id": f.item_id, "item_title": f.item_title,
            } for f in self.findings]
        else:
            d["findings"] = [f.to_dict() for f in self.findings]
        return d


@dataclass
class Block:
    """切分后的数据块。"""

    block_id: str                 # B1..B7
    name: str
    kind: str                     # excel / doc
    source_slot: str              # report / statement / income / cost / market
    source_file: str = ""
    sheets: List[Dict[str, Any]] = field(default_factory=list)   # excel：sheet 清单
    general_sheets: List[str] = field(default_factory=list)      # 强制附加的通用表
    text: str = ""                # doc：切分后正文
    paragraphs: List[Dict[str, Any]] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, with_text: bool = False) -> Dict[str, Any]:
        d = asdict(self)
        if not with_text:
            d.pop("text", None)
        return d
