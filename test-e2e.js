// 深度 E2E: 更多用户操作
const { chromium } = require('C:/Users/slow/.workbuddy/binaries/node/versions/22.22.2/node_modules/@playwright/cli/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const errors = [];
  const consoleMsgs = [];
  const requestFails = [];

  page.on('console', msg => {
    const t = msg.type();
    if (t === 'error' || t === 'warning') {
      consoleMsgs.push(`[${t}] ${msg.text()}`);
    }
  });
  page.on('pageerror', err => {
    errors.push(`[pageerror] ${err.message}\n  stack: ${(err.stack || '').split('\n').slice(0, 3).join(' | ')}`);
  });
  page.on('requestfailed', req => {
    requestFails.push(`[requestfailed] ${req.method()} ${req.url()} - ${req.failure()?.errorText}`);
  });

  await page.route('**/*', (route) => {
    const headers = { ...route.request().headers() };
    headers['Cache-Control'] = 'no-cache, no-store, must-revalidate';
    headers['Pragma'] = 'no-cache';
    headers['Expires'] = '0';
    route.continue({ headers });
  });

  console.log('=== 1. 访问首页 ===');
  await page.goto('http://localhost:5000/?_=' + Date.now(), { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);

  // 打开 settings
  console.log('=== 2. 打开 Settings ===');
  await page.evaluate(() => {
    if (typeof openSettings === 'function') openSettings();
    else document.querySelector('button[onclick*="openSettings"]')?.click();
  });
  await page.waitForTimeout(800);

  // 启用内置模板 (zai)
  console.log('=== 3. 启用内置模板 zai ===');
  await page.evaluate(() => {
    const card = document.querySelector('[data-tid="zai"]');
    if (card) {
      const btn = card.querySelector('button');
      if (btn) btn.click();
    }
  });
  await page.waitForTimeout(500);

  // 启用 minimax
  console.log('=== 4. 启用内置模板 minimax ===');
  await page.evaluate(() => {
    const card = document.querySelector('[data-tid="minimax"]');
    if (card) {
      const btn = card.querySelector('button');
      if (btn) btn.click();
    }
  });
  await page.waitForTimeout(500);

  // 启用 kimi
  console.log('=== 5. 启用内置模板 kimi ===');
  await page.evaluate(() => {
    const card = document.querySelector('[data-tid="kimi"]');
    if (card) {
      const btn = card.querySelector('button');
      if (btn) btn.click();
    }
  });
  await page.waitForTimeout(500);

  // 填入假 key 测渲染
  console.log('=== 6. 填入假 key (测渲染流程) ===');
  await page.evaluate(() => {
    document.querySelectorAll('input[data-field="key"]').forEach((inp, i) => {
      if (i < 3) inp.value = 'fake-key-test-' + i;
      inp.dispatchEvent(new Event('input', { bubbles: true }));
    });
  });
  await page.waitForTimeout(300);

  // 关闭 settings
  console.log('=== 7. 关闭 Settings ===');
  await page.evaluate(() => {
    if (typeof closeSettings === 'function') closeSettings();
  });
  await page.waitForTimeout(2000);

  // 刷新
  console.log('=== 8. Refresh ===');
  await page.evaluate(() => {
    if (typeof load === 'function') load();
  });
  await page.waitForTimeout(3000);

  // 切换 ring chip
  console.log('=== 9. 切换 trend ring chip ===');
  await page.evaluate(() => {
    document.querySelectorAll('#trend-rings-chips .chip').forEach(c => {
      c.click();
    });
  });
  await page.waitForTimeout(1500);

  // 切换 provider chip
  console.log('=== 10. 切换 trend provider chip ===');
  await page.evaluate(() => {
    document.querySelectorAll('#trend-providers-chips .chip').forEach(c => {
      c.click();
    });
  });
  await page.waitForTimeout(1500);

  // 重新打开 settings, 测试 widget 操作
  console.log('=== 11. 打开 Settings 测试 widget ===');
  await page.evaluate(() => openSettings());
  await page.waitForTimeout(800);

  // 切换 trend widget 的显示模式 (chart → hidden)
  console.log('=== 12. trend widget 切换到 hidden ===');
  await page.evaluate(() => {
    const sel = document.querySelector('.widget-row select');
    if (sel) {
      sel.value = 'hidden';
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    }
  });
  await page.waitForTimeout(1500);

  // 切回 chart
  console.log('=== 13. trend widget 切回 chart ===');
  await page.evaluate(() => {
    const sel = document.querySelector('.widget-row select');
    if (sel) {
      sel.value = 'chart';
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    }
  });
  await page.waitForTimeout(1500);

  // 关闭 settings
  console.log('=== 14. 关闭 Settings ===');
  await page.evaluate(() => closeSettings());
  await page.waitForTimeout(1000);

  // 切主题 4 次 (4 个主题都试一遍)
  console.log('=== 15. 切 4 个主题 ===');
  for (const tid of ['glass', 'minimal', 'data', 'brand']) {
    await page.evaluate((tid) => selectTheme(tid), tid);
    await page.waitForTimeout(400);
  }

  // 切 5 个强调色
  console.log('=== 16. 切 5 个强调色 ===');
  for (const aid of ['glass', 'aurora', 'berry', 'ocean', 'sunset']) {
    await page.evaluate((aid) => selectAccent(aid), aid);
    await page.waitForTimeout(400);
  }

  // 切明暗来回
  console.log('=== 17. 切明暗来回 3 次 ===');
  for (let i = 0; i < 3; i++) {
    await page.evaluate(() => {
      const html = document.documentElement;
      const isDark = html.classList.contains('dark');
      if (isDark) {
        html.classList.remove('dark');
        currentDark = false;
      } else {
        html.classList.add('dark');
        currentDark = true;
      }
      localStorage.setItem('vibeout-theme-dark', currentDark ? 'dark' : 'light');
      applyTheme();
    });
    await page.waitForTimeout(400);
  }

  console.log('\n========== 结果 ==========');
  console.log(`Console errors/warnings: ${consoleMsgs.length}`);
  consoleMsgs.forEach(m => console.log('  ' + m));
  console.log(`\nPage errors: ${errors.length}`);
  errors.forEach(e => console.log('  ' + e));
  console.log(`\nRequest failures: ${requestFails.length}`);
  requestFails.forEach(r => console.log('  ' + r));

  await browser.close();
  process.exit((errors.length + requestFails.length) > 0 ? 1 : 0);
})().catch(e => { console.error('FATAL:', e); process.exit(2); });