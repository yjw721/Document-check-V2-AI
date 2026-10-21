/* A/B 诊断：对比「原样」与「关闭 holoFlow 动画」的空闲期渲染开销 */
const { chromium } = require("playwright");

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const BASE = "http://127.0.0.1:8501";

const ROUTES = [
  ["规则", "unified/rules"],
  ["后台", "admin"],
  ["知识库", "kb"],
  ["总览", "overview"],
];

const KEYS = ["LayoutCount", "RecalcStyleCount", "RecalcStyleDuration", "ScriptDuration", "TaskDuration"];

async function sample(cdp, page, key, extraCss) {
  await page.evaluate((k) => { window.location.hash = "#" + k; }, key);
  await page.waitForTimeout(1600);
  if (extraCss) {
    await page.addStyleTag({ content: extraCss });
    await page.waitForTimeout(600);
  }
  const grab = async () => {
    const { metrics } = await cdp.send("Performance.getMetrics");
    const m = {};
    metrics.forEach((x) => { m[x.name] = x.value; });
    return m;
  };
  const a = await grab();
  await page.waitForTimeout(3000);
  const b = await grab();
  const d = {};
  KEYS.forEach((k) => { d[k] = Math.round(((b[k] ?? 0) - (a[k] ?? 0)) * 1000) / 1000; });
  return d;
}

(async () => {
  const browser = await chromium.launch({
    executablePath: EDGE,
    headless: true,
    args: ["--disable-background-timer-throttling", "--disable-renderer-backgrounding"],
  });

  const run = async (label, extraCss) => {
    const ctx = await browser.newContext({ viewport: { width: 1600, height: 950 } });
    const page = await ctx.newPage();
    const cdp = await ctx.newCDPSession(page);
    await cdp.send("Performance.enable");
    await page.goto(BASE + "/#overview", { waitUntil: "networkidle" });
    await page.waitForTimeout(2000);
    for (const [name, key] of ROUTES) {
      const d = await sample(cdp, page, key, extraCss);
      console.log(
        `  ${label.padEnd(12)} ${name.padEnd(6)} 重算次数=${String(d.RecalcStyleCount).padEnd(5)} 样式耗时=${String((d.RecalcStyleDuration * 1000).toFixed(0)).padEnd(6)}ms 任务总耗时=${String((d.TaskDuration * 1000).toFixed(0)).padEnd(6)}ms`,
      );
    }
    await ctx.close();
  };

  console.log("=== A：原样（保留全部 holoFlow 动画）===");
  await run("原样", null);

  console.log("\n=== B：仅关闭 holoFlow 动画（外观静态化，用于确认开销归属）===");
  await run("关闭动画", "*{animation:none !important}");

  await browser.close();
  process.exit(0);
})().catch((e) => {
  console.error("PROBE FAILED:", e.message);
  process.exit(1);
});
