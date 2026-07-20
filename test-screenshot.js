// 自动截图: 拦截 /api/quota 返回 mock, 让卡片正常渲染
const { chromium } = require('C:/Users/slow/.workbuddy/binaries/node/versions/22.22.2/node_modules/@playwright/cli/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });

  page.on('pageerror', err => console.log('PAGE ERR:', err.message));

  await page.route('**/*', (route) => {
    const h = { ...route.request().headers() };
    h['Cache-Control'] = 'no-cache';
    route.continue({ headers: h });
  });

  // 拦截 /api/quota, 返回 mock 数据 (让 3 家都 ok=true)
  await page.route('**/api/quota', async (route) => {
    const now = Date.now();
    const providers = [
      // zai 用 normalizeZai: 需要 {data: {limits: [...], level}}, type=TOKENS_LIMIT, unit=3 (5h) / unit=6 (周), plus TIME_LIMIT for 月
      {
        id: 'zai', label: 'Z.ai / 智谱 GLM', color: '#2B7FFF', ok: true,
        data: {
          data: {
            level: 'Pro',
            limits: [
              { type: 'TOKENS_LIMIT', unit: 3, percentage: 5,  remaining: 5700, usage: 300,  nextResetTime: new Date(now + 4*3600*1000 + 36*60*1000).toISOString() },
              { type: 'TOKENS_LIMIT', unit: 6, percentage: 9,  remaining: 8200, usage: 800,  nextResetTime: new Date(now + 6*86400*1000 + 15*3600*1000).toISOString() },
              { type: 'TIME_LIMIT',   unit: 30, percentage: 12, remaining: 2600, usage: 400,  nextResetTime: new Date(now + 28*86400*1000).toISOString() },
            ]
          }
        }
      },
      // MiniMax 用 normalizeMinimax: needs model_remains with current_interval_remaining_percent; remains_time is ms duration
      {
        id: 'minimax', label: 'MiniMax Coding Plan', color: '#8b5cf6', ok: true,
        data: {
          base_resp: { status_code: 0, status_msg: 'ok' },
          model_remains: [
            { model_name: 'general', current_interval_remaining_percent: 97, current_weekly_remaining_percent: 95, remains_time: 3*3600*1000 + 48*60*1000, weekly_remains_time: 1*86400*1000 + 22*3600*1000 },
            { model_name: 'MiniMax-VL', current_interval_remaining_percent: 100 },
          ]
        }
      },
      // Kimi 用 normalizeKimi: needs {data: {limits: [{window: {duration: 300/...}, detail: {limit, used, resetTime}}]}}
      {
        id: 'kimi', label: 'Kimi Code', color: '#000000', ok: true,
        data: {
          data: {
            limits: [
              { window: { duration: 300 }, detail: { limit: 100, used: 6,  resetTime: new Date(now + 4*3600*1000 + 29*60*1000).toISOString() } },
            ],
            usage: { limit: 100, used: 32, resetTime: new Date(now + 6*86400*1000 + 13*3600*1000).toISOString() },
            user: { membership: { level: 'Pro' } },
          }
        }
      }
    ];
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ providers }) });
  });

  // 拦截 /api/history 返回 mock 历史数据 (足够画几条线)
  await page.route('**/api/history', async (route) => {
    const now = Date.now();
    const out = [];
    // 过去 7 天, 每 3 小时一条 (覆盖所有 ring 的时间窗)
    for (let i = 0; i < 56; i++) {
      const ts = new Date(now - (56 - i) * 3 * 3600 * 1000).toISOString();
      // 5h ring: 短期起伏 (用得快恢复)
      const zai5h = 95 - i * 0.5 + Math.sin(i / 1.5) * 6 - Math.max(0, 8 - i % 8) * 3;
      const minimax5h = 97 - i * 0.3 + Math.cos(i / 1.8) * 5;
      const kimi5h = 94 - i * 0.4 + Math.sin(i / 1.2) * 7 - Math.max(0, 5 - i % 6) * 4;
      // 7d ring: 长趋势
      const zai7d = 91 - i * 0.4 + Math.sin(i / 5) * 5;
      const minimax7d = 95 - i * 0.3 + Math.cos(i / 6) * 4;
      // 30d ring: 长趋势
      const kimiMo = 68 - i * 0.15 + Math.sin(i / 8) * 4;
      out.push({
        ts,
        providers: [
          { id: 'zai', label: 'Z.ai / 智谱 GLM', ok: true, rings: [
            { title: '5 小时', percent: Math.max(0, Math.min(100, 100 - zai5h)), resetText: '4h36m后重置' },
            { title: '周', percent: Math.max(0, Math.min(100, 100 - zai7d)), resetText: '6天后重置' },
          ]},
          { id: 'minimax', label: 'MiniMax Coding Plan', ok: true, rings: [
            { title: '5 小时', percent: Math.max(0, Math.min(100, 100 - minimax5h)), resetText: '3h48m后重置' },
            { title: '周', percent: Math.max(0, Math.min(100, 100 - minimax7d)), resetText: '1天后重置' },
          ]},
          { id: 'kimi', label: 'Kimi Code', ok: true, rings: [
            { title: '5 小时', percent: Math.max(0, Math.min(100, 100 - kimi5h)), resetText: '4h29m后重置' },
            { title: '月', percent: Math.max(0, Math.min(100, 100 - kimiMo)), resetText: '6天后重置' },
          ]},
        ]
      });
    }
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ history: out }) });
  });

  console.log('1. Visit');
  await page.goto('http://localhost:5000/?_=' + Date.now(), { waitUntil: 'networkidle' });
  await page.waitForTimeout(3000);

  // 设成 dark mode (看着更"苹果")
  await page.evaluate(() => {
    document.documentElement.classList.add('dark');
    currentDark = true;
    localStorage.setItem('vibeout-theme-dark', 'dark');
    applyTheme();
  });
  await page.waitForTimeout(800);

  // 等 charts render
  await page.waitForTimeout(2000);

  console.log('2. Screenshot full');
  await page.screenshot({ path: 'C:/Users/slow/ZCodeProject/quota-dashboard/dashboard-1.png', fullPage: true });
  console.log('   saved dashboard-1.png');

  await browser.close();
})().catch(e => { console.error('FATAL:', e); process.exit(2); });