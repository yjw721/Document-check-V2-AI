# -*- coding: utf-8 -*-
"""
AI 调用客户端（评估核查专用）
=========================================================================
复用 checkers/ai_checker.py 的底层通道（Ollama 原生 / OpenAI 兼容），
为「智能切分」「9 大节点 AI 核查」「AI 综合复核」提供统一入口：

    chat(cfg, system, user) -> str

设计要点：
    * 本地 Ollama 为默认模式，文档内容不出本机；
    * 内置 JSON 提取与容错解析（模型常输出 ```json 包裹或前后废话）；
    * 全部失败向上抛 AiError，由调用方降级为提示条目，不中断流水线。
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

from checkers.ai_checker import AiError, _call_ollama, _call_openai  # noqa: PLC2701


def ai_enabled(cfg: Optional[Dict[str, Any]]) -> bool:
    """AI 是否可用（显式开启且配置完整）。"""
    if not cfg or not isinstance(cfg, dict):
        return False
    if not cfg.get("enabled"):
        return False
    mode = str(cfg.get("mode") or "local")
    if mode == "local":
        return bool(str(cfg.get("base_url") or "").strip())
    return bool(str(cfg.get("base_url") or "").strip())


def chat(cfg: Dict[str, Any], system: str, user: str,
         on_token: Optional[Callable[[str, str], None]] = None,
         think: bool = False,
         num_predict: Optional[int] = None) -> str:
    """发送一次对话，返回模型输出文本。失败抛 AiError。"""
    if not ai_enabled(cfg):
        raise AiError("AI 未启用或未配置接口地址")
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    mode = str(cfg.get("mode") or "local")
    if mode == "local":
        opts: Dict[str, Any] = {}
        if num_predict:
            opts["num_predict"] = int(num_predict)
        return _call_ollama(cfg, messages, think=think, options=opts or None,
                            on_token=on_token)
    return _call_openai(cfg, messages)


# ---------------------------------------------------------------------------
# JSON 容错解析
# ---------------------------------------------------------------------------
def extract_json(text: str) -> Any:
    """从模型输出中提取 JSON（支持 ```json 包裹 / 前后废话 / 数组与对象）。"""
    if not text:
        return None
    s = str(text).strip()
    # 去掉思考标签
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.S | re.I)
    s = re.sub(r"</?think>", "", s, flags=re.I)

    # 1) 直接解析
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # 2) 抽取代码块
    m = re.search(r"```(?:json)?\s*(.+?)```", s, flags=re.S)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            s = m.group(1).strip()

    # 3) 截取首个 [ ... ] 或 { ... }
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        i, j = s.find(open_ch), s.rfind(close_ch)
        if i >= 0 and j > i:
            frag = s[i:j + 1]
            try:
                return json.loads(frag)
            except json.JSONDecodeError:
                continue
    return None


def parse_json_list(text: str) -> List[Dict[str, Any]]:
    """解析为字典列表；失败返回空列表。"""
    data = extract_json(text)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("items", "data", "result", "results", "list", "issues", "findings"):
            v = data.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
        return [data]
    return []


def parse_json_obj(text: str) -> Dict[str, Any]:
    """解析为字典；失败返回空字典。"""
    data = extract_json(text)
    return data if isinstance(data, dict) else {}
