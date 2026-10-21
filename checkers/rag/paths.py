# -*- coding: utf-8 -*-
"""知识库路径常量。

单独成模块的目的：``engine`` 与 ``stores`` 互相需要 KB_DIR，
若任一方在模块级导入另一方就会形成循环依赖，因此把路径抽到最底层。
"""

from __future__ import annotations

import os

#: 项目根目录（checkers/ 的上一级）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 知识库数据目录
KB_DIR = os.path.join(ROOT, "config", "knowledge")
#: 文档元数据 + 分块文本（JSON，唯一事实来源）
KB_PATH = os.path.join(KB_DIR, "knowledge.json")
#: 上传原文存放目录
FILES_DIR = os.path.join(KB_DIR, "files")
#: Chroma 持久化目录（默认后端）
CHROMA_DIR = os.path.join(KB_DIR, "chroma")
