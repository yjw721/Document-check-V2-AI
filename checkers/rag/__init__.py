# -*- coding: utf-8 -*-
"""本地知识库（RAG）内核包。

分层：
    text.py     文本层：分词 / 标题层级 / 重叠切片 / 上下文前缀
    bm25.py     稀疏层：BM25 索引（预先建表，查询只做打分）
    vectors.py  稠密层：Ollama /api/embed 分批嵌入（含失败自动折半重试）
    fusion.py   融合层：RRF 混合融合 / MMR 去冗余 / 相关性阈值
    engine.py   编排层：持久化 + 增量索引 + 检索 + 问答 + 核验联动入口

对外入口统一在 ``checkers.rag_store``（兼容 façade），路由层无需改动。
"""

from checkers.rag import text as text  # noqa: F401  - 便于外部按模块引用
from checkers.rag.engine import (
    clear_kb,
    delete_docs,
    doc_file,
    index_docs,
    kb_config,
    kb_status,
    load_kb,
    retrieve,
    retrieve_for_verify,
    save_kb,
    save_kb_config,
    search_debug,
    ask,
)

__all__ = [
    "text",
    "ask",
    "clear_kb",
    "delete_docs",
    "doc_file",
    "index_docs",
    "kb_config",
    "kb_status",
    "load_kb",
    "retrieve",
    "retrieve_for_verify",
    "save_kb",
    "save_kb_config",
    "search_debug",
]
