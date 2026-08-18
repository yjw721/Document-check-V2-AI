# -*- coding: utf-8 -*-
"""
规则配置管理模块（纯本地）
=========================================
职责：
    1. 从本地 config/rules.json 读取检测规则配置
    2. 提供规则开关查询、参数读取的统一接口
    3. 支持把界面上修改后的开关状态保存回本地 JSON
    4. 支持恢复出厂默认配置

保密说明：
    本模块仅进行本地文件读写（open / json），
    不包含任何网络请求、不上报任何数据。
"""

from __future__ import annotations

import copy
import json
import os
import shutil
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# 路径常量：全部基于当前文件位置推导，避免依赖工作目录
# ---------------------------------------------------------------------------
CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CONFIG_DIR)
RULES_PATH = os.path.join(CONFIG_DIR, "rules.json")
RULES_BACKUP_PATH = os.path.join(CONFIG_DIR, "rules.default.json")

# 严重级别 -> 中文展示名（界面用）
SEVERITY_LABELS: Dict[str, str] = {
    "high": "严重",
    "medium": "一般",
    "low": "轻微",
}

# 严重级别排序权重（用于统计与排序）
SEVERITY_WEIGHT: Dict[str, int] = {"high": 3, "medium": 2, "low": 1}


class RuleConfig:
    """规则配置对象：封装一份加载到内存中的规则字典。"""

    def __init__(self, data: Dict[str, Any]) -> None:
        self._data: Dict[str, Any] = data

    # ---------------- 基础访问 ----------------
    @property
    def data(self) -> Dict[str, Any]:
        """返回底层字典（只读语义，调用方请勿直接改结构）。"""
        return self._data

    @property
    def meta(self) -> Dict[str, Any]:
        return self._data.get("meta", {})

    @property
    def global_conf(self) -> Dict[str, Any]:
        return self._data.get("global", {})

    def section(self, kind: str) -> Dict[str, Any]:
        """获取某一类文档的规则集合。kind: 'word' | 'excel'"""
        return self._data.get(kind, {})

    # ---------------- 规则查询 ----------------
    def is_enabled(self, kind: str, rule_key: str) -> bool:
        """判断某条规则是否启用；配置缺失时默认启用（安全侧：宁多检不漏检）。"""
        rule = self.section(kind).get(rule_key)
        if not isinstance(rule, dict):
            return True
        return bool(rule.get("enabled", True))

    def rule(self, kind: str, rule_key: str) -> Dict[str, Any]:
        """获取某条规则的完整定义。"""
        rule = self.section(kind).get(rule_key)
        return rule if isinstance(rule, dict) else {}

    def param(self, kind: str, rule_key: str, name: str, default: Any = None) -> Any:
        """读取某条规则的参数（阈值等）。"""
        return self.rule(kind, rule_key).get(name, default)

    def title(self, kind: str, rule_key: str) -> str:
        return self.rule(kind, rule_key).get("title", rule_key)

    def severity(self, kind: str, rule_key: str) -> str:
        sev = self.rule(kind, rule_key).get("severity", "low")
        return sev if sev in SEVERITY_LABELS else "low"

    def suggestion(self, kind: str, rule_key: str) -> str:
        return self.rule(kind, rule_key).get("suggestion", "请人工复核并按规范修正。")

    def max_issues_per_file(self) -> int:
        try:
            return int(self.global_conf.get("max_issues_per_file", 800))
        except (TypeError, ValueError):
            return 800

    def max_file_size_mb(self) -> int:
        try:
            return int(self.global_conf.get("max_file_size_mb", 100))
        except (TypeError, ValueError):
            return 100

    # ---------------- 规则修改 ----------------
    def set_enabled(self, kind: str, rule_key: str, enabled: bool) -> None:
        """设置规则开关（仅改内存，需调用 save_rules 落盘）。"""
        sec = self._data.setdefault(kind, {})
        rule = sec.setdefault(rule_key, {})
        rule["enabled"] = bool(enabled)

    def enabled_count(self, kind: str) -> int:
        return sum(1 for k in self.section(kind) if self.is_enabled(kind, k))

    def total_count(self, kind: str) -> int:
        return len(self.section(kind))

    def rule_items(self, kind: str) -> List[tuple]:
        """返回 [(rule_key, rule_dict), ...]，保持 JSON 中的原始顺序。"""
        return list(self.section(kind).items())

    def clone(self) -> "RuleConfig":
        return RuleConfig(copy.deepcopy(self._data))


# ---------------------------------------------------------------------------
# 兜底默认配置：当 rules.json 缺失或损坏时使用，保证工具仍可运行
# ---------------------------------------------------------------------------
_FALLBACK: Dict[str, Any] = {
    "meta": {"config_name": "内置兜底配置", "config_version": "fallback", "offline_only": True},
    "global": {"max_file_size_mb": 100, "max_issues_per_file": 800, "skip_hidden_files": True},
    "word": {},
    "excel": {},
}


def load_rules(path: str = RULES_PATH) -> RuleConfig:
    """
    从本地 JSON 加载规则配置。

    - 首次加载成功时，自动生成一份 rules.default.json 作为「恢复默认」的基准
    - 文件缺失 / JSON 损坏时返回内置兜底配置，不抛异常中断工具
    """
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if not isinstance(data, dict):
            raise ValueError("规则配置根节点必须是 JSON 对象")
        # 生成默认基准备份（只做一次）
        if not os.path.exists(RULES_BACKUP_PATH):
            try:
                shutil.copyfile(path, RULES_BACKUP_PATH)
            except OSError:
                pass  # 备份失败不影响主流程
        return RuleConfig(data)
    except (OSError, ValueError, json.JSONDecodeError):
        return RuleConfig(copy.deepcopy(_FALLBACK))


def save_rules(config: RuleConfig, path: str = RULES_PATH) -> bool:
    """把当前内存中的规则配置写回本地 JSON 文件。成功返回 True。"""
    try:
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fp:
            json.dump(config.data, fp, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)  # 原子替换，避免写坏配置
        return True
    except OSError:
        return False


def restore_default_rules(path: str = RULES_PATH) -> RuleConfig:
    """从 rules.default.json 恢复默认配置；若无备份则原样返回当前配置。"""
    if os.path.exists(RULES_BACKUP_PATH):
        try:
            shutil.copyfile(RULES_BACKUP_PATH, path)
        except OSError:
            pass
    return load_rules(path)


def severity_label(severity: str) -> str:
    """严重级别 -> 中文标签。"""
    return SEVERITY_LABELS.get(severity, "轻微")
