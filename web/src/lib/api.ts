/* ============================================================
   文档核验中心 · API 客户端
   与后端 FastAPI（127.0.0.1:8501）通信；同源时使用相对路径，
   异源（如内置预览面板）回退到绝对地址直连后端（后端已放行 CORS）。
   ============================================================ */
import type {
  BuildStartResp,
  BuildLogsResp,
  RuleRejected,
  AiDiff,
  AiMemoryData,
  AiMemoryResp,
  LearnLogsResp,
  AiRef,
  AiSettings,
  AiActivityLog,
  ActivityLog,
  CustomRulesData,
  DictionariesData,
  DictionaryContent,
  FilesData,
  IssueStateBody,
  IssuesData,
  KbAskResp,
  KbConfig,
  KbSearchResp,
  KbStatus,
  KbUploadResp,
  KbVerifyPreviewResp,
  KbTreeNode,
  KbFolder,
  KbGraphResp,
  KbPermissions,
  KbRecordType,
  KbRecordListResp,
  KbRecordDetailResp,
  KbRecordExportResp,
  KbParserStatus,
  OverviewData,
  RulesData,
  ScanCleanResp,
  ScanResult,
  SettingsData,
  AiSkill,
  AiSkillPackImport,
  TaskSnapshot,
  TaskStartResult,
  TemplateDraft,
  TemplateImportResult,
  WordbanksData,
  WordImportResult,
  ValMeta,
  ValUploadResp,
  ValRunResp,
  ValTaskSnapshot,
  ValResultResp,
} from "./types";

const API_BASE =
  location.origin === "http://127.0.0.1:8501" || location.origin === "http://localhost:8501"
    ? ""
    : "http://127.0.0.1:8501";

/** 本次检测任务的 AI 行为覆盖（不修改全局设置）：null/undefined = 按全局设置 */
export interface AiTaskOverride {
  enabled?: boolean | null;
  scope?: "all" | "issues" | "";
}

/* ---------- 统一数据层：AI 生成产出草稿箱 ---------- */
export interface DraftItem {
  id: string;
  bid: string;
  kind: string;
  source: string;
  source_label: string;
  created_at: string;
  status: "pending" | "accepted" | "rejected";
  filename?: string;
  stats: { rules: number; wordbanks: number; entries: number };
  /** 入库前置校验统计（过滤了多少条等） */
  filter?: Record<string, unknown>;
  /** 处置说明（如「入库规则 x 条 / 词条 y 条」） */
  note?: string;
  handled_at?: string;
}
export interface DraftDetail extends DraftItem {
  rules: Record<string, unknown>[];
  wordbanks: Record<string, unknown>[];
}
export interface DataStoreStats {
  version: number;
  total: number;
  by_status: Record<string, number>;
  by_source: Record<string, number>;
  accepted_rules: number;
  accepted_entries: number;
  path?: string;
}

/* ---------- 工作区隔离 + 访问口令（多用户 / 局域网共享） ---------- */
const WS_KEY = "docchk_ws";
const TOKEN_KEY = "docchk_token";

function safeGet(k: string): string {
  try {
    return localStorage.getItem(k) || "";
  } catch {
    return "";
  }
}
function safeSet(k: string, v: string): void {
  try {
    if (v) localStorage.setItem(k, v);
    else localStorage.removeItem(k);
  } catch {
    /* 沙箱/隐私模式：仅本次会话生效 */
  }
}

/** 每个浏览器会话一个随机工作区 id，检测结果互不覆盖；刷新后保持。 */
function getWorkspace(): string {
  let w = safeGet(WS_KEY);
  if (!w) {
    w = "w_" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
    safeSet(WS_KEY, w);
  }
  return w;
}

let accessToken = safeGet(TOKEN_KEY);
export function setAccessToken(t: string): void {
  accessToken = t;
  safeSet(TOKEN_KEY, t);
}
export function getAccessToken(): string {
  return accessToken;
}
export function clearAccessToken(): void {
  setAccessToken("");
}

/* ---------- 管理员令牌（会话级，提权用；关闭标签页即失效 = 自然登出） ---------- */
const ADMIN_TOKEN_KEY = "docchk_admin_token";

function safeSessionGet(k: string): string {
  try {
    return sessionStorage.getItem(k) || "";
  } catch {
    return "";
  }
}
function safeSessionSet(k: string, v: string): void {
  try {
    if (v) sessionStorage.setItem(k, v);
    else sessionStorage.removeItem(k);
  } catch {
    /* 隐私模式：仅本次会话生效 */
  }
}

