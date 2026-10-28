# -*- coding: utf-8 -*-
"""范本导入生成规则：上传 / 草案 / 选择 / 导入 / 清空。"""

from __future__ import annotations

import io
import os
from typing import Any, Dict, List

from fastapi import APIRouter, File, Form, UploadFile, HTTPException, Header

from checkers.custom_rules import load_custom_rules, save_custom_rules
from checkers.wordbank import load_wordbanks, save_wordbanks
from checkers.template_parser import TemplateParser


from server.state import template_state as _template_state

router = APIRouter()

# 范本导入生成规则
# ---------------------------------------------------------------------------
@router.post("/api/template/upload")
async def api_template_upload(files: List[UploadFile] = File(...), category: str = Form("general"),
                              ws: str = Header("", alias="X-Workspace")):
    """上传外部基准文件（.docx/.pdf/.txt/.csv/.scel，批量）并解析生成规则 / 词库草案。

    category：外部基准类别（general/industry/asset/practice/correction/forbidden/official）。
    离线安全：文件内容仅在本地内存（BytesIO）中处理，不写入磁盘、不外传。
    """
    parser = TemplateParser()
    inputs = []
    for idx, uf in enumerate(files):
        name = os.path.basename(uf.filename or f"file_{idx}")
        ext = os.path.splitext(name)[1].lower()
        if ext not in (".docx", ".pdf", ".txt", ".csv", ".scel"):
            continue
        content = await uf.read()
        if len(content) > _MAX_TEMPLATE_MB * 1024 * 1024:
            continue
        inputs.append((io.BytesIO(content), name))
    if not inputs:
        raise HTTPException(400, "没有可解析的基准文件（支持 .docx / .pdf / .txt / .csv / .scel，单个不超过 50MB）")
    draft = parser.parse_files(inputs, category=category)
    _template_state(ws)["parser"] = parser
    return draft


@router.get("/api/template/draft")
def api_template_draft(ws: str = Header("", alias="X-Workspace")):
    """获取当前范本解析草案（无草案时返回空结构）。"""
    p = _template_state(ws).get("parser")
    if not p:
        return {"docs": [], "rules": [], "entries": [], "conflicts": [], "references": []}
    return p.draft()


@router.post("/api/template/select")
def api_template_select(payload: Dict[str, Any], ws: str = Header("", alias="X-Workspace")):
    """更新草案选中状态（rule_ids / entry_ids 未列出的项视为取消选择）。"""
    p = _template_state(ws).get("parser")
    if not p:
        raise HTTPException(400, "当前没有范本解析草案，请先上传范本")
    p.set_selected(payload.get("rule_ids", []), payload.get("entry_ids", []))
    return {"ok": True}


@router.post("/api/template/import")
def api_template_import(payload: Dict[str, Any], ws: str = Header("", alias="X-Workspace")):
    """选择性确认导入：仅追加选中项到自定义规则 / 自定义词库，绝不覆盖原有配置。"""
    p = _template_state(ws).get("parser")
    if not p:
        raise HTTPException(400, "当前没有范本解析草案，请先上传范本")
    rule_ids = payload.get("rule_ids") or []
    entry_ids = payload.get("entry_ids") or []
    if not rule_ids and not entry_ids:
        raise HTTPException(400, "未选择任何规则或词条，请先勾选要导入的内容")
    built = p.build_import(rule_ids, entry_ids)

    imported_rules = imported_entries = 0
    filtered_rules = filtered_entries = 0
    rejected = []
    from checkers.rule_filter import build_existing, filter_rules, filter_entries
    existing = build_existing()
    if built["rule_count"]:
        data = load_custom_rules()
        # 范本解析渠道：入库前置校验（仅过滤新导入组，不触碰库内既有规则）
        group = built["rule_group"]
        kept, dropped = filter_rules(group.get("rules", []), existing, "template_import")
        group["rules"] = kept
        if not group["rules"]:
            built["rule_count"] = 0
        else:
            data.setdefault("groups", []).append(group)
            if not save_custom_rules(data):
                raise HTTPException(500, "自定义规则写入失败")
        imported_rules = len(kept)
        filtered_rules = len(dropped)
        rejected.extend(dropped)
        existing["patterns"].update(
            (str(r.get("match_mode") or "keyword"), str(r.get("pattern") or "").strip())
            for r in kept if str(r.get("pattern") or "").strip())
    if built["entry_count"]:
        data = load_wordbanks()
        group = built["entry_group"]
        kept, dropped = filter_entries(group.get("entries", []), existing, "template_import")
        group["entries"] = kept
        if not group["entries"]:
            built["entry_count"] = 0
        else:
            data.setdefault("groups", []).append(group)
            if not save_wordbanks(data):
                raise HTTPException(500, "自定义词库写入失败")
        imported_entries = len(kept)
        filtered_entries = len(dropped)
        rejected.extend(dropped)

    return {"ok": True, "imported_rules": imported_rules, "imported_entries": imported_entries,
            "filtered_rules": filtered_rules, "filtered_entries": filtered_entries,
            "rejected": rejected}


@router.post("/api/template/clear")
def api_template_clear(ws: str = Header("", alias="X-Workspace")):
    """清空当前范本解析草案。"""
    _template_state(ws)["parser"] = None
    return {"ok": True}


# ---------------------------------------------------------------------------
