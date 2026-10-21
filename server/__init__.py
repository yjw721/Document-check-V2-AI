# -*- coding: utf-8 -*-
"""文档核验中心 · 服务端包。

分层约定（依赖方向单向，禁止反向引用）：
    server.main          应用装配（FastAPI 实例、CORS、静态资源、路由注册）
      └─ server.routes.* 业务路由（只做参数校验 + 组装响应）
           └─ server.services.*  业务执行（检测 / AI 核验 / 缓存等）
                └─ server.state  全局内存态与路径常量
                     └─ checkers / config 领域实现
"""
