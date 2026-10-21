# -*- coding: utf-8 -*-
"""PDF 解析（含扫描件 OCR 兜底）。

处理策略（按页判定，而非整篇一刀切）：
1. 先用 pypdf 抽文本层；
2. 单页文本量低于阈值 → 判定为扫描页，改用 PyMuPDF 栅格化后送 OCR；
3. 整篇都无文本层且无 OCR 引擎 → 抛出**可操作**的提示，而不是静默入库为空。

混排文档（部分页有文本层、部分页是扫描图）也能正确处理。
"""

from __future__ import annotations

import io
import os
import tempfile
from typing import Any, List, Optional, Tuple

from checkers.rag.parsers.base import Block, normalize

#: 单页文本量低于该字符数即判定为扫描页
SCAN_PAGE_MIN_CHARS = 24
#: 栅格化 DPI：中文小字号下 200dpi 识别率与耗时较平衡
RASTER_DPI = 200
#: 单份 PDF 最多 OCR 的页数（防止超大扫描件卡死）
MAX_OCR_PAGES = 200


def _pypdf_pages(raw: bytes) -> List[str]:
    """按页抽取文本；单页失败返回空串而非中断。"""
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:  # noqa: BLE001
        return []
    try:
        rd = PdfReader(io.BytesIO(raw))
    except Exception:  # noqa: BLE001
        return []
    out: List[str] = []
    for pg in rd.pages:
        try:
            out.append(normalize(pg.extract_text() or ""))
        except Exception:  # noqa: BLE001
            out.append("")
    return out


def _rasterize(raw: bytes, page_indexes: List[int]) -> List[bytes]:
    """把指定页渲染为 PNG 字节流（供 OCR 使用）。"""
    if not page_indexes:
        return []
    try:
        import fitz  # type: ignore
    except Exception:  # noqa: BLE001
        return []
    imgs: List[bytes] = []
    try:
        doc = fitz.open(stream=raw, filetype="pdf")
    except Exception:  # noqa: BLE001
        return []
    try:
        zoom = RASTER_DPI / 72.0
        mat = fitz.Matrix(zoom, zoom)
        for pi in page_indexes:
            if pi >= doc.page_count:
                imgs.append(b"")
                continue
            try:
                pix = doc[pi].get_pixmap(matrix=mat)
            except Exception:  # noqa: BLE001
                try:
                    pix = doc[pi].get_pixmap()
                except Exception:  # noqa: BLE001
                    imgs.append(b"")
                    continue
            try:
                imgs.append(pix.tobytes("png"))
            except Exception:  # noqa: BLE001
                imgs.append(b"")
    finally:
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass
    return imgs


def parse_pdf(name: str, raw: bytes, ocr: bool = True,
              ocr_engine: str = "auto") -> Tuple[List[Block], Dict[str, Any]]:
    """解析 PDF。返回 ``(blocks, 诊断信息)``。

    诊断信息用于在界面上回显「文本层 / OCR」各占多少页，便于排查识别质量。
    """
    pages = _pypdf_pages(raw)
    diag: Dict[str, Any] = {"pages": len(pages), "text_pages": 0,
                            "ocr_pages": 0, "empty_pages": 0, "ocr_used": False}

    if not pages:
        # pypdf 整体失败（加密 / 损坏）：尝试 PyMuPDF 直抽
        try:
            import fitz  # type: ignore
            doc = fitz.open(stream=raw, filetype="pdf")
            pages = [normalize(doc[i].get_text() or "")
                     for i in range(doc.page_count)]
            doc.close()
        except Exception:  # noqa: BLE001
            pages = []
        diag["pages"] = len(pages)

    if not pages:
        raise ValueError("PDF 解析失败：文件可能已损坏或加密，请解密后重新上传")

    scan_idx = [i for i, t in enumerate(pages) if len(t) < SCAN_PAGE_MIN_CHARS]
    diag["text_pages"] = len(pages) - len(scan_idx)

    if scan_idx and ocr:
        from checkers.rag.parsers.ocr import get_engine
        eng = get_engine(ocr_engine)
        if eng is not None:
            limited = scan_idx[:MAX_OCR_PAGES]
            imgs = _rasterize(raw, limited)
            for pi, img in zip(limited, imgs):
                if img:
                    txt = normalize(eng.image_to_text(img))
                    if txt:
                        pages[pi] = txt
                        diag["ocr_pages"] += 1
            diag["ocr_used"] = diag["ocr_pages"] > 0
        else:
            diag["ocr_error"] = "未检测到可用 OCR 引擎，扫描页已跳过"

    blocks: List[Block] = []
    for i, t in enumerate(pages):
        if len(t) >= 2:
            blocks.append((f"第 {i + 1} 页", t))
        else:
            diag["empty_pages"] += 1

    if not blocks:
        if scan_idx and not diag.get("ocr_used"):
            raise ValueError(
                "该 PDF 无可提取文本层（疑似扫描件），且本机未安装 OCR 引擎，无法识别。"
                "请执行：pip install rapidocr-onnxruntime"
            )
        raise ValueError("未能从 PDF 中提取到有效文本")
    return blocks, diag
