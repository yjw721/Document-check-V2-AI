# -*- coding: utf-8 -*-
"""
全局后台设置管理模块（纯本地、离线、零联网）
================================================
职责：
    1. 定义 5 大类全局设置的默认结构与取值范围
    2. 从本地 config/settings.json 原子读写（落盘安全，写坏自动回退）
    3. 支持「恢复默认设置」
    4. 提供缓存过期清理、本地运行日志等辅助能力

分类：
    界面布局 / 检测通用 / 报告导出 / 文件解析 / 日志与缓存

保密说明：仅做本地文件读写，无任何网络请求、无遥测、无外部同步。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, Optional

# 配置并发写保护：多用户同时保存时防止互相覆盖 / 写坏文件
SAVE_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# 路径常量（基于本文件位置推导，不依赖工作目录）
# ---------------------------------------------------------------------------
CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CONFIG_DIR)
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")
SETTINGS_BACKUP_PATH = os.path.join(CONFIG_DIR, "settings.default.json")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
RUN_LOG_PATH = os.path.join(LOG_DIR, "run.log")

# 缓存目录（与 app.py 保持一致，由 app 注入；此处仅用于过期清理工具函数）
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "temp_cache")  # 占位，app 会传入真实路径

# 可选项常量（界面下拉用）
THEME_ACCENTS = {
    "blue":   {"label": "科技蓝", "primary": "#1F4E79", "primary_light": "#2E6FA7", "soft": "#EAF1F8"},
    "teal":   {"label": "沉静青", "primary": "#0F6E6E", "primary_light": "#1C8C8C", "soft": "#E2F3F3"},
    "purple": {"label": "稳重紫", "primary": "#4B3A8F", "primary_light": "#6B51B0", "soft": "#EFEAF9"},
    "slate":  {"label": "石墨灰", "primary": "#374151", "primary_light": "#52606D", "soft": "#EDEFF2"},
    "amber":  {"label": "暖金", "primary": "#8A5A00", "primary_light": "#B97A0A", "soft": "#FBF3E2"},
}
ROW_DENSITY = {
    "compact":    {"label": "紧凑", "row": "30px"},
    "cozy":       {"label": "适中", "row": "40px"},
    "comfortable": {"label": "宽松", "row": "50px"},
}
SCAN_PDF_SKIP = {
    "auto":   "自动跳过（全部页面无文本层时）",
    "always": "始终跳过扫描型 PDF",
    "never":  "不跳过，仍尝试解析",
}


# ---------------------------------------------------------------------------
# 默认配置（集中定义，保证「恢复默认」与「首次启动」一致）
# ---------------------------------------------------------------------------
DEFAULTS: Dict[str, Any] = {
    "ui": {
        "sidebar_default_collapsed": False,
        "animation_enabled": True,
        "theme_accent": "blue",
        "theme_scheme": "holographic",   # 配色方案：holographic 全息渐变（默认） / dark 深色收敛 / ink 水墨
        "accent_color": "",              # 自定义强调色 hex（空 = 使用方案默认强调色）
        "table_row_height": "cozy",
        "page_size": 20,
    },
    "detection": {
        "concurrency": 2,
        "parse_timeout": 30,
        "auto_ignore_blank": True,
        "abnormal_popup": True,
        "fluency_sensitivity": "normal",   # 语句通顺检测灵敏度：loose 放宽 / normal 常用 / strict 收紧
    },
    "report": {
        "default_dir": "",                 # 空 = 程序 reports 目录
        "include_cover": True,
        "detail_columns": ["index", "location", "type", "severity", "detail", "suggestion"],
    },
    "parse": {
        "enable_pdf": True,
        "enable_legacy": False,
        "scan_pdf_skip": "auto",
    },
    "ai": {
        "enabled": False,                      # AI 智能核验总开关（默认关闭，保持离线保密默认）
        "mode": "local",                       # local=本地AI（Ollama，零联网） / online=联网AI（OpenAI 兼容）
        "base_url": "http://127.0.0.1:11434",  # local: Ollama 地址；online: 服务商接口地址（如 https://api.deepseek.com/v1）
        "api_key": "",                         # 联网 AI 的 API Key（本地 AI 留空）
        "model": "qwen2.5:7b",                 # 模型名（联网时如 deepseek-chat / qwen-max 等）
        "timeout": 60,                         # 单次调用超时秒数（本地模式 CPU 推理较慢时自动放宽至不小于 300 秒）
        "max_chars": 3000,                     # 每段最大字符数
        "max_requests": 10,                    # 单文件最多调用次数（超出部分截断）
        "ref_enabled": True,                   # 核验时携带参考资料（标准/词汇/规范）
        "ref_max_chars": 2000,                 # 参考文本携带上限（字符数，超出截断）
        "create_enabled": True,                # 本地AI智能生成&自学习 全局总开关（规则词库智能生成统一模块）
        # —— 以下为「AI 配置」模块新增参数 ——
        "temperature": 0.7,                    # 生成温度（0 严谨确定 ~ 1 发散）
        "num_ctx": 4096,                       # 模型上下文长度（Ollama num_ctx）
        "role": "",                            # AI 角色设定（system 前缀，如「资深资产评估师」）
        "response_style": "balance",           # 响应风格：precise 严谨 / concise 简洁 / balance 均衡 / friendly 友好 / formal 正式
        "prompt_presets": [],                  # 自定义提示词预设 [{id,name,content,scope}]
        "active_preset": "",                   # 默认应用于 AI 核验的预设 id（空=不套用）
        "rate_limit": {                        # 限流参数
            "enabled": False,
            "rpm": 20,                         # 每分钟最大请求数（粗略节流）
            "concurrency": 1,                  # 并发调用上限（本地模型建议 1）
        },
        "cache": {                             # 响应缓存配置
            "enabled": False,
            "ttl": 3600,                       # 缓存有效期（秒）
            "max_entries": 200,                # 最大缓存条目
        },
        # —— 本地知识库（RAG 检索问答） ——
        "kb_enabled": False,                   # AI 核验时检索本地知识库作为参考资料
        "kb_top_k": 3,                         # 每次核验注入的知识库片段数量
        "embed_enabled": True,                 # 使用 Ollama 嵌入模型做向量检索（无嵌入模型自动退化为 BM25）
        "embed_model": "bge-m3",               # Ollama 嵌入模型名（如 bge-m3 / nomic-embed-text）
        # —— 核验行为（可控性增强） ——
        "retry": 1,                            # 单段调用失败（超时/连接）重试次数（0 = 不重试）
        "verify_scope": "all",                 # AI 核验范围：all 全部可解析文件 / issues 仅基础检测已命中问题的文件
        "fail_as_issue": False,                # AI 阶段失败是否写入问题列表（False=仅文件状态提示与日志，不污染统计/报告）
        # —— 增强审核力度 ——
        "deep_review": True,                   # 每段追加一轮「补漏复核」请求（耗时约翻倍，关闭可提速）
        "strict_grounding": True,              # 严格取证：引用须逐字命中原文（短引用须完整命中），抑制幻觉
        "min_confidence": 0.6,                 # 置信度门槛：模型把握度低于该值的问题直接丢弃（0 = 不过滤）
        # —— 专项技能（SKILL）——
        "active_skills": ["financial_reconciliation", "terminology_consistency"],
        # 启用的技能 id 集合（含内置与自定义）；空列表 = 全部关闭；缺省字段 = 自动启用内置 default_on
        "custom_skills": [],                   # 用户自定义技能库：[{id,name,prompt,description,file_types,default_severity}]
    },
    "log_cache": {
        "cache_expire_days": 7,
        "run_log": False,
    },
    "role": "admin",                           # 当前应用角色：admin 管理员（可见 AI 配置等后台模块）/ user 普通用户
    "security": {                              # 访问安全（多用户 / 局域网共享场景）
        "access_token": "",                    # 访问口令：空 = 免密（默认单机模式）；非空 = 局域网用户需输入后才能使用
        "listen": "127.0.0.1",                 # 监听地址：127.0.0.1 = 仅本机 / 0.0.0.0 = 开放局域网（多用户）
    },
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并：用 override 的字段覆盖 base，保证新版本新增字段有默认值。"""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings(path: str = SETTINGS_PATH) -> Dict[str, Any]:
    """
    加载设置；首次成功加载时生成 settings.default.json 基准。
    文件缺失 / 损坏 / 结构异常时回退到内置 DEFAULTS，不抛异常中断工具。
    """
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if not isinstance(data, dict):
            raise ValueError("settings root must be object")
        data = _deep_merge(DEFAULTS, data)
        if not os.path.exists(SETTINGS_BACKUP_PATH):
            try:
                with open(SETTINGS_BACKUP_PATH, "w", encoding="utf-8") as bf:
                    json.dump(DEFAULTS, bf, ensure_ascii=False, indent=2)
            except OSError:
                pass
        return data
    except (OSError, ValueError, json.JSONDecodeError):
        data = _deep_merge(DEFAULTS, {})
        try:
            save_settings(data, path)  # 首次启动 / 文件损坏：将默认配置落盘
        except OSError:
            pass
        return data


