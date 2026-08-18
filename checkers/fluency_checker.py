# -*- coding: utf-8 -*-
"""
语句通顺度智能检测引擎（纯本地，离线正则）
=================================================
检测维度（6 类，与 config/rules.json 的 fluency 段一一对应）：
    fl_logic       单句逻辑断裂（关联从句悬空、重复归因等）
    fl_incomplete  句子成分残缺（介词短语 / 状语孤立成句）
    fl_order       语序混乱（关联词错位、时序颠倒）
    fl_repeat      重复赘述（同一句内成分重复堆叠）
    fl_conj        关联词搭配错误（既…而且、无论…就 等）
    fl_mixed       句式杂糅（叠床架屋式重复表意）

机制：
    1. 模式词库：dictionaries/fluency.txt，行格式「正则|说明|灵敏度|等级」，
       并按「# ==== 中文名（fl_xxx） ====」分区注释归属规则键
    2. 灵敏度阈值（后台「全局运行参数 - 语句通顺检测灵敏度」）：
       loose=仅低误报核心模式 / normal=常用模式 / strict=全量激进
       灵敏度档位决定加载哪些模式，无需改动模式文件即可放宽 / 收紧判定
    3. 按【句】匹配：先以 。！？；!?; 切句，逐句执行模式（支持 ^ 句首 / $ 句尾锚定）
    4. 提示等级：模式自带 low=建议优化（弱提醒）/ high=强制复核；
       未标注时取规则 severity；整类开关与规则 severity 由 rules.json 控制

保密说明：纯本地文件读写与正则匹配，无任何网络行为。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from checkers.base import Issue, clip

_DICT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dictionaries")
_FLUENCY_PATH = os.path.join(_DICT_DIR, "fluency.txt")

# 灵敏度档位（后台设置值 -> 本引擎判定）
_SENS_LEVELS = {"loose": 0, "normal": 1, "strict": 2}

# 6 类检测维度（与 rules.json fluency 段规则键一致）
RULE_KEYS = ("fl_logic", "fl_incomplete", "fl_order", "fl_repeat", "fl_conj", "fl_mixed")

# 分区注释：如「# ===== 单句逻辑断裂（fl_logic） =====」（支持全角 / 半角括号）
_SEC_RE = re.compile(r"[（(](\w+)[)）]")

_SENT_SPLIT = re.compile(r"[。！？；!?;]+")


def _split_sentences(text: str) -> List[str]:
    """按句末标点切句；过滤空白与过短片段。"""
    out = []
    for seg in _SENT_SPLIT.split(text):
        s = seg.strip()
        if len(s) >= 4:
            out.append(s)
    return out


def _load_fluency(path: str = _FLUENCY_PATH) -> List[Tuple[str, str, str, str, str]]:
    """
    加载通顺度模式词库。
    返回 [(规则键, 正则源码, 说明, 灵敏度, 等级), ...]；非法行 / 无归属行自动跳过。
    """
    items: List[Tuple[str, str, str, str, str]] = []
    cur_rule = ""
    try:
        with open(path, "r", encoding="utf-8") as fp:
            lines = fp.readlines()
    except OSError:
        return items
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("#"):
            m = _SEC_RE.search(s)
            if m:
                cur_rule = m.group(1)
            continue
        parts = [p.strip() for p in s.rsplit("|", 3)]
        if len(parts) < 2 or not parts[0] or cur_rule not in RULE_KEYS:
            continue
        pat_src, note = parts[0], parts[1]
        sens = parts[2] if len(parts) > 2 and parts[2] in _SENS_LEVELS else "normal"
        sev = parts[3] if len(parts) > 3 and parts[3] in ("low", "medium", "high") else ""
        try:
            re.compile(pat_src)
        except re.error:
            continue
        items.append((cur_rule, pat_src, note, sens, sev))
    return items


# 模块级缓存：词库文件仅在首次使用时加载（与 TextNormChecker 一致）
_CACHE: Optional[List[Tuple[str, str, str, str, str]]] = None


def _fluency_items() -> List[Tuple[str, str, str, str, str]]:
    global _CACHE
    if _CACHE is None:
        _CACHE = _load_fluency()
    return _CACHE


class FluencyChecker:
    """语句通顺度检测器：与 TextNormChecker 同接口（check_text 追加核查条目）。"""

    def __init__(self, config: Any, issues_out: List[Issue], limit: int,
                 sensitivity: str = "normal") -> None:
        self.cfg = config
        self.issues = issues_out
        self._limit = limit
        self._sensitivity = sensitivity if sensitivity in _SENS_LEVELS else "normal"
        self._items = _fluency_items()

    # ---------------- 内部工具 ----------------
    def _on(self, rule_key: str) -> bool:
        return bool(self.cfg.is_enabled("fluency", rule_key))

    def _add(self, rule_key: str, location: str, detail: str,
             snippet: str, severity: Optional[str] = None) -> None:
        if len(self.issues) >= self._limit:
            return
        sev = severity or self.cfg.severity("fluency", rule_key)
        self.issues.append(Issue(
            rule_key=rule_key,
            rule_title=self.cfg.title("fluency", rule_key),
            severity=sev,
            location=location,
            detail=detail,
            snippet=clip(snippet, 120),
            suggestion=self.cfg.suggestion("fluency", rule_key),
        ))

    # ---------------- 检测入口 ----------------
    def check_text(self, location: str, text: str) -> None:
        """对一段文本执行全部启用的通顺度检测（按句匹配，可叠加多类命中）。"""
        if not any(self._on(k) for k in RULE_KEYS):
            return
        sens_lv = _SENS_LEVELS[self._sensitivity]
        for sent in _split_sentences(text):
            for rule_key, pat_src, note, sens, sev in self._items:
                if _SENS_LEVELS.get(sens, 1) > sens_lv:
                    continue  # 灵敏度阈值过滤：收紧 / 放宽判定标准
                if not self._on(rule_key):
                    continue
                try:
                    m = re.search(pat_src, sent)
                except re.error:
                    continue
                if m:
                    self._add(rule_key, location,
                              f"疑似语句不通顺：命中「{clip(m.group(0), 40)}」，{note}",
                              sent, sev or None)
                    break  # 同一句命中该模式后不再重复