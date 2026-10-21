/* 空闲期渲染开销探针：用 CDP Performance.getMetrics 采样 3 秒内的
   Layout / RecalcStyle / Script 增量，定位持续动画导致的常驻开销 */
const { chromium } = require("playwright");

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const BASE = "http://127.0.0.1:8501";

const PAGES = [
  ["总览", "overview"],
  ["检测", "detection/upload"],
  ["规则", "unified/rules"],
  ["词库", "unified/wordbanks"],
  ["导入", "unified/template"],
  ["AI生成", "unified/aiCreate"],
  ["扫描", "unified/scan"],
  ["知识库", "kb"],
  ["AI配置", "aiconfig"],
  ["后台", "admin"],
  ["缓存", "settings"],
];

const KEYS = ["LayoutCount", "RecalcStyleCount", "LayoutDuration", "RecalcStyleDuration", "ScriptDuration", "TaskDuration"];

(async () => {
  const browser = await chromium.launch({
    executablePath: EDGE,
    headless: true,
    args: ["--disable-background-timer-throttling", "--disable-renderer-backgrounding"],
  });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 950 } });
  const page = await ctx.newPage();
  const cdp = await ctx.newCDPSession(page);
  await cdp.send("Performance.enable");

  const grab = async () => {
    const { metrics } = await cdp.send("Performance.getMetrics");
    const m = {};
    metrics.forEach((x) => { m[x.name] = x.value; });
    return m;
  };

  await page.goto(BASE + "/#overview", { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);

  const rows = [];
  for (const [label, key] of PAGES) {
    await page.evaluate((k) => { window.location.hash = "#" + k; }, key);
    await page.waitForTimeout(1800); // 等加载动画结束
    const a = await grab();
    await page.waitForTimeout(3000); // 纯 idle 3 秒
    const b = await grab();
    const d = {};
    KEYS.forEach((k) => {
      const va = a[k] ?? 0, vb = b[k] ?? 0;
      d[k] = Math.round((vb - va) * 1000) / 1000;
    });
    d.label = label;
    rows.push(d);
  }

  console.log("页面".padEnd(8) + "重排次数".padEnd(10) + "样式重算".padEnd(10) + "重排耗时".padEnd(10) + "样式耗时".padEnd(10) + "脚本耗时".padEnd(10) + "任务总耗时");
  rows.forEach((r) => {
    console.log(
      r.label.padEnd(6) +
        String(r.LayoutCount).padEnd(10) +
        String(r.RecalcStyleCount).padEnd(10) +
        (r.LayoutDuration * 1000).toFixed(0).padEnd(10) +
        (r.RecalcStyleDuration * 1000).toFixed(0).padEnd(10) +
        (r.ScriptDuration * 1000).toFixed(0).padEnd(10) +
        (r.TaskDuration * 1000).toFixed(0),
    );
  });
  console.log("\n（耗时单位：ms / 3 秒空闲窗口）");

  await browser.close();
  process.exit(0);
})().catch((e) => {
  console.error("PROBE FAILED:", e.message);
  process.exit(1);
});
