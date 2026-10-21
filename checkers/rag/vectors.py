# -*- coding: utf-8 -*-
"""稠密检索层：Ollama ``/api/embed`` 分批嵌入。

旧实现 ``_embed_texts`` 把**全库所有片段一次性塞进单个请求**——几百个片段、
数十万字符，30 秒超时几乎必然失败，于是静默降级为纯 BM25：
用户在界面上看到「BM25 检索模式」却不知道向量从未生效。

本模块：
- 默认每批 24 条，逐批请求；
- 单批失败自动折半重试（down to 1），仍失败才整体判定不可用；
- 提供 ``embed_query`` 单例快捷入口（检索期只嵌一条，开销可忽略）。
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Tuple

EMBED_BATCH = 24
EMBED_TIMEOUT = 30
# 单次嵌入的文本字符上限（嵌入模型上下文通常远小于生成模型）
EMBED_MAX_CHARS = 1800

# 嵌入模型名启发式（Ollama /api/tags 里 capabilities 字段不可靠时的兜底）
_EMBED_NAME_HINT = ("embed", "bge-", "gte-", "e5-", "text-embedding")

_MODEL_CACHE: Dict[str, Any] = {"key": "", "ts": 0.0, "model": "", "ok": False}
_MODEL_TTL = 120.0


def _post(base: str, model: str, texts: Sequence[str],
          timeout: int = EMBED_TIMEOUT) -> Optional[List[List[float]]]:
    payload = json.dumps({"model": model, "input": list(texts)}).encode("utf-8")
    req = urllib.request.Request(base + "/api/embed", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        js = json.loads(resp.read().decode("utf-8", errors="replace"))
    embs = js.get("embeddings")
    if not isinstance(embs, list) or len(embs) != len(texts):
        return None
    for v in embs:
        if not isinstance(v, list) or not v:
            return None
    return embs


def _normalize_model(name: str) -> str:
    n = (name or "").strip()
    return n if ":" in n else (n + ":latest" if n else "")


def _local_models(base: str, timeout: int = 5) -> List[Dict[str, Any]]:
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=timeout) as r:
            js = json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - 服务不可用时返回空
        return []
    return js.get("models") or []


def _is_embed_model(item: Dict[str, Any]) -> bool:
    caps = item.get("capabilities") or []
    if "embedding" in caps:
        return True
    name = str(item.get("name") or "").lower()
    return any(h in name for h in _EMBED_NAME_HINT)


def _param_size(item: Dict[str, Any]) -> float:
    """把 parameter_size（7.6B / 307.58M / 8.0K）统一折算为 B（十亿）量级。"""
    raw = str((item.get("details") or {}).get("parameter_size") or "").strip().upper()
    m = re.match(r"^([\d.]+)\s*([BMK]?)", raw)
    if not m:
        return 0.0
    try:
        val = float(m.group(1))
    except ValueError:
        return 0.0
    unit = m.group(2)
    if unit == "B":
        return val
    if unit == "M":
        return val / 1024.0
    if unit == "K":
        return val / (1024.0 * 1024.0)
    return val / (1024.0 * 1024.0 * 1024.0)


def resolve_embed_model(ai_cfg: Optional[Dict[str, Any]] = None) -> Tuple[str, bool]:
    """确定实际可用的嵌入模型。

    旧实现把默认模型硬编码为 bge-m3：一旦本机装的是 qwen3-embedding /
    embeddinggemma 等其他嵌入模型，``/api/embed`` 会持续失败并静默退化为
    纯 BM25——用户看到「BM25 检索模式」却不知道向量从未生效。

    策略：优先使用用户配置的模型；若本机不存在，则自动回退到任意一个
    已安装的嵌入模型。结果缓存 2 分钟，避免每次检索都探测一次 Ollama。
    """
    cfg = ai_cfg or {}
    base = str(cfg.get("base_url") or "http://127.0.0.1:11434").rstrip("/")
    want = str(cfg.get("embed_model") or "").strip()
    if not cfg.get("embed_enabled", True):
        return want, False

    key = f"{base}|{want}"
    now = time.time()
    if _MODEL_CACHE.get("key") == key and now - float(_MODEL_CACHE.get("ts", 0.0)) < _MODEL_TTL:
        return str(_MODEL_CACHE.get("model", "")), bool(_MODEL_CACHE.get("ok"))

    models = _local_models(base)
    names = {_normalize_model(str(m.get("name") or "")) for m in models}
    if want and _normalize_model(want) in names:
        model, ok = want, True
    else:
        embeds = [m for m in models if _is_embed_model(m)]
        if embeds:
            # 优先参数量大的模型（语义表达更强）；无法解析时按名称排序保持稳定
            def _psize(m: Dict[str, Any]) -> float:
                return _param_size(m)
            embeds.sort(key=lambda m: (-_psize(m), str(m.get("name") or "")))
            model, ok = str(embeds[0].get("name") or ""), True
    if not model:
        ok = False
    _MODEL_CACHE.update(key=key, ts=now, model=model, ok=ok)
    return model, ok


def embed_status(ai_cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """供前端展示的嵌入检索可用性诊断。"""
    model, ok = resolve_embed_model(ai_cfg)
    return {"embed_model": model, "embed_resolved": ok}



def embed_batch(texts: Sequence[str], ai_cfg: Optional[Dict[str, Any]] = None,
                batch: int = EMBED_BATCH, timeout: int = EMBED_TIMEOUT
                ) -> Optional[List[List[float]]]:
    """分批嵌入；任一批次彻底失败返回 None（调用方降级为纯 BM25）。"""
    if not texts:
        return None
    cfg = ai_cfg or {}
    base = str(cfg.get("base_url") or "http://127.0.0.1:11434").rstrip("/")
    model, ok = resolve_embed_model(cfg)
    if not ok or not model:
        return None

    texts = [(t or "")[:EMBED_MAX_CHARS] for t in texts]
    out: List[List[float]] = []
    i, n = 0, len(texts)
    while i < n:
        part = texts[i:i + batch]
        got: Optional[List[List[float]]] = None
        size = len(part)
        while size >= 1:                      # 失败折半重试
            try:
                got = _post(base, model, part[:size], timeout)
            except Exception:                 # noqa: BLE001 - 网络/模型异常
                got = None
            if got is not None:
                break
            size //= 2
        if got is None:
            return None
        out.extend(got)
        i += len(part)
    return out if len(out) == n else None


def embed_query(text: str, ai_cfg: Optional[Dict[str, Any]] = None
                ) -> Optional[List[float]]:
    got = embed_batch([text], ai_cfg, batch=1, timeout=min(EMBED_TIMEOUT, 20))
    return got[0] if got else None


def embed_many(texts: Sequence[str], ai_cfg: Optional[Dict[str, Any]] = None
               ) -> List[Optional[List[float]]]:
    """一次批量嵌入多条文本，返回与输入等长的向量列表（失败项为 None）。

    用于核验链路：把所有待核验片段的向量**一次性**算好，避免逐片段各发一次请求。
    """
    if not texts:
        return []
    vecs = embed_batch(list(texts), ai_cfg)
    if vecs is None:
        return [None] * len(texts)
    return list(vecs)


def cos(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))
