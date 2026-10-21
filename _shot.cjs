/* 视觉回归截图：覆盖含 holo-bg / holo-btn-primary 的关键页面 */
const { chromium } = require("playwright");
const fs = require("fs");

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const BASE = "http://127.0.0.1:8501";
const OUT = "_shots";

const SHOTS = [
  ["01-overview", "overview"],
  ["02-unified-rules", "unified/rules"],
  ["03-unified-wordbanks", "unified/wordbanks"],
  ["04-unified-aicreate", "unified/aiCreate"],
  ["05-kb", "kb"],
  ["06-aiconfig", "aiconfig"],
  ["07-admin", "admin"],
  ["08-settings", "settings"],
];

(async () => {
  if (!fs.existsSync(OUT)) fs.mkdirSync(OUT);
  const browser = await chromium.launch({ executablePath: EDGE, headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await ctx.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(e.message.slice(0, 150)));

  await page.goto(BASE + "/#overview", { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);

  for (const [name, route] of SHOTS) {
    await page.evaluate((k) => { window.location.hash = "#" + k; }, route);
    await page.waitForTimeout(1800);
    await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: false });
    console.log("shot: " + name);
  }

  console.log("\n页面错误: " + (errs.length ? errs.join(" | ") : "无"));
  await browser.close();
  process.exit(0);
})().catch((e) => {
  console.error("SHOT FAILED:", e.message);
  process.exit(1);
});
