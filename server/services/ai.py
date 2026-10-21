# -*- coding: utf-8 -*-
"""AI 相关共享逻辑。

原本散落在 app.py 的路由实现里，被「AI 生成」与「AI 自学习」两组接口共同依赖，
现提取到服务层，避免路由模块之间横向耦合。
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from config.settings_manager import load_settings


def ai_create_enabled() -> bool:
    """全局总开关「启用本地AI智能生成&自学习」（settings.ai.create_enabled）。"""
    return bool((load_settings().get("ai") or {}).get("create_enabled", True))


# 生成渠道 → (来源码, 面向用户的来源名)
BUILD_SOURCES: Dict[str, Tuple[str, str]] = {
    "dialogue": ("ai_dialogue", "AI对话创建"),
    "text": ("ai_text", "AI文本创建"),
    "doc": ("ai_doc", "AI文档创建"),
}


def mark_source(result: Dict[str, Any], source: str, label: str) -> None:
    """给生成结果打来源标记，保存时随规则/词条一并入库。

    source：机器码（ai_dialogue / ai_text / ai_doc），供前端筛选与统计使用。
    label：中文来源名，写入独立字段 source_label —— 不占用 AI 自行生成的
    分类 tag（旧实现用 setdefault 写 tag，而 tag 已存在，导致来源标记静默丢失）。
    """
    for r in result.get("rules") or []:
        r["source"] = source
        r["source_label"] = label
    for w in result.get("wordbanks") or []:
        for e in w.get("entries") or []:
            e["source"] = source
            e["source_label"] = label
