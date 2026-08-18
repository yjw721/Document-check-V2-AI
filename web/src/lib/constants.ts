/* ============================================================
   全局常量：导航、标签页、级别、范本类别等
   ============================================================ */

export const NAV = [
  { key: "overview", name: "总览面板", ic: "📊" },
  { key: "detection", name: "检测管理", ic: "🔍" },
  { key: "unified", name: "规则与词库统一管理", ic: "🗄️" },
  { key: "admin", name: "后台设置", ic: "🛡️" },
  { key: "settings", name: "缓存与系统", ic: "🗂️" },
] as const;

export const DET_TABS = [
  { key: "upload", name: "导入与检测", ic: "📥" },
  { key: "files", name: "文件列表", ic: "📄" },
  { key: "issues", name: "错误详情", ic: "🐞" },
  { key: "waiting", name: "核验中", ic: "⏳" },
  { key: "report", name: "报告导出", ic: "📝" },
] as const;

export const UNI_TABS = [
  { key: "customRules", name: "自定义正则规则", ic: "🧩" },
  { key: "wordbanks", name: "自定义词库", ic: "📚" },
  { key: "template", name: "词库与标准规则批量导入", ic: "📥" },
  { key: "builtinRules", name: "内置标准规则", ic: "⚙️" },
  { key: "builtinDicts", name: "内置词库", ic: "📖" },
  { key: "aiBuild", name: "AI 智能创建", ic: "🤖" },
  { key: "aiMemory", name: "本地AI自学习", ic: "🧠" },
] as const;

export const SEV_LABEL: Record<string, string> = { high: "严重", medium: "一般", low: "轻微" };

export const TP_TAGS = ["执业风险警示", "格式严重错误", "笔误警示", "表述优化建议"] as const;

export const TP_TAG_CLS: Record<string, "sev-high" | "sev-medium" | "sev-low"> = {
  笔误警示: "sev-medium",
  表述优化建议: "sev-low",
  格式严重错误: "sev-high",
  执业风险警示: "sev-high",
};

export const TP_CATS: [string, string][] = [
  ["general", "通用检测规则库"],
  ["industry", "行业规范词库"],
  ["asset", "资产评估准则"],
  ["practice", "执业规范模板"],
  ["correction", "标准纠错库"],
  ["forbidden", "禁用词库"],
  ["official", "公文规范库"],
];

export const FT_ICON: Record<string, string> = {
  Word: "📘",
  PDF: "📕",
  TXT: "📄",
  CSV: "📊",
  SCEL: "🔤",
};

export const FT_LABEL: Record<string, string> = {
  Word: "Word 文档",
  PDF: "PDF 文档",
  TXT: "文本词库",
  CSV: "表格词库",
  SCEL: "搜狗词库",
};

/** 文件类型图标（表格用） */
export const FT_TABLE_ICON: Record<string, string> = {
  Word: "📘",
  Excel: "📗",
  PDF: "📕",
};
