# -*- coding: utf-8 -*-
"""本地知识库（RAG 检索问答）。"""

from __future__ import annotations

import hmac
import io
import itertools
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import (APIRouter, Depends, File, Form, UploadFile, HTTPException,
                     Request, Header)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from checkers.base import STATUS_PASS, STATUS_UNREADABLE, detect_file_type
from checkers.scanner import check_single, collect_folder, summarize, _is_blank
from config.config_manager import load_rules, save_rules, RuleConfig
from config.settings_manager import load_settings, save_settings, restore_defaults
from checkers.custom_rules import load_custom_rules, save_custom_rules
from checkers.wordbank import load_wordbanks, save_wordbanks, parse_entries_import
from checkers.template_parser import TemplateParser
from checkers.dictionary_manager import list_dictionaries, read_dictionary, save_dictionary
from checkers.fluency_checker import RULE_KEYS as FLUENCY_RULE_KEYS
from checkers.ai_checker import (
    ai_check_file, test_connection as ai_test_connection,
    list_refs, save_ref, delete_ref, set_ref_enabled,
    AI_RULE_KEY, AI_RULE_TITLE,
)
from checkers import ai_activity
from checkers import activity as rule_activity
from checkers.ai_builder import (
    build_dialogue as ai_build_dialogue, build_from_doc as ai_build_doc,
    build_from_text as ai_build_text,
)
from checkers.rule_filter import (
    filter_generated as rule_filter_generated,
    filter_rules_payload as rule_filter_rules_payload,
    filter_entries_payload as rule_filter_entries_payload,
)
from checkers.rule_scan import (
    scan_all as rule_scan_all,
    clean_items as rule_scan_clean,
    cleanable_ids as rule_scan_cleanable_ids,
    build_backup_text as rule_scan_backup_text,
    build_backup_csv as rule_scan_backup_csv,
)
from checkers.ai_memory import (
    payload as ai_memory_payload, set_enabled as ai_memory_set_enabled,
    is_enabled as ai_memory_is_enabled, learn_sample as ai_memory_learn,
    toggle_learned as ai_memory_toggle, delete_learned as ai_memory_delete,
    delete_sample as ai_memory_sample_delete, clear_all as ai_memory_clear,
    save_source_docs as ai_memory_save_source_docs,
    source_docs_status as ai_memory_source_status,
    upload_revised as ai_memory_upload_revised,
    compute_diffs as ai_memory_compute_diffs,
    add_sample as ai_memory_add_sample,
    export_learned as ai_memory_export,
)
from reporting.report_builder import build_report, default_report_name
from checkers.valuation.valuation_api import router as valuation_router
from checkers.rag_store import (
    kb_status as rag_kb_status, index_docs as rag_index_docs,
    ask as rag_ask, delete_docs as rag_delete_docs,
    clear_kb as rag_clear, doc_file as rag_doc_file,
    search_debug as rag_search, rebuild_index as rag_rebuild,
    kb_config as rag_kb_config, save_kb_config as rag_save_kb_config,
    retrieve_for_verify as rag_verify_retrieve, pack_hits as rag_pack,
    reindex_doc as rag_reindex_doc,
    kb_tree as rag_tree, list_folders as rag_folders,
    create_folder as rag_folder_create, rename_folder as rag_folder_rename,
    delete_folder as rag_folder_delete, move_folder as rag_folder_move,
    move_docs as rag_docs_move, folder_doc_ids as rag_folder_docs,
    kb_permissions as rag_permissions,
    save_kb_permissions as rag_save_permissions,
    parser_status as rag_parser_status,
    kb_graph as rag_graph,
    kb_records_add as rag_rec_add,
    kb_records_list as rag_rec_list,
    kb_records_get as rag_rec_get,
    kb_records_delete as rag_rec_del,
    kb_records_clear as rag_rec_clear,
    kb_records_export as rag_rec_export,
)
from checkers.rag.permissions import require as _require_cap