let adminToken = safeSessionGet(ADMIN_TOKEN_KEY);
export function setAdminToken(t: string): void {
  adminToken = t;
  safeSessionSet(ADMIN_TOKEN_KEY, t);
}
export function getAdminToken(): string {
  return adminToken;
}
export function clearAdminToken(): void {
  setAdminToken("");
}

export interface ApiOptions {
  /** multipart/form-data：body 直接作为 FormData */
  form?: boolean;
  /** 期望返回 Blob（报告下载） */
  blob?: boolean;
  /** fetch 缓存策略（如 "no-store" 强制绕过 HTTP 缓存，用于结果刷新） */
  cache?: RequestCache;
}

async function request<T>(method: string, path: string, body?: unknown, opts: ApiOptions = {}): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (opts.cache) init.cache = opts.cache;
  (init.headers as Record<string, string>)["X-Workspace"] = getWorkspace();
  if (accessToken) (init.headers as Record<string, string>)["X-Access-Token"] = accessToken;
  if (adminToken) (init.headers as Record<string, string>)["X-Admin-Token"] = adminToken;
  if (body !== undefined) {
    // FormData 自动按 multipart 发送（浏览器自动带 boundary，切勿手动设 JSON 头）
    if (opts.form || body instanceof FormData) {
      init.body = body as FormData;
    } else {
      (init.headers as Record<string, string>)["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
  }
  const r = await fetch(API_BASE + path, init);
  if (opts.blob) {
    if (!r.ok) {
      const j = await r.json().catch(() => null);
      if (r.status === 401) {
        if (j && (j as { admin_required?: boolean }).admin_required) {
          window.dispatchEvent(new CustomEvent("docchk:admin-unauthorized"));
        } else {
          window.dispatchEvent(new CustomEvent("docchk:unauthorized"));
        }
      }
      throw new Error((j as { detail?: string } | null)?.detail || "请求失败");
    }
    return (await r.blob()) as unknown as T;
  }
  const ct = r.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    const j = (await r.json()) as T & { detail?: string; admin_required?: boolean };
    if (!r.ok) {
      if (r.status === 401 && !path.startsWith("/api/security/ping")) {
        if (j.admin_required) {
          window.dispatchEvent(new CustomEvent("docchk:admin-unauthorized"));
        } else {
          window.dispatchEvent(new CustomEvent("docchk:unauthorized"));
        }
      }
      throw new Error(j.detail || "请求失败");
    }
    return j;
  }
  if (!r.ok) throw new Error("请求失败");
  return r as unknown as T;
}

