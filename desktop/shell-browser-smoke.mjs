import { mkdir, mkdtemp, readFile, rename, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { isAllowedRequest, parseArgs, startStaticServer, withBrowserContext } from './shell-browser-smoke-support.mjs';

const directory = dirname(fileURLToPath(import.meta.url));
const reviewedVersion = '1.61.1';

async function loadLockedPlaywright() {
  try {
    const lock = JSON.parse(await readFile(resolve(directory, '..', 'frontend', 'package-lock.json'), 'utf8'));
    const require = createRequire(resolve(directory, '..', 'frontend', 'package.json'));
    const installed = require('@playwright/test/package.json').version;
    if (lock.packages?.['node_modules/@playwright/test']?.version !== reviewedVersion || installed !== reviewedVersion) throw new Error('Installed package and reviewed lock do not match.');
    return require('@playwright/test');
  } catch (error) {
    throw new Error(`The frontend-locked @playwright/test ${reviewedVersion} is unavailable or mismatched. Run npm ci in frontend.`, { cause: error });
  }
}

async function checkShell(context, browser, origin, output, report, expect) {
  const blocked = new Map();
  await context.route('**/*', route => {
    if (isAllowedRequest(route.request().url(), origin)) return route.continue();
    const url = new URL(route.request().url());
    const key = `${url.protocol}//${url.host}${url.pathname}`;
    blocked.set(key, (blocked.get(key) ?? 0) + 1);
    return route.abort('blockedbyclient');
  });
  await context.routeWebSocket('**/*', socket => { report.blockedWebSockets++; socket.close(); });
  const page = await context.newPage();
  page.setDefaultTimeout(10_000);
  page.setDefaultNavigationTimeout(15_000);
  const errors = [];
  const popupClosures = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('popup', popup => { report.unexpectedPopups++; popupClosures.push(popup.close()); });
  report.browserVersion = browser.version();
  try {
    const response = await page.goto(`${origin}/index.html`);
    expect(response?.status()).toBe(200);
    expect(await page.evaluate(() => typeof window.__TAURI__)).toBe('undefined');
    await page.evaluate(() => {
      const badge = document.createElement('aside');
      badge.textContent = '浏览器外壳预览\n页面内置 fixture\n非原生 WebView2\n非打包 App / 非真实服务验证';
      badge.style.cssText = 'position:fixed;bottom:90px;left:12px;max-width:200px;padding:8px 12px;background:#142531;color:#eaf7ff;border:1px solid #4d7a91;border-radius:6px;font:12px/1.5 system-ui;white-space:pre-line;z-index:2147483647;pointer-events:none';
      document.body.append(badge);
    });
    // Preserve the old three DOM assertions; none establishes native readiness.
    await expect(page.locator('#task-input')).toBeVisible();
    await expect(page.locator('#service-list')).toHaveCount(1);
    await expect(page.locator('#feedback')).toHaveCount(1);
    report.checks.push('home task input, service list and feedback exist');
    await page.screenshot({ path: resolve(output, 'home.png'), fullPage: true });
    report.screenshots.home = resolve(output, 'home.png');

    await page.getByRole('button', { name: '设置', exact: true }).click();
    await expect(page.locator('#settings-view')).toBeVisible();
    await expect(page.locator('[data-settings-panel="general"]')).toBeVisible();
    await expect(page.locator('[data-settings-section="general"]')).toHaveClass(/active/);
    report.checks.push('settings view and general panel are visible');
    await page.screenshot({ path: resolve(output, 'settings.png'), fullPage: true });
    report.screenshots.settings = resolve(output, 'settings.png');

    await page.locator('[data-settings-section="monitoring"]').click();
    await expect(page.locator('[data-settings-panel="monitoring"]')).toBeVisible();
    await expect(page.locator('#monitor-stack')).toHaveCount(1);
    await expect(page.locator('#monitor-stack-state')).toHaveCount(1);
    report.checks.push('monitoring panel and stack fields exist');
    await page.screenshot({ path: resolve(output, 'monitoring.png'), fullPage: true });
    report.screenshots.monitoring = resolve(output, 'monitoring.png');
    await Promise.all(popupClosures);
    expect(errors).toEqual([]);
    expect(report.unexpectedPopups).toBe(0);
    expect(report.blockedWebSockets).toBe(0);
  } catch (error) {
    try {
      await page.screenshot({ path: resolve(output, 'failure.png'), fullPage: true });
      report.screenshots.failure = resolve(output, 'failure.png');
    } catch { /* A missing/crashed browser remains failure, without a screenshot. */ }
    throw error;
  } finally {
    report.blockedRequests = [...blocked].slice(0, 50).map(([url, count]) => ({ url, count }));
    report.omittedBlockedUrls = Math.max(0, blocked.size - 50);
  }
}

export async function runShellBrowserSmoke(options = parseArgs([])) {
  await mkdir(options.outputDirectory, { recursive: true });
  const output = await mkdtemp(resolve(options.outputDirectory, 'shell-browser-preview-'));
  const reportPath = resolve(output, 'report.json');
  const report = {
    schemaVersion: 1, scope: 'browser-shell-preview', status: 'running',
    nativeWebView2: false, packagedApplication: false, realServicesVerified: false,
    fixtureMode: 'desktop/ui browser-preview nativeInvoke fixtures',
    requestPolicy: 'browser context requests restricted to fixed static asset allowlist; not an OS network sandbox',
    requiredPlaywrightVersion: reviewedVersion, playwrightVersion: null, startedAt: new Date().toISOString(),
    checks: [], screenshots: {}, blockedRequests: [], omittedBlockedUrls: 0,
    blockedWebSockets: 0, unexpectedPopups: 0,
  };
  const save = async () => {
    await writeFile(`${reportPath}.tmp`, `${JSON.stringify(report, null, 2)}\n`);
    await rename(`${reportPath}.tmp`, reportPath);
  };
  await save();
  let failure;
  try {
    const server = await startStaticServer({ port: options.port });
    report.origin = server.origin;
    try {
      const { chromium, expect } = await loadLockedPlaywright();
      report.playwrightVersion = reviewedVersion;
      await withBrowserContext(() => chromium.launch({ headless: true, timeout: 30_000 }),
        (context, browser) => checkShell(context, browser, server.origin, output, report, expect));
    } finally { await server.close(); }
    report.status = 'passed';
  } catch (error) { failure = error; report.status = 'failed'; report.error = error instanceof Error ? error.message : String(error); }
  report.finishedAt = new Date().toISOString();
  await save();
  if (failure) {
    const error = new Error(`Desktop shell browser preview failed. Report: ${reportPath}`, { cause: failure });
    error.reportPath = reportPath;
    throw error;
  }
  return { report, reportPath };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const { reportPath } = await runShellBrowserSmoke(parseArgs(process.argv.slice(2)));
    console.log(`Desktop shell browser preview passed (not native WebView2; not a packaged application). Report: ${reportPath}`);
  } catch (error) {
    console.error(JSON.stringify({ scope: 'browser-shell-preview', status: 'failed', error: error.message, cause: error.cause?.message }));
    process.exitCode = 1;
  }
}
