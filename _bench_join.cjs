/* 合成基准：复现 LearnProcessPanel / BuildProcessPanel 中
   logs.filter().map().join("") 在轮询过程中的累积开销。
   现状为 O(n^2)（每轮都全量重算），优化后为 O(n)（只累加新增）。 */
const ROUNDS = 1500;      // 20 分钟 × 每 800ms 一轮
const PER_ROUND = 20;     // 每轮流式新增字符数

function current() {
  const logs = [];
  let total = 0;
  for (let i = 0; i < ROUNDS; i++) {
    // 模拟后端每次返回「全量日志」，前端整体替换
    const fresh = [];
    for (let k = 0; k < (i % 5 === 0 ? 1 : PER_ROUND / 4); k++) {
      fresh.push({ ts: "00:00:00", kind: "token", text: "规范表述片段" });
    }
    logs.push(...fresh);
    // 组件内：thinking + output + stages + 摘要（4 次全量扫描）
    const thinking = logs.filter((l) => l.kind === "thinking").map((l) => l.text).join("");
    const output = logs.filter((l) => l.kind === "token").map((l) => l.text).join("");
    const stages = logs.filter((l) => l.kind === "stage").map((l) => l.text);
    const recent = logs.filter((l) => l.kind === "stage" || l.kind === "error").slice(-6);
    total += thinking.length + output.length + stages.length + recent.length;
  }
  return total;
}

function optimized() {
  const logs = [];
  let thinking = "";
  let output = "";
  let cursor = 0;
  let total = 0;
  for (let i = 0; i < ROUNDS; i++) {
    const fresh = [];
    for (let k = 0; k < (i % 5 === 0 ? 1 : PER_ROUND / 4); k++) {
      fresh.push({ ts: "00:00:00", kind: "token", text: "规范表述片段" });
    }
    logs.push(...fresh);
    // 只处理本次新增的条目
    for (let j = cursor; j < logs.length; j++) {
      const l = logs[j];
      if (l.kind === "thinking") thinking += l.text;
      else if (l.kind === "token") output += l.text;
    }
    cursor = logs.length;
    total += thinking.length + output.length;
  }
  return total;
}

function time(label, fn) {
  const t0 = process.hrtime.bigint();
  const r = fn();
  const ms = Number(process.hrtime.bigint() - t0) / 1e6;
  console.log(`${label}: ${ms.toFixed(1)}ms (处理字符 ${r})`);
  return ms;
}

console.log(`模拟 ${ROUNDS} 轮轮询（本地 CPU 推理约 20 分钟）\n`);
const a = time("优化前（每轮全量 filter+map+join）", current);
const b = time("优化后（增量累加）        ", optimized);
console.log(`\n主线程阻塞减少 ${(a - b).toFixed(1)}ms（${a > 0 ? (((a - b) / a) * 100).toFixed(1) : 0}%），提速 ${(b > 0 ? (a / b) : Infinity).toFixed(1)}x`);
