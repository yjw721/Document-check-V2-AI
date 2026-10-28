# -*- coding: utf-8 -*-
"""检测报告导出。"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import FileResponse

from checkers.scanner import summarize
from config.settings_manager import load_settings
from checkers.fluency_checker import RULE_KEYS as FLUENCY_RULE_KEYS
from reporting.report_builder import build_report, default_report_name


from server import state
from server.state import REPORT_DIR, workspace as _workspace
from server.services.detection import rule_summary as _rule_summary

router = APIRouter()

# 报告导出
# ---------------------------------------------------------------------------
def _filter_results_for_report(results: List[Any], mode: str) -> List[Any]:
    """按导出范围过滤检测结果（不修改内存中的原始结果）。

    mode:
        all      全部问题（默认）
        fluency  仅语句通顺类问题（fl_logic / fl_incomplete / fl_order /
                 fl_repeat / fl_conj / fl_mixed）
    """
    if mode != "fluency":
        return results
    out = []
    for res in results:
        kept = [i for i in res.issues if i.rule_key in FLUENCY_RULE_KEYS]
        if kept:
            import copy as _copy
            r2 = _copy.copy(res)
            r2.issues = kept
            out.append(r2)
    return out


@router.post("/api/report")
def api_report(payload: Dict[str, Any], ws: str = Header("", alias="X-Workspace")):
    state = _workspace(ws)
    if not state["results"]:
        raise HTTPException(400, "暂无检测结果，无法生成报告")
    operator = payload.get("operator", "")
    org = payload.get("org", "")
    include_cover = bool(payload.get("include_cover", True))
    detail_columns = payload.get("detail_columns")
    mode = payload.get("report_filter", "all") or "all"
    filtered = _filter_results_for_report(state["results"], mode)
    if mode == "fluency" and not filtered:
        raise HTTPException(400, "当前检测结果中未发现语句通顺类问题，无法生成筛选报告")
    summary = summarize(filtered)
    # AI 核验配置与提示词记录：随报告归档，使 AI 审核的问题与提示与本地规则问题同源可溯
    ai_review: Dict[str, Any] = {}
    try:
        from checkers.ai_checker import build_prompt_record
        ai_review = build_prompt_record(load_settings().get("ai") or {})
    except Exception:  # noqa: BLE001 - 记录失败不影响报告生成
        ai_review = {}
    name = default_report_name()
    out_path = os.path.join(REPORT_DIR, name)
    build_report(
        filtered, summary, out_path,
        rule_summary=_rule_summary(),
        operator=operator, org=org,
        include_cover=include_cover, detail_columns=detail_columns,
        ai_review=ai_review,
    )
    return FileResponse(
        out_path, filename=name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


# ---------------------------------------------------------------------------
