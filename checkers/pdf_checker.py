# -*- coding: utf-8 -*-
"""
PDF (.pdf) 低级错误检测引擎
=========================================
覆盖范围：
    - 解析未加密、完整、未损坏的 PDF，提取每页原生可复制文本层
    - 对每页文本运行：文字规范检测 / 自定义规则 / 自定义词库
    - 加密文档 / 损坏文档 / 扫描图片型 PDF 统一进入异常清单

强制约束：
    - 仅提取原生文本层，绝不 OCR、绝不识别图片文字、绝不解析图层
    - 全部本地计算（pypdf），不联网、不修改原始文件

实现说明：
    pypdf 的 extract_text() 仅读取 PDF 内嵌文本流；扫描件/图片型 PDF
    文本层为空，将被判定为「扫描图片型 PDF」并隔离，符合 §1 异常隔离要求。
"""

from __future__ import annotations

import os
from typing import Callable, List, Optional

from checkers.base import (
    STATUS_ISSUE,
    STATUS_PASS,
    STATUS_UNREADABLE,
    FileResult,
    Issue,
    clip,
    pdf_location,
    precheck_pdf,
)
from checkers.textnorm_checker import TextNormChecker
from checkers.fluency_checker import FluencyChecker
from checkers.custom_rules import CustomRuleEngine
from checkers.wordbank import WordBankEngine
from config.config_manager import RuleConfig


