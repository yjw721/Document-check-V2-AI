const { chromium } = require("playwright");

const BASE = "http://127.0.0.1:8501";

const ROUTES = [
  { name: "总览面板", hash: "#dashboard" },
  { name: "检测管理", hash: "#detection" },
  { name: "规则总览", hash: "#unified/rules" },
  { name: "词库总览", hash: "#unified/wordbanks" },
  { name: "批量导入", hash: "#unified/template" },
  { name: "AI智能生成", hash: "#unified/aiCreate" },
  { name: "扫描清理", hash: "#unified/scan" },
  { name: "本地知识库", hash: "#kb" },
  { name: "AI配置", hash: "#ai" },
  { name: "后台设置", hash: "#admin" },
  { name: "缓存与系统", hash: "#cache" },
];

// AI智能生成页的子标签（含本次报障的本地AI自学习）
const SUB_TABS = ["对话式创建", "文本式创建", "本地AI自学习"];

(async () => {
  const browser = await chromium.launch({
    executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    headless: true,
  });
  let total = 0;
  let bad = 0;

  for (const r of ROUTES) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errs = [];
    page.on("pageerror", (e) => errs.push(e.message.slice(0, 200)));
    page.on("console", (m) => {
      if (m.type() === "error" && /error #\d+|is not a function|Cannot read/i.test(m.text()))
        errs.push("CONSOLE " + m.text().slice(0, 200));
    });
    try {
      await page.goto(BASE + "/" + r.hash, { waitUntil: "networkidle", timeout: 25000 });
      await page.waitForTimeout(1200);
      const len = (await page.evaluate(() => document.body.innerText || "")).length;
      total++;
      const mark = errs.length ? "❌" : len > 20 ? "✅" : "⚠️";
      if (errs.length || len <= 20) bad++;
      console.log(`${mark} ${r.name.padEnd(8)} textLen=${String(len).padStart(5)} errs=${errs.length}`);
      errs.slice(0, 3).forEach((e) => console.log("      " + e));

      // 子标签巡检
      if (r.hash === "#unified/aiCreate") {
        for (const t of SUB_TABS) {
          const tab = page.locator(`text=${t}`).first();
          if ((await tab.count()) === 0) {
            console.log(`   ⚠️ 子标签未找到: ${t}`);
            continue;
          }
          const before = errs.length;
          await tab.click();
          await page.waitForTimeout(1800);
          const l2 = (await page.evaluate(() => document.body.innerText || "")).length;
          const newErrs = errs.length - before;
          console.log(`   ${newErrs ? "❌" : l2 > 20 ? "✅" : "⚠️"} 子标签「${t}」 textLen=${l2} 新错误=${newErrs}`);
          errs.slice(before, before + 3).forEach((e) => console.log("        " + e));
        }
        await page.screenshot({ path: "_shot_learning.png" });
      }
    } catch (e) {
      bad++;
      console.log(`❌ ${r.name} 导航异常: ${e.message.slice(0, 120)}`);
    }
    await page.close();
  }

  console.log(`\n巡检完成：共 ${total} 个页面，异常 ${bad} 个`);
  await browser.close();
  process.exit(bad ? 1 : 0);
})().catch((e) => {
  console.error("脚本异常:", e);
  process.exit(2);
});
