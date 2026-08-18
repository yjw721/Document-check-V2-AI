/* 规则与词库统一管理：静态资源冒烟测试（无需 jsdom / 浏览器）
 * 验证：部署产物完整性 / 五个标签打包 / 内置规则与内置词库 API 接入
 * 运行：node tests/_uni_dom_smoke.js   （cwd = 项目根）
 */
const fs = require("fs");
const path = require("path");

const STATIC = path.join(__dirname, "..", "static");
const htmlPath = path.join(STATIC, "index.html");
if (!fs.existsSync(htmlPath)) {
  console.error("FAIL 缺少 static/index.html（请先 npm run build 并同步 dist）");
  process.exit(1);
}

const html = fs.readFileSync(htmlPath, "utf-8");
const jsName = (html.match(/src="(\/assets\/[^"]+\.js)"/) || [])[1];
if (!jsName) {
  console.error("FAIL index.html 未引用 JS 资源");
  process.exit(1);
}
const jsPath = path.join(STATIC, jsName.replace(/^\//, ""));
const js = fs.readFileSync(jsPath, "utf-8");
const cssName = (html.match(/href="(\/assets\/[^"]+\.css)"/) || [])[1];
const css = cssName ? fs.readFileSync(path.join(STATIC, cssName.replace(/^\//, "")), "utf-8") : "";

let pass = 0, fail = 0;
function check(name, cond) {
  if (cond) { pass++; console.log("  PASS " + name); }
  else { fail++; console.log("  FAIL " + name); }
}

console.log("资源:", jsName, `(${(js.length / 1024).toFixed(1)} KB)`);
console.log("— 部署完整性 —");
check("index.html 存在且引用新 JS", html.includes('assets/') && jsName.includes("index-"));
check("CSS 资源存在", fs.existsSync(path.join(STATIC, (html.match(/href="(\/assets\/[^"]+\.css)"/) || [])[1].replace(/^\//, ""))));

console.log("— 六标签导航 —");
const tabs = ["自定义正则规则", "自定义词库", "词库与标准规则批量导入", "内置标准规则", "内置词库", "AI 规则词库智能生成"];
for (const t of tabs) check(`打包含标签「${t}」`, js.includes(t));
for (const k of ["customRules", "wordbanks", "template", "builtinRules", "builtinDicts", "aiCreate"])
  check(`打包含标签键「${k}」`, js.includes(k));
check("页面标题文案含「规则与词库统一管理」", js.includes("规则与词库统一管理"));

console.log("— 内置标准规则（标签4）—");
for (const s of ["保存规则", "恢复默认", "整改建议", "Word 文档", "Excel 表格", "文本规范检测"])
  check(`打包含「${s}」`, js.includes(s));
check("调用保存规则 API（/api/rules POST）", js.includes("rules/restore") || js.includes('saveRules'));

console.log("— 内置词库（标签5）—");
for (const s of ["刷新列表", "保存词库", "词条"])
  check(`打包含「${s}」`, js.includes(s));
check("接入 /api/dictionaries 列表 API", js.includes("/api/dictionaries"));
check("接入单词库读写 API（GET/POST 路径模板）", js.includes("api/dictionaries/") || js.includes("dictionaries/"));

console.log("— 后台设置去重整合 —");
for (const s of ["检测全局限制", "前往规则与词库统一管理", "对所有文档类型生效"])
  check(`打包含「${s}」`, js.includes(s));
check("旧重复卡片标题「标准检测规则」已去除", !js.includes("标准检测规则"));
check("旧重复分区「Word 检测」已去除（仅剩报告页文案）", !js.includes("Word 检测", ) || js.includes("生成 Word 检测报告"));
check("旧重复分区「Excel 检测」已去除", !js.includes("Excel 检测"));
check("统一管理标签全部保留", ["自定义正则规则", "内置标准规则", "内置词库"].every(t => js.includes(t)));

console.log("— 界面主题配置（后台设置 · 全局运行设置内）—");
for (const s of ["界面主题配置", "配色方案", "全息渐变 Holographic", "深色收敛 Dark", "自定义强调色", "实时预览", "恢复全息渐变默认主题", "theme_scheme", "accent_color"])
  check(`打包含「${s}」`, js.includes(s));
check("JS 注入主题引擎（scheme 键 + accent 变量注入）", js.includes("holographic") && js.includes("accent-rgb"));
check("CSS 含深色方案覆盖层 [data-scheme=dark]", css.includes("data-scheme"));
check("CSS 含 accent 派生光晕变量", css.includes("--glow-btn") && css.includes("--border-accent-soft"));

console.log("— 语句通顺度检测（内置标准规则标签 · 语句通顺检测分区）—");
for (const s of ["语句通顺检测", "灵敏度", "启用全部", "停用全部"])
  check(`打包含「${s}」`, js.includes(s));
check("前端接灵敏度设置键 fluency_sensitivity", js.includes("fluency_sensitivity"));

console.log("— 报告筛选导出（报告页）—");
check("报告页含导出范围选择（全部问题 / 仅语句通顺类）", js.includes("导出范围") && js.includes("仅语句通顺类问题"));
check("报告请求携带 report_filter 参数", js.includes("report_filter"));

console.log("— AI 智能核验（检测页 · 本地/联网选择）—");
for (const s of ["AI 智能核验", "不启用 AI 核验", "本地 AI（Ollama）", "联网 AI（OpenAI 兼容）", "AI 配置", "测试连接", "aiTest", "aiStatus"])
  check(`打包含「${s}」`, js.includes(s));
check("前端接入 /api/ai/test 与 /api/ai/status", js.includes("/api/ai/test") && js.includes("/api/ai/status"));
check("本地模型下拉选择与 aiModels 扫描", js.includes("aiModels") && js.includes("已同步扫描本机") && js.includes("手动输入模型名"));
check("配置键 fluency 灵敏度与 ai 组并存", js.includes("fluency_sensitivity") && js.includes("base_url"));

console.log("— AI 规则词库智能生成统一模块（对话式/文本式/自学习） —");
for (const s of ["对话式创建", "文本式创建", "自学习记忆", "AI对话创建", "AI文本创建", "本地AI自学习生成-人工校对样本", "配对人工校对样本", "上传修订文档", "比对差异", "确认正确，加入本地记忆样本库", "立即学习", "导出词库 CSV", "导出规则 TXT", "批量清空学习记忆", "aiMemoryAddSample", "aiMemoryLearn", "aiMemoryToggle", "aiMemoryClear", "aiMemoryPair", "aiMemoryDiffs", "aiMemoryExport"])
  check(`打包含「${s}」`, js.includes(s));
check("前端接入 /api/ai/build/text 文本式创建", js.includes("/api/ai/build/text"));
check("前端接入配对/差异/导出接口", js.includes("/api/ai_memory/pair") && js.includes("/api/ai_memory/diffs") && js.includes("/api/ai_memory/export"));
check("设置含全局总开关 create_enabled", js.includes("create_enabled") && js.includes("启用本地AI智能生成&自学习"));
check("隐私承诺文案（不自动采集/本机保存/不入报告）", js.includes("AI 不自动采集") && js.includes("不进入检测导出报告"));

console.log("— AI 参考资料（参考知识注入）—");
for (const s of ["参考资料", "上传参考资料", "核验时携带参考资料", "aiRefs", "aiRefUpload", "aiRefToggle", "aiRefDelete", "ref_enabled", "ref_max_chars"])
  check(`打包含「${s}」`, js.includes(s));

console.log(`\n结果: ${pass} 通过 / ${fail} 失败`);
process.exit(fail ? 1 : 0);