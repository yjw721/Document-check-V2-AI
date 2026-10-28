# -*- coding: utf-8 -*-
"""总览 / 状态查询：概览统计、文件列表、问题列表、问题状态变更。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Header

from checkers.scanner import summarize


from server import state
from server.state import workspace as _workspace
from server.services.detection import serialize_result as _serialize_result, cache_stats as _cache_stats, rule_summary as _rule_summary

router = APIRouter()

# 概览 / 状态
# ---------------------------------------------------------------------------
@router.get("/api/overview")
def api_overview(ws: str = Header("", alias="X-Workspace")):
    state = _workspace(ws)
    summary = summarize(state["results"])
    cache = _cache_stats()
    status = [
        ("网络状态", "离线（零请求）"),
        ("已导入文件", str(len(state["results"]))),
        ("发现问题", str(int(summary["total_issues"]))),
        ("本地缓存", f"{cache['count']} 个 / {cache['size_text']}"),
        ("上次检测", (state["last_scan_time"].strftime("%m-%d %H:%M:%S")
                      if state["last_scan_time"] else "尚未执行")),
    ]
    # 最近问题（按严重度取前 10 条）
    sev_weight = {"high": 3, "medium": 2, "low": 1}
    recent = []
    for fi, res in enumerate(state["results"]):
        for ii, iss in enumerate(res.issues):
            if iss.state == "ignored":
                continue
            recent.append({
                "file_name": res.file_name,
                "file_type": res.file_type,
                "severity": iss.severity,
                "rule_title": iss.rule_title,
                "location": iss.location,
                "detail": iss.detail,
            })
    recent.sort(key=lambda x: sev_weight.get(x["severity"], 0), reverse=True)
    return {
        "summary": summary,
        "status": status,
        "cache": cache,
        "recent_issues": recent[:10],
        "rule_summary": _rule_summary(),
    }


@router.get("/api/files")
def api_files(ws: str = Header("", alias="X-Workspace")):
    return {"results": [_serialize_result(r) for r in _workspace(ws)["results"]]}


@router.get("/api/issues")
def api_issues(ws: str = Header("", alias="X-Workspace")):
    out = []
    results = _workspace(ws)["results"]
    for fi, res in enumerate(results):
        for ii, iss in enumerate(res.issues):
            d = iss.to_dict()
            d.update({"file_index": fi, "issue_index": ii, "file_name": res.file_name,
                      "file_type": res.file_type})
            out.append(d)
    return {"issues": out}


@router.post("/api/issue_state")
def api_issue_state(payload: Dict[str, Any], ws: str = Header("", alias="X-Workspace")):
    fi, ii, stt = payload.get("file_index"), payload.get("issue_index"), payload.get("state")
    try:
        _workspace(ws)["results"][fi].issues[ii].state = stt
    except Exception:
        raise HTTPException(400, "无效的条目索引")
    return {"ok": True}


# ---------------------------------------------------------------------------
