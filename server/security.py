# -*- coding: utf-8 -*-
"""访问口令门禁（多用户 / 局域网共享场景）。

  config/settings.json -> security.access_token：
      空字符串 = 免密（默认单机模式，行为与原来完全一致）
      非空     = 所有 /api/* 请求均需携带 X-Access-Token 头，值匹配才放行
  单机本机使用可保持为空；开放局域网时务必设置，防止他人读取 / 删除检测数据。
"""
from __future__ import annotations

import hmac
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config.settings_manager import load_settings

router = APIRouter()


def access_token() -> str:
    """当前生效的访问口令；未设置时返回空串（免密）。"""
    try:
        return str((load_settings().get("security") or {}).get("access_token", "") or "")
    except Exception:  # noqa: BLE001 - 设置损坏时按免密处理，保证工具可用
        return ""


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
        return await call_next(request)


@router.get("/api/security/ping")
def api_security_ping(request: Request) -> Dict[str, Any]:
    """免鉴权探针：返回是否开启口令，以及当前携带的口令是否正确。"""
    token = access_token()
    authorized = True
    if token:
        got = request.headers.get("x-access-token", "") or ""
        authorized = bool(got) and hmac.compare_digest(got.encode("utf-8"), token.encode("utf-8"))
    return {"ok": True, "auth_required": bool(token), "authorized": authorized}
