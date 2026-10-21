// ============================================================================
// API 数据契约类型定义 (自动生成，请勿手动修改)
// 生成脚本: scripts/generate_ts_types.py
// ============================================================================

export type Severity = "high" | "medium" | "low";
export type IssueState = "normal" | "ignored" | "checked";
export type TaskStatus = "running" | "done" | "cancelled" | "error";
export type ScanCategory = "invalid" | "duplicate" | "normal";
export type FileType = "Word" | "Excel" | "PDF" | "不支持";
export type MatchMode = "keyword" | "regex";
export type RuleCategory = "format_error" | "expression";
export type WordModule = "text_word" | "format_regex";
export type AiMode = "local" | "online";
export type AiBuildKind = "dialogue" | "text" | "doc";
export type AiMemorySampleStatus = "pending" | "learning" | "done" | "failed";
export type AiMemoryKind = "wordbank" | "rule";

export interface Issue {
  rule_key: string;
  rule_title: string;
  severity: "high" | "medium" | "low";
  location: string;
  detail: string;
  snippet?: string = "";
  suggestion?: string = "";
  state?: "normal" | "ignored" | "checked" = "normal";
  category?: string = "";
  source?: string = "";
  group?: string = "";
  tag?: string = "";
  file_index?: number | null;
  issue_index?: number | null;
}

export interface FileResult {
  file_name: string;
  file_type: "Word" | "Excel" | "PDF" | "不支持";
  file_size: number;
  status: "pass" | "issue" | "error";
  error_message?: string = "";
  issues?: Issue[] = [];
  stats?: Record<string, unknown> = {};
  truncated?: boolean = false;
  ai_status?: "" | "ok" | "skip" | "fail" | "partial";
  ai_note?: string = "";
}

export interface Summary {
  total_files?: number = 0;
  total_issues?: number = 0;
  pass_files?: number = 0;
  error_files?: number = 0;
  severity?: Record<string, number> = {"high": 0, "medium": 0, "low": 0};
  by_type?: Record<string, number> = {};
  by_rule?: Record<string, number> = {};
  ignored_count?: number = 0;
  checked_count?: number = 0;
}

export interface OverviewData {
  summary: Summary;
  recent_issues?: Issue[] = [];
  status?: string[][] = [];
  cache?: Record<string, unknown> = {};
}

export interface TaskSnapshot {
  status: "running" | "done" | "cancelled" | "error";
  progress: number;
  stage: string;
  stage_text: string;
  logs?: string[] = [];
  error?: string = "";
  result?: unknown | null;
  ai_stream?: AiStream | null;
  ai_summary?: { files: number; hit: number; ok: number; skip: number; fail: number; partial: number } | null;
}

export interface TaskStartResult {
  ok: boolean;
  task_id: string;
}

export interface CustomRule {
  id: string;
  name: string;
  enabled?: boolean = true;
  match_mode?: "keyword" | "regex" = "keyword";
  pattern: string;
  severity?: "high" | "medium" | "low" = "low";
  tag?: string = "";
  suggestion?: string = "";
}

export interface CustomRuleGroup {
  id: string;
  name: string;
  category?: "format_error" | "expression" = "format_error";
  scope?: "all" | "word" | "excel" | "pdf" = "all";
  enabled?: boolean = true;
  rules?: CustomRule[] = [];
}

export interface CustomRulesData {
  groups?: CustomRuleGroup[] = [];
}

export interface WordEntry {
  id: string;
  keyword: string;
  tag?: string = "";
  suggestion?: string = "";
  enabled?: boolean = true;
}

export interface WordGroup {
  id: string;
  name: string;
  module?: "text_word" | "format_regex" = "text_word";
  scope?: "all" | "word" | "excel" | "pdf" = "all";
  enabled?: boolean = true;
  entries?: WordEntry[] = [];
}

export interface WordbanksData {
  groups?: WordGroup[] = [];
}

export interface SettingsData {
  ui?: UISettings;
  detection?: DetectionSettings;
  parse?: ParseSettings;
  report?: ReportSettings;
  ai?: AiSettings;
  role?: "admin" | "user" = "admin";
}

export interface AiBuildResult {
  wordbanks?: AiBuildWordbank[] = [];
  rules?: AiBuildRule[] = [];
}

export interface AiBuildRule {
  name: string;
  match_mode: "keyword" | "regex";
  pattern: string;
  severity: "high" | "medium" | "low";
  suggestion: string;
}

export interface AiBuildWordbank {
  name: string;
  entries: AiBuildEntry[];
}

export interface AiMemoryData {
  enabled: boolean;
  samples?: AiMemorySample[] = [];
  learned?: AiMemoryLearned[] = [];
  source_status: AiMemorySourceStatus;
  stats?: Record<string, number> = {};
}

export interface AiMemorySample {
  id: string;
  diffs?: AiDiff[] = [];
  source_doc?: string = "";
  revised_doc?: string = "";
  note?: string = "";
  status?: "pending" | "learning" | "done" | "failed" = "pending";
  enabled?: boolean = true;
  learned_at?: string | null;
  result_count?: number = 0;
  error?: string = "";
  created_at?: string = "";
}

export interface AiMemoryLearned {
  id: string;
  sample_id: string;
  kind: "wordbank" | "rule";
  group_id: string;
  entity_id: string;
  keyword?: string | null;
  name?: string | null;
  match_mode?: string | null;
  pattern?: string | null;
  severity?: string | null;
  suggestion?: string = "";
  enabled?: boolean = true;
  created_at?: string = "";
}

export interface ScanItem {
  item_id: string;
  source: "custom_rules" | "wordbanks" | "dictionary";
  source_label: string;
  group_id: string;
  group_label: string;
  entity_id: string;
  line_no?: number | null;
  kind: "rule" | "entry" | "line";
  name: string;
  pattern: string;
  suggestion: string;
  match_mode: string;
  enabled: boolean;
  tag: string;
  category: "invalid" | "duplicate" | "normal";
  reason: string;
  keep: boolean;
  source_tag?: string = "";
}

export interface ScanResult {
  time: string;
  stats: ScanStats;
  items: ScanItem[];
}

export interface ScanCleanResp {
  ok: boolean;
  message: string;
  removed: number;
  backup_file?: string | null;
  stats?: ScanStats | null;
}

export interface ValMeta {
  slots: ValSlotMeta[];
  stages: ValStage[];
  blocks: ValBlock[];
  nodes: ValNode[];
  node_count: number;
  item_count: number;
}

export interface ValNode {
  id: string;
  no: string;
  name: string;
  type: string;
  inputs?: string[] = [];
  item_total: number;
  items?: ValItem[] = [];
}

export interface ApiResponse {
  ok: boolean;
  message?: string | null;
}

export interface BuildStartResp {
  ok: boolean;
  message?: string | null;
  running?: boolean = false;
  bid?: string | null;
}

export interface BuildLogsResp {
  logs?: ProcessLogEntry[] = [];
  state: ProcessState;
}
