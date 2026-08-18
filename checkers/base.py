# -*- coding: utf-8 -*-
"""
检测引擎公共基础模块
=========================================
定义：
    - Issue：单条问题记录的数据结构
    - FileResult：单个文件的检测结果
    - 通用工具函数：文件类型识别、加密/损坏文件预检、文本清洗、摘要截断

保密说明：纯本地计算，无任何网络访问。
"""

from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 支持与不支持的扩展名
# ---------------------------------------------------------------------------
SUPPORTED_WORD_EXT = {".docx"}
SUPPORTED_EXCEL_EXT = {".xlsx", ".xlsm"}
SUPPORTED_PDF_EXT = {".pdf"}
SUPPORTED_EXT = SUPPORTED_WORD_EXT | SUPPORTED_EXCEL_EXT | SUPPORTED_PDF_EXT
# 明确不支持但常见的老格式（给出友好提示）
LEGACY_EXT = {".doc": "Word 97-2003 旧格式", ".xls": "Excel 97-2003 旧格式",
              ".ppt": "PowerPoint 旧格式", ".wps": "WPS 旧格式", ".et": "WPS 表格旧格式"}

# 状态常量
STATUS_PASS = "pass"          # 检测通过，无问题
STATUS_ISSUE = "issue"        # 检测完成，存在问题
STATUS_UNREADABLE = "error"   # 无法解析（加密/损坏/格式不支持）


@dataclass
class Issue:
    """单条低级错误记录。"""

    rule_key: str                 # 规则标识，如 full_width_space
    rule_title: str               # 规则中文名，如 多余全角空格
    severity: str                 # high / medium / low
    location: str                 # 位置描述，如「第 12 段」「Sheet1!C5」「表 2 第 3 行」
    detail: str                   # 问题说明
    snippet: str = ""             # 问题原文片段
    suggestion: str = ""          # 整改建议
    # 界面交互状态：normal / ignored / checked
    state: str = "normal"
    # 扩展字段（自定义规则 / 词库命中使用；内置规则留空，保持向后兼容）
    category: str = ""            # format_error | expression | custom_rule | wordbank
    source: str = ""              # builtin | custom | wordbank
    group: str = ""               # 规则/词库分组名
    tag: str = ""                 # 问题类型标签

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FileResult:
    """单个文件的完整检测结果。"""

    file_name: str
    file_path: str
    file_type: str                       # Word / Excel / 不支持
    file_size: int                       # 字节
    status: str = STATUS_PASS
    error_message: str = ""              # 无法解析时的原因
    issues: List[Issue] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)   # 文档规模统计
    truncated: bool = False              # 是否因超过上限而截断问题列表

    # ---------------- 便捷属性 ----------------
    @property
    def issue_count(self) -> int:
        return len(self.issues)

    @property
    def active_issue_count(self) -> int:
        """未被忽略的问题数量。"""
        return sum(1 for i in self.issues if i.state != "ignored")

    def severity_count(self) -> Dict[str, int]:
        """按严重级别汇总。"""
        out = {"high": 0, "medium": 0, "low": 0}
        for issue in self.issues:
            if issue.state == "ignored":
                continue
            if issue.severity in out:
                out[issue.severity] += 1
        return out

    def rule_count(self) -> Dict[str, int]:
        """按规则名称汇总（用于统计面板）。"""
        out: Dict[str, int] = {}
        for issue in self.issues:
            if issue.state == "ignored":
                continue
            out[issue.rule_title] = out.get(issue.rule_title, 0) + 1
        return out

    @property
    def size_text(self) -> str:
        return human_size(self.file_size)

    @property
    def status_text(self) -> str:
        if self.status == STATUS_UNREADABLE:
            return "无法解析"
        if self.active_issue_count > 0:
            return "存在问题"
        return "检测通过"


# ---------------------------------------------------------------------------
# 通用工具函数
# ---------------------------------------------------------------------------
def human_size(num_bytes: int) -> str:
    """字节数转换为可读文本。"""
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def detect_file_type(file_name: str) -> str:
    """根据扩展名判断文件类型：Word / Excel / PDF / 不支持。"""
    ext = os.path.splitext(file_name)[1].lower()
    if ext in SUPPORTED_WORD_EXT:
        return "Word"
    if ext in SUPPORTED_EXCEL_EXT:
        return "Excel"
    if ext in SUPPORTED_PDF_EXT:
        return "PDF"
    return "不支持"


