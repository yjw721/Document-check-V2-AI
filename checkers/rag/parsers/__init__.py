# -*- coding: utf-8 -*-
"""多格式解析器统一入口。

对外只暴露一个函数 ``extract_blocks(name, raw)``，
新增格式 = 新增一个解析模块 + 在本文件的 ``_PARSERS`` 登记，
切片层 / 索引层完全不感知文件类型。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Sequence, Tuple

from checkers.rag.parsers.base import Block, decode_text, normalize
from checkers.rag.parsers.image import IMAGE_EXTS, parse_image
from checkers.rag.parsers.office import (parse_csv, parse_doc, parse_docx,
                                         parse_plain, parse_xlsx)
from checkers.rag.parsers.pdf import parse_pdf

#: 解析器注册表：扩展名 → 解析函数
_PARSERS: Dict[str, Callable[..., Any]] = {}
for _e in (".txt", ".md"):
    _PARSERS[_e] = parse_plain
_PARSERS[".csv"] = parse_csv
_PARSERS[".docx"] = parse_docx
_PARSERS[".doc"] = parse_doc
_PARSERS[".xlsx"] = parse_xlsx
_PARSERS[".pdf"] = parse_pdf
for _e in IMAGE_EXTS:
    _PARSERS[_e] = parse_image

#: 上传时用于校验的全部受支持扩展名
SUPPORTED_EXTS: Tuple[str, ...] = tuple(sorted(_PARSERS))

#: 需要 OCR 的扩展名（用于提示与诊断）
OCR_EXTS: Tuple[str, ...] = tuple(sorted(IMAGE_EXTS))


def extract_blocks(name: str, raw: bytes, ocr: bool = True,
                   ocr_engine: str = "auto") -> Tuple[List[Block], Dict[str, Any]]:
    """解析文件为 ``(blocks, 诊断信息)``。

    ``blocks``: ``[(位置标识, 文本), ...]``
    ``诊断信息``: 供界面回显（页数 / OCR 页数 / 抽字数等），不含正文。
    """
    import os

    ext = os.path.splitext(str(name or ""))[1].lower()
    fn = _PARSERS.get(ext)
    if fn is None:
        raise ValueError(
            "不支持的文件类型：" + (ext or "(无扩展名)")
            + "；当前支持 " + "、".join(SUPPORTED_EXTS)
        )
    if not raw:
        raise ValueError("文件内容为空")

    if ext == ".pdf":
        return fn(name, raw, ocr, ocr_engine)
    if ext in IMAGE_EXTS:
        return fn(name, raw, ocr, ocr_engine)
    blocks = fn(name, raw)
    return blocks, {"chars": sum(len(t or "") for _l, t in blocks), "blocks": len(blocks)}


def parser_status(ocr_engine: str = "auto") -> Dict[str, Any]:
    """各格式解析能力自检（供「知识库 → 解析能力」面板展示）。"""
    from checkers.rag.parsers import ocr as _ocr

    def _mod(m: str) -> bool:
        import importlib.util as u
        try:
            return u.find_spec(m) is not None
        except (ImportError, ValueError):
            return False

    ost = _ocr.ocr_status(ocr_engine)
    return {
        "ok": True,
        "formats": [
            {"ext": ".txt/.md", "label": "纯文本 / Markdown", "available": True,
             "note": "标准库解析"},
            {"ext": ".docx", "label": "Word（新版）", "available": _mod("docx"),
             "note": "python-docx"},
            {"ext": ".doc", "label": "Word（旧版二进制）", "available": _mod("docx"),
             "note": "需 antiword/catdoc，否则请转存 .docx"},
            {"ext": ".pdf", "label": "PDF（含扫描件 OCR）", "available": _mod("pypdf"),
             "note": "pypdf + PyMuPDF 栅格化 + OCR"},
            {"ext": ".xlsx", "label": "Excel", "available": _mod("openpyxl"),
             "note": "openpyxl"},
            {"ext": ".csv", "label": "CSV", "available": True, "note": "标准库解析"},
            {"ext": "图片", "label": "PNG/JPG/BMP/TIFF/WEBP（OCR）",
             "available": bool(ost.get("available")),
             "note": (ost.get("engine") or "需安装 rapidocr-onnxruntime")
             + (" / " + ost["error"] if ost.get("error") else "")},
        ],
        "ocr": ost,
        "supported": list(SUPPORTED_EXTS),
    }


__all__ = ["Block", "SUPPORTED_EXTS", "OCR_EXTS", "extract_blocks",
           "parser_status", "decode_text", "normalize"]
