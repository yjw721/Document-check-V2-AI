/* ============================================================================
 * 已停用（SKIP）：DOM 冒烟测试 - 检测管理模块（旧版前端 DOM 结构）
 * 原因：当前前端已重构为 React + Vite（bundle 为 ES Module <script type="module">），
 *       jsdom 不支持执行 ES Module 脚本，本测试无法渲染页面，自 React 重构后从未运行。
 * 说明：检测管理模块的集成验证由以下替代：
 *       1) tests/_uni_dom_smoke.js          —— 静态产物/字符串冒烟（node 直接运行）
 *       2) C:\Users\86135\AppData\Local\Temp\opencode\task_e2e*.py
 *                                          —— 真实服务 API 端到端（进度/阶段/日志/取消/报告）
 *       3) 浏览器人工走查（上传 → 核验中 → 自动跳转错误详情 → 报告导出）
 * 如需恢复本测试：需将 bundle 改为 IIFE 输出或用 Playwright 等真实浏览器驱动。
 * ============================================================================ */
const fs = require("fs");
const { JSDOM } = require("jsdom");

console.log("[SKIP] _det_dom_smoke.js 已停用：React+ESM 产物无法在 jsdom 中渲染，详见文件头注释。");
process.exit(0);