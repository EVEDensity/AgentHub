import type { ChildProcess } from 'node:child_process';
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { ownedPythonFixtureLogs, startOwnedPythonFixture, stopOwnedPythonFixture } from './helpers/ownedPythonFixture';

const ORIGIN = 'http://127.0.0.1:38100';
const WORKSPACE = 'workspace-chat-browser';
const HEADERS = { Authorization: 'Bearer chat-browser-human' };
const TEXT = '固定测试输出：产物 中文。仅验证聊天传输，不代表任务验收通过。';
const admitted = new Set<string>();

async function selectWorkspace(page: Page) {
  await expect(page.getByRole('combobox', { name: '工作空间' })).toBeEnabled();
  await page.getByRole('combobox', { name: '工作空间' }).selectOption(WORKSPACE);
  await expect(page.getByRole('textbox', { name: '任务要求' })).toBeVisible();
}

async function openMain(page: Page, select = true) {
  const posts: string[] = [];
  const requests: string[] = [];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { requests.push(request.url()); if (request.method() === 'POST') posts.push(new URL(request.url()).pathname); });
  await page.addInitScript(() => {
    localStorage.setItem('agenthub_token', 'chat-browser-human');
    localStorage.setItem('agenthub_user', JSON.stringify({ id: 'workspace-chat-browser', name: 'Chat fixture human', role: 'developer' }));
  });
  // The native profile must work with no Go platform catalog or legacy chat API.
  await page.route('**/platform/**', route => route.abort('connectionfailed'));
  await page.goto('/');
  if (select) await selectWorkspace(page);
  return { posts, requests, errors };
}

async function submit(page: Page, text = 'Native Mission request 中文') {
  await page.getByRole('textbox', { name: '任务要求' }).fill(text);
  const acknowledgement = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/chat/mission' && response.request().method() === 'POST');
  await page.getByRole('button', { name: '提交 Mission', exact: true }).click();
  const response = await acknowledgement;
  expect(response.status()).toBe(202);
  const body = await response.json();
  expect(body.missionId).toBeTruthy();
  admitted.add(body.missionId);
  return body as { missionId: string; sessionId: string };
}

async function snapshot(request: APIRequestContext, missionId: string) {
  const response = await request.get(`${ORIGIN}/fixture/missions/${missionId}`, { headers: HEADERS });
  expect(response.status()).toBe(200);
  return response.json();
}

async function advance(request: APIRequestContext, missionId: string, action: string) {
  const response = await request.post(`${ORIGIN}/fixture/missions/${missionId}/advance`, { headers: HEADERS, data: { action } });
  expect(response.status(), await response.text()).toBe(200);
  return response.json();
}

async function openHistory(page: Page, sessionId: string, missionId: string) {
  await page.getByRole('button', { name: new RegExp(`打开会话 .*\\(${sessionId}\\)`) }).click();
  await page.getByRole('button', { name: new RegExp(`观察 Mission .*\\(${missionId}\\)`) }).click();
  await expect(page.getByRole('heading', { name: '历史任务观察' })).toBeVisible();
}

