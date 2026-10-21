#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pydantic -> TypeScript 类型生成脚本
用法: python scripts/generate_ts_types.py
输出: web/src/lib/schemas/api.d.ts
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Set, Union

from pydantic import BaseModel

# 将项目根目录加入路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from schemas.api_models import (
    AiBuildResult,
    AiBuildRule,
    AiBuildWordbank,
    AiMemoryData,
    AiMemoryLearned,
    AiMemorySample,
    ApiResponse,
    BuildLogsResp,
    BuildStartResp,
    CustomRule,
    CustomRuleGroup,
    CustomRulesData,
    FileResult,
    Issue,
    OverviewData,
    ScanCleanResp,
    ScanItem,
    ScanResult,
    SettingsData,
    Summary,
    TaskSnapshot,
    TaskStartResult,
    ValMeta,
    ValNode,
    WordEntry,
    WordGroup,
    WordbanksData,
)

# 需要导出的模型列表
MODELS = [
    ("Issue", Issue),
    ("FileResult", FileResult),
    ("Summary", Summary),
    ("OverviewData", OverviewData),
    ("TaskSnapshot", TaskSnapshot),
    ("TaskStartResult", TaskStartResult),
    ("CustomRule", CustomRule),
    ("CustomRuleGroup", CustomRuleGroup),
    ("CustomRulesData", CustomRulesData),
    ("WordEntry", WordEntry),
    ("WordGroup", WordGroup),
    ("WordbanksData", WordbanksData),
    ("SettingsData", SettingsData),
    ("AiBuildResult", AiBuildResult),
    ("AiBuildRule", AiBuildRule),
    ("AiBuildWordbank", AiBuildWordbank),
    ("AiMemoryData", AiMemoryData),
    ("AiMemorySample", AiMemorySample),
    ("AiMemoryLearned", AiMemoryLearned),
    ("ScanItem", ScanItem),
    ("ScanResult", ScanResult),
    ("ScanCleanResp", ScanCleanResp),
    ("ValMeta", ValMeta),
    ("ValNode", ValNode),
    ("ApiResponse", ApiResponse),
    ("BuildStartResp", BuildStartResp),
    ("BuildLogsResp", BuildLogsResp),
]

# 类型映射
PY_TO_TS: Dict[str, str] = {
    "str": "string",
    "int": "number",
    "float": "number",
    "bool": "boolean",
    "datetime": "string",  # ISO 8601
    "Any": "unknown",
    "None": "null",
}

# 需要手动处理的特殊类型
SPECIAL_TYPES = {
    "Severity": '"high" | "medium" | "low"',
    "IssueState": '"normal" | "ignored" | "checked"',
    "TaskStatus": '"running" | "done" | "cancelled" | "error"',
    "ScanCategory": '"invalid" | "duplicate" | "normal"',
    "FileType": '"Word" | "Excel" | "PDF" | "不支持"',
    "MatchMode": '"keyword" | "regex"',
    "RuleCategory": '"format_error" | "expression"',
    "WordModule": '"text_word" | "format_regex"',
    "AiMode": '"local" | "online"',
    "AiBuildKind": '"dialogue" | "text" | "doc"',
    "AiMemorySampleStatus": '"pending" | "learning" | "done" | "failed"',
    "AiMemoryKind": '"wordbank" | "rule"',
    "Severity": '"high" | "medium" | "low"',
}


def get_ts_type(annotation: Any, seen: Optional[Set[str]] = None) -> str:
    """将 Python 类型注解转为 TypeScript 类型"""
    if seen is None:
        seen = set()

    # 处理 Optional/Union
    if hasattr(annotation, "__origin__"):
        origin = annotation.__origin__
        args = annotation.__args__

        if origin is Union:
            # Optional[T] = Union[T, None]
            non_none = [a for a in args if a is not type(None)]
            if len(non_none) == 1:
                return f"{get_ts_type(non_none[0], seen)} | null"
            return " | ".join(get_ts_type(a, seen) for a in non_none)

        if origin is list or origin is List:
            item_type = get_ts_type(args[0], seen)
            return f"{item_type}[]"

        if origin is dict or origin is Dict:
            key_type = get_ts_type(args[0], seen)
            val_type = get_ts_type(args[1], seen)
            return f"Record<{key_type}, {val_type}>"

    # 处理 Literal
    if hasattr(annotation, "__origin__") and annotation.__origin__ is Literal:
        values = annotation.__args__
        return " | ".join(f'"{v}"' for v in values)

    # 处理 Enum
    if hasattr(annotation, "__bases__") and any(
        issubclass(b, Enum) for b in annotation.__bases__
    ):
        name = annotation.__name__
        if name in SPECIAL_TYPES:
            return SPECIAL_TYPES[name]
        return name

    # 处理 BaseModel 子类
    if hasattr(annotation, "__bases__") and any(
        issubclass(b, BaseModel) for b in annotation.__bases__
    ):
        name = annotation.__name__
        if name not in seen:
            seen.add(name)
        return name

    # 处理基本类型
    if annotation is str:
        return "string"
    if annotation is int:
        return "number"
    if annotation is float:
        return "number"
    if annotation is bool:
        return "boolean"
    if annotation is type(None):
        return "null"
    if annotation is Any:
        return "unknown"

    # 回退：使用类名
    return getattr(annotation, "__name__", str(annotation))


def generate_interface(model_cls: type) -> str:
    """生成单个模型的 TypeScript 接口"""
    fields = model_cls.model_fields
    lines = [f"export interface {model_cls.__name__} {{"]

    for name, field in fields.items():
        ts_type = get_ts_type(field.annotation)
        optional = "?" if not field.is_required() else ""
        default = ""
        if field.default is not None and not field.is_required():
            try:
                default = f" = {json.dumps(field.default)}"
            except (TypeError, ValueError):
                default = ""
        lines.append(f"  {name}{optional}: {ts_type}{default};")

    lines.append("}")
    return "\n".join(lines)


def generate_enums() -> str:
    """生成枚举类型"""
    enums = [
        ('Severity', ['high', 'medium', 'low']),
        ('IssueState', ['normal', 'ignored', 'checked']),
        ('TaskStatus', ['running', 'done', 'cancelled', 'error']),
        ('ScanCategory', ['invalid', 'duplicate', 'normal']),
        ('FileType', ['Word', 'Excel', 'PDF', '不支持']),
        ('MatchMode', ['keyword', 'regex']),
        ('RuleCategory', ['format_error', 'expression']),
        ('WordModule', ['text_word', 'format_regex']),
        ('AiMode', ['local', 'online']),
        ('AiBuildKind', ['dialogue', 'text', 'doc']),
        ('AiMemorySampleStatus', ['pending', 'learning', 'done', 'failed']),
        ('AiMemoryKind', ['wordbank', 'rule']),
    ]

    lines = []
    for name, values in enums:
        joined = " | ".join('"{}"'.format(v) for v in values)
        lines.append(f"export type {name} = {joined};")
    return "\n".join(lines)


def main():
    output_path = PROJECT_ROOT / "web" / "src" / "lib" / "schemas" / "api.d.ts"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "// ============================================================================",
        "// API 数据契约类型定义 (自动生成，请勿手动修改)",
        "// 生成脚本: scripts/generate_ts_types.py",
        "// ============================================================================",
        "",
        generate_enums(),
        "",
    ]

    for name, model in MODELS:
        lines.append(generate_interface(model))
        lines.append("")

    content = "\n".join(lines)
    output_path.write_text(content, encoding="utf-8")
    print(f"OK generated: {output_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()