/* ============================================================
   文档核验中心 · API 客户端
   与后端 FastAPI（127.0.0.1:8501）通信；同源时使用相对路径，
   异源（如内置预览面板）回退到绝对地址直连后端（后端已放行 CORS）。
   ============================================================ */
import type {
  AiBuildResult,
  AiMemoryData,
  AiMemoryResp,
  AiRef,
  AiSettings,
  CustomRulesData,
  DictionariesData,
  DictionaryContent,
  FilesData,
  IssueStateBody,
  IssuesData,
  OverviewData,
  RulesData,
  SettingsData,
  TaskSnapshot,
  TaskStartResult,
  TemplateDraft,
  TemplateImportResult,
  WordbanksData,
  WordImportResult,
} from "./types";

const API_BASE =
  location.origin === "http://127.0.0.1:8501" || location.origin === "http://localhost:8501"
    ? ""
    : "http://127.0.0.1:8501";

export interface ApiOptions {
  /** multipart/form-data：body 直接作为 FormData */
  form?: boolean;
  /** 期望返回 Blob（报告下载） */
  blob?: boolean;
}

async function request<T>(method: string, path: string, body?: unknown, opts: ApiOptions = {}): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body !== undefined) {
    if (opts.form) {
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
      throw new Error((j as { detail?: string } | null)?.detail || "请求失败");
    }
    return (await r.blob()) as unknown as T;
  }
  const ct = r.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    const j = (await r.json()) as T & { detail?: string };
    if (!r.ok) throw new Error(j.detail || "请求失败");
    return j;
  }
  if (!r.ok) throw new Error("请求失败");
  return r as unknown as T;
}

export const api = {
  /* ---------- 总览 ---------- */
  overview: () => request<OverviewData>("GET", "/api/overview"),

  /* ---------- 文件 / 检测 ---------- */
  files: () => request<FilesData>("GET", "/api/files"),
  issues: () => request<IssuesData>("GET", "/api/issues"),
  setIssueState: (body: IssueStateBody) => request<{ ok: boolean }>("POST", "/api/issue_state", body),
  upload: (fd: FormData) => request<TaskStartResult>("POST", "/api/upload", fd, { form: true }),
  scanFolder: (body: { folder: string; recursive: boolean }) =>
    request<TaskStartResult>("POST", "/api/scan_folder", body),
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
  saveCustomRules: (data: CustomRulesData) => request<{ ok: boolean }>("POST", "/api/custom_rules", data),
  wordbanks: () => request<WordbanksData>("GET", "/api/wordbanks"),
  saveWordbanks: (data: WordbanksData) => request<{ ok: boolean }>("POST", "/api/wordbanks", data),
  wordbankImport: (body: { text: string }) => request<WordImportResult>("POST", "/api/wordbanks/import", body),

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
  aiModels: () => request<{ ok: boolean; models: string[]; message?: string }>("GET", "/api/ai/models"),
  aiTest: (body: Partial<AiSettings>) =>
    request<{ ok: boolean; message: string }>("POST", "/api/ai/test", body),

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

  /* ---------- AI 规则/词库生成 ---------- */
  aiBuildDialogue: (text: string) =>
    request<{ ok: boolean; message: string; result: AiBuildResult }>("POST", "/api/ai/build/dialogue", { text }),
  aiBuildDoc: (file: File) => {
    const fd = new FormData();
    fd.append("files", file);
    return request<{ ok: boolean; message: string; result: AiBuildResult }>("POST", "/api/ai/build/doc", fd);
  },

  /* ---------- 本地 AI 自学习记忆 ---------- */
  aiMemory: () => request<AiMemoryData>("GET", "/api/ai_memory"),
  aiMemoryToggle: (enabled: boolean) =>
    request<{ ok: boolean; enabled: boolean }>("POST", "/api/ai_memory/toggle", { enabled }),
  aiMemoryAddSample: (body: { content: string; source?: string; note?: string }) =>
    request<AiMemoryResp>("POST", "/api/ai_memory/samples", body),
  aiMemoryLearn: (sid: string) =>
    request<AiMemoryResp>("POST", `/api/ai_memory/samples/${sid}/learn`),
  aiMemorySampleToggle: (sid: string, enabled: boolean) =>
    request<AiMemoryResp>("POST", `/api/ai_memory/samples/${sid}/toggle`, { enabled }),
  aiMemorySampleDelete: (sid: string) =>
    request<AiMemoryResp>("DELETE", `/api/ai_memory/samples/${sid}`),
  aiMemoryLearnedToggle: (lid: string, enabled: boolean) =>
    request<AiMemoryResp>("POST", `/api/ai_memory/learned/${lid}/toggle`, { enabled }),
  aiMemoryLearnedDelete: (lid: string) =>
    request<AiMemoryResp>("DELETE", `/api/ai_memory/learned/${lid}`),
  aiMemoryClear: () => request<AiMemoryResp>("POST", "/api/ai_memory/clear"),

  /* ---------- 报告 ---------- */
  report: (body: { operator: string; org: string; include_cover: boolean; report_filter?: "all" | "fluency" }) =>
    request<Blob>("POST", "/api/report", body, { blob: true }),
};
