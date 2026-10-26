# -*- coding: utf-8 -*-
"""统一本地数据层：AI 生成产出草稿箱 + 全链路来源追踪（零联网、本地 JSON 持久化）。

解决的问题
----------
1. **永久存储**：AI 规则/词库智能生成的产出原先只放在内存 `_BUILD_STATE`，
   任务数超过上限即被回收，产出会静默丢失。现在生成完成即落盘为本层草稿。
2. **可管理性**：所有产出统一登记（来源 / 状态 / 统计 / 时间），可列表、筛选、
   逐条查看与处置（采纳 / 丢弃）。
3. **与其他环节联动**：草稿经人工确认「采纳」后并入正式规则库 / 词库
   （config/custom_rules.json、config/wordbanks.json），随即作用于检测链路；
   全程写入操作日志，报告可溯源统计。

存储：config/data_store.json（原子写，损坏时读取端返回空结构，不中断工具）。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
STORE_PATH = os.path.join(CONFIG_DIR, "data_store.json")

# 并发写保护：与规则 / 词库的保存保持一致，防止互相覆盖
_STORE_LOCK = threading.Lock()

STORE_VERSION = 1

# 草稿状态
DRAFT_PENDING = "pending"      # 待确认
DRAFT_ACCEPTED = "accepted"    # 已采纳（并入正式库）
DRAFT_REJECTED = "rejected"    # 已丢弃


def _empty_store() -> Dict[str, Any]:
    return {"version": STORE_VERSION, "seq": 0, "drafts": []}


def load_store(path: str = STORE_PATH) -> Dict[str, Any]:
    """读取统一数据层；文件缺失 / 损坏时返回空结构，不抛异常。"""
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if not isinstance(data, dict):
            return _empty_store()
        data.setdefault("drafts", [])
        data.setdefault("seq", 0)
        return data
    except (OSError, ValueError, json.JSONDecodeError):
        return _empty_store()


def save_store(data: Dict[str, Any], path: str = STORE_PATH) -> bool:
    """原子写入统一数据层；成功返回 True。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with _STORE_LOCK:
            with open(tmp, "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        return True
    except OSError:
        return False


def new_id(prefix: str = "d") -> str:
    """生成短随机 id（12 hex，碰撞概率极低）。"""
    return prefix + uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# 草稿登记
# ---------------------------------------------------------------------------
def add_draft(bid: str, kind: str, result: Dict[str, Any],
              source: str = "", source_label: str = "",
              filter_stat: Optional[Dict[str, Any]] = None,
              filename: str = "", path: str = STORE_PATH) -> Optional[str]:
    """把一次生成产出持久化为草稿，返回草稿 id；写入失败返回 None。"""
    store = load_store(path)
    store["seq"] = int(store.get("seq") or 0) + 1
    did = new_id()
    rules = list(result.get("rules") or [])
    wbs = list(result.get("wordbanks") or [])
    entries = sum(len(w.get("entries") or []) for w in wbs)
    draft: Dict[str, Any] = {
        "id": did,
        "bid": bid,
        "kind": kind,
        "source": source or f"ai_{kind}",
        "source_label": source_label or "AI生成",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": DRAFT_PENDING,
        "filename": filename,
        "stats": {"rules": len(rules), "wordbanks": len(wbs), "entries": entries},
        "filter": filter_stat or {},
        "rules": rules,
        "wordbanks": wbs,
    }
    store.setdefault("drafts", []).append(draft)
    return did if save_store(store, path) else None


def list_drafts(status: str = "", source: str = "", path: str = STORE_PATH) -> List[Dict[str, Any]]:
    """列出草稿元信息（不含大载荷，保证列表轻快）；最新的在前。"""
    out: List[Dict[str, Any]] = []
    for d in (load_store(path).get("drafts") or []):
        if status and d.get("status") != status:
            continue
        if source and d.get("source") != source:
            continue
        out.append({
            "id": d.get("id"), "bid": d.get("bid"), "kind": d.get("kind"),
            "source": d.get("source"), "source_label": d.get("source_label"),
            "created_at": d.get("created_at"), "status": d.get("status"),
            "filename": d.get("filename"), "stats": d.get("stats") or {},
            "filter": d.get("filter") or {},
        })
    out.reverse()
    return out


def get_draft(did: str, path: str = STORE_PATH) -> Optional[Dict[str, Any]]:
    """取草稿完整内容（含 rules / wordbanks）。"""
    for d in (load_store(path).get("drafts") or []):
        if d.get("id") == did:
            return d
    return None


def set_draft_status(did: str, status: str, note: str = "", path: str = STORE_PATH) -> bool:
    """变更草稿状态（pending / accepted / rejected）。"""
    store = load_store(path)
    for d in store.get("drafts") or []:
        if d.get("id") == did:
            d["status"] = status
            d["handled_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            if note:
                d["note"] = note
            return save_store(store, path)
    return False


# ---------------------------------------------------------------------------
# 统一统计（可管理性 / 报告溯源）
# ---------------------------------------------------------------------------
def store_stats(path: str = STORE_PATH) -> Dict[str, Any]:
    """返回数据层总体统计：草稿数、状态分布、来源分布、已采纳条目数。"""
    store = load_store(path)
    drafts = store.get("drafts") or []
    by_status: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    accepted_rules = accepted_entries = 0
    for d in drafts:
        st = str(d.get("status") or DRAFT_PENDING)
        by_status[st] = by_status.get(st, 0) + 1
        src = str(d.get("source") or "unknown")
        by_source[src] = by_source.get(src, 0) + 1
        if st == DRAFT_ACCEPTED:
            s = d.get("stats") or {}
            accepted_rules += int(s.get("rules") or 0)
            accepted_entries += int(s.get("entries") or 0)
    return {
        "version": store.get("version", STORE_VERSION),
        "total": len(drafts),
        "by_status": by_status,
        "by_source": by_source,
        "accepted_rules": accepted_rules,
        "accepted_entries": accepted_entries,
        "path": STORE_PATH,
    }
