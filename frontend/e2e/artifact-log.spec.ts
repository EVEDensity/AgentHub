import type { ChildProcess } from 'node:child_process';
import type { Terminal } from '@xterm/xterm';
import { expect, test, type APIRequestContext, type Frame, type Page } from '@playwright/test';
import { projectArtifactLog } from '../lib/artifacts/artifactLogProjection';
import { ownedPythonFixtureLogs, startOwnedPythonFixture, stopOwnedPythonFixture } from './helpers/ownedPythonFixture';

const ORIGIN = 'http://127.0.0.1:38100';
const MISSION = 'mis-log-browser';
const HEADERS = { Authorization: 'Bearer artifact-log-human' };
type Audit = { created: number; disposed: number; clipboard: number; navigation: number; transports: number };
type LogWindow = Window & { __logTerminal?: Terminal; __logAudit: Audit; LOG_PREVIEW_EXECUTED?: unknown };

async function openMission(page: Page) {
  const reads: string[] = [];
  const writes: string[] = [];
  const requests: string[] = [];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    requests.push(request.url()); const pathname = new URL(request.url()).pathname;
    if (pathname.startsWith('/api/v1/missions')) {
      if (pathname.endsWith('/preview')) reads.push(pathname);
      if (request.method() !== 'GET') writes.push(`${request.method()} ${pathname}`);
    }
  });
  await page.addInitScript(() => {
    if (window.top === window) {
      localStorage.setItem('agenthub_workspace_id', 'workspace-log');
      localStorage.setItem('agenthub_token', 'artifact-log-human');
      localStorage.setItem('agenthub_user', JSON.stringify({ id: 'workspace-log', name: 'Log fixture human', role: 'developer' }));
      localStorage.setItem('agenthub_chat_closed_at', String(Date.now()));
      (window as unknown as LogWindow).__logAudit = { created: 0, disposed: 0, clipboard: 0, navigation: 0, transports: 0 };
    }
    const audit = () => (window.parent as unknown as LogWindow).__logAudit;
    // Observe the public constructor only in the test; production has no
    // instance/debug global and no additional transport or command interface.
    let constructor: unknown;
    Object.defineProperty(window, 'Terminal', { configurable: true, get: () => constructor,
      set: (Base: typeof import('@xterm/xterm').Terminal) => {
        constructor = class extends Base {
          constructor(options?: import('@xterm/xterm').ITerminalOptions) {
            super(options); (window as unknown as LogWindow).__logTerminal = this; audit().created++;
          }
          dispose() { super.dispose(); audit().disposed++; delete (window as unknown as LogWindow).__logTerminal; }
        };
      } });
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
      writeText: async () => { audit().clipboard++; }, readText: async () => { audit().clipboard++; return 'test-only'; },
      write: async () => { audit().clipboard++; }, read: async () => { audit().clipboard++; return []; },
    } });
    window.open = () => { audit().navigation++; return null; };
    // The outer Mission panel already observes its ledger. Only the private
    // log renderer must have no socket/SSE transport of its own.
    if (window.parent !== window) for (const name of ['WebSocket', 'EventSource']) Object.defineProperty(window, name, { configurable: true,
      value: class { constructor() { audit().transports++; throw new Error('Unexpected log-view transport'); } } });
  });
  await page.route('**/api/**', route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.startsWith('/api/v1/missions')) return route.fallback();
    const body = pathname === '/api/auth/me' ? { id: 'workspace-log', name: 'Log fixture human', role: 'developer' } : [];
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.route('**/platform/**', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ workspaces: [{ id: 'workspace-log', name: 'Actual log fixture workspace' }] }) }));
  await page.goto('/admin');
  await page.getByRole('button', { name: /^.*Mission Control/ }).click();
  await expect(page.getByRole('heading', { name: 'Registered pytest log fixture' })).toBeVisible();
  await expect(page.getByRole('button', { name: '预览 artifact-log-real' })).toBeEnabled();
  expect(reads).toEqual([]);
  return { reads, writes, requests, errors };
}

