# -*- coding: utf-8 -*-
"""
自定义词库引擎（本地离线、独立模块、零联网）
============================================
职责：
    1. 从本地 config/wordbanks.json 读取用户自建词库（WordBankGroup / Entry）
    2. 词条匹配：关键词 / 短语子串精确匹配（与 §4 数据契约一致）
    3. 按 scope（all / word / excel / pdf）过滤生效文档类型
    4. 命中产出 category=wordbank 的 Issue，独立分类、完整携带 §2 位置信息

数据契约（与 §0 统一）：
    WordBankGroup = { id, name, module(format_regex|text_word),
                      scope(all|word|excel|pdf), enabled, entries:[Entry] }
    Entry = { id, keyword, tag, suggestion, enabled }

保密说明：纯本地 JSON 读写 + 子串匹配，无任何网络行为。
"""

from __future__ import annotations

import csv
import io
import json
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

from checkers.base import Issue, clip

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
WORDBANKS_PATH = os.path.join(CONFIG_DIR, "wordbanks.json")

VALID_SCOPE = {"all", "word", "excel", "pdf"}
VALID_MODULE = {"format_regex", "text_word"}

_SUSPECT_HINT = "（自定义词库命中，仅供人工复核）"


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------
def _empty_data() -> Dict[str, Any]:
    return {
        "meta": {"name": "自定义词库", "offline_only": True,
                 "desc": "用户在界面【词库管理】中自建的离线词库，本地持久化。"},
        "groups": [],
    }


def load_wordbanks(path: str = WORDBANKS_PATH) -> Dict[str, Any]:
    """读取词库；文件缺失或损坏时返回空结构。"""
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if not isinstance(data, dict):
            return _empty_data()
        data.setdefault("groups", [])
        return data
    except (OSError, ValueError, json.JSONDecodeError):
        return _empty_data()


def save_wordbanks(data: Dict[str, Any], path: str = WORDBANKS_PATH) -> bool:
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


def gen_id(prefix: str = "wb") -> str:
    return prefix + uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# 批量导入解析（CSV / TXT）
# ---------------------------------------------------------------------------
def parse_entries_import(raw_text: str) -> List[Dict[str, Any]]:
    """
    解析批量导入的词条文本，返回 Entry 字典列表。

    支持两种格式：
        - CSV：首行表头固定 keyword,tag,suggestion（可省略 tag/suggestion）
        - TXT：每行一条，支持「keyword|tag|suggestion」或纯「keyword」
    """
    raw_text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    stripped = raw_text.strip()
    if not stripped:
        return []

    # 启发式判断是否为 CSV（含逗号且首行像表头）
    first_line = stripped.split("\n", 1)[0].strip().lower()
    is_csv = ("," in first_line) and first_line.replace(" ", "").startswith("keyword")

    out: List[Dict[str, Any]] = []
    if is_csv:
        try:
            reader = csv.DictReader(io.StringIO(stripped))
            for row in reader:
                kw = (row.get("keyword") or "").strip()
                if not kw:
                    continue
                out.append(_mk_entry(kw, (row.get("tag") or "").strip(),
                                     (row.get("suggestion") or "").strip()))
        except Exception:  # noqa: BLE001
            pass
    else:
        for line in stripped.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            kw = parts[0]
            if not kw:
                continue
            tag = parts[1] if len(parts) > 1 else ""
            sug = parts[2] if len(parts) > 2 else ""
            out.append(_mk_entry(kw, tag, sug))
    return out


def _mk_entry(keyword: str, tag: str, suggestion: str) -> Dict[str, Any]:
    return {
        "id": gen_id("e"),
        "keyword": keyword,
        "tag": tag,
        "suggestion": suggestion,
        "enabled": True,
    }


# ---------------------------------------------------------------------------
# 词库引擎
# ---------------------------------------------------------------------------
class WordBankEngine:
    """对传入文本做词库匹配，命中追加 Issue 到共享列表。"""

    def __init__(self, issues_out: List[Issue], limit: int,
                 data: Optional[Dict[str, Any]] = None) -> None:
        self.issues = issues_out
        self._limit = limit
        self._data = data if data is not None else load_wordbanks()
        self._groups = self._data.get("groups", [])

    def _scope_ok(self, scope: str, doc_type: str) -> bool:
        return scope == "all" or scope == doc_type

    def check_text(self, doc_type: str, location: str, text: str) -> None:
        """对单段文本跑全部生效的词库分组。"""
        if not text or len(self.issues) >= self._limit:
            return
        for grp in self._groups:
            if not grp.get("enabled", True):
                continue
            if not self._scope_ok(grp.get("scope", "all"), doc_type):
                continue
            for entry in grp.get("entries", []):
                if not entry.get("enabled", True):
                    continue
                if len(self.issues) >= self._limit:
                    return
                kw = entry.get("keyword", "")
                if kw and kw in text:
                    self._add(grp, entry, location, text)

    def _add(self, grp: Dict[str, Any], entry: Dict[str, Any],
             location: str, text: str) -> None:
        kw = entry.get("keyword", "")
        self.issues.append(Issue(
            rule_key=f"wb_{grp.get('id', 'g')}_{entry.get('id', 'e')}",
            rule_title=kw,
            severity="low",
            location=location,
            detail=f"命中自定义词库「{grp.get('name', '')}」：匹配到「{kw}」{_SUSPECT_HINT}",
            snippet=clip(text, 120),
            suggestion=entry.get("suggestion", "请按词库建议人工复核修正。"),
            category="wordbank",
            source="wordbank",
            group=grp.get("name", ""),
            tag=entry.get("tag", ""),
        ))