from server import state
from server.state import (
    ROOT, STATIC_DIR, CACHE_DIR, REPORT_DIR, STAGE_NAMES,
    WORKSPACES, SCAN_STATE, TEMPLATE_STATES, TASKS, TASK_LOCK, WS_LOCK,
    workspace as _workspace,
    template_state as _template_state,
    make_task as _make_task,
    task_snapshot as _task_snapshot,
    task_hook as _task_hook,
    cancel_task as _cancel_task,
    try_acquire_scan as _try_acquire_scan,
    release_scan as _release_scan,
    WORKSPACES as _WORKSPACES,
    SCAN_STATE as _SCAN_STATE,
    TEMPLATE_STATES as _TEMPLATE_STATES,
    TASKS as _TASKS,
    TASK_LOCK as _TASK_LOCK,
    WS_LOCK as _WS_LOCK,
)
from server.services.detection import (
    serialize_result as _serialize_result,
    cache_stats as _cache_stats,
    clear_cache as _clear_cache,
    rule_summary as _rule_summary,
    run_detection as _run_detection,
    run_task as _run_task,
    task_alive as _task_alive,
    state_payload as _state_payload,
)

router = APIRouter()

# 本地知识库（RAG 检索问答）
# ---------------------------------------------------------------------------
@router.get("/api/rag/status", dependencies=[_require_cap("kb.view")])
def api_rag_status():
    return rag_kb_status()


@router.post("/api/rag/upload", dependencies=[_require_cap("kb.upload")])
async def api_rag_upload(files: List[UploadFile] = File(...),
                         folder: str = Form("")):
    """批量上传文档并入知识库索引。

    支持 .docx / .doc / .pdf / .txt / .md / .csv / .xlsx 与
    .png / .jpg / .jpeg / .bmp / .tif / .tiff / .webp（走 OCR）。
    ``folder`` 指定归入的目录 id，留空为根目录。
    """
    items: List[tuple] = []
    for uf in files:
        name = os.path.basename(uf.filename or "file")
        try:
            content = await uf.read()
        except OSError:
            continue
        if not content:
            continue
        items.append((name, content))
    if not items:
        raise HTTPException(400, "没有可上传的合法文件")
    added, docs, errors = rag_index_docs(items, str(folder or "").strip())
    if added == 0 and not docs:
        raise HTTPException(400, errors[0] if errors else "文件导入失败")
    return {"ok": True if added else False, "added": added, "docs": docs,
            "errors": errors}


@router.post("/api/rag/ask", dependencies=[_require_cap("kb.search")])
def api_rag_ask(payload: Dict[str, Any]):
    """基于本地知识库检索并生成回答（返回答案、来源片段与可信度）。"""
    question = str(payload.get("question") or payload.get("q") or "").strip()
    if len(question) < 2:
        raise HTTPException(400, "请输入要查询的问题")
    try:
        top_k = max(1, min(8, int(payload.get("top_k") or 3)))
    except (TypeError, ValueError):
        top_k = 3
    ai = (load_settings().get("ai") or {}).copy()
    if not ai.get("enabled"):
        raise HTTPException(400, "知识库问答需要 AI 支持：请先在「AI 配置」启用 AI 并测试连接")

    # 目录限定：folder 展开为该子树下的文档 id
    res = rag_ask(question, ai, top_k, _resolve_doc_ids(payload))
    _capture_ask(question, res, payload, top_k)
    return {"ok": True, **res}


@router.post("/api/rag/search", dependencies=[_require_cap("kb.search")])
def api_rag_search(payload: Dict[str, Any]):
    """仅检索、不生成：返回各路得分与融合结果，用于诊断知识库相关性。

    可选 ``folder``：把诊断范围限定在某个目录子树。
    """
    q = str(payload.get("question") or payload.get("query") or "").strip()
    if len(q) < 2:
        raise HTTPException(400, "请输入检索关键词")
    try:
        top_k = max(1, min(10, int(payload.get("top_k") or 5)))
    except (TypeError, ValueError):
        top_k = 5
    ai = (load_settings().get("ai") or {}).copy()
    res = rag_search(q, ai, top_k, _resolve_doc_ids(payload))
    _capture_search(q, res, payload, top_k)
    return {"ok": True, **res}


