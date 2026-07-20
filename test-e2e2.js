// 边角测试: dirty check / alert / modal
const { chromium } = require('C:/Users/slow/.workbuddy/binaries/node/versions/22.22.2/node_modules/@playwright/cli/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const errors = [];
  const consoleMsgs = [];

  page.on('console', msg => {
    if (msg.type() === 'error' || msg.type() === 'warning') {
      consoleMsgs.push(`[${msg.type()}] ${msg.text()}`);
    }
  });
  page.on('pageerror', err => {
    errors.push(`[pageerror] ${err.message}\n  ${(err.stack || '').split('\n').slice(0, 3).join(' | ')}`);
  });

  await page.route('**/*', (route) => {
    const h = { ...route.request().headers() };
    h['Cache-Control'] = 'no-cache';
    route.continue({ headers: h });
  });

  await page.goto('http://localhost:5000/?_=' + Date.now(), { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);

  console.log('=== 1. 打开 Settings ===');
  await page.evaluate(() => openSettings());
  await page.waitForTimeout(800);

  console.log('=== 2. 修改 provider label (触发 dirty) ===');
  await page.evaluate(() => {
    const inp = document.querySelector('input[data-field="label"]');
    if (inp) {
      inp.value = 'Custom Test Label';
      inp.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });
  await page.waitForTimeout(500);

  console.log('=== 3. 关闭 Settings (会触发 confirm 因为有 dirty) ===');
  page.on('dialog', async (dialog) => {
    console.log(`  native dialog: ${dialog.type()} - ${dialog.message()}`);
    await dialog.accept();
  });
  await page.evaluate(() => closeSettings());
  await page.waitForTimeout(1000);

  console.log('=== 4. 打开 + 切 ring dropdown 触发 widget field update ===');
  await page.evaluate(() => openSettings());
  await page.waitForTimeout(800);
  await page.evaluate(() => {
    const sels = document.querySelectorAll('.widget-row select');
    if (sels.length >= 2) {
      sels[1].value = '5 小时';
      sels[1].dispatchEvent(new Event('change', { bubbles: true }));
    }
  });
  await page.waitForTimeout(1500);

  console.log('=== 5. 关闭 settings ===');
  await page.evaluate(() => closeSettings());
  await page.waitForTimeout(800);

  console.log('=== 6. 测试 global alert 区域 ===');
  const hasGlobalAlert = await page.evaluate(() => {
    return !!document.getElementById('global-alert');
  });
  console.log(`  global-alert element: ${hasGlobalAlert}`);

  console.log('=== 7. 测试通知中心 (🔔) ===');
  const bellBtn = await page.$('button[onclick*="toggleAlertCenter"], button:has-text("🔔")');
  if (bellBtn) {
    await bellBtn.click();
    await page.waitForTimeout(500);
  }

  console.log('=== 8. 多次快速切主题 + accent (压力测试) ===');
  const themes = ['glass', 'minimal', 'data', 'brand'];
  const accents = ['glass', 'aurora', 'berry', 'ocean', 'sunset'];
  for (let i = 0; i < 5; i++) {
    await page.evaluate((args) => {
      selectTheme(args.t);
      selectAccent(args.a);
    }, { t: themes[i % 4], a: accents[i % 5] });
    await page.waitForTimeout(100);
  }
  await page.waitForTimeout(1000);

  console.log('=== 9. 触发 widget toggle ===');
  await page.evaluate(() => openSettings());
  await page.waitForTimeout(800);
  await page.evaluate(() => {
    const cb = document.querySelector('.widget-row input[type="checkbox"]');
    if (cb) cb.click();
  });
  await page.waitForTimeout(1500);

  console.log('=== 10. 测试 escape 键 ===');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(500);

  console.log('\n========== 结果 ==========');
  console.log(`Console errors/warnings: ${consoleMsgs.length}`);
  consoleMsgs.forEach(m => console.log('  ' + m));
  console.log(`\nPage errors: ${errors.length}`);
  errors.forEach(e => console.log('  ' + e));

  await browser.close();
  process.exit(errors.length > 0 ? 1 : 0);
})().catch(e => { console.error('FATAL:', e); process.exit(2); });