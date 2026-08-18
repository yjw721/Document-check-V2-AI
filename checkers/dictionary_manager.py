# -*- coding: utf-8 -*-
"""
内置词库管理模块（纯本地）
=========================================
职责：
    1. 列出 dictionaries/ 下全部内置词库（对应 textnorm 各检测项）
    2. 读取 / 保存单个词库文件（原子写，白名单校验，零联网）

保密说明：仅本地文件读写，无任何网络行为。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

DICT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dictionaries")

# 词库文件 -> 对应规则键 / 展示名（新增词库文件时在此登记）
DICT_META: List[Dict[str, str]] = [
    {"file": "colloquial.txt", "rule": "tn_colloquial", "title": "口语化 / 不规范用词"},
    {"file": "redundant.txt", "rule": "tn_redundant", "title": "重复冗余词句"},
    {"file": "confusable.txt", "rule": "tn_confusable", "title": "易混淆近义词"},
    {"file": "ambiguous.txt", "rule": "tn_ambiguous", "title": "歧义句式 / 表意模糊词"},
    {"file": "typo.txt", "rule": "tn_typo", "title": "中文错别字"},
    {"file": "en_typo.txt", "rule": "tn_en_typo", "title": "英文拼写错误"},
    {"file": "abbrev.txt", "rule": "tn_abbrev", "title": "非正式简称 / 自创缩写"},
    {"file": "units.txt", "rule": "tn_units", "title": "数量 / 单位表述"},
    {"file": "grammar.txt", "rule": "tn_grammar", "title": "中英文语法错误"},
    {"file": "vocab.txt", "rule": "tn_vocab", "title": "中英文词汇搭配"},
    {"file": "asset_terms.txt", "rule": "tn_asset_terms", "title": "资产评估准则术语（2020）"},
]

_ALLOWED = {m["file"] for m in DICT_META}
_MAX_BYTES = 2 * 1024 * 1024  # 单文件保存上限 2 MB，防误操作撑爆


def _count_entries(content: str) -> int:
    """统计有效词条数：非空且非 # 注释的行。"""
    n = 0
    for ln in content.splitlines():
        s = ln.strip()
        if s and not s.startswith("#"):
            n += 1
    return n


def list_dictionaries(rule_titles: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """列出全部内置词库元信息。

    rule_titles: {规则键: 规则标题}，来自规则配置，用于展示对应检测项名称。
    """
    out: List[Dict[str, Any]] = []
    for m in DICT_META:
        name = m["file"]
        path = os.path.join(DICT_DIR, name)
        size = 0
        content = ""
        try:
            size = os.path.getsize(path)
            with open(path, "r", encoding="utf-8") as fp:
                content = fp.read()
        except OSError:
            pass
        rule_title = (rule_titles or {}).get(m["rule"], m["rule"])
        out.append({
            "file": name,
            "title": m["title"],
            "rule": m["rule"],
            "rule_title": rule_title,
            "count": _count_entries(content),
            "size": size,
        })
    return out


def read_dictionary(name: str) -> Optional[str]:
    """读取词库全文；文件不存在或名称非法返回 None。"""
    if name not in _ALLOWED:
        return None
    path = os.path.join(DICT_DIR, name)
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return fp.read()
    except OSError:
        return None


def save_dictionary(name: str, content: str) -> bool:
    """保存词库全文（原子写：tmp + os.replace）。成功返回 True。"""
    if name not in _ALLOWED:
        return False
    if len(content.encode("utf-8")) > _MAX_BYTES:
        return False
    path = os.path.join(DICT_DIR, name)
    try:
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as fp:
            fp.write(content)
        os.replace(tmp_path, path)
        return True
    except OSError:
        try:
            if os.path.exists(path + ".tmp"):
                os.remove(path + ".tmp")
        except OSError:
            pass
        return False