def _resolve_doc_ids(payload: Dict[str, Any]) -> Optional[List[str]]:
    """从请求体解析检索范围：显式 doc_ids 优先，其次按 folder 展开子树。"""
    ids = payload.get("doc_ids")
    if isinstance(ids, list) and ids:
        return [str(i) for i in ids]
    folder = payload.get("folder")
    if folder:
        try:
            from checkers.rag.engine import load_kb
            from checkers.rag.tree import docs_in_subtree
            return docs_in_subtree(load_kb(), str(folder))
        except Exception:  # noqa: BLE001 - 目录异常时退化为全库检索
            return None
    return None


def _scope_label(payload: Dict[str, Any]) -> str:
    """把检索范围归纳成人类可读标签。"""
    if payload.get("doc_ids"):
        return "指定文档"
    if payload.get("folder"):
        return str(payload.get("folder"))
    return "全库"


def _capture_ask(question: str, res: Dict[str, Any], payload: Dict[str, Any], top_k: int) -> None:
    """结果返回后异步式落盘一条问答记录（异常吞掉，绝不拖慢主流程）。"""
    try:
        a = res.get("audit") or {}
        summary = {
            "answer_preview": (res.get("answer") or "")[:300],
            "sources_count": len(res.get("sources") or []),
            "grounded": bool(a.get("grounded")),
            "refused": bool(a.get("refused")),
            "confidence": float(a.get("confidence") or 0.0),
            "elapsed": res.get("elapsed"),
            "has_error": bool(res.get("error")),
        }
        rag_rec_add("ask", question, {"top_k": top_k, "scope": _scope_label(payload)}, summary, res)
    except Exception:  # noqa: BLE001
        pass


def _capture_search(q: str, res: Dict[str, Any], payload: Dict[str, Any], top_k: int) -> None:
    """结果返回后异步式落盘一条检索记录（异常吞掉，绝不拖慢主流程）。"""
    try:
        hits = res.get("hits") or []
        top = hits[0] if hits else None
        summary = {
            "hits_count": len(hits),
            "mode": res.get("mode"),
            "top_file": (top or {}).get("file"),
            "top_section": (top or {}).get("section"),
            "max_cos": max([float(h.get("cos") or 0.0) for h in hits] or [0.0]),
        }
        rag_rec_add("search", q, {"top_k": top_k, "scope": _scope_label(payload)}, summary, res)
    except Exception:  # noqa: BLE001
        pass


@router.post("/api/rag/verify-preview",
             dependencies=[_require_cap("kb.search")])
def api_rag_verify_preview(payload: Dict[str, Any]):
    """核验预检：把一段待核验文本切分后，回显核验时会被注入的知识库片段。

    用于确认「AI 核验注入知识库」实际生效、以及命中质量是否符合预期。
    """
    text = str(payload.get("text") or "").strip()
    if len(text) < 4:
        raise HTTPException(400, "请输入待核验的文本（至少 4 个字符）")
    try:
        top_k = max(1, min(8, int(payload.get("top_k") or 3)))
    except (TypeError, ValueError):
        top_k = 3
    from config.settings_manager import load_settings
    ai = (load_settings().get("ai") or {}).copy()
    # 切分口径与 ai_check_file 保持一致
    try:
        max_chars = max(300, int(ai.get("max_chars") or 3000))
    except (TypeError, ValueError):
        max_chars = 3000
    try:
        max_requests = max(1, min(20, int(ai.get("max_requests") or 10)))
    except (TypeError, ValueError):
        max_requests = 10
    try:
        from checkers.ai_checker import _chunk_blocks as _ai_chunk
        segs = [t for _l, t in _ai_chunk([("片段", text)], max_chars, max_requests)]
    except Exception:  # noqa: BLE001 - 切分失败时退化为整段
        segs = [text]
    hits = rag_verify_retrieve(segs, top_k=top_k,
                               dense=bool(ai.get("kb_dense_in_verify", False)),
                               ai_cfg=ai)
    ctx, refs = rag_pack(hits, 6000)
    return {"ok": True, "segments": len(segs), "context": ctx, "refs": refs,
            "hits": hits}


@router.get("/api/rag/config")
def api_rag_config_get():
    """读取知识库配置（切片参数 / 向量后端 / OCR）。"""
    return rag_kb_config()


@router.post("/api/rag/config", dependencies=[_require_cap("kb.manage")])
def api_rag_config_post(payload: Dict[str, Any]):
    """更新知识库配置；切片参数与后端变更需执行「重建索引」生效。"""
    return rag_save_kb_config(payload or {})


