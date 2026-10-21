/* 综合卡顿矩阵探针：idle 占用 / 输入延迟 / 滚动帧率 / 导航切换 */
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

const OBSERVER = `
  window.__lt = [];
  try {
    new PerformanceObserver(function (list) {
      for (const e of list.getEntries()) window.__lt.push(Math.round(e.duration));
    }).observe({ entryTypes: ["longtask"] });
  } catch (e) {}
`;

/* 在页面内按 rAF 统计帧间隔，返回 p95 帧时长与掉帧数 */
const FPS_PROBE = `(function (ms) {
  return new Promise(function (resolve) {
    var frames = [], last = performance.now(), stop = last + ms;
    function tick(now) {
      frames.push(now - last); last = now;
      if (now < stop) requestAnimationFrame(tick);
      else {
        var s = frames.slice(1).sort(function (a, b) { return a - b; });
        var p50 = s.length ? s[Math.floor(s.length * 0.5)] : 0;
        var p95 = s.length ? s[Math.floor(s.length * 0.95)] : 0;
        resolve({
          frames: s.length,
          p50: Math.round(p50 * 10) / 10,
          p95: Math.round(p95 * 10) / 10,
          janky: s.filter(function (d) { return d > 32; }).length
        });
      }
    }
    requestAnimationFrame(tick);
  });
})(2500)`;

(async () => {
  const browser = await chromium.launch({
    executablePath: EDGE,
    headless: true,
    args: ["--disable-background-timer-throttling", "--disable-renderer-backgrounding"],
  });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 950 } });
  await ctx.addInitScript(OBSERVER);
  const page = await ctx.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(e.message.slice(0, 120)));

  await page.goto(BASE + "/#overview", { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);

  console.log("页面".padEnd(8) + "idle-p95帧".padEnd(12) + "掉帧".padEnd(8) + "输入p95(ms)".padEnd(14) + "滚动p95(ms)".padEnd(14) + "导航阻塞(ms)");

  for (const [label, key] of PAGES) {
    /* 1. 导航到目标页并测量阻塞 */
    await page.evaluate(() => { window.__lt.length = 0; });
    await page.evaluate((k) => { window.location.hash = "#" + k; }, key);
    await page.waitForTimeout(1200);
    const navLt = await page.evaluate(() => window.__lt.slice());
    const navTotal = navLt.reduce((a, b) => a + b, 0);

    /* 2. idle 帧率 */
    const idle = await page.evaluate(FPS_PROBE);

    /* 3. 输入延迟：找到第一个可输入元素，逐字符输入，记录每次到下一帧的耗时 */
    let inp = { p95: 0 };
    try {
      const target = page.locator("textarea, input[type=text], input:not([type])").first();
      if (await target.count()) {
        await target.click({ timeout: 2000 });
        const durs = await page.evaluate(async () => {
          const el = document.activeElement;
          if (!el || (el.tagName !== "TEXTAREA" && el.tagName !== "INPUT")) return [];
          const out = [];
          const txt = "资产评估报告规范性检查测试用例输入文本";
          for (const ch of txt) {
            const t = performance.now();
            const setter = Object.getOwnPropertyDescriptor(
              el.tagName === "TEXTAREA"
                ? HTMLTextAreaElement.prototype
                : HTMLInputElement.prototype,
              "value",
            ).set;
            setter.call(el, el.value + ch);
            el.dispatchEvent(new Event("input", { bubbles: true }));
            await new Promise((r) => requestAnimationFrame(() => r()));
            out.push(performance.now() - t);
          }
          // 清空，避免污染页面状态
          setter.call(el, "");
          el.dispatchEvent(new Event("input", { bubbles: true }));
          return out;
        });
        if (durs.length) {
          const s = [...durs].sort((a, b) => a - b);
          inp = { p95: Math.round(s[Math.floor(s.length * 0.95)] * 10) / 10 };
        }
      }
    } catch (e) {
      /* 该页无输入框，跳过 */
    }

    /* 4. 滚动帧率 */
    const scroll = await page.evaluate(`(function () {
      return new Promise(function (resolve) {
        var y = 0, frames = [], last = performance.now(), n = 0;
        function step(now) {
          frames.push(now - last); last = now;
          y += 60; window.scrollTo(0, y);
          n++;
          if (n < 60) requestAnimationFrame(step);
          else {
            var s = frames.slice(1).sort(function (a, b) { return a - b; });
            resolve({ p95: Math.round((s.length ? s[Math.floor(s.length * 0.95)] : 0) * 10) / 10,
                     janky: s.filter(function (d) { return d > 32; }).length });
          }
        }
        requestAnimationFrame(step);
      });
    })()`);
    await page.evaluate(() => window.scrollTo(0, 0));

    console.log(
      label.padEnd(6) +
        String(idle.p95).padEnd(12) +
        String(idle.janky).padEnd(8) +
        String(inp.p95).padEnd(14) +
        String(scroll.p95).padEnd(14) +
        navTotal,
    );
  }

  console.log("\n页面错误: " + (errs.length ? errs.join(" | ") : "无"));
  await browser.close();
  process.exit(0);
})().catch((e) => {
  console.error("PROBE FAILED:", e.message);
  process.exit(1);
});
