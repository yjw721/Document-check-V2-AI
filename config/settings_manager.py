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
import time
from typing import Any, Dict, Optional

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
        "theme_scheme": "holographic",   # 配色方案：holographic 全息渐变（默认） / dark 深色收敛
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
        "timeout": 60,                         # 单次调用超时秒数
        "max_chars": 3000,                     # 每段最大字符数
        "max_requests": 10,                    # 单文件最多调用次数（超出部分截断）
        "ref_enabled": True,                   # 核验时携带参考资料（标准/词汇/规范）
        "ref_max_chars": 2000,                 # 参考文本携带上限（字符数，超出截断）
    },
    "log_cache": {
        "cache_expire_days": 7,
        "run_log": False,
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
    """原子写入设置；成功返回 True。"""
    try:
        tmp = path + ".tmp"
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
