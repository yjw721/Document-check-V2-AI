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
  /* AI 智能核验阶段状态（"" 未核验 / ok 完成 / skip 跳过 / fail 失败 / partial 部分失败） */
  ai_status?: string;
  ai_note?: string;
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
  /* 知识库（RAG）联动：本条判定所依据的规范资料出处（空 = 未命中知识库） */
  evidence?: string;
  file_index: number;
  issue_index: number;
  state: IssueState;
}

export interface IssuesData {
  issues: Issue[];
}

/* ---------- 核验任务（等待界面） ---------- */
export type TaskStatus = "running" | "done" | "cancelled" | "error";

export interface AiChunkOut {
  chunk: number;
  total: number;
  content: string;
  thinking: string;
  preview?: string;
}

export interface AiStream {
  file: string;
  chunk: number;
  total: number;
  content: string;
  thinking: string;
  preview: string;
  history: AiChunkOut[];
}

export interface TaskSnapshot {
  status: TaskStatus;
  progress: number;
  stage: string;
  stage_text: string;
  logs: string[];
  error: string;
  result?: ScanResult | null;
  ai_stream?: AiStream | null;
  ai_summary?: AiTaskSummary | null;
}

/** AI 核验阶段汇总（等待界面展示） */
export interface AiTaskSummary {
  files: number;
  hit: number;
  ok: number;
  skip: number;
  fail: number;
  partial: number;
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
  /** 生成来源机器码（AI 生成渠道 / 自学习写入） */
  source?: string;
  /** 生成来源中文名，如「AI对话创建」 */
  source_label?: string;
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
  /** 生成来源机器码（AI 生成渠道 / 自学习写入） */
  source?: string;
  /** 生成来源中文名，如「AI对话创建」 */
  source_label?: string;
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
  filtered_rules?: number;
  filtered_entries?: number;
  rejected?: RuleRejected[];
}

/* ---------- 设置 ---------- */
export interface UISettings {
  sidebar_default_collapsed?: boolean;
  animation_enabled?: boolean;
  theme_accent?: "blue" | "teal" | "purple" | "slate" | "amber" | string;
  theme_scheme?: "holographic" | "dark" | "ink" | string;
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
  role?: "admin" | "user";
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
  create_enabled?: boolean;
  /* —— AI 配置模块新增参数 —— */
  temperature?: number;
  num_ctx?: number;
  role?: string;
  response_style?: "precise" | "concise" | "balance" | "friendly" | "formal" | string;
  prompt_presets?: AiPromptPreset[];
  active_preset?: string;
  rate_limit?: AiRateLimit;
  cache?: AiCacheConfig;
  /* —— 本地知识库（RAG） —— */
  kb_enabled?: boolean;
  kb_top_k?: number;
  kb_dense_in_verify?: boolean;
  embed_enabled?: boolean;
  embed_model?: string;
  /* —— 核验行为（可控性增强） —— */
  retry?: number;
  verify_scope?: "all" | "issues" | string;
  fail_as_issue?: boolean;
  /* —— 增强审核力度 —— */
  deep_review?: boolean;
  strict_grounding?: boolean;
  min_confidence?: number;
  /* —— 专项技能（SKILL） —— */
  active_skills?: string[];        // 启用的技能 id 集合（内置 + 自定义）
  custom_skills?: AiSkill[];       // 用户自定义技能库
}

/* ---------- AI 专项技能（SKILL） ---------- */
export interface AiSkill {
  id: string;
  name: string;
  description?: string;
  prompt?: string;                 // 注入系统提示的专项核验指令
  default_on?: boolean;            // 仅内置技能：是否默认启用
  file_types?: string;             // 适用文件类型（逗号分隔短标签：doc/xls/pdf/txt/all）
  default_severity?: string;       // 建议严重度：high/medium/low/空
}

/* ---------- AI 自定义提示词预设 ---------- */
export interface AiPromptPreset {
  id: string;
  name: string;
  content: string;
  scope?: "verify" | "build" | "all" | string;
}

