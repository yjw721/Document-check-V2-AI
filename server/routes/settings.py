# -*- coding: utf-8 -*-
"""全局设置：读取 / 保存 / 恢复默认。

管理员身份判定：
    以 ``security.admin_token`` + 请求头 ``X-Admin-Token`` 为准（会话级、服务端强制）。
    未配置 admin_token 时（默认）视为全员管理员，保持向后兼容。
"""

from __future__ import annotations

import hmac
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from config.settings_manager import (
    load_settings, save_settings, restore_defaults,
    mask_secrets, merge_settings_patch,
)
from server.security import admin_token

router = APIRouter()


def is_admin_request(request: Request) -> bool:
    """根据请求是否携带正确的 X-Admin-Token 判定管理员身份。

    未配置 admin_token 时（默认）返回 True，保持向后兼容（全员管理员）。
    """
    atoken = admin_token()
    if not atoken:
        return True
    got = request.headers.get("x-admin-token", "") or ""
    return bool(got) and hmac.compare_digest(got.encode("utf-8"), atoken.encode("utf-8"))


def require_admin(request: Request) -> None:
    if not is_admin_request(request):
        raise HTTPException(403, "仅管理员可执行此操作")


# 设置
# ---------------------------------------------------------------------------
@router.get("/api/settings")
def api_settings():
    return mask_secrets(load_settings())


@router.post("/api/settings")
def api_save_settings(payload: Dict[str, Any], request: Request):
    current = load_settings()
    merged = merge_settings_patch(current, payload)
    if not is_admin_request(request):
        # 非管理员：禁止修改后台配置（AI 配置 / 访问安全）
        for section in ("ai", "security"):
            if merged.get(section) != current.get(section):
                raise HTTPException(403, "仅管理员可修改 AI 配置与访问安全设置")
    if not save_settings(merged):
        raise HTTPException(500, "设置保存失败")
    return {"ok": True}


@router.post("/api/settings/restore", dependencies=[Depends(require_admin)])
def api_restore_settings(request: Request):
    return mask_secrets(restore_defaults())