class PdfChecker:
    """PDF 检测器。一个实例对应一次文件检测。"""

    def __init__(self, config: RuleConfig, fluency_sensitivity: str = "normal",
                 progress: Optional[Callable[[float, str, str], bool]] = None,
                 ocr_enabled: bool = False, ocr_engine: str = "auto",
                 scan_pdf_skip: str = "auto") -> None:
        self.cfg = config
        self.kind = "pdf"
        self.issues: List[Issue] = []
        self._limit = config.max_issues_per_file()
        self._fluency_sensitivity = fluency_sensitivity
        self._progress = progress
        # OCR 兜底：扫描件 PDF 在原生文本层为空时，是否栅格化后经 OCR 引擎识别。
        # 默认取决于后台设置（通常关闭，保持离线保守）；无可用引擎时自动跳过。
        self.ocr_enabled = ocr_enabled
        self.ocr_engine = ocr_engine
        self.scan_pdf_skip = scan_pdf_skip

    def _hook(self, pct: float, stage: str, log: str) -> bool:
        if not self._progress:
            return True
        try:
            return bool(self._progress(pct, stage, log))
        except Exception:  # noqa: BLE001 - 钩子异常不影响检测
            return True

    def _add_engine_note(self, msg: str) -> None:
        self.issues.append(Issue(
            rule_key="engine_note",
            rule_title="检测引擎提示",
            severity="low",
            location="—",
            detail=msg,
            snippet="",
            suggestion="该部分内容结构特殊，建议人工重点复核。",
        ))

    def check(self, path: str, display_name: Optional[str] = None) -> FileResult:
        """检测单个 PDF。异常一律转为「无法解析」结果。"""
        name = display_name or os.path.basename(path)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0

        result = FileResult(file_name=name, file_path=path, file_type="PDF", file_size=size)

        # 1) 结构预检：加密 / 损坏
        if not self._hook(2.0, "parse", f"正在解析「{name}」：PDF 结构预检…"):
            return result
        reason = precheck_pdf(path)
        if reason:
            result.status = STATUS_UNREADABLE
            result.error_message = reason
            return result

        # 2) 提取文本（本地 pypdf，无 OCR）
        if not self._hook(5.0, "parse", f"正在解析「{name}」PDF 文档提取文本…"):
            return result
        try:
            from pypdf import PdfReader
            reader = PdfReader(path)
            pages = reader.pages
            total = len(pages)
        except Exception as exc:  # noqa: BLE001
            result.status = STATUS_UNREADABLE
            result.error_message = "PDF 解析失败：" + clip(str(exc), 120)
            return result

        # 3) 逐页提取原生文本层
        all_text: List[str] = []
        for idx, page in enumerate(pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:  # noqa: BLE001
                text = ""
            all_text.append(text.strip())

        stat = {"pages": total, "ocr_used": False, "ocr_pages": 0}

        # 4) 扫描图片型 PDF：按需 OCR 兜底（默认关闭，需在设置中启用）
        if not any(t.strip() for t in all_text):
            recovered = self._try_ocr(path, total, stat)
            if recovered is None:
                result.status = STATUS_UNREADABLE
                if self.ocr_enabled and self.scan_pdf_skip != "always":
                    result.error_message = (
                        "扫描图片型 PDF（无可提取文本层）；已尝试 OCR 但本机未安装可用 "
                        "OCR 引擎（建议安装 tesseract+chi_sim 语言包或 rapidocr-onnxruntime），无法解析"
                    )
                else:
                    result.error_message = "扫描图片型 PDF（无可提取文本层），本工具不执行 OCR，无法解析"
                return result
            all_text = recovered

        # 5) 规则检测
        if not self._run_page_checks(all_text, total):
            return result

        self._hook(100.0, "summary", f"正在收集「{name}」位置信息：页码…")
        result.issues = self.issues
        result.stats = stat
        result.truncated = len(self.issues) >= self._limit
        result.status = STATUS_ISSUE if self.issues else STATUS_PASS
        return result

    def _run_page_checks(self, all_text: List[str], total: int) -> bool:
        """对每页文本跑规则检测；返回 False 表示任务被取消。"""
        self.issues = []
        tn = TextNormChecker(self.cfg, self.issues, self._limit)
        fl = FluencyChecker(self.cfg, self.issues, self._limit, self._fluency_sensitivity)
        cust = CustomRuleEngine(self.issues, self._limit)
        wb = WordBankEngine(self.issues, self._limit)

        for idx, text in enumerate(all_text, start=1):
            if not self._hook(8.0 + 82.0 * (idx - 1) / max(total, 1), "format_error",
                              f"正在检测第 {idx}/{total} 页（格式规范/语句通顺/行业词库）…"):
                return False
            if len(self.issues) >= self._limit:
                break
            if not text.strip():
                continue
            loc = pdf_location(idx)
            try:
                tn.check_text(loc, text)
                fl.check_text(loc, text)
                cust.check_text("pdf", loc, text)
                wb.check_text("pdf", loc, text)
            except Exception as exc:  # noqa: BLE001
                self._add_engine_note(f"第 {idx} 页检测异常：{type(exc).__name__}")
        return True

    def _try_ocr(self, path: str, total: int, stat: dict) -> Optional[List[str]]:
        """扫描件 OCR 兜底：栅格化各页后经 OCR 引擎识别。

        返回 OCR 后的每页文本列表；未启用 / 跳过 / 无可用引擎 / 仍无文本时返回 None。
        """
        if not self.ocr_enabled or self.scan_pdf_skip == "always":
            return None
        try:
            from checkers.rag.parsers.ocr import get_engine
            from checkers.rag.parsers.pdf import _rasterize
        except Exception:  # noqa: BLE001
            return None
        engine = get_engine(self.ocr_engine)
        if engine is None:
            return None
        self._hook(15.0, "ocr", f"正在对「{os.path.basename(path)}」扫描页做 OCR 识别…")
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            return None
        imgs = _rasterize(raw, list(range(total)))
        recovered: List[str] = []
        for img in imgs:
            try:
                txt = engine.image_to_text(img) if img else ""
            except Exception:  # noqa: BLE001 - 单页失败不影响整体
                txt = ""
            recovered.append((txt or "").strip())
        used = sum(1 for t in recovered if t)
        stat["ocr_used"] = used > 0
        stat["ocr_pages"] = used
        if not any(t.strip() for t in recovered):
            return None
        return recovered


def check_pdf(path: str, config: RuleConfig, display_name: Optional[str] = None,
              fluency_sensitivity: str = "normal",
              progress: Optional[Callable[[float, str, str], bool]] = None,
              ocr_enabled: bool = False, ocr_engine: str = "auto",
              scan_pdf_skip: str = "auto") -> FileResult:
    """对外统一函数式入口。"""
    return PdfChecker(config, fluency_sensitivity, progress,
                      ocr_enabled, ocr_engine, scan_pdf_skip).check(path, display_name)