test.describe('Native Mission sessions real API', () => {
  test.describe.configure({ mode: 'serial', retries: 0, timeout: 90000 });
  let fixture: ChildProcess | undefined;
  test.beforeAll(async ({ request }) => { fixture = await startOwnedPythonFixture(request, 'tests.integration.mission_chat_http_fixture', WORKSPACE); });
  test.afterEach(async ({ request }, testInfo) => {
    if (testInfo.status !== testInfo.expectedStatus) {
      const logs = ownedPythonFixtureLogs(fixture);
      if (logs) await testInfo.attach('owned-native-session-api-log', { body: JSON.stringify(logs), contentType: 'application/json' });
    }
    for (const missionId of admitted) {
      const current = await snapshot(request, missionId);
      if (!['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(current.mission.status)) {
        const response = await request.post(`${ORIGIN}/api/v1/missions/${missionId}/cancel`, { headers: HEADERS, data: {} });
        expect(response.ok()).toBe(true);
      }
    }
    admitted.clear();
  });
  test.afterAll(async () => { await stopOwnedPythonFixture(fixture); });

  test('lists the actually admitted session and restores its Mission after reload with GET only', async ({ page, request }, testInfo) => {
    const calls = await openMain(page);
    const body = await submit(page, '刷新后恢复的真实任务');
    await expect(page.getByRole('region', { name: 'Mission 执行状态' })).toContainText('等待 Runner 连接，任务尚未执行');
    await expect(page.getByRole('button', { name: new RegExp(`打开会话 .*${body.sessionId}`) })).toBeVisible();
    const actual = await snapshot(request, body.missionId);
    expect(actual.mission.status).toBe('RUNNING');
    expect(actual.workUnits[0].status).toBe('PENDING');
    expect(actual.evidence).toEqual([]);
    await page.reload();
    await selectWorkspace(page);
    await openHistory(page, body.sessionId, body.missionId);
    await expect(page.getByRole('region', { name: 'Mission 执行状态' })).toContainText('等待 Runner 连接');
    await expect(page.getByText('任务目标（服务端记录）')).toBeVisible();
    await expect(page.getByRole('button', { name: '提交 Mission', exact: true })).toBeDisabled();
    expect(calls.posts).toEqual(['/api/v1/chat/mission']);
    expect(calls.requests.filter(url => /\/api\/(chat\/sessions|tasks|chat\/send)|\/platform\//.test(url))).toEqual([]);
    expect(calls.errors).toEqual([]);
    const input = page.getByRole('textbox', { name: '任务要求' });
    await input.focus();
    await page.keyboard.press('Tab');
    await page.keyboard.press('Shift+Tab');
    await expect(input).toBeFocused();
    const inputStyles = await input.evaluate(element => {
      const style = getComputedStyle(element);
      return { border: parseFloat(style.borderTopWidth), outline: parseFloat(style.outlineWidth), outlineStyle: style.outlineStyle };
    });
    expect(inputStyles.border).toBeGreaterThan(0);
    expect(inputStyles.outline).toBeGreaterThan(0);
    expect(inputStyles.outlineStyle).toBe('solid');
    await page.screenshot({ path: testInfo.outputPath('native-mission-restored-waiting.png'), fullPage: true });
  });

  test('restores real Harness observations while VERIFYING and never executes source text', async ({ page, request }, testInfo) => {
    const calls = await openMain(page);
    const body = await submit(page, '<img src="/fixture/should-never-load.png" onerror="window.NATIVE_EXECUTED=true"> 中文');
    await advance(request, body.missionId, 'start');
    await advance(request, body.missionId, 'text');
    await expect(page.getByText(TEXT, { exact: true })).toBeVisible();
    const actual = await advance(request, body.missionId, 'complete');
    expect(actual.workUnits[0].status).toBe('VERIFYING');
    expect(actual.evidence).toEqual([]);
    await page.reload(); await selectWorkspace(page);
    await openHistory(page, body.sessionId, body.missionId);
    await expect(page.getByText(TEXT, { exact: true })).toHaveCount(1);
    await expect(page.getByRole('region', { name: 'Mission 执行状态' })).toContainText('等待独立验收');
    await expect(page.getByText('观察 · harness.assistant.completed', { exact: true })).toHaveCount(1);
    expect(await page.evaluate(() => (window as unknown as { NATIVE_EXECUTED?: unknown }).NATIVE_EXECUTED)).toBeUndefined();
    expect(calls.requests.filter(url => url.includes('should-never-load.png'))).toEqual([]);
    expect(calls.requests.filter(url => !['http://localhost:3000', 'http://127.0.0.1:3000'].includes(new URL(url).origin))).toEqual([]);
    expect(calls.posts).toEqual(['/api/v1/chat/mission']);
    expect(calls.errors).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath('native-mission-restored-verifying.png'), fullPage: true });
  });

  test('switches between two durable sessions without extra work or cancellation', async ({ page, request }) => {
    const calls = await openMain(page);
    const first = await submit(page, 'first session secret');
    await advance(request, first.missionId, 'start');
    await advance(request, first.missionId, 'text');
    await expect(page.getByText(TEXT, { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '新会话', exact: true }).click();
    const second = await submit(page, 'second session request');
    expect(second.sessionId).not.toBe(first.sessionId);
    await page.getByRole('textbox', { name: '任务要求' }).fill('private draft in second session');
    await openHistory(page, first.sessionId, first.missionId);
    await expect(page.getByRole('textbox', { name: '任务要求' })).toHaveValue('');
    await expect(page.getByText(TEXT, { exact: true })).toHaveCount(1);
    await expect(page.getByRole('region', { name: '原生 Mission 对话' })).not.toContainText('second session request');
    await openHistory(page, second.sessionId, second.missionId);
    await expect(page.getByRole('region', { name: 'Mission 对话观察' })).not.toContainText(TEXT);
    expect((await snapshot(request, first.missionId)).mission.status).toBe('RUNNING');
    expect((await snapshot(request, second.missionId)).mission.status).toBe('RUNNING');
    expect(calls.posts).toEqual(['/api/v1/chat/mission', '/api/v1/chat/mission']);
  });

  test('real directory 403 stays visible and read retry never resubmits a task', async ({ page }) => {
    const calls = await openMain(page, false);
    const pattern = '**/api/v1/sessions?**';
    await page.route(pattern, route => route.continue({ headers: { ...route.request().headers(), authorization: 'Bearer chat-browser-foreign' } }));
    await selectWorkspace(page);
    await expect(page.getByRole('complementary', { name: '原生会话目录' }).getByRole('alert')).toContainText('403');
    await page.unroute(pattern);
    await page.getByRole('button', { name: '重试读取会话' }).click();
    await expect(page.getByRole('complementary', { name: '原生会话目录' }).getByRole('alert')).toHaveCount(0);
    expect(calls.posts).toEqual([]);
  });

  test('loads another real Mission history page without duplicate ids or new browser commands', async ({ page, request }) => {
    const calls = await openMain(page);
    const body = await submit(page, 'pagination objective 00');
    for (let index = 1; index <= 20; index++) {
      const response = await request.post(`${ORIGIN}/api/v1/chat/mission`, { headers: HEADERS, data: { message: `pagination objective ${String(index).padStart(2, '0')}`, workspaceId: WORKSPACE, sessionId: body.sessionId, stream: true } });
      expect(response.status()).toBe(202);
      admitted.add((await response.json()).missionId);
    }
    await page.getByRole('button', { name: '刷新 Mission', exact: true }).click();
    const list = page.getByRole('region', { name: '会话 Mission 目录' });
    await expect(list.getByRole('button', { name: /^观察 Mission/ })).toHaveCount(20);
    await list.getByRole('button', { name: '加载更多 Mission' }).click();
    await expect(list.getByRole('button', { name: /^观察 Mission/ })).toHaveCount(21);
    const ids = await list.getByRole('button', { name: /^观察 Mission/ }).allTextContents();
    expect(new Set(ids).size).toBe(21);
    await expect(list.getByRole('button', { name: '加载更多 Mission' })).toHaveCount(0);
    expect(calls.posts).toEqual(['/api/v1/chat/mission']);
  });

  test('cancellation of a restored Mission reports a real rejection before server acknowledgement', async ({ page, request }) => {
    const calls = await openMain(page);
    const body = await submit(page);
    await page.reload(); await selectWorkspace(page); await openHistory(page, body.sessionId, body.missionId);
    const pattern = `**/api/v1/missions/${body.missionId}/cancel`;
    await page.route(pattern, route => route.continue({ headers: { ...route.request().headers(), authorization: 'Bearer chat-browser-foreign' } }));
    const rejected = page.waitForResponse(response => response.url().endsWith(`/${body.missionId}/cancel`) && response.status() === 403);
    await page.getByRole('button', { name: '取消任务', exact: true }).click();
    await rejected;
    await expect(page.getByRole('region', { name: '原生 Mission 对话' }).getByRole('alert')).toContainText('Workspace access denied');
    expect((await snapshot(request, body.missionId)).mission.status).toBe('RUNNING');
    await page.unroute(pattern);
    await page.getByRole('button', { name: '取消任务', exact: true }).click();
    await expect(page.getByRole('region', { name: 'Mission 执行状态' })).toContainText('任务已取消');
    expect((await snapshot(request, body.missionId)).mission.status).toBe('CANCELLED');
    expect(calls.posts).toEqual(['/api/v1/chat/mission', `/api/v1/missions/${body.missionId}/cancel`, `/api/v1/missions/${body.missionId}/cancel`]);
  });

  test('a committed admission with a lost acknowledgement is recovered through reads and cannot resend the draft', async ({ page, request }) => {
    const calls = await openMain(page);
    const before = await (await request.get(`${ORIGIN}/fixture/health`)).json();
    let committed: { missionId: string; sessionId: string } | undefined;
    await page.route('**/api/v1/chat/mission', async route => {
      const actual = await route.fetch();
      expect(actual.status()).toBe(202);
      committed = await actual.json();
      admitted.add(committed!.missionId);
      await route.abort('connectionfailed');
    }, { times: 1 });
    await page.getByRole('textbox', { name: '任务要求' }).fill('commit before acknowledgement loss');
    await page.getByRole('button', { name: '提交 Mission', exact: true }).click();
    await expect(page.getByText('提交结果未确认，请刷新会话目录核对；当前草稿不会重发。如需另一任务请新建会话。')).toBeVisible();
    await expect(page.getByRole('textbox', { name: '任务要求' })).toHaveValue('commit before acknowledgement loss');
    await expect(page.getByRole('button', { name: '提交 Mission', exact: true })).toBeDisabled();
    await page.getByRole('textbox', { name: '任务要求' }).press('Control+Enter');
    await page.waitForTimeout(1000); // An uncertain request must not trigger a delayed automatic POST retry.
    const health = await (await request.get(`${ORIGIN}/fixture/health`)).json();
    expect(health.commandCounters['POST /api/v1/chat/mission']).toBe((before.commandCounters['POST /api/v1/chat/mission'] ?? 0) + 1);
    expect(calls.posts).toEqual(['/api/v1/chat/mission']);
    await page.getByRole('button', { name: '刷新会话', exact: true }).click();
    await openHistory(page, committed!.sessionId, committed!.missionId);
    await expect(page.getByRole('region', { name: 'Mission 执行状态' })).toContainText('等待 Runner');
    expect((await snapshot(request, committed!.missionId)).mission.status).toBe('RUNNING');
    const after = await (await request.get(`${ORIGIN}/fixture/health`)).json();
    expect(after.commandCounters['POST /api/v1/chat/mission']).toBe(health.commandCounters['POST /api/v1/chat/mission']);
    expect(calls.posts).toEqual(['/api/v1/chat/mission']);
  });

  test('an explicit real admission 403 preserves the draft and permits a deliberate corrected retry', async ({ page }) => {
    const calls = await openMain(page);
    const pattern = '**/api/v1/chat/mission';
    await page.route(pattern, route => route.continue({ headers: { ...route.request().headers(), authorization: 'Bearer chat-browser-foreign' } }));
    await page.getByRole('textbox', { name: '任务要求' }).fill('403 retry complete 中文');
    const rejected = page.waitForResponse(response => response.url().endsWith('/api/v1/chat/mission') && response.status() === 403);
    await page.getByRole('button', { name: '提交 Mission', exact: true }).click();
    await rejected;
    await expect(page.getByRole('region', { name: '原生 Mission 对话' }).getByRole('alert')).toContainText('Workspace access denied');
    await expect(page.getByRole('textbox', { name: '任务要求' })).toHaveValue('403 retry complete 中文');
    await expect(page.getByRole('button', { name: '提交 Mission', exact: true })).toBeEnabled();
    await page.unroute(pattern);
    await submit(page, '403 retry complete 中文');
    expect(calls.posts).toEqual(['/api/v1/chat/mission', '/api/v1/chat/mission']);
  });

  test('a real different principal cannot supply the active personal workspace', async ({ page }) => {
    await page.route('**/api/v1/access/whoami', route => route.continue({ headers: { ...route.request().headers(), authorization: 'Bearer chat-browser-foreign' } }));
    const calls = await openMain(page, false);
    await expect(page.getByRole('alert').filter({ hasText: '服务端身份与当前登录用户不一致' })).toBeVisible();
    expect(calls.requests.filter(url => /\/api\/v1\/(sessions|missions)(\/|\?)/.test(url))).toEqual([]);
    expect(calls.posts).toEqual([]);
    await page.unroute('**/api/v1/access/whoami');
    await page.getByRole('button', { name: '刷新工作空间' }).click();
    await selectWorkspace(page);
  });

  test('changing actual credentials clears old bytes and draft without cancelling accepted work', async ({ page, request }) => {
    const calls = await openMain(page);
    const body = await submit(page, 'old principal private request');
    await page.getByRole('textbox', { name: '任务要求' }).fill('old principal private draft');
    // A storage event represents another tab signing in. The new scope still
    // has to pass the production whoami and directory authorization routes.
    await page.evaluate(() => {
      localStorage.setItem('agenthub_token', 'chat-browser-foreign');
      localStorage.setItem('agenthub_user', JSON.stringify({ id: 'workspace-chat-foreign', name: 'Other fixture principal', role: 'developer' }));
      window.dispatchEvent(new StorageEvent('storage', { key: 'agenthub_token' }));
    });
    await expect(page.getByText('Other fixture principal', { exact: true })).toBeVisible();
    await expect(page.getByRole('option', { name: '个人工作空间 (workspace-chat-foreign)' })).toBeAttached();
    await page.getByRole('combobox', { name: '工作空间' }).selectOption('workspace-chat-foreign');
    await expect(page.getByRole('textbox', { name: '任务要求' })).toHaveValue('');
    await expect(page.getByRole('region', { name: 'Mission 对话观察' })).not.toContainText('old principal private request');
    await expect(page.getByRole('complementary', { name: '原生会话目录' })).toContainText('workspace-chat-foreign');
    expect((await snapshot(request, body.missionId)).mission.status).toBe('RUNNING');
    expect(calls.posts).toEqual(['/api/v1/chat/mission']);
  });

  test('a real whoami 401 exposes an unavailable scope and never fabricates a default workspace', async ({ page }) => {
    await page.route('**/api/v1/access/whoami', route => route.continue({ headers: { ...route.request().headers(), authorization: 'Bearer invalid-fixture-token' } }));
    const calls = await openMain(page, false);
    await expect(page.getByRole('alert').filter({ hasText: '工作空间读取失败（HTTP 401）' })).toBeVisible();
    await expect(page.getByRole('combobox', { name: '工作空间' })).toBeDisabled();
    await expect(page.getByRole('option')).toHaveCount(1);
    expect(calls.requests.filter(url => /\/api\/v1\/(sessions|missions)(\/|\?)/.test(url))).toEqual([]);
    expect(calls.posts).toEqual([]);
    await page.unroute('**/api/v1/access/whoami');
    await page.getByRole('button', { name: '刷新工作空间' }).click();
    await selectWorkspace(page);
  });
});
