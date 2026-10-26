# -*- coding: utf-8 -*-
"""业务路由汇总。

每个模块导出同名 `router`，由 server.main 统一 include_router。
新增路由模块时在此登记即可。
"""
from server import security
from server.routes import (
    ai_build,
    ai_config,
    ai_memory,
    detection,
    dicts,
    ocr,
    overview,
    rag,
    report,
    rules,
    scan,
    settings,
    template,
)

# 注册顺序仅影响 OpenAPI 文档分组展示，不影响功能
ROUTERS = [
    security.router,     # 访问口令探针（免鉴权）
    overview.router,     # 概览 / 文件 / 问题
    detection.router,    # 导入与检测 / 异步任务
    rules.router,        # 内置规则配置
    dicts.router,        # 内置词库
    scan.router,         # 规则词库一键扫描清理
    template.router,     # 范本导入生成规则
    settings.router,     # 全局设置
    ai_config.router,    # AI 核验配置 / 参考资料 / 日志
    ai_build.router,     # AI 规则词库智能生成
    ai_memory.router,    # 本地 AI 自学习
    rag.router,          # 本地知识库问答
    ocr.router,          # OCR 状态（核验侧）
    report.router,       # 报告导出
]

__all__ = ["ROUTERS"]
