/* 前端卡顿探针：mock 大流量增量日志，实测主线程 long task 阻塞 */
const { chromium } = require("playwright");

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const BASE = "http://127.0.0.1:8501";
const TOTAL = 4000; // 模拟 4000 条日志（约等于一次中等长度推理）

const OBSERVER = `
  window.__lt = [];
  try {
    new PerformanceObserver(function (list) {
      for (const e of list.getEntries()) window.__lt.push(Math.round(e.duration));
    }).observe({ entryTypes: ["longtask"] });
  } catch (e) {}
`;

function mkLogs(from, n) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const idx = from + i;
    if (idx % 500 === 0) {
      out.push({ ts: "00:00:0" + (idx % 9), kind: "stage", text: "[infer] 阶段推进 " + idx });
    } else {
      out.push({ ts: "00:00:0" + (idx % 9), kind: "token", text: "这是第 " + idx + " 段模型输出内容，" });
    }
  }
  return out;
}

(async () => {
  const browser = await chromium.launch({
    executablePath: EDGE,
    headless: true,
    args: ["--disable-background-timer-throttling", "--disable-renderer-backgrounding"],
  });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 950 } });
  await ctx.addInitScript(OBSERVER);
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.log("  [pageerror]", e.message.slice(0, 160)));

  await page.goto(BASE + "/#unified/aiCreate", { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);

  /* 拦截生成日志接口：
     - URL 带 since= → 新前端（增量模式），只返回新增的一段
     - URL 无 since  → 旧前端（全量模式），返回从 0 到当前的全量数组
     这样同一脚本可在优化前后各跑一次，得到可对比的阻塞数据 */
  let served = 0;
  let reqs = 0;
  await page.route("**/api/ai/build/logs**", async (route) => {
    const url = route.request().url();
    const m = /since=(\d+)/.exec(url);
    reqs += 1;
    const n = Math.min(40, TOTAL - served);
    served += n;
    const done = served >= TOTAL;
    const body = {
      logs: m ? mkLogs(Number(m[1]), n) : mkLogs(0, served),
      offset: served,
      state: {
        running: !done,
        done,
        ok: done,
        stage: "infer",
        progress: Math.round((served / TOTAL) * 100),
        chars: served * 12,
      },
    };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  /* 触发一次文本式生成 */
  await page.locator("textarea").first().fill("资产评估报告中常见错误表述的规范写法测试输入。");
  await page.getByRole("button", { name: /生成规则与词库/ }).first().click();

  await page.evaluate(() => { window.__lt.length = 0; });
  const t0 = Date.now();
  /* 等日志流跑满，最多 70s */
  for (let i = 0; i < 140; i++) {
    await page.waitForTimeout(500);
    if (served >= TOTAL) break;
  }
  const lt = await page.evaluate(() => window.__lt.slice());
  const elapsed = Date.now() - t0;

  const total = lt.reduce((a, b) => a + b, 0);
  const max = lt.length ? Math.max.apply(null, lt) : 0;
  console.log("采样窗口: " + elapsed + " ms");
  console.log("轮询请求次数: " + reqs);
  console.log("已下发日志条数: " + served);
  console.log("long task 次数: " + lt.length);
  console.log("long task 总阻塞: " + total + " ms");
  console.log("最长单次阻塞: " + max + " ms");
  console.log("阻塞占比: " + ((total / elapsed) * 100).toFixed(2) + "%");
  console.log("长任务明细(ms): " + lt.join(", "));

  await browser.close();
  process.exit(0);
})().catch((e) => {
  console.error("PROBE FAILED:", e.message);
  process.exit(1);
});