def save_settings(data: Dict[str, Any], path: str = SETTINGS_PATH) -> bool:
    """原子写入设置；成功返回 True。多用户并发写加锁串行化。"""
    tmp = path + ".tmp"
    with SAVE_LOCK:
        try:
            with open(tmp, "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            return True
        except OSError:
            return False


def restore_defaults(path: str = SETTINGS_PATH) -> Dict[str, Any]:
    """恢复出厂默认（用 settings.default.json 覆盖；无备份则回退 DEFAULTS）。"""
    if os.path.exists(SETTINGS_BACKUP_PATH):
        try:
            with open(SETTINGS_BACKUP_PATH, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            if isinstance(data, dict):
                save_settings(data, path)
                return _deep_merge(DEFAULTS, data)
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    save_settings(DEFAULTS, path)
    return _deep_merge(DEFAULTS, {})


# ---------------------------------------------------------------------------
# 缓存过期清理（日志与缓存设置 -> 自动缓存过期周期）
# ---------------------------------------------------------------------------
def clear_cache_expired(cache_dir: str, expire_days: int) -> int:
    """
    删除缓存目录中修改时间超过 expire_days 天的文件，返回删除数量。
    expire_days <= 0 表示不自动过期（保留全部）。
    """
    if expire_days <= 0 or not os.path.isdir(cache_dir):
        return 0
    now = time.time()
    limit = expire_days * 86400.0
    removed = 0
    try:
        for name in os.listdir(cache_dir):
            p = os.path.join(cache_dir, name)
            try:
                if os.path.isfile(p) and (now - os.path.getmtime(p)) > limit:
                    os.remove(p)
                    removed += 1
            except OSError:
                continue
    except OSError:
        pass
    return removed


# ---------------------------------------------------------------------------
# 本地运行日志（日志与缓存设置 -> 开关本地运行日志记录）
# ---------------------------------------------------------------------------
def log_event(message: str, enabled: bool = False) -> None:
    """当 enabled 时，追加一行本地运行日志到 logs/run.log。"""
    if not enabled:
        return
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(RUN_LOG_PATH, "a", encoding="utf-8") as fp:
            fp.write(f"[{ts}] {message}\n")
    except OSError:
        pass
