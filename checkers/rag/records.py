# -*- coding: utf-8 -*-
"""检索与知识库问答的「使用记录」持久化与管理。

设计目标
--------
- 零侵入：检索 / 问答接口在返回结果**之后**调用 ``add_record``，任何异常都被吞掉，
  绝不拖慢或中断主流程（与门面的「故障不传染」原则一致）。
- 本地落盘：所有记录写入 ``config/knowledge/records.json``（与 knowledge.json 同目录），
  进程内用列表缓存（最新在前），文件写采用临时文件 + 原子替换，避免半写损坏。
- 上限自保护：超过 ``MAX_RECORDS`` 自动丢弃最旧记录，防止长期运行后文件无限膨胀。

记录字段
--------
- ``id``        唯一 id（r<epoch>_<seq>）
- ``type``      "ask"（知识库问答）| "search"（仅检索诊断）
- ``ts``        epoch 秒（排序 / 区间筛选）
- ``created_at`` 本地时间 "YYYY-MM-DD HH:MM:SS"
- ``query``     用户的问题 / 检索词
- ``params``    检索参数（top_k / scope 等）
- ``summary``   列表展示用的轻量摘要（避免回传完整 result）
- ``result``    完整结果载荷（详情接口才返回，列表不回传以省流量）
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from checkers.rag.paths import KB_DIR

RECORDS_PATH = os.path.join(KB_DIR, "records.json")
_lock = threading.RLock()
MAX_RECORDS = 1000
_cache: Optional[List[Dict[str, Any]]] = None
_seq = 0


# --------------------------------------------------------------------------- #
# 内部加载 / 保存
# --------------------------------------------------------------------------- #
def _load() -> List[Dict[str, Any]]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(RECORDS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _cache = data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _cache = []
    return _cache


def _save() -> None:
    os.makedirs(KB_DIR, exist_ok=True)
    tmp = RECORDS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_cache, f, ensure_ascii=False, indent=1)
    os.replace(tmp, RECORDS_PATH)


# --------------------------------------------------------------------------- #
# 写入
# --------------------------------------------------------------------------- #
def add_record(
    rec_type: str,
    query: str,
    params: Optional[Dict[str, Any]],
    summary: Optional[Dict[str, Any]],
    result: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """追加一条记录（最新置于列表头部），返回写入后的记录；异常时返回空 dict。"""
    global _seq
    global _cache
    try:
        with _lock:
            _load()
            _seq = (_seq + 1) % 100000
            rid = "r%d_%d" % (int(time.time()), _seq)
            rec = {
                "id": rid,
                "type": rec_type if rec_type in ("ask", "search") else "search",
                "ts": int(time.time()),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "query": (query or "").strip(),
                "params": params or {},
                "summary": summary or {},
                "result": result or {},
            }
            _cache.insert(0, rec)
            if len(_cache) > MAX_RECORDS:
                _cache = _cache[:MAX_RECORDS]
            _save()
        return rec
    except Exception:  # noqa: BLE001 - 记录写入失败绝不影响主流程
        return {}


# --------------------------------------------------------------------------- #
# 读取
# --------------------------------------------------------------------------- #
def list_records(
    rec_type: Optional[str] = None,
    keyword: Optional[str] = None,
    since: Optional[int] = None,
    until: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """分页列出记录（列表视图不回传 ``result`` 以省流量），并附带整体统计。"""
    with _lock:
        _load()
        items = _cache
        if rec_type:
            items = [r for r in items if r.get("type") == rec_type]
        if keyword:
            kw = (keyword or "").strip().lower()
            if kw:
                items = [
                    r
                    for r in items
                    if (r.get("query") or "").lower().find(kw) >= 0
                    or json.dumps(r.get("summary") or {}, ensure_ascii=False).lower().find(kw) >= 0
                ]
        if since:
            try:
                items = [r for r in items if int(r.get("ts", 0)) >= int(since)]
            except (TypeError, ValueError):
                pass
        if until:
            try:
                items = [r for r in items if int(r.get("ts", 0)) <= int(until)]
            except (TypeError, ValueError):
                pass

        total = len(items)
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        page = items[offset : offset + limit]
        light = [{k: v for k, v in r.items() if k != "result"} for r in page]
    return {
        "ok": True,
        "items": light,
        "total": total,
        "limit": limit,
        "offset": offset,
        "stats": _stats_locked(),
    }


def get_record(rid: str) -> Dict[str, Any]:
    """返回完整记录（含 ``result``）；不存在返回 ok=False。"""
    with _lock:
        _load()
        for r in _cache:
            if r.get("id") == rid:
                return {"ok": True, **r}
    return {"ok": False, "message": "记录不存在或已被删除"}


def stats() -> Dict[str, Any]:
    with _lock:
        _load()
        return _stats_locked()


def _stats_locked() -> Dict[str, Any]:
    s: Dict[str, Any] = {"total": len(_cache), "ask": 0, "search": 0}
    for r in _cache:
        t = r.get("type")
        if t in ("ask", "search"):
            s[t] += 1
    if _cache:
        s["latest"] = _cache[0].get("created_at")
        s["earliest"] = _cache[-1].get("created_at")
    return s


# --------------------------------------------------------------------------- #
# 删除 / 清空
# --------------------------------------------------------------------------- #
def delete_record(rid: str) -> Dict[str, Any]:
    """删除单条记录（逐条管理）。"""
    global _cache
    with _lock:
        _load()
        before = len(_cache)
        _cache = [r for r in _cache if r.get("id") != rid]
        if len(_cache) != before:
            _save()
            return {"ok": True, "deleted": before - len(_cache)}
    return {"ok": False, "message": "记录不存在或已被删除"}


def clear_records(rec_type: Optional[str] = None) -> Dict[str, Any]:
    """清空记录：type 为空时清空全部，否则只清该类型（整体管理）。"""
    global _cache
    with _lock:
        _load()
        if rec_type:
            _cache = [r for r in _cache if r.get("type") != rec_type]
        else:
            _cache = []
        _save()
    return {"ok": True, "cleared_type": rec_type or "all"}


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #
def export_records(
    rec_type: Optional[str] = None,
    keyword: Optional[str] = None,
    fmt: str = "json",
) -> Dict[str, Any]:
    """导出筛选后的全部记录：fmt=json 返回 JSON 文本，fmt=md 返回 Markdown 文本。"""
    with _lock:
        _load()
        items = _cache
        if rec_type:
            items = [r for r in items if r.get("type") == rec_type]
        if keyword:
            kw = (keyword or "").strip().lower()
            if kw:
                items = [
                    r
                    for r in items
                    if (r.get("query") or "").lower().find(kw) >= 0
                ]
    if (fmt or "json") == "md":
        content = _to_markdown(items)
        return {"ok": True, "format": "md", "content": content, "filename": "rag-records.md"}
    return {
        "ok": True,
        "format": "json",
        "content": json.dumps(items, ensure_ascii=False, indent=2),
        "filename": "rag-records.json",
    }


def _to_markdown(items: List[Dict[str, Any]]) -> str:
    lines: List[str] = ["# 检索与问答记录导出", "", f"> 共 {len(items)} 条", ""]
    for r in items:
        kind = "知识库问答" if r.get("type") == "ask" else "检索诊断"
        lines.append(f"## [{kind}] {r.get('created_at', '')}  `{r.get('id', '')}`")
        lines.append("")
        lines.append(f"- **检索词 / 问题**：{r.get('query', '')}")
        p = r.get("params") or {}
        scope = p.get("scope") or "全库"
        lines.append(f"- **参数**：top_k={p.get('top_k', '-')} · 范围={scope}")
        s = r.get("summary") or {}
        if r.get("type") == "ask":
            lines.append(
                f"- **摘要**：可信度 {int(round((s.get('confidence') or 0) * 100))}% · "
                f"可溯源={'是' if s.get('grounded') else '否'} · 来源 {s.get('sources_count', 0)} 处"
                f" · 耗时 {s.get('elapsed', 0)}s"
            )
        else:
            lines.append(
                f"- **摘要**：命中 {s.get('hits_count', 0)} 段 · 模式 {s.get('mode', '-')} · "
                f"最佳 {s.get('top_file') or '-'}"
            )
        res = r.get("result") or {}
        if r.get("type") == "ask":
            lines.append("")
            lines.append("**回答：**")
            lines.append("")
            lines.append((res.get("answer") or "").strip() or "（无回答）")
            srcs = res.get("sources") or []
            if srcs:
                lines.append("")
                lines.append("**来源：**")
                for i, src in enumerate(srcs, 1):
                    lines.append(
                        f"{i}. {src.get('file', '')} · {src.get('section', '')}"
                    )
        else:
            hits = res.get("hits") or []
            if hits:
                lines.append("")
                lines.append("**命中片段：**")
                for h in hits[:20]:
                    lines.append(
                        f"- #{h.get('rank')} {h.get('file', '')} / {h.get('section', '')} "
                        f"· RRF {h.get('rrf')} · BM25 {h.get('bm25')}"
                        f"{(' · 向量 ' + str(h.get('cos'))) if h.get('cos') else ''}"
                    )
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)