async function openLog(page: Page, id = 'real') {
  const response = page.waitForResponse(value => new URL(value.url()).pathname === `/api/v1/missions/${MISSION}/artifacts/artifact-log-${id}/preview`);
  await page.getByRole('button', { name: `预览 artifact-log-${id}`, exact: true }).click();
  const actual = await response;
  expect(actual.status()).toBe(200);
  const preview = await actual.json();
  const element = page.getByTitle(`产物 artifact-log-${id} 的只读日志`);
  await expect(element).toBeVisible();
  const frame = await (await element.elementHandle())!.contentFrame();
  if (!frame) throw new Error('Owned registered-log frame did not load');
  await frame.waitForFunction(() => !!(window as unknown as LogWindow).__logTerminal);
  await expect(page.getByRole('dialog').getByText('正在加载本地只读日志视图', { exact: true })).toHaveCount(0);
  return { frame, preview };
}

async function terminalText(frame: Frame) {
  return frame.evaluate(() => {
    const buffer = (window as unknown as LogWindow).__logTerminal!.buffer.active;
    let text = '';
    for (let index = 0; index < buffer.length; index++) {
      const line = buffer.getLine(index)!;
      if (index && !line.isWrapped) text += '\n';
      text += line.translateToString(true);
    }
    return text.trimEnd();
  });
}

async function unchanged(request: APIRequestContext) {
  const mission = await request.get(`${ORIGIN}/api/v1/missions/${MISSION}`, { headers: HEADERS });
  expect(mission.status()).toBe(200); expect((await mission.json()).status).toBe('RUNNING');
  const units = await request.get(`${ORIGIN}/api/v1/missions/${MISSION}/work-units`, { headers: HEADERS });
  expect((await units.json()).workUnits[0].status).toBe('VERIFYING');
  const evidence = await request.get(`${ORIGIN}/api/v1/missions/${MISSION}/evidence`, { headers: HEADERS });
  expect(await evidence.json()).toEqual({ evidence: [] });
}