export const api = {
  /* ---------- 访问安全探针（免口令） ---------- */
  securityPing: () =>
    request<{
      ok: boolean;
      auth_required: boolean;
      authorized: boolean;
      admin_required: boolean;
      admin_authorized: boolean;
    }>("GET", "/api/security/ping"),

  /* ---------- 总览 ---------- */
  overview: () => request<OverviewData>("GET", "/api/overview"),

  /* ---------- 文件 / 检测 ---------- */
  files: () => request<FilesData>("GET", "/api/files"),
  issues: (opts?: ApiOptions) => request<IssuesData>("GET", "/api/issues", undefined, opts),
  setIssueState: (body: IssueStateBody) => request<{ ok: boolean }>("POST", "/api/issue_state", body),
  upload: (fd: FormData, ai?: AiTaskOverride) => {
    if (ai) {
      if (ai.enabled != null) fd.append("ai_enabled", ai.enabled ? "1" : "0");
      if (ai.scope) fd.append("ai_scope", ai.scope);
    }
    return request<TaskStartResult>("POST", "/api/upload", fd, { form: true });
  },
  scanFolder: (body: { folder: string; recursive: boolean; ai?: AiTaskOverride }) => {
    const { ai, ...rest } = body;
    return request<TaskStartResult>("POST", "/api/scan_folder", {
      ...rest,
      ai_enabled: ai && ai.enabled != null ? (ai.enabled ? "1" : "0") : undefined,
      ai_scope: ai && ai.scope ? ai.scope : undefined,
    });
  },
  taskPoll: (taskId: string) => request<TaskSnapshot>("GET", `/api/task/${taskId}`),
  taskCancel: (taskId: string) => request<{ ok: boolean }>("POST", `/api/task/${taskId}/cancel`),
  clearData: () => request<{ ok: boolean }>("POST", "/api/clear_data"),
  clearCache: () => request<{ count: number }>("POST", "/api/clear_cache"),

  /* ---------- 规则 ---------- */
  rules: () => request<RulesData>("GET", "/api/rules"),
  saveRules: (data: RulesData) => request<{ ok: boolean }>("POST", "/api/rules", data),
  restoreRules: () => request<{ data: RulesData }>("POST", "/api/rules/restore"),

  /* ---------- 内置词库 ---------- */
  dictionaries: () => request<DictionariesData>("GET", "/api/dictionaries"),
  dictionary: (name: string) => request<DictionaryContent>("GET", `/api/dictionaries/${name}`),
  saveDictionary: (name: string, content: string) =>
    request<{ ok: boolean }>("POST", `/api/dictionaries/${name}`, { content }),

  /* ---------- 自定义规则 / 词库 ---------- */
  customRules: () => request<CustomRulesData>("GET", "/api/custom_rules"),
  saveCustomRules: (data: CustomRulesData) =>
    request<{ ok: boolean; filtered_rules?: number; rejected?: RuleRejected[] }>("POST", "/api/custom_rules", data),
  wordbanks: () => request<WordbanksData>("GET", "/api/wordbanks"),
  saveWordbanks: (data: WordbanksData) =>
    request<{ ok: boolean; filtered_entries?: number; rejected?: RuleRejected[] }>("POST", "/api/wordbanks", data),
  wordbankImport: (body: { text: string }) => request<WordImportResult>("POST", "/api/wordbanks/import", body),

  /* ---------- 规则词库一键扫描 ---------- */
  scanStart: () => request<TaskStartResult>("POST", "/api/scan/start"),
  scanLast: () => request<{ ok: boolean; result: ScanResult | null }>("GET", "/api/scan/last"),
  scanExport: (body: { ids: string[]; format: "txt" | "csv" }) =>
    request<Blob>("POST", "/api/scan/export", body, { blob: true }),
  scanClean: (body: { ids: string[] }) => request<ScanCleanResp>("POST", "/api/scan/clean", body),

  /* ---------- 范本解析（词库与标准规则批量导入） ---------- */
  templateDraft: () => request<TemplateDraft>("GET", "/api/template/draft"),
  templateUpload: (fd: FormData) => request<TemplateDraft>("POST", "/api/template/upload", fd, { form: true }),
  templateSelect: (body: { rule_ids: string[]; entry_ids: string[] }) =>
    request<{ ok: boolean }>("POST", "/api/template/select", body),
  templateImport: (body: { rule_ids: string[]; entry_ids: string[] }) =>
    request<TemplateImportResult>("POST", "/api/template/import", body),
  templateClear: () => request<{ ok: boolean }>("POST", "/api/template/clear"),

  /* ---------- 设置 ---------- */
  settings: () => request<SettingsData>("GET", "/api/settings"),
  saveSettings: (data: SettingsData) => request<{ ok: boolean }>("POST", "/api/settings", data),
  restoreSettings: () => request<SettingsData>("POST", "/api/settings/restore"),

  /* ---------- AI 智能核验 ---------- */
  aiStatus: () => request<{ ai: AiSettings }>("GET", "/api/ai/status"),
  aiSkills: () => request<{ builtin: AiSkill[] }>("GET", "/api/ai/skills"),
  /* 导入 zipskill 标准技能包（Agent Skills ZIP），后端解析返回技能列表 */
  aiImportSkillPack: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<AiSkillPackImport>("POST", "/api/ai/skills/import", fd, { form: true });
  },
  aiModels: (baseUrl?: string) =>
    request<{ ok: boolean; models: string[]; base_url?: string; message?: string }>(
      "GET",
      baseUrl ? `/api/ai/models?base_url=${encodeURIComponent(baseUrl)}` : "/api/ai/models",
    ),
  aiTest: (body: Partial<AiSettings>) =>
    request<{ ok: boolean; message: string }>("POST", "/api/ai/test", body),

  /* ---------- AI 活动日志 ---------- */
  aiLogs: (limit = 200, offset = 0) =>
    request<{ ok: boolean; logs: AiActivityLog[]; total: number }>(
      "GET", `/api/ai/logs?limit=${limit}&offset=${offset}`,
    ),
  aiLogsClear: () =>
    request<{ ok: boolean; cleared: number }>("POST", "/api/ai/logs/clear"),

  /* ---------- 通用操作日志（规则/词库保存等） ---------- */
  activityLogs: (module = "", limit = 200, offset = 0) =>
    request<{ ok: boolean; logs: ActivityLog[]; total: number }>(
      "GET", `/api/activity/logs?module=${encodeURIComponent(module)}&limit=${limit}&offset=${offset}`,
    ),
  logActivity: (payload: { module: string; action: string; detail?: string; operator?: string; ok?: boolean }) =>
    request<{ ok: boolean }>("POST", "/api/activity/logs", payload),
  activityLogsClear: (module = "") =>
    request<{ ok: boolean; cleared: number }>("POST", "/api/activity/logs/clear", { module }),

  /* ---------- AI 参考资料 ---------- */
  aiRefs: () => request<{ refs: AiRef[] }>("GET", "/api/ai/refs"),
  aiRefUpload: (file: File) => {
    const fd = new FormData();
    fd.append("files", file);
    return request<{ ok: boolean; message: string; refs: AiRef[] }>("POST", "/api/ai/refs/upload", fd);
  },
  aiRefToggle: (name: string, enabled: boolean) =>
    request<{ ok: boolean; refs: AiRef[] }>("POST", "/api/ai/refs/toggle", { name, enabled }),
  aiRefDelete: (name: string) =>
    request<{ ok: boolean; refs: AiRef[] }>("POST", "/api/ai/refs/delete", { name }),

  /* ---------- AI 规则/词库智能生成（对话式 / 文本式 / 文档式） ---------- */
  aiBuildDialogue: (text: string) =>
    request<BuildStartResp>("POST", "/api/ai/build/dialogue", { text }),
  aiBuildText: (text: string) =>
    request<BuildStartResp>("POST", "/api/ai/build/text", { text }),
  aiBuildDoc: (file: File) => {
    const fd = new FormData();
    fd.append("files", file);
    return request<BuildStartResp>("POST", "/api/ai/build/doc", fd);
  },
  aiBuildLogs: (bid: string, since = 0) =>
    request<BuildLogsResp>(
      "GET",
      `/api/ai/build/logs?bid=${encodeURIComponent(bid)}${since > 0 ? `&since=${since}` : ""}`,
    ),
  aiBuildCancel: (bid: string) =>
    request<{ ok: boolean; message?: string }>("POST", "/api/ai/build/cancel", { bid }),

  /* ---------- 统一数据层：AI 生成产出草稿箱 ---------- */
  drafts: (p?: { status?: string; source?: string }) => {
    const q = new URLSearchParams();
    if (p?.status) q.set("status", p.status);
    if (p?.source) q.set("source", p.source);
    const qs = q.toString();
    return request<{ ok: boolean; items: DraftItem[] }>("GET", `/api/ai/drafts${qs ? `?${qs}` : ""}`);
  },
  draft: (id: string) => request<{ ok: boolean; draft: DraftDetail }>("GET", `/api/ai/drafts/${encodeURIComponent(id)}`),
  draftAccept: (id: string) =>
    request<{ ok: boolean; message: string; added: { rules: number; entries: number } }>(
      "POST", `/api/ai/drafts/${encodeURIComponent(id)}/accept`),
  draftReject: (id: string) =>
    request<{ ok: boolean; message: string }>("POST", `/api/ai/drafts/${encodeURIComponent(id)}/reject`),
  dataStats: () => request<{ ok: boolean; stats: DataStoreStats }>("GET", "/api/data/stats"),

  /* ---------- 本地 AI 自学习记忆（成对样本） ---------- */
  aiMemory: () => request<AiMemoryData>("GET", "/api/ai_memory"),
  aiMemoryToggle: (enabled: boolean) =>
    request<{ ok: boolean; enabled: boolean }>("POST", "/api/ai_memory/toggle", { enabled }),
  aiMemoryPair: (file: File) => {
    const fd = new FormData();
    fd.append("files", file);
    return request<AiMemoryResp>("POST", "/api/ai_memory/pair", fd);
  },
  aiMemoryDiffs: () => request<{ ok: boolean; message?: string; diffs: AiDiff[] }>("POST", "/api/ai_memory/diffs"),
  aiMemoryAddSample: (body: {
    diffs?: AiDiff[];
    source_doc?: string;
    revised_doc?: string;
    note?: string;
    content?: string;
  }) => request<AiMemoryResp>("POST", "/api/ai_memory/samples", body),
  aiMemoryLearn: (sid: string) =>
    request<AiMemoryResp>("POST", `/api/ai_memory/samples/${sid}/learn`),
  aiMemoryLearnLogs: (sid: string, since = 0) =>
    request<LearnLogsResp>(
      "GET",
      `/api/ai_memory/samples/${sid}/learn/logs${since > 0 ? `?since=${since}` : ""}`,
    ),
  aiMemoryLearnCancel: (sid: string) =>
    request<{ ok: boolean; message?: string }>("POST", `/api/ai_memory/samples/${sid}/learn/cancel`),
  aiMemorySampleToggle: (sid: string, enabled: boolean) =>
    request<AiMemoryResp>("POST", `/api/ai_memory/samples/${sid}/toggle`, { enabled }),
  aiMemorySampleDelete: (sid: string) =>
    request<AiMemoryResp>("DELETE", `/api/ai_memory/samples/${sid}`),
  aiMemoryLearnedToggle: (lid: string, enabled: boolean) =>
    request<AiMemoryResp>("POST", `/api/ai_memory/learned/${lid}/toggle`, { enabled }),
  aiMemoryLearnedDelete: (lid: string) =>
    request<AiMemoryResp>("DELETE", `/api/ai_memory/learned/${lid}`),
  aiMemoryClear: () => request<AiMemoryResp>("POST", "/api/ai_memory/clear"),
  aiMemoryExport: (format: "csv" | "txt", kind: "wordbanks" | "rules") =>
    request<Blob>("GET", `/api/ai_memory/export?format=${format}&kind=${kind}`, undefined, { blob: true }),

  /* ---------- 本地知识库（RAG 检索问答） ---------- */
  kbStatus: () => request<KbStatus>("GET", "/api/rag/status"),
  kbUpload: (files: File[], folder = "") => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    fd.append("folder", folder || "");
    return request<KbUploadResp>("POST", "/api/rag/upload", fd, { form: true });
  },
  kbAsk: (body: { question: string; top_k?: number; folder?: string }) =>
    request<KbAskResp>("POST", "/api/rag/ask", body),
  kbDelete: (ids: string[]) => request<{ ok: boolean; removed: number; names: string[] }>("POST", "/api/rag/delete", { ids }),
  kbClear: () => request<{ ok: boolean }>("POST", "/api/rag/clear"),
  kbDownload: (docId: string) => request<Blob>("GET", `/api/rag/file/${encodeURIComponent(docId)}`, undefined, { blob: true }),
  kbSearch: (body: { question: string; top_k?: number; folder?: string }) =>
    request<KbSearchResp>("POST", "/api/rag/search", body),
  kbConfig: () => request<{ ok: boolean; config: KbConfig }>("GET", "/api/rag/config"),
  kbSaveConfig: (body: Partial<KbConfig>) => request<{ ok: boolean; config: KbConfig }>("POST", "/api/rag/config", body),
  kbRebuild: () => request<{ ok: boolean; rebuilt: number; skipped: number; chunks: number; embed_model: string }>(
    "POST", "/api/rag/rebuild"),
  kbVerifyPreview: (body: { text: string; top_k?: number }) =>
    request<KbVerifyPreviewResp>("POST", "/api/rag/verify-preview", body),

  /* 树形目录 */
  kbTree: () => request<{ ok: boolean; tree: KbTreeNode }>("GET", "/api/rag/tree"),
  kbFolders: () => request<{ ok: boolean; folders: KbFolder[] }>("GET", "/api/rag/folders"),
  kbCreateFolder: (name: string, parent = "") =>
    request<{ ok: boolean; folder?: KbFolder; message?: string }>(
      "POST", "/api/rag/folder", { name, parent }),
  kbRenameFolder: (id: string, name: string) =>
    request<{ ok: boolean; folder?: KbFolder; message?: string }>(
      "POST", "/api/rag/folder/rename", { id, name }),
  kbMoveFolder: (id: string, parent: string) =>
    request<{ ok: boolean; folder?: KbFolder; message?: string }>(
      "POST", "/api/rag/folder/move", { id, parent }),
  kbDeleteFolder: (id: string, cascade = false) =>
    request<{ ok: boolean; message?: string; [k: string]: unknown }>(
      "POST", "/api/rag/folder/delete", { id, cascade }),
  kbMoveDocs: (ids: string[], folder = "") =>
    request<{ ok: boolean; moved: number; message?: string }>(
      "POST", "/api/rag/docs/move", { ids, folder }),

  /* 单文档重向量化 */
  kbReindexDoc: (id: string) =>
    request<{ ok: boolean; message?: string; [k: string]: unknown }>(
      "POST", "/api/rag/doc/reindex", { id }),

  /* 权限 */
  kbPermissions: () => request<KbPermissions>("GET", "/api/rag/permissions"),
  kbSavePermissions: (body: Record<string, unknown>) =>
    request<KbPermissions & { message?: string }>("POST", "/api/rag/permissions", body),

  /* 解析能力 / 向量后端 */
  kbParsers: () => request<KbParserStatus>("GET", "/api/rag/parsers"),

  /* 思维图谱 */
  kbGraph: (folder = "", include_sub = true) =>
    request<KbGraphResp>("GET", `/api/rag/graph?folder=${encodeURIComponent(folder)}&include_sub=${include_sub ? 1 : 0}`),

  /* 检索 / 问答使用记录（逐条 + 整体管理） */
  kbRecords: (p?: {
    type?: KbRecordType | "";
    keyword?: string;
    since?: number;
    until?: number;
    limit?: number;
    offset?: number;
  }) => {
    const q = new URLSearchParams();
    if (p?.type) q.set("type", p.type);
    if (p?.keyword) q.set("keyword", p.keyword);
    if (p?.since) q.set("since", String(p.since));
    if (p?.until) q.set("until", String(p.until));
    if (p?.limit != null) q.set("limit", String(p.limit));
    if (p?.offset != null) q.set("offset", String(p.offset));
    const qs = q.toString();
    return request<KbRecordListResp>("GET", `/api/rag/records${qs ? `?${qs}` : ""}`);
  },
  kbRecord: (id: string) => request<KbRecordDetailResp>("GET", `/api/rag/records/${encodeURIComponent(id)}`),
  kbRecordDelete: (id: string) => request<{ ok: boolean; deleted?: number }>("DELETE", `/api/rag/records/${encodeURIComponent(id)}`),
  kbRecordsClear: (type?: KbRecordType | "") =>
    request<{ ok: boolean; cleared_type?: string }>(
      "DELETE",
      type ? `/api/rag/records?type=${encodeURIComponent(type)}` : "/api/rag/records",
    ),
  kbRecordsExport: (p?: { type?: KbRecordType | ""; keyword?: string; fmt?: "json" | "md" }) => {
    const q = new URLSearchParams();
    if (p?.type) q.set("type", p.type);
    if (p?.keyword) q.set("keyword", p.keyword);
    if (p?.fmt) q.set("fmt", p.fmt);
    const qs = q.toString();
    return request<KbRecordExportResp>("GET", `/api/rag/records/export${qs ? `?${qs}` : ""}`);
  },

  /* ---------- 报告 ---------- */
  report: (body: { operator: string; org: string; include_cover: boolean; report_filter?: "all" | "fluency" }) =>
    request<Blob>("POST", "/api/report", body, { blob: true }),

  /* ---------- 资产评估报告智能核查（四阶段闭环） ---------- */
  valMeta: () => request<ValMeta>("GET", "/api/valuation/meta"),
  valUpload: (files: File[], slots: string[]) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    slots.forEach((s) => fd.append("slots", s));
    return request<ValUploadResp>("POST", "/api/valuation/upload", fd, { form: true });
  },
  valRun: (body: { session_id: string; use_ai: boolean; nodes?: string[] }) =>
    request<ValRunResp>("POST", "/api/valuation/run", body),
  valTask: (taskId: string) => request<ValTaskSnapshot>("GET", `/api/valuation/task/${taskId}`),
  valCancel: (taskId: string) => request<{ ok: boolean }>("POST", `/api/valuation/task/${taskId}/cancel`),
  valResult: (taskId: string) => request<ValResultResp>("GET", `/api/valuation/result/${taskId}`),
  valReport: (taskId: string) => request<Blob>("GET", `/api/valuation/report/${taskId}`, undefined, { blob: true }),
  valReset: (sessionId?: string) => request<{ ok: boolean }>("POST", "/api/valuation/reset", { session_id: sessionId }),
};
