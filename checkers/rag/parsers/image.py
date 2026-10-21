# -*- coding: utf-8 -*-
"""图片解析（OCR）。

支持常见位图格式；识别前做两件提升中文准确率的事：
- 统一转 RGB，避免 CMYK / 调色板模式导致识别异常；
- 短边过小时按倍数放大（OCR 对 <20px 的中文几乎无解）。
"""

from __future__ import annotations

import io
from typing import Any, Dict, List, Tuple

from checkers.rag.parsers.base import Block, normalize

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp")

#: 短边低于该像素时放大，避免小字号中文识别失败
MIN_SIDE = 1000


def _prepare(image_bytes: bytes) -> bytes:
    """规范化图片，返回 PNG 字节；处理失败时返回原始字节。"""
    try:
        from PIL import Image  # type: ignore
    except Exception:  # noqa: BLE001 - 无 Pillow 时直接送原始字节
        return image_bytes
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        short = min(w, h)
        if short and short < MIN_SIDE:
            scale = MIN_SIDE / float(short)
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                             Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return image_bytes


def parse_image(name: str, raw: bytes, ocr: bool = True,
                ocr_engine: str = "auto") -> Tuple[List[Block], Dict[str, Any]]:
    """识别图片文字。返回 ``(blocks, 诊断信息)``。"""
    diag: Dict[str, Any] = {"ocr_used": False, "engine": ""}
    if not ocr:
        raise ValueError("已关闭 OCR，图片文件无法入库")
    from checkers.rag.parsers.ocr import get_engine, _installed
    eng = get_engine(ocr_engine)
    if eng is None:
        raise ValueError(
            "未检测到可用 OCR 引擎，图片无法入库。"
            "请执行：pip install rapidocr-onnxruntime"
        )
    diag["engine"] = eng.name
    text = normalize(eng.image_to_text(_prepare(raw)))
    diag["chars"] = len(text)
    diag["ocr_used"] = bool(text)
    if not text:
        raise ValueError("OCR 未从图片中识别出文字（可能图片过糊或不含文字）")
    return [("图片 OCR", text)], diag
