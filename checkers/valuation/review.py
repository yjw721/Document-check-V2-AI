# -*- coding: utf-8 -*-
"""
阶段四 · AI 综合复核
=========================================================================
对应需求「🧠 AI 综合复核」：对汇总结果做两向校正——

    排查误报：剔除因上下文截断、口径理解偏差产生的错误结论；
    补充漏报：在全局视角下补充各单节点因视野局限而遗漏的问题。

AI 不可用 / 调用失败时自动跳过，并在复核结论中注明，不阻断报告输出。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from .ai_client import ai_enabled, chat, parse_json_obj, AiError
from .models import Finding

_SYS = """你是资产评估报告复核合伙人，负责对一个智能核查流水线的初步结果做最终复核。

你手上有两类信息：
1）九个核查节点产出的【初步问题清单】（可能包含误报，也可能有遗漏）；
2）被核查材料的【原文摘录】。

请做两件事：
【排查误报】对初步清单中的每一条判断其是否成立。属于下列情形的，判定为误报并剔除：
   - 问题描述与材料原文不符（数值算错、张冠李戴）；
   - 属于正常的行业惯例 / 合理的口径差异（如单位换算、取整、百分比与小数）；
   - 只是表述风格问题、不构成实质差错。
【补充漏报】在全局视角下，补充清单中没有提到、但确实存在的重大问题，
   重点关注跨文件不一致、勾稽错误、披露缺失、结论矛盾。补充项必须在 detail 中
   注明材料中的原文依据（quote 字段需逐字出自原文）。

输出严格 JSON（不要任何其它文字）：
{"removed":[{"id":"问题id","reason":"剔除理由"}],
 "added":[{"location":"位置","quote":"原文","issue":"问题简述","detail":"举证说明","suggestion":"建议","severity":"high|medium|low","node":"应归属的节点名称"}],
 "conclusion":"一段复核结论，说明剔除多少条误报、补充多少条漏报、整体质量评价"}

限制：最多补充 20 条漏报；宁缺毋滥，无法确证的不要补充。
"""

_USER = """【初步问题清单】
{issues}

【原文摘录】
{material}

请输出 JSON 复核结果。"""


def _brief(findings: List[Finding], limit: int = 120) -> str:
    """把问题清单压缩为可送入模型的精简 JSON。"""
    rows = []
    for f in findings[:limit]:
        rows.append({
            "id": f.id,
            "node": f"{f.node_no}{f.node_name}",
            "item": f.item_title,
            "severity": f.severity,
            "location": f.location,
            "detail": (f.detail or "")[:220],
        })
    import json
    return json.dumps(rows, ensure_ascii=False, indent=1)


def ai_review(findings: List[Finding], material: str, ai_cfg: Dict[str, Any],
              log: Optional[Callable[[str], None]] = None,
              on_token: Optional[Callable[[str, str], None]] = None
              ) -> Dict[str, Any]:
    """执行 AI 综合复核。

    返回 {ok, removed: {id: reason}, added: [Finding], conclusion, message}
    """
    log = log or (lambda s: None)
    result: Dict[str, Any] = {
        "ok": False, "removed": {}, "added": [], "conclusion": "", "message": "",
    }
    if not ai_enabled(ai_cfg):
        result["message"] = "AI 未启用，已跳过综合复核（仅输出规则与节点核查结果）"
        return result
    if not findings:
        result["message"] = "初步清单为空，已跳过综合复核"
        return result

    max_chars = int(ai_cfg.get("max_chars") or 3000)
    mat = (material or "")[:max(6000, max_chars * 3)]
    user = _USER.format(issues=_brief(findings), material=mat)

    try:
        raw = chat(ai_cfg, _SYS, user, on_token=on_token)
    except AiError as exc:
        result["message"] = f"AI 综合复核调用失败，已跳过：{exc}"
        log(result["message"])
        return result

    data = parse_json_obj(raw)
    if not data:
        result["message"] = "AI 复核响应无法解析为 JSON，已跳过"
        log(result["message"])
        return result

    # 误报剔除
    for row in (data.get("removed") or []):
        if isinstance(row, dict) and row.get("id"):
            result["removed"][str(row["id"])] = str(row.get("reason") or "")[:200]

    # 漏报补充
    norm_mat = re.sub(r"\s+", "", mat)
    for row in (data.get("added") or [])[:20]:
        if not isinstance(row, dict):
            continue
        detail = str(row.get("detail") or row.get("issue") or "").strip()
        if not detail:
            continue
        quote = str(row.get("quote") or "").strip()
        if quote and re.sub(r"\s+", "", quote) not in norm_mat:
            continue            # 引用不实，判为幻觉
        sev = str(row.get("severity") or "medium").lower()
        if sev not in ("high", "medium", "low"):
            sev = "medium"
        f = Finding(
            node_id="R",
            node_no="⑩",
            node_name="AI 综合复核",
            item_id="R-01",
            item_no=0,
            item_title=str(row.get("node") or "全局复核补充"),
            source="AI 综合复核",
            engine="ai",
            severity=sev,
            file=str(row.get("node") or "全局复核"),
            location=str(row.get("location") or "-")[:120],
            detail=detail,
            snippet=quote[:300],
            suggestion=str(row.get("suggestion") or ""),
            origin="review",
        )
        result["added"].append(f)

    result["conclusion"] = str(data.get("conclusion") or "")[:2000]
    result["ok"] = True
    result["message"] = (
        f"综合复核完成：剔除误报 {len(result['removed'])} 条，补充漏报 {len(result['added'])} 条"
    )
    log(result["message"])
    return result
