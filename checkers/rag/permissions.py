# -*- coding: utf-8 -*-
"""知识库权限模型 —— 与核验系统既有角色体系打通。

角色来源唯一：``config/settings.json`` 的 ``role`` 字段
（与「AI 配置 / 后台模块可见性」共用同一份角色，避免两套权限各说各话）。

- ``admin``    管理员：全部能力
- ``operator`` 核验操作员：日常使用（查看 / 检索 / 上传 / 重向量化），
                但不能删除资料、不能改全局参数 —— 防止误操作毁库
- ``viewer``   只读访客：仅查看与检索
- ``user``     历史值，自动归一为 ``operator``

**强制点在服务端**：前端只是隐藏按钮，真正的拦截在路由层 ``require()``，
直接调用接口也会被 403 挡下。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

# 必须在模块级导入：本文件启用了 PEP 563（from __future__ import annotations），
# require() 中 `_guard(request: Request)` 的注解是字符串，FastAPI 只能从模块 globals 解析。
# 若改为函数体内局部导入，注解无法解析，request 会被当成必填 query 参数导致全部守卫路由 422。
from fastapi import Request

# ---------------------------------------------------------------------------
# 角色定义
# ---------------------------------------------------------------------------
ROLE_DEFS: List[Dict[str, str]] = [
    {"id": "admin", "label": "管理员", "desc": "全部权限：目录管理、参数配置、删除与清空"},
    {"id": "operator", "label": "核验操作员", "desc": "查看、检索问答、上传、重向量化；不可删除与改配置"},
    {"id": "viewer", "label": "只读访客", "desc": "仅可查看目录与检索问答"},
]

#: 历史 / 别名归一
ROLE_ALIAS: Dict[str, str] = {
    "user": "operator",
    "member": "operator",
    "guest": "viewer",
    "root": "admin",
    "administrator": "admin",
}

# ---------------------------------------------------------------------------
# 能力定义
# ---------------------------------------------------------------------------
CAP_DEFS: List[Dict[str, str]] = [
    {"id": "kb.view", "label": "查看", "desc": "浏览目录树、文档列表、下载原文"},
    {"id": "kb.search", "label": "检索问答", "desc": "关键词 / 语义检索与溯源问答、核验联动检索"},
    {"id": "kb.upload", "label": "上传", "desc": "批量上传文档并入索引"},
    {"id": "kb.reindex", "label": "重向量化", "desc": "单文档更新重嵌、全量重建索引"},
    {"id": "kb.delete", "label": "删除", "desc": "删除文档、清空知识库"},
    {"id": "kb.manage", "label": "管理", "desc": "新建 / 重命名 / 删除目录，修改切片参数与后端配置"},
    {"id": "ai.config", "label": "AI 配置管理", "desc": "查看与修改 AI 配置、引用库、活动日志等后台模块（仅管理员）"},
    {"id": "ai.use", "label": "AI 生成", "desc": "使用 AI 规则/词库智能生成、AI 记忆学习等生成能力"},
]

ALL_CAPS: List[str] = [c["id"] for c in CAP_DEFS]

DEFAULT_CAPS: Dict[str, List[str]] = {
    "admin": list(ALL_CAPS),
    "operator": ["kb.view", "kb.search", "kb.upload", "kb.reindex", "ai.use"],
    "viewer": ["kb.view", "kb.search"],
}


# ---------------------------------------------------------------------------
# 角色解析
# ---------------------------------------------------------------------------
def normalize_role(role: Any) -> str:
    """把任意角色值归一为受支持的角色 id。"""
    r = str(role or "").strip().lower()
    r = ROLE_ALIAS.get(r, r)
    return r if r in DEFAULT_CAPS else "operator"


def current_role() -> str:
    """读取当前应用角色（与核验系统同源）。

    未配置管理员口令（security.admin_token 为空）时，向后兼容视为管理员（全员可用）；
    已配置时，回退到 settings.role 作为「非提权用户」的默认知识库角色。
    注：提权会话（携带正确 X-Admin-Token）的判定在 require() / save_permissions() 内完成。
    """
    try:
        from config.settings_manager import load_settings
        data = load_settings() or {}
        sec = data.get("security") or {}
        atoken = str(sec.get("admin_token", "") or "") if isinstance(sec, dict) else ""
        if not atoken:
            return "admin"
        return normalize_role(data.get("role"))
    except Exception:  # noqa: BLE001 - 配置不可用时按操作员处理
        return "operator"


def _role_from_request(request) -> Optional[str]:
    """若请求携带正确的 X-Admin-Token 则返回 'admin'，否则返回 None（交由 current_role 兜底）。"""
    if request is None:
        return None
    try:
        import hmac
        from config.settings_manager import load_settings
        sec = load_settings().get("security") or {}
        atoken = str(sec.get("admin_token", "") or "") if isinstance(sec, dict) else ""
        if not atoken:
            return None
        got = request.headers.get("x-admin-token", "") or ""
        if bool(got) and hmac.compare_digest(got.encode("utf-8"), atoken.encode("utf-8")):
            return "admin"
    except Exception:  # noqa: BLE001
        return None
    return None


# ---------------------------------------------------------------------------
# 能力判定
# ---------------------------------------------------------------------------
def _overrides() -> Dict[str, Any]:
    """读取自定义权限覆盖（管理员可在界面上调整）。"""
    try:
        from checkers.rag.engine import load_kb
        kb = load_kb()
        perms = kb.get("permissions")
        return perms if isinstance(perms, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def caps_of(role: Any) -> List[str]:
    """返回该角色具备的能力 id 列表（含自定义覆盖）。"""
    r = normalize_role(role)
    ov = _overrides()
    raw = ov.get(r)
    if isinstance(raw, list):
        return [str(c) for c in raw if str(c) in ALL_CAPS]
    return list(DEFAULT_CAPS.get(r, DEFAULT_CAPS["operator"]))


def can(role: Any, cap: str) -> bool:
    """能力判定；未知能力一律拒绝（fail-closed）。"""
    c = str(cap or "").strip()
    if c not in ALL_CAPS:
        return False
    return c in caps_of(role)


# ---------------------------------------------------------------------------
# FastAPI 依赖：路由层强制校验
# ---------------------------------------------------------------------------
def require(cap: str):
    """生成 FastAPI 依赖项：无权限时抛 403。

    管理员令牌（security.admin_token + 请求头 X-Admin-Token）一旦校验通过即视为管理员，
    拥有全部能力；否则回退到 settings.role 的常规角色判定（与核验系统同源）。

    用法::

        @router.post("/api/rag/clear", dependencies=[Depends(require("kb.delete"))])
    """
    from fastapi import Depends, HTTPException
    import hmac

    def _guard(request: Request) -> None:
        # 配置了管理员口令且请求携带正确令牌时，视为管理员（拥有全部能力）
        try:
            from config.settings_manager import load_settings
            sec = load_settings().get("security") or {}
            atoken = str(sec.get("admin_token", "") or "") if isinstance(sec, dict) else ""
        except Exception:  # noqa: BLE001
            atoken = ""
        if atoken:
            got = request.headers.get("x-admin-token", "") or ""
            if bool(got) and hmac.compare_digest(got.encode("utf-8"), atoken.encode("utf-8")):
                return  # 管理员放行
        role = current_role()
        if not can(role, cap):
            from checkers.rag.permissions import label_of
            raise HTTPException(
                403, f"当前角色「{label_of(role)}」无此操作权限：{cap}")

    return Depends(_guard)


def label_of(role: Any) -> str:
    """角色中文名。"""
    r = normalize_role(role)
    for d in ROLE_DEFS:
        if d["id"] == r:
            return d["label"]
    return "核验操作员"


# ---------------------------------------------------------------------------
# 对外载荷
# ---------------------------------------------------------------------------
def permissions_payload(request=None) -> Dict[str, Any]:
    """供前端渲染权限矩阵与当前能力。

    ``request`` 可选：携带正确 X-Admin-Token 时按管理员视角返回。
    """
    role = _role_from_request(request) or current_role()
    caps = caps_of(role)
    return {
        "ok": True,
        "role": role,
        "role_label": label_of(role),
        "caps": {c: (c in caps) for c in ALL_CAPS},
        "roles": [
            dict(d, caps={c: (c in caps_of(d["id"])) for c in ALL_CAPS})
            for d in ROLE_DEFS
        ],
        "cap_defs": CAP_DEFS,
    }


def save_permissions(patch: Optional[Dict[str, Any]] = None, request=None) -> Dict[str, Any]:
    """管理员自定义角色能力矩阵。

    ``patch`` 形如 ``{"operator": ["kb.view", "kb.search", "kb.upload"]}``；
    传入 ``{"reset": true}`` 恢复默认矩阵。
    提权会话（携带正确 X-Admin-Token）始终允许；否则需当前角色为管理员。
    """
    if _role_from_request(request) != "admin" and current_role() != "admin":
        return {"ok": False, "message": "仅管理员可修改权限配置"}

    from checkers.rag.engine import load_kb, save_kb

    kb = load_kb()
    data = patch or {}
    if data.get("reset"):
        kb["permissions"] = {}
    else:
        perms = kb.get("permissions")
        perms = dict(perms) if isinstance(perms, dict) else {}
        for rid, caps in data.items():
            if rid in DEFAULT_CAPS and isinstance(caps, list):
                perms[rid] = [str(c) for c in caps if str(c) in ALL_CAPS]
        kb["permissions"] = perms
    save_kb(kb)
    return {"ok": True, **permissions_payload()}
