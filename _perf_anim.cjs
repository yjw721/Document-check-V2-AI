/* 列出页面上运行中的 CSS 动画，定位持续 style recalc 的来源 */
const { chromium } = require("playwright");

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const BASE = "http://127.0.0.1:8501";

const ROUTES = [
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

(async () => {
  const browser = await chromium.launch({
    executablePath: EDGE,
    headless: true,
    args: ["--disable-background-timer-throttling", "--disable-renderer-backgrounding"],
  });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 950 } });
  const page = await ctx.newPage();
  await page.goto(BASE + "/#overview", { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);

  for (const [label, key] of ROUTES) {
    await page.evaluate((k) => { window.location.hash = "#" + k; }, key);
    await page.waitForTimeout(1600);
    const info = await page.evaluate(() => {
      const list = (document.getAnimations ? document.getAnimations() : []);
      const running = list.filter((a) => a.playState === "running");
      const byName = {};
      const elems = {};
      running.forEach((a) => {
        const n = (a.animationName || (a.effect && a.effect.getKeyframes && "css") || "?");
        byName[n] = (byName[n] || 0) + 1;
        const t = a.effect && a.effect.target;
        const cls = t ? (t.className && typeof t.className === "string" ? t.className.slice(0, 60) : t.tagName) : "?";
        elems[cls] = (elems[cls] || 0) + 1;
      });
      return {
        total: list.length,
        running: running.length,
        byName,
        elems: Object.entries(elems).sort((x, y) => y[1] - x[1]).slice(0, 6),
      };
    });
    console.log(
      `[${label}] 动画总数=${info.total} 运行中=${info.running}  名称分布=${JSON.stringify(info.byName)}`,
    );
    console.log(`         高频元素: ${info.elems.map((e) => e[0] + "×" + e[1]).join(" | ")}`);
  }

  await browser.close();
  process.exit(0);
})().catch((e) => {
  console.error("PROBE FAILED:", e.message);
  process.exit(1);
});
