# -*- coding: utf-8 -*-
"""
自定义规则引擎（本地离线、可视化配置、零联网）
================================================
职责：
    1. 从本地 config/custom_rules.json 读取用户自建规则（RuleGroup / Rule）
    2. 自定义规则匹配：keyword=子串精确匹配，regex=本地 re 匹配
    3. 按 scope（all / word / excel / pdf）过滤生效文档类型
    4. 命中产出 category=custom_rule 的 Issue，完整携带 §2 位置信息

数据契约（与 §0 统一）：
    RuleGroup = { id, name, category(format_error|expression),
                  scope(all|word|excel|pdf), enabled, rules:[Rule] }
    Rule = { id, name, enabled, match_mode(keyword|regex), pattern,
             severity(low|medium|high), tag, suggestion }

保密说明：纯本地 JSON 读写 + re 模块计算，无任何网络行为、不调用外部语义接口。
"""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any, Dict, List, Optional

from checkers.base import Issue, clip

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
CUSTOM_RULES_PATH = os.path.join(CONFIG_DIR, "custom_rules.json")

SEVERITY_LABELS = {"high", "medium", "low"}
VALID_SCOPE = {"all", "word", "excel", "pdf"}
VALID_CATEGORY = {"format_error", "expression"}
VALID_MODE = {"keyword", "regex"}

_SUSPECT_HINT = "（自定义规则命中，仅供人工复核）"


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------
def _empty_data() -> Dict[str, Any]:
    return {
        "meta": {"name": "自定义检测规则", "offline_only": True,
                 "desc": "用户在界面【规则配置面板】中自建的规则，本地持久化。"},
        "groups": [],
    }


def load_custom_rules(path: str = CUSTOM_RULES_PATH) -> Dict[str, Any]:
    """读取自定义规则；文件缺失或损坏时返回空结构，不中断工具。"""
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if not isinstance(data, dict):
            return _empty_data()
        data.setdefault("groups", [])
        return data
    except (OSError, ValueError, json.JSONDecodeError):
        return _empty_data()


def save_custom_rules(data: Dict[str, Any], path: str = CUSTOM_RULES_PATH) -> bool:
    """写回本地 JSON（原子替换）。成功返回 True。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def gen_id(prefix: str = "g") -> str:
    """生成短随机 id。"""
    return prefix + uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# 自定义规则引擎
# ---------------------------------------------------------------------------
class CustomRuleEngine:
    """对传入文本做自定义规则匹配，命中追加 Issue 到共享列表。"""

    def __init__(self, issues_out: List[Issue], limit: int,
                 data: Optional[Dict[str, Any]] = None) -> None:
        self.issues = issues_out
        self._limit = limit
        self._data = data if data is not None else load_custom_rules()
        self._groups = self._data.get("groups", [])

    def _scope_ok(self, scope: str, doc_type: str) -> bool:
        return scope == "all" or scope == doc_type

    def _match(self, rule: Dict[str, Any], text: str) -> bool:
        mode = rule.get("match_mode", "keyword")
        pattern = rule.get("pattern", "")
        if not pattern:
            return False
        if mode == "regex":
            try:
                return re.search(pattern, text) is not None
            except re.error:
                return False
        # 默认 keyword：子串精确匹配
        return pattern in text

    def check_text(self, doc_type: str, location: str, text: str) -> None:
        """对单段文本跑全部生效的自定义规则。"""
        if not text or len(self.issues) >= self._limit:
            return
        for grp in self._groups:
            if not grp.get("enabled", True):
                continue
            if not self._scope_ok(grp.get("scope", "all"), doc_type):
                continue
            for rule in grp.get("rules", []):
                if not rule.get("enabled", True):
                    continue
                if len(self.issues) >= self._limit:
                    return
                if self._match(rule, text):
                    self._add(grp, rule, location, text)

    def _add(self, grp: Dict[str, Any], rule: Dict[str, Any],
             location: str, text: str) -> None:
        sev = rule.get("severity", "low")
        if sev not in SEVERITY_LABELS:
            sev = "low"
        detail = f"命中自定义规则「{rule.get('name', rule.get('id', ''))}」"
        pat = rule.get("pattern", "")
        if pat:
            detail += f"：匹配「{pat}」"
        detail += _SUSPECT_HINT
        self.issues.append(Issue(
            rule_key=f"cust_{grp.get('id', 'g')}_{rule.get('id', 'r')}",
            rule_title=rule.get("name", "自定义规则"),
            severity=sev,
            location=location,
            detail=detail,
            snippet=clip(text, 120),
            suggestion=rule.get("suggestion", "请按自定义规则要求人工复核修正。"),
            category="custom_rule",
            source="custom",
            group=grp.get("name", ""),
            tag=rule.get("tag", ""),
        ))
