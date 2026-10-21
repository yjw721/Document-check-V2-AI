# -*- coding: utf-8 -*-
"""
阶段四 · 结果汇总与自动去重
=========================================================================
九个核查节点的结果统一汇总；同一问题被多个节点同时发现时自动去重合并，
避免最终报告重复罗列（对应需求「🔗 结果汇总（自动去重）」）。

去重键 = 文件 + 位置 + 问题性质（数值差异归一化后比较），
因此「⑥ 报告↔说明」「⑦ 说明↔收益法」等联查节点与单文件节点之间的
重复命中会被合并为一条，并保留：
    - 严重级别最高的那一条作为主发现；
    - 其余条目的节点来源计入 merged_from，merged_count 累加。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .models import Finding

_SEV_ORDER = {"high": 3, "medium": 2, "low": 1}
_ORIGIN_ORDER = {"rule": 0, "ai": 1, "review": 2}


def dedup_findings(findings: List[Finding]) -> Tuple[List[Finding], Dict[str, Any]]:
    """归并重复发现，返回 (保留列表, 统计信息)。"""
    groups: Dict[str, List[Finding]] = {}
    order: List[str] = []
    for f in findings:
        if getattr(f, "state", "normal") == "ignored":
            continue
        key = f.dedup_key
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(f)

    kept: List[Finding] = []
    removed = 0
    for key in order:
        bucket = groups[key]
        if len(bucket) == 1:
            kept.append(bucket[0])
            continue
        # 主发现：严重级别优先 → 来源（规则 > AI）→ 描述更完整
        bucket.sort(key=lambda x: (-_SEV_ORDER.get(x.severity, 1),
                                   _ORIGIN_ORDER.get(x.origin, 9),
                                   -len(x.detail or "")))
        main = bucket[0]
        merged_from = sorted({f"{b.node_no}{b.node_name}" for b in bucket[1:]})
        main.merged_count = len(bucket)
        note = f"（另有 {len(bucket) - 1} 个节点重复命中：{'、'.join(merged_from)}）"
        if note not in (main.detail or ""):
            main.detail = (main.detail or "") + " " + note
        removed += len(bucket) - 1
        kept.append(main)

    stats = {
        "total": len(findings),
        "kept": len(kept),
        "merged": removed,
        "by_severity": {
            "high": sum(1 for f in kept if f.severity == "high"),
            "medium": sum(1 for f in kept if f.severity == "medium"),
            "low": sum(1 for f in kept if f.severity == "low"),
        },
        "by_origin": {
            "rule": sum(1 for f in kept if f.origin == "rule"),
            "ai": sum(1 for f in kept if f.origin == "ai"),
            "review": sum(1 for f in kept if f.origin == "review"),
        },
    }
    return kept, stats
