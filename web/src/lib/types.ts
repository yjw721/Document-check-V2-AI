/* ============================================================
   文档核验中心 · API 数据结构类型定义
   与后端 FastAPI 契约严格对齐（app.py /api/*）
   ============================================================ */

export interface Summary {
  total_files: number;
  total_issues: number;
  pass_files: number;
  error_files: number;
  severity: { high: number; medium: number; low: number };
  by_type: Record<string, number>;
}

export interface CacheInfo {
  count: number;
  size_text: string;
}

export interface RecentIssue {
  severity: "high" | "medium" | "low";
  rule_title: string;
  file_name: string;
  location: string;
}

export interface OverviewData {
  summary: Summary;
  recent_issues: RecentIssue[];
  status: [string, string][];
  cache: CacheInfo;
}

export interface FileResult {
  file_name: string;
  file_type: string;
  size_text: string;
  status: "ok" | "error" | string;
  active_issue_count: number;
  file_path: string;
}

export interface FilesData {
  results: FileResult[];
}

export type Severity = "high" | "medium" | "low";
export type IssueState = "normal" | "ignored" | "checked";

export interface Issue {
  severity: Severity;
  rule_title: string;
  file_name: string;
  location: string;
  detail: string;
  snippet?: string;
  suggestion?: string;
  file_index: number;
  issue_index: number;
  state: IssueState;
}

export interface IssuesData {
  issues: Issue[];
}

/* ---------- 核验任务（等待界面） ---------- */
export type TaskStatus = "running" | "done" | "cancelled" | "error";

export interface TaskSnapshot {
  status: TaskStatus;
  progress: number;
  stage: string;
  stage_text: string;
  logs: string[];
  error: string;
}

export interface TaskStartResult {
  ok: boolean;
  task_id: string;
}

/* ---------- 规则 ---------- */
export interface RuleDef {
  title?: string;
  enabled?: boolean;
  severity?: Severity;
  suggestion?: string;
  [k: string]: unknown;
}
export interface RulesData {
  meta?: Record<string, unknown>;
  global?: Record<string, unknown>;
  [section: string]: Record<string, RuleDef> | Record<string, unknown> | undefined;
}

/* ---------- 自定义规则 ---------- */
export interface CustomRule {
  id: string;
  name: string;
  enabled: boolean;
  match_mode: "keyword" | "regex";
  pattern: string;
  severity: Severity;
  tag: string;
  suggestion: string;
}
export interface CustomRuleGroup {
  id: string;
  name: string;
  category: "format_error" | "expression" | string;
  scope: "all" | "word" | "excel" | "pdf" | string;
  enabled: boolean;
  rules: CustomRule[];
}
export interface CustomRulesData {
  groups: CustomRuleGroup[];
}

/* ---------- 自定义词库 ---------- */
export interface WordEntry {
  id: string;
  keyword: string;
  tag: string;
  suggestion: string;
  enabled: boolean;
}
export interface WordGroup {
  id: string;
  name: string;
  module: "text_word" | "format_regex" | string;
  scope: "all" | "word" | "excel" | "pdf" | string;
  enabled: boolean;
  entries: WordEntry[];
}
export interface WordbanksData {
  groups: WordGroup[];
}

/* ---------- 词库与标准规则批量导入（范本解析） ---------- */
export type TemplateTag = "执业风险警示" | "格式严重错误" | "笔误警示" | "表述优化建议";

export interface TemplateDoc {
  name: string;
  file_type: "Word" | "PDF" | "TXT" | "CSV" | "SCEL" | string;
  ok: boolean;
  error?: string;
  chunks?: number;
  pages?: number;
}

export interface TemplateRuleDraft {
  id: string;
  name: string;
  tag: TemplateTag | string;
  selected: boolean;
  match_mode: "keyword" | "regex";
  pattern: string;
  source_doc: string;
  source_page?: number;
  suggestion: string;
}

export interface TemplateEntryDraft {
  id: string;
  keyword: string;
  tag: TemplateTag | string;
  selected: boolean;
  source_doc: string;
  source_page?: number;
  suggestion: string;
}

export interface TemplateConflict {
  topic: string;
  docs: string[];
  statements: { text: string }[];
  suggestion: string;
}

export interface TemplateReference {
  sentence: string;
  source_doc: string;
  source_page?: number;
}

