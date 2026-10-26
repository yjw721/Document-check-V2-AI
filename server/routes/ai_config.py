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


# ---------------------------------------------------------------------------
# zipskill 标准技能包导入
# 采用公开的 Agent Skills 开放标准：ZIP 包内每个技能是一个含 SKILL.md 的目录，
# SKILL.md = YAML frontmatter（name/description 必填，metadata 可放扩展字段）
#            + Markdown 正文（技能指令）。正文映射为本系统的 prompt（注入系统提示），
#            metadata 承载本系统专属字段（display-name / file-types / default-severity）。
# ---------------------------------------------------------------------------
_ZIP_MAX_BYTES = 5 * 1024 * 1024                 # 上传包体上限 5MB
_ZIP_MAX_ENTRIES = 500                           # 包内条目数上限
_ZIP_MAX_TOTAL_UNCOMPRESSED = 20 * 1024 * 1024   # 解压后总大小上限（防 zip 炸弹）
_SKILL_MAX_BYTES = 512 * 1024                    # 单个 SKILL.md 上限 512KB

_KNOWN_FTYPES = {"all", "doc", "xls", "pdf", "txt"}
_KNOWN_SEVERITY = {"high", "medium", "low"}


def _split_frontmatter(text: str):
    """拆分 SKILL.md 的 YAML frontmatter 与 Markdown 正文。返回 (yaml_text, body)；无 frontmatter 返回 (None, None)。"""
    lines = text.lstrip("\ufeff").lstrip().splitlines()
    if not lines or lines[0].strip() != "---":
        return None, None
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return None, None


def _slugify(value: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _is_slug(value: str) -> bool:
    """判断是否为 Agent Skills 标准的 name（小写字母数字 + 单个连字符，不以连字符开头结尾）。"""
    import re
    return bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", str(value or "")))


def _sanitize_ftypes(value: str) -> str:
    toks = [t.strip().lower() for t in str(value or "").split(",") if t.strip()]
    keep = [t for t in toks if t in _KNOWN_FTYPES]
    if not keep or "all" in keep:
        return "all"
    return ",".join(dict.fromkeys(keep))


def _sanitize_severity(value: str) -> str:
    s = str(value or "").strip().lower()
    return s if s in _KNOWN_SEVERITY else ""


def _parse_skill_md(member: str, text: str):
    """把一个 SKILL.md 解析为本系统 AiSkill 字典。返回 (skill | None, error | None)。"""
    try:
        import yaml
    except Exception:  # noqa: BLE001
        return None, f"{member}：服务端缺少 PyYAML，无法解析 frontmatter"

    yaml_text, body = _split_frontmatter(text)
    if yaml_text is None:
        return None, f"{member}：缺少 YAML frontmatter（--- ... ---）"
    try:
        meta = yaml.safe_load(yaml_text) or {}
    except Exception as exc:  # noqa: BLE001
        return None, f"{member}：YAML 解析失败（{exc}）"
    if not isinstance(meta, dict):
        return None, f"{member}：frontmatter 不是键值映射"

    metadata = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}

    def pick(*keys: str, default: str = "") -> str:
        # 优先取 frontmatter 顶层自定义键，其次取 metadata（Agent Skills 标准的扩展位）
        for k in keys:
            v = meta.get(k)
            if v is None or str(v).strip() == "":
                v = metadata.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
        return default

    raw_name = str(meta.get("name") or "").strip()
    description = str(meta.get("description") or "").strip()
    prompt = str(body or "").strip()

    # id：显式 metadata.id > 合法 slug 的 name > name 派生 > 目录名派生 > 内容哈希
    explicit_id = pick("id")
    if explicit_id:
        skill_id = explicit_id
    elif _is_slug(raw_name):
        skill_id = raw_name
    elif raw_name:
        skill_id = _slugify(raw_name)
    else:
        skill_id = ""
    if not skill_id:
        dir_name = member.replace("\\", "/").rsplit("/", 1)[0].rsplit("/", 1)[-1] if "/" in member.replace("\\", "/") else ""
        skill_id = _slugify(dir_name)
    if not skill_id:
        import hashlib
        skill_id = "sk_" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]

    # 显示名：metadata.display-name/title > name（中文名直接作为显示名）> id
    display = pick("display-name", "display_name", "title") or raw_name or skill_id

    if not display:
        return None, f"{member}：技能名称为空"
    if not prompt:
        return None, f"{member}：正文（prompt）为空，缺少技能指令"

    return {
        "id": skill_id,
        "name": display,
        "description": description,
        "prompt": prompt,
        "file_types": _sanitize_ftypes(pick("file-types", "file_types", default="all")),
        "default_severity": _sanitize_severity(pick("default-severity", "default_severity")),
    }, None


@router.post("/api/ai/skills/import")
async def api_ai_skills_import(file: UploadFile = File(...)):
    """导入 zipskill 标准技能包（Agent Skills 开放标准的 ZIP：每个含 SKILL.md 的目录 = 一个技能）。
    服务端解析并校验（大小 / 条目 / 防 zip 炸弹 / 字段消毒），返回提取到的技能列表，由前端合并进自定义库。"""
    import io
    import zipfile

    def fail(msg: str, errors: Optional[List[str]] = None):
        return {"ok": False, "skills": [], "count": 0, "message": msg, "errors": errors or []}

    raw = await file.read()
    if not raw:
        return fail("未收到文件")
    if len(raw) > _ZIP_MAX_BYTES:
        return fail(f"技能包过大（>{_ZIP_MAX_BYTES // (1024 * 1024)}MB）")
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        return fail("不是有效的 ZIP 技能包")

    errors: List[str] = []
    skills: List[Dict[str, Any]] = []
    seen = set()

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            infos = zf.infolist()
            if len(infos) > _ZIP_MAX_ENTRIES:
                return fail(f"包内条目过多（>{_ZIP_MAX_ENTRIES}）")
            if sum(i.file_size for i in infos) > _ZIP_MAX_TOTAL_UNCOMPRESSED:
                return fail("解压后总大小超限（防 zip 炸弹）")

            for info in infos:
                if info.is_dir():
                    continue
                member = info.filename.replace("\\", "/")
                segs = member.split("/")
                # 仅处理 SKILL.md，跳过 __MACOSX / 隐藏目录等非技能内容
                if segs[-1].lower() != "skill.md":
                    continue
                if any(s.startswith("__MACOSX") or s.startswith(".") for s in segs[:-1]):
                    continue
                if info.file_size > _SKILL_MAX_BYTES:
                    errors.append(f"{member}：过大（>{_SKILL_MAX_BYTES // 1024}KB），已跳过")
                    continue
                try:
                    text = zf.read(info).decode("utf-8", errors="replace")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{member}：读取失败（{exc}）")
                    continue
                skill, err = _parse_skill_md(member, text)
                if err:
                    errors.append(err)
                    continue
                if skill["id"] in seen:
                    errors.append(f"{member}：技能 id「{skill['id']}」在包内重复，已跳过")
                    continue
                seen.add(skill["id"])
                skills.append(skill)
    except zipfile.BadZipFile:
        return fail("ZIP 已损坏，无法解析")

    if not skills:
        return fail("未在包内找到可用的 SKILL.md（Agent Skills 标准格式）", errors)
    return {"ok": True, "skills": skills, "count": len(skills),
            "message": f"已解析 {len(skills)} 个技能", "errors": errors}


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
