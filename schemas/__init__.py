# -*- coding: utf-8 -*-
"""API 数据契约层。

api_models.py 是前后端契约的单一真相源：
后端在这里定义 Pydantic 模型，前端 TypeScript 类型由
`scripts/generate_ts_types.py` 自动生成，避免手写类型漂移。
"""