def precheck_pdf(path: str) -> Optional[str]:
    """
    PDF 文件预检（本地读取，不联网、不 OCR）。

    返回 None 表示可继续解析；返回字符串表示无法解析原因：
        - 文件不存在 / 空文件
        - 加密文档（需密码）
        - 结构损坏（PdfReadError）
    """
    if not os.path.exists(path):
        return "文件不存在或已被移动"
    try:
        size = os.path.getsize(path)
    except OSError:
        return "无法读取文件属性，可能被其他程序占用"
    if size == 0:
        return "文件为空（0 字节），内容已损坏"

    try:
        from pypdf import PdfReader
    except Exception:  # noqa: BLE001 - 库缺失属环境异常，按损坏处理避免崩溃
        return "PDF 解析组件缺失，无法解析"

    try:
        reader = PdfReader(path)
        if getattr(reader, "is_encrypted", False):
            return "PDF 已设置打开密码，加密文档无法解析"
        # 触发一次元数据读取，捕获结构损坏
        _ = len(reader.pages)
    except Exception as exc:  # noqa: BLE001
        return "PDF 解析失败（可能已损坏）：" + clip(str(exc), 120)
    return None


def unsupported_reason(file_name: str) -> str:
    """给出不支持的具体原因文案。"""
    ext = os.path.splitext(file_name)[1].lower()
    if ext in LEGACY_EXT:
        return f"{LEGACY_EXT[ext]}（{ext}）不受支持，请另存为 .docx / .xlsx 后重新检测"
    if not ext:
        return "文件无扩展名，无法识别格式"
    return f"不支持的文件格式（{ext}），当前仅支持 .docx / .xlsx"


def precheck_ooxml(path: str) -> Optional[str]:
    """
    OOXML 文件预检（本地读取字节，不联网）。

    返回 None 表示可以继续解析；返回字符串表示无法解析的原因：
        - 文件不存在 / 空文件
        - 老式 OLE 复合文档（.doc/.xls 改名或真旧格式）
        - 加密文档（OLE 容器 + EncryptedPackage）
        - ZIP 结构损坏
    """
    if not os.path.exists(path):
        return "文件不存在或已被移动"

    try:
        size = os.path.getsize(path)
    except OSError:
        return "无法读取文件属性，可能被其他程序占用"

    if size == 0:
        return "文件为空（0 字节），内容已损坏"

    # 读取文件头做魔数判断
    try:
        with open(path, "rb") as fp:
            head = fp.read(8)
    except OSError:
        return "文件被占用或无读取权限，请关闭正在编辑的程序后重试"

    # OLE2 复合文档头：D0 CF 11 E0 A1 B1 1A E1
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "文档已加密或为旧版二进制格式（OLE 复合文档），无法解析"

    # 正常 OOXML 必须是 ZIP：PK\x03\x04
    if not head.startswith(b"PK"):
        return "文件结构异常，非有效的 Office OOXML 文档（可能已损坏）"

    # ZIP 结构与关键部件校验
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad is not None:
                return f"压缩包内部损坏（首个损坏部件：{bad}）"
            names = set(zf.namelist())
            if "EncryptedPackage" in names:
                return "文档已设置打开密码，加密文档无法解析"
            if "[Content_Types].xml" not in names:
                return "缺少 [Content_Types].xml，文档结构损坏"
    except zipfile.BadZipFile:
        return "文件不是有效的压缩包结构，文档已损坏"
    except OSError:
        return "读取文件失败，文件可能被占用或损坏"

    return None


def clip(text: str, limit: int = 80) -> str:
    """截断文本用于展示，避免界面/报告被超长内容撑坏。"""
    if text is None:
        return ""
    text = str(text).replace("\r", " ").replace("\n", "⏎").replace("\t", "→")
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def visualize_ws(text: str) -> str:
    """把不可见空白字符可视化，便于在界面上定位问题字符。"""
    mapping = {
        "\u3000": "␣(全角空格)",
        "\u00a0": "␣(不换行空格)",
        "\u200b": "·(零宽空格)",
        "\u200c": "·(零宽非连接)",
        "\u200d": "·(零宽连接)",
        "\ufeff": "·(BOM)",
        "\u000b": "⇩(垂直制表)",
    }
    out = text
    for raw, show in mapping.items():
        out = out.replace(raw, show)
    return out


# ---------------------------------------------------------------------------
# 全局精准位置格式化（§2 统一展示文案）
# ---------------------------------------------------------------------------
def word_location(page: Optional[int], paragraph: int,
                  estimated: bool = False, note: str = "") -> str:
    """
    Word 位置文案：第 N 页 · 第 X 段。

    page 为 None 时（分页无法读取）按规范显示固定 fallback 文案。
    estimated=True 表示页码为行数估算（文档无显式分页符）。
    """
    if page is None:
        return "页码无法自动识别，请人工核对"
    suffix = "（估算）" if estimated else ""
    base = f"第 {page} 页{suffix} · 第 {paragraph} 段"
    return f"{base}（{note}）" if note else base


def excel_location(sheet: str, row: int, col: int) -> str:
    """Excel 位置文案：{sheet}：第 X 行，第 X 列。"""
    return f"{sheet}：第 {row} 行，第 {col} 列"


def pdf_location(page: int) -> str:
    """PDF 位置文案：第 N 页。"""
    return f"第 {page} 页"
