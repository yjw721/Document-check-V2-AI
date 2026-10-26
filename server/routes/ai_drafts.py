# -*- coding: utf-8 -*-
"""统一数据层：AI 生成产出草稿箱（列表 / 详情 / 采纳 / 丢弃 / 统一统计）。

产出先落草稿箱（pending），经人工确认「采纳」后并入正式规则库 / 词库，
随即作用于检测链路；全程写入操作日志，供报告与操作记录溯源。
"""
from __future__ import annotations

import re
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from checkers import datastore
from checkers.custom_rules import load_custom_rules, save_custom_rules
from checkers.custom_rules import gen_id as gen_rule_id
from checkers.wordbank import load_wordbanks, save_wordbanks
from checkers.wordbank import gen_id as gen_wb_id
from checkers.rag.permissions import require as _require_cap

# 与 AI 生成同权限：核验操作员与管理员可用
router = APIRouter(dependencies=[_require_cap("ai.use")])

_VALID_SEVERITY = ("high", "medium", "low")
_VALID_MODE = ("keyword", "regex")


def _pattern_ok(mode: str, pattern: str) -> bool:
    """校验规则 pattern：非空、长度合理；regex 模式须可编译且非灾难性回溯。"""
    p = (pattern or "").strip()
    if not p or len(p) > 500:
        return False
    if mode != "regex":
        return True
    try:
        re.compile(p)
    except re.error:
        return False
    return not re.search(r"\([^()]*[+*?]\)[+*?{]", p)


def _merge_into_libraries(draft: Dict[str, Any]) -> Dict[str, int]:
    """把草稿中的规则 / 词条并入正式库，返回并入数量统计。"""
    from checkers import activity as rule_activity

    added_rules = added_entries = 0
    src = str(draft.get("source") or "")
    label = str(draft.get("source_label") or "AI生成")

    # ---- 规则 ----
    rules = [r for r in (draft.get("rules") or []) if isinstance(r, dict)]
    if rules:
        data = load_custom_rules()
        groups = data.setdefault("groups", [])
        grp: Dict[str, Any] = {
            "id": gen_rule_id("g"),
            "name": f"AI生成·{label}·{draft.get('created_at') or ''}",
            "category": "expression",
            "scope": "all",
            "enabled": True,
            "rules": [],
        }
        for r in rules:
            mode = str(r.get("match_mode") or "keyword").strip().lower()
            if mode not in _VALID_MODE:
                mode = "keyword"
            pattern = str(r.get("pattern") or "").strip()
            if not _pattern_ok(mode, pattern):
                continue
            sev = str(r.get("severity") or "medium").strip().lower()
            if sev not in _VALID_SEVERITY:
                sev = "medium"
            grp["rules"].append({
                "id": gen_rule_id("r"),
                "name": str(r.get("name") or "AI生成规则").strip(),
                "enabled": True,
                "match_mode": mode,
                "pattern": pattern,
                "severity": sev,
                "tag": str(r.get("tag") or "").strip(),
                "suggestion": str(r.get("suggestion") or "").strip(),
                "source": src,
                "source_label": label,
            })
        if grp["rules"]:
            groups.append(grp)
            if save_custom_rules(data):
                added_rules = len(grp["rules"])

    # ---- 词库 ----
    wbs = [w for w in (draft.get("wordbanks") or []) if isinstance(w, dict)]
    if wbs:
        data = load_wordbanks()
        groups = data.setdefault("groups", [])
        merged = 0
        for w in wbs:
            entries = [e for e in (w.get("entries") or []) if isinstance(e, dict)]
            entries = [e for e in entries if str(e.get("keyword") or "").strip()]
            if not entries:
                continue
            groups.append({
                "id": gen_wb_id("wb"),
                "name": str(w.get("name") or "AI生成词库").strip(),
                "module": "word",
                "scope": "all",
                "enabled": True,
                "entries": [{
                    "id": gen_wb_id("e"),
                    "keyword": str(e.get("keyword") or "").strip(),
                    "tag": str(e.get("tag") or "").strip(),
                    "suggestion": str(e.get("suggestion") or "").strip(),
                    "enabled": True,
                    "source": src,
                    "source_label": label,
                } for e in entries],
            })
            merged += len(entries)
        if merged:
            if save_wordbanks(data):
                added_entries = merged

    if added_rules or added_entries:
        rule_activity.log_activity(
            module="ai_draft", action="accept",
            detail=f"采纳草稿 {draft.get('id')}（来源 {label}）："
                   f"规则 +{added_rules} 条、词条 +{added_entries} 条",
            ok=True)
    return {"rules": added_rules, "entries": added_entries}


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------
@router.get("/api/ai/drafts")
def api_drafts_list(status: str = "", source: str = ""):
    """草稿列表（元信息）。status / source 可选过滤。"""
    return {"ok": True, "items": datastore.list_drafts(status=status, source=source)}


@router.get("/api/data/stats")
def api_data_stats():
    """统一数据层统计（草稿数 / 状态分布 / 来源分布 / 已采纳条目数）。"""
    return {"ok": True, "stats": datastore.store_stats()}


@router.get("/api/ai/drafts/{did}")
def api_draft_get(did: str):
    """草稿详情（含完整 rules / wordbanks）。"""
    d = datastore.get_draft(did)
    if not d:
        raise HTTPException(404, "草稿不存在")
    return {"ok": True, "draft": d}


@router.post("/api/ai/drafts/{did}/accept")
def api_draft_accept(did: str):
    """采纳草稿：并入正式规则库 / 词库，随即作用于检测链路。"""
    d = datastore.get_draft(did)
    if not d:
        raise HTTPException(404, "草稿不存在")
    if d.get("status") == datastore.DRAFT_ACCEPTED:
        return {"ok": True, "message": "该草稿已采纳过", "added": {"rules": 0, "entries": 0}}
    added = _merge_into_libraries(d)
    datastore.set_draft_status(did, datastore.DRAFT_ACCEPTED,
                               note=f"入库规则 {added['rules']} 条 / 词条 {added['entries']} 条")
    if not added["rules"] and not added["entries"]:
        return {"ok": True, "message": "无可入库的有效条目（可能全部被校验过滤）", "added": added}
    return {"ok": True, "message": f"已入库：规则 +{added['rules']} 条、词条 +{added['entries']} 条",
            "added": added}


@router.post("/api/ai/drafts/{did}/reject")
def api_draft_reject(did: str):
    """丢弃草稿（不入库）。"""
    d = datastore.get_draft(did)
    if not d:
        raise HTTPException(404, "草稿不存在")
    from checkers import activity as rule_activity
    datastore.set_draft_status(did, datastore.DRAFT_REJECTED, note="人工丢弃")
    rule_activity.log_activity(
        module="ai_draft", action="reject",
        detail=f"丢弃草稿 {did}（来源 {d.get('source_label') or ''}）", ok=True)
    return {"ok": True, "message": "已丢弃"}
