# -*- coding: utf-8 -*-
"""本地知识库（RAG 检索问答）—— 对外入口 / 兼容 façade。

真正的实现已下沉到 ``checkers.rag`` 包：

    checkers/rag/text.py      分词 / 标题层级 / 重叠切片 / 上下文前缀
    checkers/rag/bm25.py      BM25 稀疏索引（一次建表，多次查询）
    checkers/rag/vectors.py   Ollama 嵌入（分批 + 失败折半重试）
    checkers/rag/fusion.py    RRF 融合 / MMR 去冗余 / 相关性阈值
    checkers/rag/engine.py    持久化、增量索引、检索、问答、核验联动

本模块只做命名转发：**保留全部旧函数与常量签名**，
因此 ``server/routes/*`` 中 13 处 ``from checkers.rag_store import ...``
无需任何改动即可继续工作。

流程（升级后）：
    1. 上传 .docx / .pdf / .txt / .md / .csv / .xlsx → 按标题层级切片（带重叠）
    2. 索引：BM25（中文 bigram，不跨标点）+ 可选 Ollama 向量（分批嵌入，
       片段带「文档名 / 章节路径」上下文前缀）
    3. 检索：稀疏 + 稠密双路 RRF 融合 → 相关性阈值 → MMR 去冗余 → TopK
    4. 问答：复用 AI 调用层生成回答，强制标注来源
    5. 联动：核验链路逐片段检索，命中的知识依据回传至问题条目

设计约束不变：仅标准库、无嵌入模型时自动退化为 BM25、所有 I/O 限于
config/knowledge/，零网络外发。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from checkers.rag import engine as _engine
from checkers.rag import parsers as parsers
from checkers.rag import permissions as permissions
from checkers.rag import stores as stores
from checkers.rag import tree as tree
from checkers.rag.bm25 import BM25Index as BM25Index
from checkers.rag.text import (
    CHUNK_OVERLAP,
    CHUNK_SCHEMA_VERSION,
    CHUNK_SIZE,
    MAX_CHUNKS_PER_DOC,
    MAX_DOC_CHARS,
    chunk_blocks as chunk_blocks,
    context_prefix as context_prefix,
    heading_level as heading_level,
    is_heading as is_heading,
    prune_query as prune_query,
    tokenize as tokenize,
)
from checkers.rag.vectors import (
    EMBED_BATCH,
    cos as cos,
    embed_batch as embed_batch,
    embed_many as embed_many,
    embed_query as embed_query,
    embed_status as embed_status,
    resolve_embed_model as resolve_embed_model,
)

# ---------------------------------------------------------------------------
# 常量（保持旧名）
# ---------------------------------------------------------------------------
KB_DIR = _engine.KB_DIR
KB_PATH = _engine.KB_PATH
FILES_DIR = _engine.FILES_DIR
DEFAULT_EMBED_MODEL = _engine.DEFAULT_EMBED_MODEL
MAX_FILE_MB = _engine.MAX_FILE_MB
MAX_DOC_CHARS = _engine.MAX_DOC_CHARS
CHUNK_SIZE = _engine.CHUNK_SIZE
MAX_CHUNKS_PER_DOC = _engine.MAX_CHUNKS_PER_DOC
MAX_PROMPT_CTX = _engine.MAX_PROMPT_CTX
DEFAULT_CONFIG = _engine.DEFAULT_CONFIG

_LOCK = _engine._LOCK

# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------
load_kb = _engine.load_kb
save_kb = _engine.save_kb
kb_status = _engine.kb_status
kb_config = _engine.kb_config
save_kb_config = _engine.save_kb_config

# ---------------------------------------------------------------------------
# 文本 / 切片
# ---------------------------------------------------------------------------
_decode_text = _engine._decode_text
_extract_blocks = _engine._extract_blocks
_is_heading = _engine._is_heading
_chunk_blocks = _engine._chunk_blocks

# ---------------------------------------------------------------------------
# 检索内核
# ---------------------------------------------------------------------------
_build_bm25 = _engine._build_bm25
_bm25_score = _engine._bm25_score
_embed_texts = _engine._embed_texts
_norm01 = _engine._norm01
_cos = _engine._cos
load_index = _engine.load_index
invalidate = _engine.invalidate
pack_hits = _engine.pack_hits

# ---------------------------------------------------------------------------
# 对外主流程
# ---------------------------------------------------------------------------
index_docs = _engine.index_docs
rebuild_index = _engine.rebuild_index
retrieve = _engine.retrieve
retrieve_for_verify = _engine.retrieve_for_verify
search_debug = _engine.search_debug
ask = _engine.ask
delete_docs = _engine.delete_docs
clear_kb = _engine.clear_kb
doc_file = _engine.doc_file
reindex_doc = _engine.reindex_doc

# ---------------------------------------------------------------------------
# 树形目录
# ---------------------------------------------------------------------------
kb_tree = tree.build_tree
list_folders = tree.flat_folders
create_folder = tree.create_folder
rename_folder = tree.rename_folder
delete_folder = tree.delete_folder
move_folder = tree.move_folder
move_docs = tree.move_docs
folder_doc_ids = tree.docs_in_subtree

# ---------------------------------------------------------------------------
# 权限
# ---------------------------------------------------------------------------
kb_permissions = permissions.permissions_payload
save_kb_permissions = permissions.save_permissions
current_role = permissions.current_role
role_can = permissions.can
require_cap = permissions.require

# ---------------------------------------------------------------------------
# 解析能力 / 向量后端
# ---------------------------------------------------------------------------
parser_status = parsers.parser_status
supported_exts = parsers.SUPPORTED_EXTS
vector_backends = stores.vector_backends
reset_store = stores.reset_store

__all__ = [
    "BM25Index", "CHUNK_OVERLAP", "CHUNK_SCHEMA_VERSION", "CHUNK_SIZE",
    "DEFAULT_CONFIG", "DEFAULT_EMBED_MODEL", "EMBED_BATCH", "FILES_DIR",
    "KB_DIR", "KB_PATH", "MAX_CHUNKS_PER_DOC", "MAX_DOC_CHARS", "MAX_FILE_MB",
    "MAX_PROMPT_CTX", "ask", "chunk_blocks", "clear_kb", "context_prefix",
    "cos", "create_folder", "current_role", "delete_docs", "delete_folder",
    "doc_file", "folder_doc_ids", "heading_level", "index_docs", "invalidate",
    "is_heading", "kb_config", "kb_permissions", "kb_status", "kb_tree",
    "list_folders", "load_index", "load_kb", "move_docs", "move_folder",
    "pack_hits", "parser_status", "prune_query", "rebuild_index",
    "reindex_doc", "rename_folder", "require_cap", "reset_store",
    "retrieve", "retrieve_for_verify", "role_can", "save_kb",
    "save_kb_config", "save_kb_permissions", "search_debug",
    "supported_exts", "tokenize", "pack_hits", "embed_many", "embed_status",
    "resolve_embed_model", "stores", "tree", "permissions", "parsers",
]
