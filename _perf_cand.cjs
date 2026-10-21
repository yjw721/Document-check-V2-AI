/* 优化候选实测：在「规则页」（49 个动画元素，最重）上对比各方案的空闲开销 */
const { chromium } = require("playwright");

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const BASE = "http://127.0.0.1:8501";
const ROUTE = "unified/rules";

const CANDIDATES = [
  ["A 原样", ""],
  ["B animation:none（下限基准）", "*{animation:none !important}"],
  ["C will-change:background-position", ".holo-bg,.holo-text,.holo-btn-primary{will-change:background-position}"],
  ["D contain:paint", ".holo-bg,.holo-text,.holo-btn-primary{contain:paint}"],
  ["E contain:strict", ".holo-bg,.holo-text,.holo-btn-primary{contain:strict}"],
  ["F will-change:transform（强制提层）", ".holo-bg,.holo-text,.holo-btn-primary{will-change:transform}"],
];

(async () => {
  const browser = await chromium.launch({
    executablePath: EDGE,
    headless: true,
    args: ["--disable-background-timer-throttling", "--disable-renderer-backgrounding"],
  });

  for (const [label, css] of CANDIDATES) {
    const ctx = await browser.newContext({ viewport: { width: 1600, height: 950 } });
    const page = await ctx.newPage();
    const cdp = await ctx.newCDPSession(page);
    await cdp.send("Performance.enable");
    await page.goto(BASE + "/#" + ROUTE, { waitUntil: "networkidle" });
    await page.waitForTimeout(2000);
    if (css) {
      await page.addStyleTag({ content: css });
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
    const d = (k) => Math.round(((b[k] ?? 0) - (a[k] ?? 0)) * 1000);
    console.log(
      `${label.padEnd(34)} 重算次数=${String(d("RecalcStyleCount")).padEnd(5)} 样式耗时=${String(d("RecalcStyleDuration")).padEnd(6)}ms 任务总耗时=${String(d("TaskDuration")).padEnd(6)}ms`,
    );
    await ctx.close();
  }

  await browser.close();
  process.exit(0);
})().catch((e) => {
  console.error("PROBE FAILED:", e.message);
  process.exit(1);
});
