# -*- coding: utf-8 -*-
"""
批量检测调度模块
=========================================
职责：
    1. 根据文件类型分发到 Word / Excel 检测引擎
    2. 批量检测时提供进度回调（供界面实时展示进度）
    3. 单文件失败自动降级为「无法解析」记录，绝不中断整体任务
    4. 汇总统计数据（供统计面板与报告使用）

保密说明：所有文件读取均在本地完成，无任何网络行为。
"""

from __future__ import annotations

import concurrent.futures
import os
from typing import Callable, Dict, List, Optional, Tuple

from checkers.base import (
    STATUS_UNREADABLE,
    SUPPORTED_EXT,
    FileResult,
    LEGACY_EXT,
    detect_file_type,
    unsupported_reason,
)
from checkers.excel_checker import check_excel
from checkers.word_checker import check_word
from checkers.pdf_checker import check_pdf
from config.config_manager import RuleConfig

# 进度回调签名：(已完成数, 总数, 当前文件名) -> None
ProgressCallback = Callable[[int, int, str], None]

# 阶段进度钩子签名：(文件内进度百分比 0-100, 阶段 key, 日志文本) -> 是否继续(False=已取消)
# 阶段 key：parse 文件解析 / page 页码定位 / format_error 格式错误检测 /
#           fluency 语句通顺度检测 / wordbank 行业词库规则匹配 / summary 结果汇总
ProgressHook = Callable[[float, str, str], bool]


def make_progress_hook(cancel_check: Optional[Callable[[], bool]] = None,
                       emit: Optional[Callable[[float, str, str], None]] = None) -> ProgressHook:
    """构造进度钩子：emit 输出进度/日志；cancel_check 为 True 时返回 False（取消）。"""
    def hook(percent: float, stage: str, log: str) -> bool:
        if cancel_check and cancel_check():
            return False
        if emit:
            try:
                emit(percent, stage, log)
            except Exception:  # noqa: BLE001 - 输出失败不影响检测
                pass
        return True
    return hook