/* ---------- AI 限流参数 ---------- */
export interface AiRateLimit {
  enabled?: boolean;
  rpm?: number;
  concurrency?: number;
}

/* ---------- AI 响应缓存配置 ---------- */
export interface AiCacheConfig {
  enabled?: boolean;
  ttl?: number;
  max_entries?: number;
}

/* ---------- AI 参考资料（标准/词汇/规范） ---------- */
export interface AiRef {
  name: string;
  chars: number;
  enabled: boolean;
  updated: string;
}

/* ---------- AI 活动日志 ---------- */
export interface AiActivityLog {
  ts: string;
  event: "verify" | "build_dialogue" | "build_text" | "build_doc" | "test" | "learn" | string;
  model: string;
  ok: boolean;
  detail: string;
  [k: string]: unknown;
}

/* ---------- 通用操作日志（规则/词库保存等） ---------- */
export interface ActivityLog {
  ts: string;
  module: string;
  action: string;
  operator: string;
  ok: boolean;
  detail: string;
  [k: string]: unknown;
}

/* ---------- AI 规则/词库生成（对话式 + 文档自建） ---------- */
export interface AiBuildEntry {
  keyword: string;
  tag: string;
  suggestion: string;
  /** 来源机器码：ai_dialogue / ai_text / ai_doc / ai_learning */
  source?: string;
  /** 面向用户的来源名，如「AI对话创建」 */
  source_label?: string;
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
  /** 来源机器码：ai_dialogue / ai_text / ai_doc / ai_learning */
  source?: string;
  /** 面向用户的来源名，如「AI对话创建」 */
  source_label?: string;
}
export interface AiBuildResult {
  wordbanks: AiBuildWordbank[];
  rules: AiBuildRule[];
}

/* 入库前置校验过滤统计（AI 生成 / 范本解析 / 手动保存 通用） */
export interface RuleRejected {
  name: string;
  pattern: string;
  reason: string;
}
export interface RuleFilterStat {
  generated_rules: number;
  filtered_rules: number;
  accepted_rules: number;
  generated_entries: number;
  filtered_entries: number;
  accepted_entries: number;
  rejected: RuleRejected[];
}
export interface AiBuildResp {
  ok: boolean;
  message: string;
  result: AiBuildResult;
  filter?: RuleFilterStat;
}

/* 生成任务启动响应（对话/文本/文档式：后台流式推理，返回任务 bid 供轮询） */
export interface BuildStartResp {
  ok: boolean;
  message?: string;
  running?: boolean;
  bid?: string;
}

