# -*- coding: utf-8 -*-
"""内置词库（dictionaries/*.txt）读写。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from config.config_manager import load_rules
from checkers.custom_rules import load_custom_rules, save_custom_rules
from checkers.wordbank import load_wordbanks, save_wordbanks, parse_entries_import
from checkers.dictionary_manager import list_dictionaries, read_dictionary, save_dictionary
from checkers.rule_filter import filter_rules_payload as rule_filter_rules_payload, filter_entries_payload as rule_filter_entries_payload



router = APIRouter()

# 内置词库（dictionaries/*.txt，与 textnorm 检测项一一对应）
# ---------------------------------------------------------------------------
def _rule_titles() -> Dict[str, str]:
    cfg = load_rules()
    out: Dict[str, str] = {}
    for kind in ("word", "excel", "textnorm"):
        for key, rule in cfg.rule_items(kind):
            if isinstance(rule, dict):
                out[key] = str(rule.get("title", key))
    return out


@router.get("/api/dictionaries")
def api_dictionaries():
    return {"files": list_dictionaries(_rule_titles())}


@router.get("/api/dictionaries/{name}")
def api_dictionary_get(name: str):
    content = read_dictionary(name)
    if content is None:
        raise HTTPException(404, "词库不存在或名称非法")
    return {"name": name, "content": content}


@router.post("/api/dictionaries/{name}")
def api_dictionary_save(name: str, payload: Dict[str, Any]):
    content = payload.get("content")
    if not isinstance(content, str):
        raise HTTPException(400, "content 必须为字符串")
    if not save_dictionary(name, content):
        raise HTTPException(400, "词库保存失败（名称非法 / 内容超限 / 写入错误）")
    return {"ok": True}


@router.get("/api/custom_rules")
def api_custom_rules():
    return load_custom_rules()


@router.post("/api/custom_rules")
def api_save_custom_rules(payload: Dict[str, Any]):
    """保存自定义规则：入库前置校验（所有渠道统一），无效规则过滤并记录日志。"""
    cleaned, dropped = rule_filter_rules_payload(payload, channel="manual_save")
    if not save_custom_rules(cleaned):
        raise HTTPException(500, "自定义规则保存失败")
    return {"ok": True, "filtered_rules": len(dropped), "rejected": dropped}


@router.get("/api/wordbanks")
def api_wordbanks():
    return load_wordbanks()


@router.post("/api/wordbanks")
def api_save_wordbanks(payload: Dict[str, Any]):
    """保存自定义词库：入库前置校验，无效词条过滤并记录日志。"""
    cleaned, dropped = rule_filter_entries_payload(payload, channel="manual_save")
    if not save_wordbanks(cleaned):
        raise HTTPException(500, "词库保存失败")
    return {"ok": True, "filtered_entries": len(dropped), "rejected": dropped}


@router.post("/api/wordbanks/import")
def api_wordbanks_import(payload: Dict[str, Any]):
    """批量解析词条文本（CSV/TXT），返回解析后的 Entry 列表。"""
    raw = payload.get("text", "")
    return {"entries": parse_entries_import(raw)}


# ---------------------------------------------------------------------------