test.describe('Artifact log real API', () => {
  test.describe.configure({ mode: 'serial', retries: 0, timeout: 60000 });
  let child: ChildProcess | undefined;
  test.beforeAll(async ({ request }) => { child = await startOwnedPythonFixture(request, 'tests.integration.artifact_log_http_fixture', 'workspace-log'); });
  test.afterEach(async ({}, testInfo) => {
    if (testInfo.status !== testInfo.expectedStatus) {
      const logs = ownedPythonFixtureLogs(child);
      if (logs) await testInfo.attach('owned-log-fixture', { body: JSON.stringify(logs), contentType: 'application/json' });
    }
  });
  test.afterAll(async () => { await stopOwnedPythonFixture(child); });

  test('renders actual pytest stdout/stderr as escaped CJK text with no ANSI, links or executable content', async ({ page, request }, testInfo) => {
    await page.setViewportSize({ width: 1800, height: 1000 });
    const calls = await openMission(page);
    const activity = page.getByRole('complementary', { name: '前端活动' });
    await expect(activity).toContainText('不是执行日志、工具调用或服务端状态');
    await expect(activity.getByRole('textbox')).toHaveCount(0);
    await expect(activity.getByText('NAV', { exact: true })).toBeVisible();
    await expect(activity.getByText(/SYS|v5\.1\.0|控制台已就绪/)).toHaveCount(0);
    const { frame, preview } = await openLog(page);
    const expected = projectArtifactLog(preview.text);
    expect(preview.text).toContain('\x1b[32m');
    expect(preview.text).toContain('真实 stdout 中文'); expect(preview.text).toContain('真实 stderr 中文');
    expect(await terminalText(frame)).toBe(expected.text.trimEnd());
    expect(await terminalText(frame)).toContain('[U+001B]]52;');
    expect(await terminalText(frame)).toContain('progress 10%[U+000D]progress 100%');
    expect(await frame.evaluate(() => (window as unknown as LogWindow).__logTerminal!.options.disableStdin)).toBe(true);
    expect(await frame.title()).toBe('登记日志只读快照');
    expect(await page.evaluate(() => (window as unknown as LogWindow).LOG_PREVIEW_EXECUTED)).toBeUndefined();
    expect(await frame.evaluate(() => (window as unknown as LogWindow).LOG_PREVIEW_EXECUTED)).toBeUndefined();
    await expect(frame.locator('a')).toHaveCount(0);
    expect(await page.evaluate(() => (window as unknown as LogWindow).__logAudit)).toMatchObject({ clipboard: 0, navigation: 0, transports: 0 });
    expect(calls.requests.filter(url => !['http://localhost:3000', 'http://127.0.0.1:3000'].includes(new URL(url).origin) || url.includes('should-never-load'))).toEqual([]);
    expect(calls.requests.some(url => url.endsWith('/xterm/6.0.0/xterm.js'))).toBe(true);
    expect(calls.requests.some(url => url.includes('/monaco/'))).toBe(false);
    expect(calls.writes).toEqual([]); expect(calls.errors).toEqual([]);
    await unchanged(request);
    await page.screenshot({ path: testInfo.outputPath('registered-log-xterm.png'), fullPage: true });
  });

  test('rejects input, permits Tab and Shift+Tab, closes on Escape and disposes before reopening', async ({ page, request }) => {
    const calls = await openMission(page);
    const { frame } = await openLog(page);
    const before = await terminalText(frame);
    const input = frame.getByRole('textbox', { name: '登记日志只读浏览，不能输入命令' });
    await input.focus(); await page.keyboard.type('echo must never execute');
    await frame.evaluate(() => {
      const data = new DataTransfer(); data.setData('text/plain', 'echo paste must never execute');
      (window as unknown as LogWindow).__logTerminal!.textarea!.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
    });
    expect(await terminalText(frame)).toBe(before);
    await input.focus(); await page.keyboard.press('Tab');
    expect(await frame.evaluate(() => document.activeElement === (window as unknown as LogWindow).__logTerminal!.textarea)).toBe(false);
    await page.keyboard.press('Shift+Tab');
    await expect(input).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(page.getByRole('button', { name: '预览 artifact-log-real', exact: true })).toBeFocused();
    await expect.poll(() => page.evaluate(() => (window as unknown as LogWindow).__logAudit.disposed)).toBe(1);
    const reopened = await openLog(page);
    expect(await terminalText(reopened.frame)).toBe(before);
    expect(await page.evaluate(() => (window as unknown as LogWindow).__logAudit)).toMatchObject({ created: 2, disposed: 1, transports: 0, clipboard: 0 });
    await page.getByRole('button', { name: '关闭产物预览' }).click();
    await expect.poll(() => page.evaluate(() => (window as unknown as LogWindow).__logAudit.disposed)).toBe(2);
    expect(calls.writes).toEqual([]); await unchanged(request);
  });

  test('shows genuine empty output and refuses excessive and confidential metadata before byte reads', async ({ page, request }) => {
    const calls = await openMission(page);
    await expect(page.getByRole('button', { name: '预览 artifact-log-large', exact: true })).toBeDisabled();
    await expect(page.getByRole('button', { name: '预览 artifact-log-denied', exact: true })).toBeDisabled();
    await page.getByRole('button', { name: '预览 artifact-log-empty', exact: true }).click();
    await expect(page.getByRole('dialog')).toContainText('空产物（0 字节）');
    await expect(page.getByRole('dialog').locator('iframe')).toHaveCount(0);
    const health = await (await request.get(`${ORIGIN}/fixture/health`)).json();
    expect(health.reads['artifact-log-large'] ?? 0).toBe(0); expect(health.reads['artifact-log-denied'] ?? 0).toBe(0);
    expect(calls.reads).toEqual([`/api/v1/missions/${MISSION}/artifacts/artifact-log-empty/preview`]);
    expect(calls.writes).toEqual([]); await unchanged(request);
  });

  test('keeps real tampered, missing and invalid UTF-8 errors visible without constructing a terminal', async ({ page, request }) => {
    const calls = await openMission(page);
    for (const [id, status] of [['tampered', 409], ['missing', 503], ['invalid', 415]] as const) {
      const rejected = page.waitForResponse(response => response.url().endsWith(`/artifact-log-${id}/preview`) && response.status() === status);
      await page.getByRole('button', { name: `预览 artifact-log-${id}`, exact: true }).click(); await rejected;
      await expect(page.getByRole('dialog').getByRole('alert')).toBeVisible();
      await expect(page.getByRole('dialog').locator('iframe')).toHaveCount(0);
      await page.getByRole('button', { name: '关闭产物预览' }).click();
    }
    expect(await page.evaluate(() => (window as unknown as LogWindow).__logAudit.created)).toBe(0);
    expect(calls.writes).toEqual([]); await unchanged(request);
  });

  test('shows an actual workspace 403 and retries only the authorized GET after identity is restored', async ({ page, request }) => {
    const calls = await openMission(page);
    const path = `**/api/v1/missions/${MISSION}/artifacts/artifact-log-real/preview`;
    await page.route(path, route => route.continue({ headers: { ...route.request().headers(), authorization: 'Bearer artifact-log-foreign' } }));
    const denied = page.waitForResponse(response => response.url().endsWith('/artifact-log-real/preview') && response.status() === 403);
    await page.getByRole('button', { name: '预览 artifact-log-real', exact: true }).click(); await denied;
    await expect(page.getByRole('dialog').getByRole('alert')).toContainText('没有权限');
    await page.unroute(path);
    const accepted = page.waitForResponse(response => response.url().endsWith('/artifact-log-real/preview') && response.status() === 200);
    await page.getByRole('button', { name: '重试读取产物' }).click(); await accepted;
    await expect(page.getByTitle('产物 artifact-log-real 的只读日志')).toBeVisible();
    expect(calls.reads).toHaveLength(2); expect(calls.writes).toEqual([]); await unchanged(request);
  });

  test('keeps a network failure visible and read retry opens the same registered snapshot without creating work', async ({ page, request }) => {
    const calls = await openMission(page);
    await page.route(`**/api/v1/missions/${MISSION}/artifacts/artifact-log-real/preview`, route => route.abort('connectionfailed'), { times: 1 });
    await page.getByRole('button', { name: '预览 artifact-log-real', exact: true }).click();
    await expect(page.getByRole('dialog').getByRole('alert')).toBeVisible();
    await page.getByRole('button', { name: '重试读取产物' }).click();
    await expect(page.getByTitle('产物 artifact-log-real 的只读日志')).toBeVisible();
    expect(calls.reads).toHaveLength(2); expect(calls.writes).toEqual([]); await unchanged(request);
  });

  test('shows actual projection omissions and long-line truncation while retaining the entire bounded terminal window', async ({ page, request }) => {
    const calls = await openMission(page);
    const { frame, preview } = await openLog(page, 'bounded');
    const expected = projectArtifactLog(preview.text);
    expect(expected).toMatchObject({ sourceLines: 2002, omittedLines: 2, truncatedLines: 1 });
    await expect(page.getByRole('dialog')).toContainText('源文本 2002 行 · 省略 2 行 · 截短 1 行');
    expect(await terminalText(frame)).toBe(expected.text.trimEnd());
    expect(await terminalText(frame)).toContain('fixture-log-bounded-tail-2001');
    expect(await terminalText(frame)).not.toContain('fixture-line-0000');
    expect(await frame.evaluate(() => (window as unknown as LogWindow).__logTerminal!.buffer.active.length)).toBeLessThanOrEqual(6024);
    expect(calls.writes).toEqual([]); await unchanged(request);
  });
});