/* 生成过程日志轮询响应（与自学习推理流同构） */
export interface BuildLogsResp {
  logs: LearnLogEntry[];
  /** 服务端日志总条数；下次轮询作为 since 传入即可只取增量 */
  offset?: number;
  state: {
    running: boolean;
    done: boolean;
    ok?: boolean;
    message?: string;
    result?: AiBuildResult;
    filter?: RuleFilterStat;
    /** 任务已被用户中止 */
    cancelled?: boolean;
  };
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

/* ---------- 本地 AI 自学习记忆（成对样本：原始文档 + 人工修订文档） ---------- */
export type AiMemorySampleStatus = "pending" | "learning" | "done" | "failed";

export interface LearnLogEntry {
  ts: string;
  kind: "stage" | "token" | "thinking" | "error";
  text: string;
}

export interface LearnLogsResp {
  ok: boolean;
  logs: LearnLogEntry[];
  /** 服务端日志总条数；下次轮询作为 since 传入即可只取增量 */
  offset?: number;
  state: {
    running: boolean;
    done: boolean;
    ok?: boolean;
    message?: string;
    stats?: {
      entries: number;
      rules: number;
      skipped: number;
      filtered: number;
      standard_expression?: string;
    };
    /** 当前阶段机器名：start / prompt / infer / parse / validate / done */
    stage?: string;
    /** 学习进度百分比 0-100（后端按阶段 + 推理输出量实时估算） */
    progress?: number;
    /** 模型已输出字符数 */
    chars?: number;
    /** 是否已触发「首次无产出自动重试」 */
    retry?: boolean;
    /** 累计耗时（秒），查询时实时计算 */
    elapsed_sec?: number;
    /** 输出速率（字/秒） */
    chars_per_sec?: number;
    /** 任务已被用户中止 */
    cancelled?: boolean;
  };
}

export interface AiDiff {
  old: string;
  new: string;
}

export interface AiMemorySourceStatus {
  available: boolean;
  valid: boolean;
  files: { name: string; path: string }[];
  /** 已配对的修订文档信息（后端返回对象而非文件名字符串） */
  revised: { name: string; path: string; time: string } | null;
  message: string;
}

export interface AiMemorySample {
  id: string;
  diffs: AiDiff[];
  source_doc: string;
  revised_doc: string;
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
  source_status: AiMemorySourceStatus;
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
  source_status?: AiMemorySourceStatus;
  stats?: {
    entries?: number;
    rules?: number;
    standard_expression?: string;
    skipped?: number;
    filtered?: number;
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

/* ---------- 规则词库一键扫描 ---------- */
export type ScanCategory = "invalid" | "duplicate" | "normal";

export interface ScanItem {
  item_id: string;
  source: "custom_rules" | "wordbanks" | "dictionary";
  source_label: string;
  group_id: string;
  group_label: string;
  entity_id: string;
  line_no: number | null;
  kind: "rule" | "entry" | "line";
  name: string;
  pattern: string;
  suggestion: string;
  match_mode: string;
  enabled: boolean;
  tag: string;
  category: ScanCategory;
  reason: string;
  keep: boolean;
  source_tag: string;
}

export interface ScanSourceStats {
  label: string;
  scanned: number;
  invalid: number;
  duplicate: number;
  normal: number;
}

export interface ScanStats {
  scanned: number;
  invalid: number;
  duplicate: number;
  duplicate_cleanable: number;
  normal: number;
  cleanable: number;
  readonly: number;
  sources: number;
  by_source: Record<string, ScanSourceStats>;
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
  backup_file?: string;
  stats?: ScanStats;
}

/* ============================================================
   资产评估报告智能核查（四阶段闭环 · 9 节点 / 96 事项）
   与后端 /api/valuation/* 契约对齐
   ============================================================ */

export interface ValSlotMeta {
  key: string;
  name: string;
  kind: "doc" | "excel";
  ext: string[];
  need: boolean;
}

export interface ValStage {
  id: string;
  no: string;
  name: string;
  color?: string;
}

export interface ValBlock {
  id: string;
  name: string;
  kind: string;
  from: string;
  serve: string;
}

export interface ValItem {
  id: string;
  title: string;
  engine: string;
  severity?: string;
}

export interface ValNode {
  id: string;
  no: string;
  name: string;
  type: string;
  inputs: string[];
  item_total: number;
  items: ValItem[];
}

export interface ValMeta {
  slots: ValSlotMeta[];
  stages: ValStage[];
  blocks: ValBlock[];
  nodes: ValNode[];
  node_count: number;
  item_count: number;
}

export interface ValUploadedFile {
  slot: string;
  name: string;
  size: number;
}

export interface ValUploadResp {
  ok: boolean;
  session_id: string;
  files: ValUploadedFile[];
}

export interface ValRunResp {
  ok: boolean;
  task_id: string;
  use_ai: boolean;
}

export interface ValFinding {
  id: string;
  node_id: string;
  node_no: string;
  node_name: string;
  item_id: string;
  item_no: number;
  item_title: string;
  source: string;
  engine: string;
  severity: string;
  file: string;
  block: string;
  location: string;
  detail: string;
  snippet: string;
  suggestion: string;
  state: string;
  duplicate_of: string;
  merged_count: number;
  origin: string;
}

export interface ValSummary {
  node_total: number;
  node_done: number;
  node_skipped: number;
  item_total: number;
  item_done: number;
  findings_raw: number;
  dedup_merged: number;
  review_removed: number;
  review_added: number;
  total: number;
  high: number;
  medium: number;
  low: number;
  by_node: Record<string, number>;
  by_origin: Record<string, number>;
}

export interface ValNodeBrief {
  id: string;
  severity: string;
  origin: string;
  location: string;
  detail: string;
  item_id: string;
  item_title: string;
}

export interface ValNodeRun {
  node_id: string;
  node_no: string;
  node_name: string;
  node_type: string;
  inputs: string[];
  status: string;
  item_total: number;
  item_done: number;
  rule_count: number;
  ai_count: number;
  message: string;
  seconds: number;
  finding_brief: ValNodeBrief[];
}

export interface ValPreItem {
  slot: string;
  file: string;
  message: string;
  clean?: {
    hidden_sheets?: string[];
    defined_names?: number;
    external_links?: number;
  };
}

export interface ValBlockRun {
  id: string;
  name: string;
  kind: string;
  source_slot: string;
  source_file?: string;
  chars?: number;
  general_sheets?: string[];
  stats?: { own_sheets?: string[] };
}

export interface ValReview {
  ok: boolean;
  conclusion?: string;
  removed?: Record<string, string>;
  added_count?: number;
}

export interface ValResult {
  status: string;
  stage: string;
  percent: number;
  message: string;
  logs: string[];
  preprocess: {
    items: ValPreItem[];
    blocked?: boolean;
    blocked_message?: string;
    message?: string;
  };
  split: { blocks: ValBlockRun[] };
  nodes: ValNodeRun[];
  summary: ValSummary;
  findings: ValFinding[];
  review?: ValReview;
  seconds?: number;
}

export interface ValTaskSnapshot {
  task_id: string;
  status: string;
  progress: number;
  stage: string;
  stage_text: string;
  logs: string[];
  error: string;
  result: ValResult | null;
  done: boolean;
}

export interface ValResultResp {
  ready: boolean;
  status: string;
  progress?: number;
  result?: ValResult;
}

/* ---------- 本地知识库（RAG 检索问答） ---------- */
export interface KbDoc {
  id: string;
  name: string;
  size: number;
  chars: number;
  chunks: number;
  added_at: string;
}
export interface KbConfig {
  chunk_size: number;
  chunk_overlap: number;
}
export interface KbStatus {
  ok: boolean;
  docs: KbDoc[];
  total_chunks: number;
  embed_model: string;
  embed_available: boolean;
  embed_resolved?: boolean;
  embed_resolved_model?: string;
  embed_dim?: number;
  updated_at: string;
  config?: KbConfig;
}
export interface KbSource {
  file: string;
  section: string;
  path?: string;
  loc: string;
  snippet: string;
  score: number;
  doc_id: string;
}
export interface KbAskResp {
  ok: boolean;
  answer: string;
  sources: KbSource[];
  embed_used: boolean;
  embed_model: string;
  mode?: string;
  elapsed: number;
  error?: string;
}
export interface KbUploadResp {
  ok: boolean;
  added: number;
  docs: { id: string; name: string; chars: number; chunks: number; reused?: boolean }[];
  errors: string[];
}
/* 检索诊断：仅检索、不生成，返回各路得分与融合名次 */
export interface KbSearchHit {
  rank: number;
  chunk_id: string;
  doc_id: string;
  file: string;
  section: string;
  path: string;
  loc: string;
  rrf: number;
  bm25: number;
  cos: number;
  text: string;
}
export interface KbSearchResp {
  ok: boolean;
  hits: KbSearchHit[];
  mode: string;
  query_terms: string[];
  stats: { chunks: number; docs: number; vectors: number };
}
/* 核验预检：回显某段待核验文本会被注入哪些知识库片段 */
export interface KbVerifyPreviewResp {
  ok: boolean;
  segments: number;
  context: string;
  refs: KbSource[];
  hits: { chunk_id: string; file: string; path: string; score: number; rank: number }[];
}