@router.post("/api/rag/rebuild", dependencies=[_require_cap("kb.reindex")])
def api_rag_rebuild():
    """按已存原文重新切片并全量重嵌向量（调整切片参数或切换后端后执行）。"""
    ai = (load_settings().get("ai") or {}).copy()
    return rag_rebuild(ai)


@router.post("/api/rag/doc/reindex",
             dependencies=[_require_cap("kb.reindex")])
def api_rag_doc_reindex(payload: Dict[str, Any]):
    """单个文档重新切片并重新向量化（文档更新后使用，不影响其他文档）。"""
    doc_id = str(payload.get("id") or payload.get("doc_id") or "").strip()
    if not doc_id:
        raise HTTPException(400, "请提供要重建的文档 id")
    ai = (load_settings().get("ai") or {}).copy()
    res = rag_reindex_doc(doc_id, ai)
    if not res.get("ok"):
        raise HTTPException(400, res.get("message") or "重建失败")
    return {"ok": True, **res}


@router.post("/api/rag/delete", dependencies=[_require_cap("kb.delete")])
def api_rag_delete(payload: Dict[str, Any]):
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "请提供要删除的文档 id")
    removed, names = rag_delete_docs(ids)
    return {"ok": True, "removed": removed, "names": names}


@router.post("/api/rag/clear", dependencies=[_require_cap("kb.delete")])
def api_rag_clear():
    rag_clear()
    return {"ok": True}


# ---------------------------------------------------------------------------
# 树形目录
# ---------------------------------------------------------------------------
@router.get("/api/rag/tree")
def api_rag_tree():
    """目录树（目录 + 文档），供左侧导航渲染。"""
    return {"ok": True, "tree": rag_tree()}


@router.get("/api/rag/folders")
def api_rag_folders():
    """扁平目录列表（含路径与文档数），用于「移动到…」下拉。"""
    return {"ok": True, "folders": rag_folders()}


@router.post("/api/rag/folder", dependencies=[_require_cap("kb.manage")])
def api_rag_folder_create(payload: Dict[str, Any]):
    """新建目录。"""
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "请输入目录名")
    res = rag_folder_create(name, str(payload.get("parent") or "").strip())
    if not res.get("ok"):
        raise HTTPException(400, res.get("message") or "新建失败")
    return res


@router.post("/api/rag/folder/rename",
             dependencies=[_require_cap("kb.manage")])
def api_rag_folder_rename(payload: Dict[str, Any]):
    """重命名目录。"""
    fid = str(payload.get("id") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not fid or not name:
        raise HTTPException(400, "请提供目录 id 与新名称")
    res = rag_folder_rename(fid, name)
    if not res.get("ok"):
        raise HTTPException(400, res.get("message") or "重命名失败")
    return res


@router.post("/api/rag/folder/move",
             dependencies=[_require_cap("kb.manage")])
def api_rag_folder_move(payload: Dict[str, Any]):
    """移动目录到新的父目录（服务端校验环路）。"""
    fid = str(payload.get("id") or "").strip()
    if not fid:
        raise HTTPException(400, "请提供目录 id")
    res = rag_folder_move(fid, str(payload.get("parent") or "").strip())
    if not res.get("ok"):
        raise HTTPException(400, res.get("message") or "移动失败")
    return res


@router.post("/api/rag/folder/delete",
             dependencies=[_require_cap("kb.delete")])
def api_rag_folder_delete(payload: Dict[str, Any]):
    """删除目录。``cascade=true`` 时连同子目录与文档一并删除，
    否则把子目录与文档上移到父级（不丢数据）。"""
    fid = str(payload.get("id") or "").strip()
    if not fid:
        raise HTTPException(400, "请提供目录 id")
    res = rag_folder_delete(fid, bool(payload.get("cascade")))
    if not res.get("ok"):
        raise HTTPException(400, res.get("message") or "删除失败")
    return res


@router.post("/api/rag/docs/move",
             dependencies=[_require_cap("kb.upload")])
def api_rag_docs_move(payload: Dict[str, Any]):
    """批量移动文档到目标目录。"""
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "请选择要移动的文档")
    res = rag_docs_move(ids, str(payload.get("folder") or "").strip())
    if not res.get("ok"):
        raise HTTPException(400, res.get("message") or "移动失败")
    return res


