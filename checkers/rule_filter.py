# -*- coding: utf-8 -*-
"""
规则 / 词条入库前置校验（强制，所有渠道统一执行）
==================================================
AI 生成、范本解析、本地自学习、手动保存的规则与词条，在写入规则库 /
渲染到前端列表之前，必须通过以下校验，任一条不满足即丢弃该条：
    1. 核心逻辑：匹配式文本不能与【建议替换目标文本】完全相同
    2. 非空校验：匹配式不能为空 / 全空白；建议字段不能为空
    3. 内容长度：匹配式不能为纯通用无意义单字（避免大量误命中）
    4. 业务逻辑：规则用途为错误检测，匹配式必须是【错误写法、错别字、
       不规范表述、禁用语句】；标准正确术语不能作为匹配触发条件，
       标准术语只能放在【建议替换为】栏
    5. 重复校验：匹配式 + 模式与库内已有规则完全一致 → 去重拒绝
被过滤项记录到 logs/rule_filter.log（时间 / 渠道 / 规则内容 / 丢弃原因），
不写入规则库、不推送到前端规则列表。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from checkers.custom_rules import load_custom_rules
from checkers.wordbank import load_wordbanks

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "logs")
LOG_PATH = os.path.join(LOG_DIR, "rule_filter.log")

# 纯通用无意义单字（虚词 / 代词 / 数词 / 方位 / 高频助动词等），
# 作为匹配式几乎必然大量误命中，直接拒绝
NOISE_SINGLE_CHARS = set(
    "的 了 是 在 和 与 或 及 为 以 于 之 其 而 且 但 也 就 都 很 把 被 对 "
    "从 向 往 由 因 当 这 那 此 你 我 他 她 它 们 一 不 有 无 上 下 中 内 "
    "外 前 后 时 年 月 日 等 并 若 如 则 即 按 据 依 随 应 须 需 可 能 会 "
    "要 请 让 用 各 每 某 本 该 既 亦 又 再 才 只 将 已 尚 多 少 数 几 与 "
    "及 或 而 亦 何".split()
)

_logger = logging.getLogger("rule_filter")
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        _h = logging.FileHandler(LOG_PATH, encoding="utf-8")
        _h.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        _logger.addHandler(_h)
    except OSError:
        pass


def _log_drop(channel: str, kind: str, item: Dict[str, Any], reason: str) -> None:
    """后台留存丢弃日志：记录规则内容与丢弃原因。"""
    try:
        name = item.get("name") or item.get("keyword") or item.get("id") or ""
        pat = item.get("pattern") or item.get("keyword") or ""
        sug = item.get("suggestion") or ""
        _logger.info("【%s】丢弃%s | 名称=%s | 匹配式=%s | 建议=%s | 原因=%s",
                     channel, kind, name, pat, sug, reason)
    except Exception:  # noqa: BLE001 - 日志失败不影响主流程
        pass


def build_existing() -> Dict[str, Any]:
    """汇总库内已有规则的 (模式, 匹配式) 集合、全部建议集合、词条关键词集合。

    suggestions：库内规则建议 + 词库词条建议 —— 出现在建议栏的均为标准正确术语，
    若被新规则当作匹配式，即违反「标准术语不能作为匹配触发条件」。
    """
    patterns: set = set()
    suggestions: set = set()
    keywords: set = set()
    for g in load_custom_rules().get("groups", []):
        for r in g.get("rules", []):
            mode = str(r.get("match_mode") or "keyword")
            pat = str(r.get("pattern") or "").strip()
            if pat:
                patterns.add((mode, pat))
            sug = str(r.get("suggestion") or "").strip()
            if sug:
                suggestions.add(sug)
    for g in load_wordbanks().get("groups", []):
        for e in g.get("entries", []):
            kw = str(e.get("keyword") or "").strip()
            if kw:
                keywords.add(kw)
            sug = str(e.get("suggestion") or "").strip()
            if sug:
                suggestions.add(sug)
    return {"patterns": patterns, "suggestions": suggestions, "keywords": keywords}


def _is_noise_single(pattern: str) -> bool:
    if len(pattern) != 1:
        return False
    return pattern in NOISE_SINGLE_CHARS


def validate_rule(rule: Dict[str, Any], existing: Dict[str, Any]) -> Tuple[bool, str]:
    """校验单条校验规则。返回 (是否通过, 未通过原因)。"""
    pattern = str(rule.get("pattern") or "").strip()
    suggestion = str(rule.get("suggestion") or "").strip()
    mode = str(rule.get("match_mode") or "keyword")
    if not pattern:
        return False, "匹配式为空或全为空白字符"
    if not suggestion:
        return False, "建议替换字段为空"
    if pattern == suggestion:
        return False, "匹配式与建议替换完全相同，为无效规则"
    if _is_noise_single(pattern):
        return False, "匹配式为纯通用无意义单字，易大量误命中"
    if mode == "regex":
        try:
            re.compile(pattern)
        except re.error:
            return False, "正则表达式无法编译"
    if pattern in existing.get("suggestions", set()):
        return False, "标准正确术语不能作为匹配触发条件（标准术语只能放在建议替换栏）"
    if (mode, pattern) in existing.get("patterns", set()):
        return False, "匹配式与模式和库内已有规则完全一致，重复规则拒绝入库"
    return True, ""


def validate_entry(entry: Dict[str, Any], existing: Dict[str, Any],
                   check_standard: bool = True) -> Tuple[bool, str]:
    """校验单条词库词条。返回 (是否通过, 未通过原因)。"""
    keyword = str(entry.get("keyword") or "").strip()
    suggestion = str(entry.get("suggestion") or "").strip()
    if not keyword:
        return False, "关键词为空或全为空白字符"
    if not suggestion:
        return False, "建议替换字段为空"
    if keyword == suggestion:
        return False, "关键词与建议替换完全相同，为无效词条"
    if _is_noise_single(keyword):
        return False, "关键词为纯通用无意义单字，易大量误命中"
    if check_standard and keyword in existing.get("suggestions", set()):
        return False, "标准正确术语不能作为匹配触发条件（标准术语只能放在建议替换栏）"
    if keyword in existing.get("keywords", set()):
        return False, "关键词与库内已有词条重复，拒绝重复入库"
    return True, ""


def filter_rules(rules: List[Dict[str, Any]], existing: Dict[str, Any],
                 channel: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """批量校验规则。返回 (保留列表, 丢弃明细)。丢弃明细含 reason 供前端提示。"""
    kept: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for r in rules:
        ok, reason = validate_rule(r, existing)
        if ok:
            kept.append(r)
        else:
            _log_drop(channel, "规则", r, reason)
            rejected.append({"name": r.get("name") or "",
                             "pattern": r.get("pattern") or "",
                             "reason": reason})
    return kept, rejected


def filter_entries(entries: List[Dict[str, Any]], existing: Dict[str, Any],
                   channel: str, check_standard: bool = True) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """批量校验词条。返回 (保留列表, 丢弃明细)。"""
    kept: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for e in entries:
        ok, reason = validate_entry(e, existing, check_standard=check_standard)
        if ok:
            kept.append(e)
        else:
            _log_drop(channel, "词条", e, reason)
            rejected.append({"name": e.get("name") or e.get("keyword") or "",
                             "pattern": e.get("keyword") or "",
                             "reason": reason})
    return kept, rejected


def _existing_ids() -> set:
    """库内已有实体的 id 集合（已有数据不做入库校验，仅校验新增项）。"""
    ids: set = set()
    for g in load_custom_rules().get("groups", []):
        ids.update(r.get("id") for r in g.get("rules", []) if r.get("id"))
    for g in load_wordbanks().get("groups", []):
        ids.update(e.get("id") for e in g.get("entries", []) if e.get("id"))
    return ids


def filter_rules_payload(payload: Dict[str, Any], channel: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """对整个自定义规则载荷（全部分组）过滤，返回 (过滤后载荷, 丢弃明细)。

    仅校验新增规则（id 不在库内）；库内已有规则保持原样，避免保存时静默改动历史数据。
    """
    existing = build_existing()
    known_ids = _existing_ids()
    dropped: List[Dict[str, Any]] = []
    groups = payload.get("groups") or []
    for g in groups:
        if not isinstance(g, dict):
            continue
        rules = g.get("rules") or []
        kept, rejected = filter_rules_new(rules, existing, channel, known_ids)
        g["rules"] = kept
        dropped.extend(rejected)
        existing["patterns"].update((str(r.get("match_mode") or "keyword"),
                                     str(r.get("pattern") or "").strip())
                                    for r in kept if str(r.get("pattern") or "").strip())
    return payload, dropped


def filter_rules_new(rules: List[Dict[str, Any]], existing: Dict[str, Any],
                     channel: str, known_ids: Optional[set] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """校验规则列表：库内已有 id 的规则放行（保持原样），仅校验新增项。"""
    known_ids = known_ids if known_ids is not None else _existing_ids()
    kept: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for r in rules:
        if r.get("id") in known_ids:
            kept.append(r)
            continue
        ok, reason = validate_rule(r, existing)
        if ok:
            kept.append(r)
        else:
            _log_drop(channel, "规则", r, reason)
            rejected.append({"name": r.get("name") or "",
                             "pattern": r.get("pattern") or "",
                             "reason": reason})
    return kept, rejected


def filter_entries_payload(payload: Dict[str, Any], channel: str,
                           check_standard: bool = True) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """对整个词库载荷（全部分组）过滤，返回 (过滤后载荷, 丢弃明细)。

    仅校验新增词条（id 不在库内）；库内已有词条保持原样。
    """
    existing = build_existing()
    known_ids = _existing_ids()
    dropped: List[Dict[str, Any]] = []
    groups = payload.get("groups") or []
    for g in groups:
        if not isinstance(g, dict):
            continue
        entries = g.get("entries") or []
        kept, rejected = filter_entries_new(entries, existing, channel, known_ids,
                                            check_standard=check_standard)
        g["entries"] = kept
        dropped.extend(rejected)
        existing["keywords"].update(str(e.get("keyword") or "").strip()
                                    for e in kept if str(e.get("keyword") or "").strip())
    return payload, dropped


def filter_entries_new(entries: List[Dict[str, Any]], existing: Dict[str, Any],
                       channel: str, known_ids: Optional[set] = None,
                       check_standard: bool = True) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """校验词条列表：库内已有 id 的词条放行，仅校验新增项。"""
    known_ids = known_ids if known_ids is not None else _existing_ids()
    kept: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for e in entries:
        if e.get("id") in known_ids:
            kept.append(e)
            continue
        ok, reason = validate_entry(e, existing, check_standard=check_standard)
        if ok:
            kept.append(e)
        else:
            _log_drop(channel, "词条", e, reason)
            rejected.append({"name": e.get("name") or e.get("keyword") or "",
                             "pattern": e.get("keyword") or "",
                             "reason": reason})
    return kept, rejected


def filter_generated(result: Dict[str, Any], channel: str) -> Dict[str, Any]:
    """过滤 AI 生成结果（wordbanks + rules），返回统计信息。

    返回: {"generated_rules","filtered_rules","accepted_rules",
           "generated_entries","filtered_entries","accepted_entries",
           "rejected":[{name,pattern,reason}]}
    """
    existing = build_existing()
    wbs, rls = result.get("wordbanks") or [], result.get("rules") or []
    gen_rules = len(rls)
    gen_entries = sum(len(w.get("entries") or []) for w in wbs)

    kept_rules, rejected_rules = [], []
    for r in rls:
        ok, reason = validate_rule(r, existing)
        if ok:
            kept_rules.append(r)
            existing["patterns"].add((str(r.get("match_mode") or "keyword"),
                                      str(r.get("pattern") or "").strip()))
        else:
            _log_drop(channel, "规则", r, reason)
            rejected_rules.append({"name": r.get("name") or "",
                                   "pattern": r.get("pattern") or "",
                                   "reason": reason})
    result["rules"] = kept_rules

    kept_entries, rejected_entries = [], []
    for w in wbs:
        kept_w: List[Dict[str, Any]] = []
        for e in (w.get("entries") or []):
            ok, reason = validate_entry(e, existing, check_standard=True)
            if ok:
                kept_w.append(e)
                existing["keywords"].add(str(e.get("keyword") or "").strip())
            else:
                _log_drop(channel, "词条", e, reason)
                rejected_entries.append({"name": e.get("name") or e.get("keyword") or "",
                                         "pattern": e.get("keyword") or "",
                                         "reason": reason})
        w["entries"] = kept_w
        kept_entries.extend(kept_w)
    result["wordbanks"] = [w for w in wbs if (w.get("entries") or [])]
    return {
        "generated_rules": gen_rules, "filtered_rules": len(rejected_rules),
        "accepted_rules": len(kept_rules),
        "generated_entries": gen_entries, "filtered_entries": len(rejected_entries),
        "accepted_entries": len(kept_entries),
        "rejected": rejected_rules + rejected_entries,
    }