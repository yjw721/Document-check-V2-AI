# -*- coding: utf-8 -*-
"""访问口令门禁（多用户 / 局域网共享场景）。

  config/settings.json -> security.access_token：
      空字符串 = 免密（默认单机模式，行为与原来完全一致）
      非空     = 所有 /api/* 请求均需携带 X-Access-Token 头，值匹配才放行
  单机本机使用可保持为空；开放局域网时务必设置，防止他人读取 / 删除检测数据。

  管理员校验（可选增强）：
  config/settings.json -> security.admin_token：
      空字符串 = 不启用管理员口令（默认；所有用户拥有完整后台写权限，向后兼容）
      非空     = 后台写接口（设置保存 / AI 配置 / 知识库权限等）须额外携带正确的
                 X-Admin-Token 头，否则返回 401。普通用户仍可只读 / 使用应用。
"""
from __future__ import annotations

import hmac
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config.settings_manager import load_settings

router = APIRouter()

# 需要管理员令牌才能写入的后台路径前缀（普通用户只读 / 使用，无写权限）
ADMIN_WRITE_PREFIXES = (
    "/api/settings",          # 设置保存 / 读取
    "/api/ai/",               # AI 配置读写
    "/api/rag/permissions",   # 知识库权限
)


def _security() -> Dict[str, Any]:
    try:
        sec = load_settings().get("security") or {}
        return sec if isinstance(sec, dict) else {}
    except Exception:  # noqa: BLE001 - 设置损坏时按免密处理，保证工具可用
        return {}


def access_token() -> str:
    """当前生效的访问口令；未设置时返回空串（免密）。"""
    return str(_security().get("access_token", "") or "")


def admin_token() -> str:
    """当前生效的管理员口令；未设置时返回空串（免管理员校验）。"""
    return str(_security().get("admin_token", "") or "")


def _is_admin_protected(path: str) -> bool:
    """路径是否命中管理员写保护前缀（去掉结尾斜杠后匹配）。"""
    p = path.rstrip("/")
    return any(p == pref.rstrip("/") or p.startswith(pref) for pref in ADMIN_WRITE_PREFIXES)


def install(app: Any) -> None:
    """注册口令校验中间件（/api/security/ping 本身免鉴权）。"""

    @app.middleware("http")
    async def access_gate(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path.rstrip("/") != "/api/security/ping":
            token = access_token()
            if token:
                got = request.headers.get("x-access-token", "") or ""
                if not hmac.compare_digest(got.encode("utf-8"), token.encode("utf-8")):
                    return JSONResponse({"ok": False, "message": "访问口令错误或未设置"}, status_code=401)
            # 管理员写保护：命中后台写前缀且为写操作（非 GET/HEAD）且已设置管理员口令时，
            # 必须携带正确的 X-Admin-Token；读操作对所有已认证用户开放。
            atoken = admin_token()
            if atoken and request.method.upper() not in ("GET", "HEAD") and _is_admin_protected(path):
                got = request.headers.get("x-admin-token", "") or ""
                if not hmac.compare_digest(got.encode("utf-8"), atoken.encode("utf-8")):
                    return JSONResponse(
                        {"ok": False, "message": "需要管理员口令", "admin_required": True},
                        status_code=401,
                    )
        return await call_next(request)


@router.get("/api/security/ping")
def api_security_ping(request: Request) -> Dict[str, Any]:
    """免鉴权探针：返回是否开启访问口令 / 管理员口令，以及当前携带的口令是否正确。"""
    token = access_token()
    authorized = True
    if token:
        got = request.headers.get("x-access-token", "") or ""
        authorized = bool(got) and hmac.compare_digest(got.encode("utf-8"), token.encode("utf-8"))

    atoken = admin_token()
    admin_authorized = True
    if atoken:
        got = request.headers.get("x-admin-token", "") or ""
        admin_authorized = bool(got) and hmac.compare_digest(got.encode("utf-8"), atoken.encode("utf-8"))
    return {
        "ok": True,
        "auth_required": bool(token),
        "authorized": authorized,
        "admin_required": bool(atoken),
        "admin_authorized": admin_authorized,
    }
