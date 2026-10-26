# -*- coding: utf-8 -*-
"""全局设置：读取 / 保存 / 恢复默认。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from config.settings_manager import (
    load_settings, save_settings, restore_defaults,
    mask_secrets, merge_settings_patch,
)

router = APIRouter()


def _current_role() -> str:
    from checkers.rag.permissions import normalize_role
    return normalize_role((load_settings() or {}).get("role"))


def _require_admin() -> None:
    if _current_role() != "admin":
        raise HTTPException(403, "仅管理员可执行此操作")


# 设置
# ---------------------------------------------------------------------------
@router.get("/api/settings")
def api_settings():
    return mask_secrets(load_settings())


@router.post("/api/settings")
def api_save_settings(payload: Dict[str, Any]):
    from checkers.rag.permissions import normalize_role

    current = load_settings()
    merged = merge_settings_patch(current, payload)
    role = normalize_role(current.get("role"))
    if role != "admin":
        for section in ("ai", "security"):
            if merged.get(section) != current.get(section):
                raise HTTPException(403, "仅管理员可修改 AI 配置与访问安全设置")
        if normalize_role(merged.get("role")) != normalize_role(current.get("role")):
            raise HTTPException(403, "仅管理员可切换角色")
    if not save_settings(merged):
        raise HTTPException(500, "设置保存失败")
    return {"ok": True}


@router.post("/api/settings/restore", dependencies=[Depends(_require_admin)])
def api_restore_settings():
    return mask_secrets(restore_defaults())


# ---------------------------------------------------------------------------