# ---------------------------------------------------------------------------
# 权限
# ---------------------------------------------------------------------------
@router.get("/api/rag/permissions")
def api_rag_permissions_get():
    """当前角色及其能力矩阵。"""
    return rag_permissions()


@router.post("/api/rag/permissions",
             dependencies=[_require_cap("kb.manage")])
def api_rag_permissions_post(payload: Dict[str, Any]):
    """自定义角色能力矩阵；``{"reset": true}`` 恢复默认。"""
    res = rag_save_permissions(payload or {})
    if not res.get("ok"):
        raise HTTPException(403, res.get("message") or "保存失败")
    return res


# ---------------------------------------------------------------------------
# 解析能力自检
# ---------------------------------------------------------------------------
@router.get("/api/rag/parsers")
def api_rag_parsers():
    """各格式解析与 OCR 可用性，用于上传前的提示与排查。"""
    return rag_parser_status()


@router.get("/api/rag/file/{doc_id}", dependencies=[_require_cap("kb.view")])
def api_rag_file(doc_id: str):
    """下载知识库中的原始文档（来源跳转）。"""
    p = rag_doc_file(doc_id)
    if not p:
        raise HTTPException(404, "文档不存在")
    base = os.path.basename(p)
    ext = os.path.splitext(base)[1].lower()
    media = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".csv": "text/csv; charset=utf-8",
    }.get(ext, "application/octet-stream")
    return FileResponse(p, filename=base, media_type=media)


# ---------------------------------------------------------------------------
# 思维图谱
# ---------------------------------------------------------------------------
@router.get("/api/rag/graph", dependencies=[_require_cap("kb.view")])
def api_rag_graph(folder: str = "", include_sub: bool = True):
    """知识库思维图谱：文档 / 章节节点 + 结构边 + 关联边（语义相似或关键词共现）。

    可选 ``folder``：仅统计该目录下的文档；``include_sub=false`` 时不含子目录。
    """
    try:
        return rag_graph(str(folder or "").strip(), bool(include_sub))
    except Exception as exc:  # noqa: BLE001 - 图谱构建异常不应拖垮页面其他部分
        return {"ok": False, "nodes": [], "edges": [],
                "stats": {"docs": 0, "sections": 0, "related": 0},
                "message": f"图谱构建失败：{exc}"}


# ---------------------------------------------------------------------------
# 检索 / 问答使用记录（逐条 + 整体管理）
# ---------------------------------------------------------------------------
@router.get("/api/rag/records", dependencies=[_require_cap("kb.search")])
def api_rag_records(
    type: str = "", keyword: str = "", since: int = 0, until: int = 0,
    limit: int = 50, offset: int = 0,
):
    """列出检索 / 问答记录（整体管理：类型 / 关键词筛选 + 分页 + 整体统计）。"""
    return rag_rec_list(type or None, keyword or None, since or None, until or None, limit, offset)


@router.get("/api/rag/records/export", dependencies=[_require_cap("kb.search")])
def api_rag_records_export(type: str = "", keyword: str = "", fmt: str = "json"):
    """导出记录为 JSON 或 Markdown（整体管理）。"""
    return rag_rec_export(type or None, keyword or None, fmt or "json")


@router.get("/api/rag/records/{rid}", dependencies=[_require_cap("kb.search")])
def api_rag_record_get(rid: str):
    """获取单条记录完整内容（逐条管理：详情）。"""
    return rag_rec_get(rid)


@router.delete("/api/rag/records/{rid}", dependencies=[_require_cap("kb.delete")])
def api_rag_record_delete(rid: str):
    """删除单条记录（逐条管理，需删除权限）。"""
    return rag_rec_del(rid)


@router.delete("/api/rag/records", dependencies=[_require_cap("kb.delete")])
def api_rag_records_clear(type: str = ""):
    """清空记录（整体管理，需删除权限）：type 为空清全部，否则按类型清。"""
    return rag_rec_clear(type or None)


# ---------------------------------------------------------------------------
