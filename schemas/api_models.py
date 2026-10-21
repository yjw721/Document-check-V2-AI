# -*- coding: utf-8 -*-
"""
API 数据契约模型（Pydantic v2）
单一真相源：后端请求/响应模型，前端 TypeScript 类型由脚本自动生成
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# 通用枚举
# ============================================================================
class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IssueState(str, Enum):
    NORMAL = "normal"
    IGNORED = "ignored"
    CHECKED = "checked"


class TaskStatus(str, Enum):
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"
    ERROR = "error"


class ScanCategory(str, Enum):
    INVALID = "invalid"
    DUPLICATE = "duplicate"
    NORMAL = "normal"


class FileType(str, Enum):
    WORD = "Word"
    EXCEL = "Excel"
    PDF = "PDF"
    UNSUPPORTED = "不支持"


class MatchMode(str, Enum):
    KEYWORD = "keyword"
    REGEX = "regex"


class RuleCategory(str, Enum):
    FORMAT_ERROR = "format_error"
    EXPRESSION = "expression"


class WordModule(str, Enum):
    TEXT_WORD = "text_word"
    FORMAT_REGEX = "format_regex"


class AiMode(str, Enum):
    LOCAL = "local"
    ONLINE = "online"


class AiBuildKind(str, Enum):
    DIALOGUE = "dialogue"
    TEXT = "text"
    DOC = "doc"


class AiMemorySampleStatus(str, Enum):
    PENDING = "pending"
    LEARNING = "learning"
    DONE = "done"
    FAILED = "failed"


class AiMemoryKind(str, Enum):
    WORDBANK = "wordbank"
    RULE = "rule"


# ============================================================================
# 基础响应模型
# ============================================================================
class ApiResponse(BaseModel):
    ok: bool
    message: Optional[str] = None


class PaginatedResponse(BaseModel):
    ok: bool
    logs: List[Any] = []
    total: int = 0


# ============================================================================
# 核心检测模型
# ============================================================================
class Issue(BaseModel):
    rule_key: str
    rule_title: str
    severity: Severity
    location: str
    detail: str
    snippet: str = ""
    suggestion: str = ""
    state: IssueState = IssueState.NORMAL
    category: str = ""
    source: str = ""
    group: str = ""
    tag: str = ""
    file_index: Optional[int] = None
    issue_index: Optional[int] = None


class FileResult(BaseModel):
    file_name: str
    file_type: FileType
    file_size: int
    status: Literal["pass", "issue", "error"]
    error_message: str = ""
    issues: List[Issue] = []
    stats: Dict[str, Any] = {}
    truncated: bool = False

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    @property
    def active_issue_count(self) -> int:
        return sum(1 for i in self.issues if i.state != IssueState.IGNORED)

    def severity_count(self) -> Dict[str, int]:
        out = {"high": 0, "medium": 0, "low": 0}
        for issue in self.issues:
            if issue.state == IssueState.IGNORED:
                continue
            if issue.severity.value in out:
                out[issue.severity.value] += 1
        return out


class Summary(BaseModel):
    total_files: int = 0
    total_issues: int = 0
    pass_files: int = 0
    error_files: int = 0
    severity: Dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    by_type: Dict[str, int] = {}
    by_rule: Dict[str, int] = {}
    ignored_count: int = 0
    checked_count: int = 0


class OverviewData(BaseModel):
    summary: Summary
    recent_issues: List[Issue] = []
    status: List[List[str]] = []
    cache: Dict[str, Any] = {}


# ============================================================================
# 任务模型
# ============================================================================
class AiChunkOut(BaseModel):
    chunk: int
    total: int
    content: str
    thinking: str
    preview: Optional[str] = None


class AiStream(BaseModel):
    file: str
    chunk: int
    total: int
    content: str
    thinking: str
    preview: str
    history: List[AiChunkOut] = []


class TaskSnapshot(BaseModel):
    status: TaskStatus
    progress: float
    stage: str
    stage_text: str
    logs: List[str] = []
    error: str = ""
    result: Optional[Any] = None
    ai_stream: Optional[AiStream] = None


class TaskStartResult(BaseModel):
    ok: bool
    task_id: str


# ============================================================================
# 规则模型
# ============================================================================
class RuleDef(BaseModel):
    title: Optional[str] = None
    enabled: bool = True
    severity: Severity = Severity.LOW
    suggestion: str = ""
    model_config = ConfigDict(extra="allow")


class RulesData(BaseModel):
    meta: Dict[str, Any] = Field(default_factory=dict)
    global_: Dict[str, Any] = Field(default_factory=dict, alias="global")
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class CustomRule(BaseModel):
    id: str
    name: str
    enabled: bool = True
    match_mode: MatchMode = MatchMode.KEYWORD
    pattern: str
    severity: Severity = Severity.LOW
    tag: str = ""
    suggestion: str = ""


class CustomRuleGroup(BaseModel):
    id: str
    name: str
    category: RuleCategory = RuleCategory.FORMAT_ERROR
    scope: Literal["all", "word", "excel", "pdf"] = "all"
    enabled: bool = True
    rules: List[CustomRule] = []


class CustomRulesData(BaseModel):
    groups: List[CustomRuleGroup] = []


# ============================================================================
# 词库模型
# ============================================================================
class WordEntry(BaseModel):
    id: str
    keyword: str
    tag: str = ""
    suggestion: str = ""
    enabled: bool = True


class WordGroup(BaseModel):
    id: str
    name: str
    module: WordModule = WordModule.TEXT_WORD
    scope: Literal["all", "word", "excel", "pdf"] = "all"
    enabled: bool = True
    entries: List[WordEntry] = []


class WordbanksData(BaseModel):
    groups: List[WordGroup] = []


# ============================================================================
# 设置模型
# ============================================================================
class UISettings(BaseModel):
    sidebar_default_collapsed: bool = False
    animation_enabled: bool = True
    theme_scheme: Literal["holographic", "dark", "ink"] = "holographic"
    accent_color: str = "#00d4ff"
    table_row_height: Literal["compact", "cozy", "comfortable"] = "comfortable"
    page_size: int = 50


class DetectionSettings(BaseModel):
    concurrency: int = 2
    parse_timeout: int = 0
    auto_ignore_blank: bool = False
    abnormal_popup: bool = True
    fluency_sensitivity: Literal["loose", "normal", "strict"] = "normal"


class ParseSettings(BaseModel):
    enable_pdf: bool = True
    enable_legacy: bool = False
    scan_pdf_skip: Literal["auto", "always", "never"] = "auto"


class ReportSettings(BaseModel):
    include_cover: bool = True
    default_dir: str = ""


class AiSettings(BaseModel):
    enabled: bool = False
    mode: AiMode = AiMode.LOCAL
    base_url: str = "http://127.0.0.1:11434"
    api_key: str = ""
    model: str = ""
    timeout: int = 120
    max_chars: int = 8000
    max_requests: int = 10
    ref_enabled: bool = True
    ref_max_chars: int = 4000
    create_enabled: bool = True
    temperature: float = 0.3
    num_ctx: int = 4096
    role: str = ""
    response_style: Literal["precise", "concise", "balance", "friendly", "formal"] = "balance"
    rate_limit: Optional[Dict[str, Any]] = None
    cache: Optional[Dict[str, Any]] = None


class SettingsData(BaseModel):
    ui: UISettings = UISettings()
    detection: DetectionSettings = DetectionSettings()
    parse: ParseSettings = ParseSettings()
    report: ReportSettings = ReportSettings()
    ai: AiSettings = AiSettings()
    role: Literal["admin", "user"] = "admin"
    model_config = ConfigDict(extra="allow")


# ============================================================================
# AI 相关模型
# ============================================================================
class AiRef(BaseModel):
    name: str
    chars: int
    enabled: bool
    updated: str


class AiActivityLog(BaseModel):
    ts: str
    event: str
    model: str
    ok: bool
    detail: str


class AiBuildEntry(BaseModel):
    keyword: str
    tag: str
    suggestion: str


class AiBuildWordbank(BaseModel):
    name: str
    entries: List[AiBuildEntry]


class AiBuildRule(BaseModel):
    name: str
    match_mode: MatchMode
    pattern: str
    severity: Severity
    suggestion: str


class AiBuildResult(BaseModel):
    wordbanks: List[AiBuildWordbank] = []
    rules: List[AiBuildRule] = []


class RuleRejected(BaseModel):
    name: str
    pattern: str
    reason: str


class RuleFilterStat(BaseModel):
    generated_rules: int = 0
    filtered_rules: int = 0
    accepted_rules: int = 0
    generated_entries: int = 0
    filtered_entries: int = 0
    accepted_entries: int = 0
    rejected: List[RuleRejected] = []


class BuildStartResp(BaseModel):
    ok: bool
    message: Optional[str] = None
    running: bool = False
    bid: Optional[str] = None


class BuildLogsResp(BaseModel):
    logs: List[ProcessLogEntry] = []
    state: ProcessState


# ============================================================================
# AI 自学习模型
# ============================================================================
class AiDiff(BaseModel):
    old: str
    new: str


class AiMemorySourceStatus(BaseModel):
    available: bool
    valid: bool
    files: List[Dict[str, str]] = []
    revised: Optional[str] = None
    message: str = ""


class AiMemorySample(BaseModel):
    id: str
    diffs: List[AiDiff] = []
    source_doc: str = ""
    revised_doc: str = ""
    note: str = ""
    status: AiMemorySampleStatus = AiMemorySampleStatus.PENDING
    enabled: bool = True
    learned_at: Optional[str] = None
    result_count: int = 0
    error: str = ""
    created_at: str = ""


class AiMemoryLearned(BaseModel):
    id: str
    sample_id: str
    kind: AiMemoryKind
    group_id: str
    entity_id: str
    keyword: Optional[str] = None
    name: Optional[str] = None
    match_mode: Optional[str] = None
    pattern: Optional[str] = None
    severity: Optional[str] = None
    suggestion: str = ""
    enabled: bool = True
    created_at: str = ""


class AiMemoryData(BaseModel):
    enabled: bool
    samples: List[AiMemorySample] = []
    learned: List[AiMemoryLearned] = []
    source_status: AiMemorySourceStatus
    stats: Dict[str, int] = {}


# ============================================================================
# 扫描模型
# ============================================================================
class ScanItem(BaseModel):
    item_id: str
    source: Literal["custom_rules", "wordbanks", "dictionary"]
    source_label: str
    group_id: str
    group_label: str
    entity_id: str
    line_no: Optional[int] = None
    kind: Literal["rule", "entry", "line"]
    name: str
    pattern: str
    suggestion: str
    match_mode: str
    enabled: bool
    tag: str
    category: ScanCategory
    reason: str
    keep: bool
    source_tag: str = ""


class ScanSourceStats(BaseModel):
    label: str
    scanned: int
    invalid: int
    duplicate: int
    normal: int


class ScanStats(BaseModel):
    scanned: int
    invalid: int
    duplicate: int
    duplicate_cleanable: int
    normal: int
    cleanable: int
    readonly: int
    sources: int
    by_source: Dict[str, ScanSourceStats]


class ScanResult(BaseModel):
    time: str
    stats: ScanStats
    items: List[ScanItem]


class ScanCleanResp(BaseModel):
    ok: bool
    message: str
    removed: int
    backup_file: Optional[str] = None
    stats: Optional[ScanStats] = None


# ============================================================================
# 资产评估模型
# ============================================================================
class ValSlotMeta(BaseModel):
    key: str
    name: str
    kind: Literal["doc", "excel"]
    ext: List[str]
    need: bool


class ValStage(BaseModel):
    id: str
    no: str
    name: str
    color: Optional[str] = None


class ValBlock(BaseModel):
    id: str
    name: str
    kind: str
    from_: str = Field(alias="from")
    serve: str


class ValItem(BaseModel):
    id: str
    title: str
    engine: str
    severity: Optional[str] = None


class ValNode(BaseModel):
    id: str
    no: str
    name: str
    type: str
    inputs: List[str] = []
    item_total: int
    items: List[ValItem] = []


class ValMeta(BaseModel):
    slots: List[ValSlotMeta]
    stages: List[ValStage]
    blocks: List[ValBlock]
    nodes: List[ValNode]
    node_count: int
    item_count: int


# ============================================================================
# 进程日志条目（内部共用）
# ============================================================================
class ProcessLogEntry(BaseModel):
    ts: str
    kind: Literal["stage", "token", "thinking", "error"]
    text: str


class ProcessState(BaseModel):
    running: bool = False
    done: bool = False
    ok: Optional[bool] = None
    message: Optional[str] = None
    stats: Optional[Dict[str, Any]] = None


class ProcessStage(BaseModel):
    key: str
    label: str
    icon: str
    done: bool
    active: bool


# 解析模块内尚未定义完的前向引用（from __future__ import annotations 下为字符串注解）
for _model in (BuildLogsResp,):
    _model.model_rebuild()