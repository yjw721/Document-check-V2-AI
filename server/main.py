# -*- coding: utf-8 -*-
"""FastAPI 应用装配入口。

只负责：创建 app、安装中间件、挂载静态资源、注册各业务路由。
具体接口实现见 server/routes/*，业务逻辑见 server/services/*。
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from checkers.rag_store import (
    clear_kb as rag_clear,
    delete_docs as rag_delete_docs,
    doc_file as rag_doc_file,
    index_docs as rag_index_docs,
    kb_status as rag_kb_status,
    ask as rag_ask,
)
from checkers.valuation.valuation_api import router as valuation_router

from server import security
from server.routes import ROUTERS
from server.state import STATIC_DIR

app = FastAPI(title="文档低级错误检查工具", version="2.0.0")

# 允许跨域：本工具仅监听 127.0.0.1，但内置预览面板可能将页面嵌在自有域名下
# 以异源方式加载，此时前端 fetch 相对路径会指向错误地址 / 被 CORS 拦截。
# 放开同源限制后，前端在「异源直连 127.0.0.1:8501」场景下即可正常通信。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# 访问口令门禁（/api/security/ping 自身免鉴权）
security.install(app)

for _r in ROUTERS:
    app.include_router(_r)

# 资产评估报告智能核查（四阶段闭环）
app.include_router(valuation_router)

# 静态资源（含 index.html）— 必须最后挂载，避免覆盖 API 路由
if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
