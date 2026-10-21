const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  page.setDefaultTimeout(10000);
  const errors = [];
  page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('CONSOLE.ERROR: ' + m.text()); });

  let dialogAction = 'accept';
  page.on('dialog', async (d) => { log('DIALOG', d.type(), 'action=' + dialogAction); await d[dialogAction](); });

  const log = (...a) => console.log(...a);

  await page.goto('http://127.0.0.1:8501/', { waitUntil: 'networkidle', timeout: 20000 });
  await page.locator('text=规则与词库统一管理').first().click({ timeout: 10000 });
  await page.waitForTimeout(700);
  await page.locator('button:has-text("规则")').first().click({ timeout: 8000 });
  await page.waitForTimeout(700);

  // 1) 新建规则组
  await page.locator('button:has-text("新建规则组")').first().click();
  await page.waitForTimeout(400);
  const nameInput = page.locator('input[value="新规则组"]').first();
  await nameInput.fill('测试规则组A');
  await page.waitForTimeout(150);

  // 2) 添加规则并填写
  await page.locator('button:has-text("添加规则")').last().click();
  await page.waitForTimeout(300);
  const lastRow = page.locator('table.holo-table tbody tr').last();
  await lastRow.locator('input').nth(0).fill('测试规则');      // 规则名
  await lastRow.locator('input').nth(1).fill('错误写法xx');     // 匹配式
  await lastRow.locator('input').nth(2).fill('正确写法yy');     // 建议
  await page.waitForTimeout(150);

  // 3) 保存本组修改
  await page.locator('button:has-text("保存本组修改")').last().click();
  await page.waitForTimeout(1500);
  const toast = await page.locator('.holo-toast').last().innerText().catch(() => '(none)');
  log('SAVE TOAST:', toast);

  // 4) 校验持久化（刷新后仍在）
  await page.reload({ waitUntil: 'networkidle' });
  await page.locator('text=规则与词库统一管理').first().click();
  await page.waitForTimeout(500);
  await page.locator('button:has-text("规则")').first().click();
  await page.waitForTimeout(700);
  const persisted = await page.locator('input[value="测试规则组A"]').count();
  log('PERSISTED (期望1):', persisted);
  const rulePersist = await page.locator('input[value="错误写法xx"]').count();
  log('RULE PATTERN PERSISTED (期望1):', rulePersist);

  // 5) 操作日志记录
  const logHas = await page.locator('text=测试规则组A').count();
  log('ACTIVITY LOG HAS GROUP (期望>0):', logHas);

  // 6) 重置
  const e2 = page.locator('input[value="测试规则组A"]').first();
  await e2.fill('测试规则组B');
  await page.waitForTimeout(150);
  await page.locator('button:has-text("重置")').last().click();
  await page.waitForTimeout(500);
  const reverted = await page.locator('input[value="测试规则组A"]').count();
  log('AFTER RESET A EXISTS (期望1):', reverted);

  // 7) 离开守卫：先制造未保存改动，再切换子标签（拒绝离开）
  const e3 = page.locator('input[value="测试规则组A"]').first();
  await e3.fill('未保存改动X');
  await page.waitForTimeout(150);
  dialogAction = 'dismiss';
  await page.locator('button:has-text("词库")').first().click();
  await page.waitForTimeout(600);
  const blocked = await page.locator('button:has-text("保存本组修改")').count();
  log('GUARD BLOCKED (save btn 仍存在=拦截成功, 期望>0):', blocked);

  // 8) 离开守卫：确认离开
  dialogAction = 'accept';
  await page.locator('button:has-text("词库")').first().click();
  await page.waitForTimeout(1500);
  const left = await page.locator('text=自定义词库').count();
  const stillRules = await page.locator('text=本页操作记录').count();
  log('URL after accept:', page.url());
  log('LEFT TO WORDBANK (期望>0):', left, ' RULES TAB GONE (期望0):', stillRules);

  // 9) 新规则校验失败：回到规则，新建组+空规则保存应失败
  await page.locator('text=规则与词库统一管理').first().click();
  await page.waitForTimeout(400);
  await page.locator('button:has-text("规则")').first().click();
  await page.waitForTimeout(500);
  await page.locator('button:has-text("新建规则组")').first().click();
  await page.waitForTimeout(300);
  await page.locator('button:has-text("添加规则")').last().click();
  await page.waitForTimeout(200);
  await page.locator('button:has-text("保存本组修改")').last().click();
  await page.waitForTimeout(1200);
  const failToast = await page.locator('.holo-toast').last().innerText().catch(() => '(none)');
  log('EMPTY-RULE SAVE TOAST (应含保存失败):', failToast);

  log('=== ERRORS (' + errors.length + ') ===');
  errors.forEach((e) => log(e));
  await browser.close();
})().catch((e) => { console.error('SCRIPT FAIL', e); process.exit(1); });