export interface TemplateDraft {
  docs: TemplateDoc[];
  rules: TemplateRuleDraft[];
  entries: TemplateEntryDraft[];
  conflicts: TemplateConflict[];
  references: TemplateReference[];
}

export interface TemplateImportResult {
  imported_rules: number;
  imported_entries: number;
}

/* ---------- 设置 ---------- */
export interface UISettings {
  sidebar_default_collapsed?: boolean;
  animation_enabled?: boolean;
  theme_accent?: "blue" | "teal" | "purple" | "slate" | "amber" | string;
  theme_scheme?: "holographic" | "dark" | string;
  accent_color?: string;
  table_row_height?: "compact" | "cozy" | "comfortable" | string;
  page_size?: number;
}
export interface DetectionSettings {
  concurrency?: number;
  parse_timeout?: number;
  auto_ignore_blank?: boolean;
  abnormal_popup?: boolean;
  fluency_sensitivity?: "loose" | "normal" | "strict" | string;
}
export interface ParseSettings {
  enable_pdf?: boolean;
  enable_legacy?: boolean;
  scan_pdf_skip?: "auto" | "always" | "never" | string;
}
export interface ReportSettings {
  include_cover?: boolean;
  default_dir?: string;
}
export interface LogCacheSettings {
  cache_expire_days?: number;
  run_log?: boolean;
}
export interface SettingsData {
  ui?: UISettings;
  detection?: DetectionSettings;
  parse?: ParseSettings;
  report?: ReportSettings;
  log_cache?: LogCacheSettings;
  ai?: AiSettings;
  [k: string]: unknown;
}

/* ---------- AI 智能核验 ---------- */
export interface AiSettings {
  enabled?: boolean;
  mode?: "local" | "online" | string;
  base_url?: string;
  api_key?: string;
  model?: string;
  timeout?: number;
  max_chars?: number;
  max_requests?: number;
  ref_enabled?: boolean;
  ref_max_chars?: number;
}

/* ---------- AI 参考资料（标准/词汇/规范） ---------- */
export interface AiRef {
  name: string;
  chars: number;
  enabled: boolean;
  updated: string;
}

/* ---------- AI 规则/词库生成（对话式 + 文档自建） ---------- */
export interface AiBuildEntry {
  keyword: string;
  tag: string;
  suggestion: string;
}
export interface AiBuildWordbank {
  name: string;
  entries: AiBuildEntry[];
}
export interface AiBuildRule {
  name: string;
  match_mode: "keyword" | "regex" | string;
  pattern: string;
  severity: Severity | string;
  suggestion: string;
}
export interface AiBuildResult {
  wordbanks: AiBuildWordbank[];
  rules: AiBuildRule[];
}

/* ---------- 内置词库 ---------- */
export interface DictMeta {
  file: string;
  title: string;
  rule: string;
  rule_title: string;
  count: number;
  size: number;
}
export interface DictionariesData {
  files: DictMeta[];
}
export interface DictionaryContent {
  name: string;
  content: string;
}

/* ---------- 本地 AI 自学习记忆 ---------- */
export type AiMemorySampleStatus = "pending" | "learning" | "done" | "failed";

export interface AiMemorySample {
  id: string;
  content: string;
  source: string;
  note: string;
  status: AiMemorySampleStatus;
  enabled: boolean;
  learned_at: string | null;
  result_count: number;
  error: string;
  created_at: string;
}

export interface AiMemoryLearned {
  id: string;
  sample_id: string;
  kind: "wordbank" | "rule";
  group_id: string;
  entity_id: string;
  keyword?: string;
  name?: string;
  match_mode?: string;
  pattern?: string;
  severity?: string;
  suggestion: string;
  enabled: boolean;
  created_at: string;
}

export interface AiMemoryData {
  enabled: boolean;
  samples: AiMemorySample[];
  learned: AiMemoryLearned[];
  stats: {
    samples: number;
    pending: number;
    done: number;
    failed: number;
    entries: number;
    rules: number;
  };
}

export interface AiMemoryResp {
  ok: boolean;
  message?: string;
  data?: AiMemoryData;
  enabled?: boolean;
  stats?: {
    entries?: number;
    rules?: number;
    standard_expression?: string;
    skipped?: number;
  };
  removed?: number;
}

/* ---------- 其他 ---------- */
export interface WordImportResult {
  entries: WordEntry[];
}
export interface IssueStateBody {
  file_index: number;
  issue_index: number;
  state: IssueState;
}
