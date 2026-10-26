# -*- coding: utf-8 -*-
"""OCR 状态接口（文档核验侧）。

返回 OCR 在「核验链路」中的可用性诊断：是否已在后台设置启用、首选引擎、
当前可用引擎、以及预留的云 OCR（百度/腾讯）配置状态。
"""

from __future__ import annotations

from fastapi import APIRouter

from checkers.rag.parsers.ocr import ocr_status as _ocr_status
from config.settings_manager import load_settings

router = APIRouter()


@router.get("/api/ocr/status")
def api_ocr_status() -> dict:
    settings = load_settings()
    ocr_set = settings.get("ocr", {}) or {}
    preferred = str(ocr_set.get("engine", "tesseract") or "tesseract")
    st = _ocr_status(preferred)
    st["enabled_in_settings"] = bool(ocr_set.get("enabled", False))
    st["preferred_engine"] = preferred
    return st