def _unsupported_result(path: str, name: str, reason: str) -> FileResult:
    """生成一个「无法解析 / 不支持」的结果（统一体积读取）。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    return FileResult(
        file_name=name, file_path=path, file_type="不支持", file_size=size,
        status=STATUS_UNREADABLE, error_message=reason,
    )


def _dispatch(path: str, config: RuleConfig, name: str, ftype: str,
              fluency_sensitivity: str = "normal",
              progress: Optional[ProgressHook] = None) -> FileResult:
    """按文件类型分发到具体引擎（在线程内执行）。"""
    if ftype == "Word":
        return check_word(path, config, name, fluency_sensitivity, progress=progress)
    if ftype == "PDF":
        return check_pdf(path, config, name, fluency_sensitivity, progress=progress)
    return check_excel(path, config, name, fluency_sensitivity, progress=progress)


def _run_with_timeout(fn: Callable, args: tuple, timeout: float) -> FileResult:
    """在独立线程中执行检测，超时则抛出 concurrent.futures.TimeoutError。"""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn, *args)
        return fut.result(timeout=timeout)


def _is_blank(result: FileResult) -> bool:
    """判断一个「通过且无问题」的结果是否为空白文档（用于自动忽略）。"""
    s = result.stats or {}
    if result.file_type == "Word":
        return int(s.get("paragraphs", 0)) == 0 and int(s.get("tables", 0)) == 0
    if result.file_type == "Excel":
        return int(s.get("sheets", 0)) == 0 or int(s.get("cells", 0)) == 0
    return False


def check_single(path: str, config: RuleConfig, display_name: Optional[str] = None,
                 opts: Optional[Dict[str, Any]] = None,
                 progress: Optional[ProgressHook] = None) -> FileResult:
    """
    检测单个文件（自动分发引擎）。

    任何异常都会被捕获为「无法解析」结果，保证调用方永不因单文件崩溃。

    progress: 阶段进度钩子（percent 0-100, 阶段 key, 日志文本）→ bool
        返回 False 表示任务已被取消，检测立即中止并返回当前结果（调用方负责丢弃）。

    opts（来自后台「检测通用 / 文件解析」设置）：
        enable_pdf        是否开启 PDF 检测
        enable_legacy     是否开启旧版 .doc/.xls 兼容提示
        parse_timeout     单文件解析超时秒数（0 = 不限制）
        auto_ignore_blank 是否自动忽略空白文件（由调用方在 run_detection 中过滤）
        fluency_sensitivity 语句通顺检测灵敏度（loose/normal/strict）
    """
    name = display_name or os.path.basename(path)
    o = opts or {}
    enable_pdf = bool(o.get("enable_pdf", True))
    enable_legacy = bool(o.get("enable_legacy", False))
    parse_timeout = o.get("parse_timeout", 0) or 0
    fluency_sensitivity = o.get("fluency_sensitivity", "normal") or "normal"

    ext = os.path.splitext(name)[1].lower()
    ftype = detect_file_type(name)

    # 旧版 .doc/.xls：无解析组件，按开关给出不同提示（不进入通用「不支持」分支）
    if ext in LEGACY_EXT:
        if enable_legacy:
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            return FileResult(
                file_name=name, file_path=path, file_type="不支持", file_size=size,
                status=STATUS_UNREADABLE,
                error_message="旧版格式兼容解析已开启，但当前环境未集成 .doc/.xls 解析组件，"
                              "建议另存为 .docx/.xlsx 后重试",
            )
        return _unsupported_result(path, name, unsupported_reason(name))

    # 明确不支持的格式
    if ftype == "不支持":
        return _unsupported_result(path, name, unsupported_reason(name))

    # PDF 检测开关（后台设置关闭时跳过）
    if ftype == "PDF" and not enable_pdf:
        return _unsupported_result(path, name, "PDF 检测已在后台设置中关闭，本次跳过 PDF 解析")

    # 文件体积上限保护
    try:
        size_mb = os.path.getsize(path) / (1024 * 1024)
        limit = config.max_file_size_mb()
        if size_mb > limit:
            return FileResult(
                file_name=name,
                file_path=path,
                file_type=ftype,
                file_size=int(size_mb * 1024 * 1024),
                status=STATUS_UNREADABLE,
                error_message=f"文件体积 {size_mb:.1f} MB 超过配置上限 {limit} MB，已跳过",
            )
    except OSError:
        pass

    try:
        if parse_timeout > 0:
            result = _run_with_timeout(_dispatch, (path, config, name, ftype, fluency_sensitivity, progress),
                                       parse_timeout)
        else:
            result = _dispatch(path, config, name, ftype, fluency_sensitivity, progress)
    except concurrent.futures.TimeoutError:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        return FileResult(
            file_name=name, file_path=path, file_type=ftype, file_size=size,
            status=STATUS_UNREADABLE,
            error_message=f"解析超过 {parse_timeout}s 超时，已跳过（可在后台设置调高超时时间）",
        )
    except Exception as exc:  # noqa: BLE001 - 最外层兜底，保证批量任务不中断
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        return FileResult(
            file_name=name,
            file_path=path,
            file_type=ftype,
            file_size=size,
            status=STATUS_UNREADABLE,
            error_message=f"检测过程发生未预期异常：{type(exc).__name__} - {str(exc)[:120]}",
        )

    return result


def check_batch(
    items: List[Tuple[str, str]],
    config: RuleConfig,
    progress_cb: Optional[ProgressCallback] = None,
) -> List[FileResult]:
    """
    批量检测。

    参数:
        items: [(实际磁盘路径, 展示文件名), ...]
        config: 规则配置
        progress_cb: 进度回调，用于界面实时刷新
    """
    results: List[FileResult] = []
    total = len(items)
    for idx, (path, name) in enumerate(items, start=1):
        if progress_cb:
            try:
                progress_cb(idx - 1, total, name)
            except Exception:  # noqa: BLE001 - 回调异常不影响检测
                pass
        results.append(check_single(path, config, name))
    if progress_cb:
        try:
            progress_cb(total, total, "全部完成")
        except Exception:  # noqa: BLE001
            pass
    return results


def collect_folder(folder: str, recursive: bool = True, skip_hidden: bool = True) -> List[str]:
    """
    扫描本地文件夹，收集所有支持的 Office 文件路径。

    自动跳过：
        - Office 临时锁定文件（以 ~$ 开头）
        - 隐藏文件与隐藏目录（可配置）
    """
    found: List[str] = []
    if not os.path.isdir(folder):
        return found

    for root, dirs, files in os.walk(folder):
        if skip_hidden:
            dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in files:
            if fname.startswith("~$"):
                continue
            if skip_hidden and fname.startswith("."):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext in SUPPORTED_EXT:
                found.append(os.path.join(root, fname))
        if not recursive:
            break
    return sorted(found)


def summarize(results: List[FileResult]) -> Dict[str, object]:
    """
    汇总统计（供统计面板与 Word 报告使用）。

    返回字段：
        total_files / pass_files / issue_files / error_files
        total_issues / severity{high,medium,low}
        by_rule{规则名: 数量} / by_type{Word: n, Excel: n}
        ignored_count / checked_count
    """
    summary: Dict[str, object] = {
        "total_files": len(results),
        "pass_files": 0,
        "issue_files": 0,
        "error_files": 0,
        "total_issues": 0,
        "severity": {"high": 0, "medium": 0, "low": 0},
        "by_rule": {},
        "by_type": {"Word": 0, "Excel": 0, "PDF": 0, "不支持": 0},
        "ignored_count": 0,
        "checked_count": 0,
    }

    by_rule: Dict[str, int] = {}
    by_type: Dict[str, int] = {"Word": 0, "Excel": 0, "PDF": 0, "不支持": 0}
    sev: Dict[str, int] = {"high": 0, "medium": 0, "low": 0}

    for res in results:
        by_type[res.file_type] = by_type.get(res.file_type, 0) + 1

        if res.status == STATUS_UNREADABLE:
            summary["error_files"] = int(summary["error_files"]) + 1
        elif res.active_issue_count > 0:
            summary["issue_files"] = int(summary["issue_files"]) + 1
        else:
            summary["pass_files"] = int(summary["pass_files"]) + 1

        for issue in res.issues:
            if issue.state == "ignored":
                summary["ignored_count"] = int(summary["ignored_count"]) + 1
                continue
            if issue.state == "checked":
                summary["checked_count"] = int(summary["checked_count"]) + 1
            summary["total_issues"] = int(summary["total_issues"]) + 1
            if issue.severity in sev:
                sev[issue.severity] += 1
            by_rule[issue.rule_title] = by_rule.get(issue.rule_title, 0) + 1

    summary["severity"] = sev
    summary["by_rule"] = dict(sorted(by_rule.items(), key=lambda kv: kv[1], reverse=True))
    summary["by_type"] = by_type
    return summary
