# -*- coding: utf-8 -*-
"""AI 智能核验配置：状态、技能、模型、连通性测试、参考资料、日志。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, UploadFile
from config.settings_manager import load_settings, SECRET_MASK, mask_secrets
from checkers.ai_checker import test_connection as ai_test_connection, list_refs, save_ref, delete_ref, set_ref_enabled
from checkers import ai_activity
from checkers import activity as rule_activity

from checkers.rag.permissions import require as _require_cap

# 整个「AI 配置」后台模块仅管理员可见（与前端隐藏逻辑一致，服务端 fail-closed 兜底）
router = APIRouter(dependencies=[_require_cap("ai.config")])

# AI 智能核验（可选：本地 AI / 联网 AI）
# ---------------------------------------------------------------------------
@router.get("/api/ai/status")
def api_ai_status():
    return {"ai": mask_secrets(load_settings()).get("ai") or {}}


@router.get("/api/ai/skills")
def api_ai_skills():
    """返回内置专项技能列表（前端技能开关与自定义管理的唯一来源）。"""
    from checkers.ai_checker import BUILTIN_SKILLS
    return {"builtin": BUILTIN_SKILLS}


@router.get("/api/ai/models")
def api_ai_models(base_url: Optional[str] = None):
    """获取本地 Ollama 已安装模型列表（供前端下拉选择；Ollama 未运行返回空）。
    可通过 ?base_url=... 指定临时地址（不写入 settings），便于切换模型时即时探测。"""
    import json
    import urllib.error
    import urllib.request
    ai = load_settings().get("ai") or {}
    raw = (base_url or ai.get("base_url") or "http://127.0.0.1:11434")
    base = str(raw).rstrip("/") or "http://127.0.0.1:11434"
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        names = sorted(set(str(m.get("name") or "") for m in data.get("models", []) if m.get("name")))
        return {"ok": True, "models": names, "base_url": base}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "models": [], "base_url": base, "message": f"无法连接本地 AI 服务：{exc}"}


@router.post("/api/ai/test")
def api_ai_test(payload: Dict[str, Any]):
    """测试 AI 连接（mode/base_url/api_key/model 传入即覆盖设置）。"""
    current = load_settings().get("ai") or {}
    cfg = {**current, **{k: v for k, v in payload.items()
                         if k in ("mode", "base_url", "api_key", "model", "timeout")}}
    if cfg.get("api_key") == SECRET_MASK:
        cfg["api_key"] = current.get("api_key", "")
    ok, msg = ai_test_connection(cfg)
    return {"ok": ok, "message": msg}


# --- 参考资料（标准/词汇/规范，AI 核验时自动携带） ---
@router.get("/api/ai/refs")
def api_ai_refs():
    return {"refs": list_refs()}


@router.post("/api/ai/refs/upload")
async def api_ai_refs_upload(files: List[UploadFile] = File(...)):
    """上传参考资料（multipart files，支持 .txt/.md/.csv/.docx/.pdf）。"""
    if not files:
        return {"ok": False, "message": "未收到文件"}
    results, errors = [], []
    for f in files:
        try:
            raw = await f.read()
            if len(raw) > 5 * 1024 * 1024:
                errors.append(f"{f.filename}：文件过大（>5MB）")
                continue
            results.append(save_ref(f.filename or "ref.txt", raw))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{f.filename}：{exc}")
    return {"ok": bool(results), "message": f"已保存 {len(results)} 个参考资料"
            + (f"，失败 {len(errors)} 个：" + "；".join(errors) if errors else ""),
            "refs": list_refs()}


@router.post("/api/ai/refs/toggle")
def api_ai_refs_toggle(payload: Dict[str, Any]):
    name = str(payload.get("name") or "")
    ok = set_ref_enabled(name, bool(payload.get("enabled", True)))
    return {"ok": ok, "refs": list_refs()}


@router.post("/api/ai/refs/delete")
def api_ai_refs_delete(payload: Dict[str, Any]):
    name = str(payload.get("name") or "")
    existed = delete_ref(name)
    return {"ok": existed, "refs": list_refs()}


@router.get("/api/ai/logs")
def api_ai_logs(limit: int = 200, offset: int = 0):
    """AI 活动日志（核验 / 生成 / 测试连接 / 自学习）。"""
    items = ai_activity.get_logs(limit=max(1, int(limit)), offset=max(0, int(offset)))
    return {"ok": True, "logs": items, "total": len(items)}


@router.post("/api/ai/logs/clear")
def api_ai_logs_clear():
    n = ai_activity.clear_logs()
    return {"ok": True, "cleared": n}


# --- 通用操作日志（规则/词库等后台保存操作） ---
@router.get("/api/activity/logs")
def api_activity_logs(module: str = "", limit: int = 200, offset: int = 0):
    """操作记录（修改项 / 操作人 / 时间）。module 可过滤，如 rules / wordbank。"""
    items = rule_activity.get_logs(
        module=module or None, limit=max(1, int(limit)), offset=max(0, int(offset)),
    )
    return {"ok": True, "logs": items, "total": len(items)}


@router.post("/api/activity/logs")
def api_activity_log_create(payload: Dict[str, Any]):
    """写入一条操作记录（修改项 / 操作人 / 时间）。"""
    rule_activity.log_activity(
        module=str(payload.get("module") or "unknown"),
        action=str(payload.get("action") or "save"),
        detail=str(payload.get("detail") or ""),
        operator=payload.get("operator"),
    )
    return {"ok": True}


@router.post("/api/activity/logs/clear")
def api_activity_logs_clear(payload: Dict[str, Any]):
    module = (payload or {}).get("module") or None
    n = rule_activity.clear_logs(module=module)
    return {"ok": True, "cleared": n}


# --- AI 规则/词库智能生成（对话式 + 文本式 + 文档式，统一入库校验与来源标记） ---
