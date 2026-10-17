import type { ChildProcess } from 'node:child_process';
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { ownedPythonFixtureLogs, startOwnedPythonFixture, stopOwnedPythonFixture } from './helpers/ownedPythonFixture';

const ORIGIN = 'http://127.0.0.1:38100';
const WORKSPACE = 'workspace-chat-browser';
const HEADERS = { Authorization: 'Bearer chat-browser-human' };
const TEXT = '固定测试输出：产物 中文。仅验证聊天传输，不代表任务验收通过。';
const admitted = new Set<string>();

async function openAssistant(page: Page) {
  const posts: string[] = [];
  const requests: string[] = [];
  const errors: string[] = [];
  const streams: Array<Record<string, string>> = [];
  const streamResponses: unknown[] = [];
  page.on('response', response => { if (new URL(response.url()).pathname.endsWith('/events/stream')) streamResponses.push({ status: response.status(), headers: response.headers() }); });
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    const url = new URL(request.url());
    requests.push(request.url());
    if (request.method() === 'POST') posts.push(url.pathname);
    if (url.pathname.endsWith('/events/stream')) streams.push(request.headers());
  });
  await page.addInitScript(() => {
    localStorage.setItem('agenthub_workspace_id', 'workspace-chat-browser');
    localStorage.setItem('agenthub_token', 'chat-browser-human');
    localStorage.setItem('agenthub_user', JSON.stringify({ id: 'workspace-chat-browser', name: 'Chat fixture human', role: 'developer' }));
    localStorage.setItem('agenthub_chat_closed_at', String(Date.now()));
  });
  await page.route('**/api/**', route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.startsWith('/api/v1/missions') || pathname.startsWith('/api/v1/chat/') || pathname.startsWith('/api/v1/sessions/')) return route.fallback();
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });
  await page.route('**/platform/**', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ workspaces: [
    { id: WORKSPACE, name: 'Chat fixture workspace', member_count: 1 },
    { id: 'workspace-other', name: 'Other fixture workspace', member_count: 1 },
  ] }) }));
  await page.goto('/admin');
  const trigger = page.getByTitle('AI 对话 (Ctrl+J)');
  await trigger.click();
  const dialog = page.getByRole('dialog', { name: 'Mission 助手' });
  await expect(dialog.getByRole('textbox', { name: '任务要求' })).toBeVisible();
  await expect(dialog).toContainText(WORKSPACE);
  expect(posts).toEqual([]);
  return { dialog, trigger, posts, requests, errors, streams, streamResponses };
}

async function submit(page: Page, text = 'Inspect this fixture workspace') {
  await page.getByRole('textbox', { name: '任务要求' }).fill(text);
  const admission = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/chat/mission' && response.request().method() === 'POST');
  await page.getByRole('button', { name: '提交 Mission' }).click();
  const response = await admission;
  expect(response.status()).toBe(202);
  const body = await response.json();
  if (body.missionId) admitted.add(body.missionId);
  return body as { missionId?: string; pendingId?: string; sessionId: string };
}

async function advance(request: APIRequestContext, missionId: string, action: string) {
  const response = await request.post(`${ORIGIN}/fixture/missions/${missionId}/advance`, { headers: HEADERS, data: { action } });
  expect(response.status(), await response.text()).toBe(200);
  return response.json();
}

async function snapshot(request: APIRequestContext, missionId: string) {
  const response = await request.get(`${ORIGIN}/fixture/missions/${missionId}`, { headers: HEADERS });
  expect(response.status()).toBe(200);
  return response.json();
}

test.describe('Mission assistant real API', () => {
  test.describe.configure({ mode: 'serial', retries: 0, timeout: 60000 });
  let fixture: ChildProcess | undefined;
  test.beforeAll(async ({ request }) => {
    test.setTimeout(60000);
    fixture = await startOwnedPythonFixture(request, 'tests.integration.mission_chat_http_fixture', WORKSPACE);
  });
  test.afterEach(async ({ request }) => {
    if (test.info().status !== test.info().expectedStatus) {
      const logs = ownedPythonFixtureLogs(fixture);
      if (logs) {
        await test.info().attach('owned-mission-api-log', { body: JSON.stringify(logs), contentType: 'application/json' });
        console.error(logs.stderr);
      }
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

  test('admission visibly waits for a real Runner and does not invent a model reply', async ({ page, request }, testInfo) => {
    const calls = await openAssistant(page);
    const body = await submit(page, '请分析 中文 fixture');
    const banner = calls.dialog.getByRole('region', { name: 'Mission 执行状态' });
    await expect(banner).toContainText('等待 Runner 连接，任务尚未执行');
    await expect(calls.dialog.getByText('请分析 中文 fixture', { exact: true })).toBeVisible();
    await expect(calls.dialog.getByRole('button', { name: '提交 Mission' })).toBeDisabled();
    const actual = await snapshot(request, body.missionId!);
    expect(actual.mission.status).toBe('RUNNING');
    expect(actual.workUnits[0].status).toBe('PENDING');
    expect(actual.evidence).toEqual([]);
    expect(calls.posts).toEqual(['/api/v1/chat/mission']);
    expect(calls.requests.filter(url => new URL(url).pathname === '/api/chat/send')).toEqual([]);
    expect(calls.requests.filter(url => !['http://localhost:3000', 'http://127.0.0.1:3000'].includes(new URL(url).origin))).toEqual([]);
    expect(calls.errors).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath('mission-chat-waiting.png'), fullPage: true });
  });

  test('real Harness text is an observation and completion remains VERIFYING without Evidence', async ({ page, request }, testInfo) => {
    const calls = await openAssistant(page);
    const prompt = '<img src="/fixture/should-never-load.png" onerror="window.CHAT_EXECUTED=true"> inspect';
    const body = await submit(page, prompt);
    await advance(request, body.missionId!, 'start');
    await expect(calls.dialog.getByRole('region', { name: 'Mission 执行状态' })).toContainText('正在执行');
    await advance(request, body.missionId!, 'text');
    const ledger = await request.get(`${ORIGIN}/api/v1/missions/${body.missionId}/events`, { headers: HEADERS });
    expect(ledger.status()).toBe(200);
    try { await expect(calls.dialog.getByText(TEXT, { exact: true })).toBeVisible(); }
    catch (error) {
      const boundedStream = await page.evaluate(async ({ missionId, token }) => {
        const response = await fetch(`/api/v1/missions/${missionId}/events/stream?maxSeconds=0.1`, { headers: { Authorization: `Bearer ${token}` } });
        return { status: response.status, headers: Array.from(response.headers.entries()), text: await response.text() };
      }, { missionId: body.missionId!, token: 'chat-browser-human' });
      const diagnostics = { streams: calls.streams, responses: calls.streamResponses, errors: calls.errors, ledger: await ledger.json(), boundedStream };
      await testInfo.attach('real-stream-diagnostics', { body: JSON.stringify(diagnostics), contentType: 'application/json' });
      console.error(JSON.stringify(diagnostics));
      throw error;
    }
    await expect(calls.dialog.getByText('观察 · harness.assistant.completed', { exact: true })).toHaveCount(1);
    const actual = await advance(request, body.missionId!, 'complete');
    expect(actual.workUnits[0].status).toBe('VERIFYING');
    expect(actual.evidence).toEqual([]);
    await expect(calls.dialog.getByRole('region', { name: 'Mission 执行状态' })).toContainText('等待独立验收');
    await expect(calls.dialog.getByRole('button', { name: '提交 Mission' })).toBeDisabled();
    expect(await page.evaluate(() => (window as unknown as { CHAT_EXECUTED?: unknown }).CHAT_EXECUTED)).toBeUndefined();
    expect(calls.requests.filter(url => url.includes('should-never-load.png'))).toEqual([]);
    expect(calls.errors).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath('mission-chat-verifying.png'), fullPage: true });
  });

  test('real lease expiry removes the executing label without claiming a terminal outcome', async ({ page, request }) => {
    const calls = await openAssistant(page);
    const body = await submit(page);
    const actual = await advance(request, body.missionId!, 'expire');
    expect(actual.mission.status).toBe('RUNNING');
    await expect(calls.dialog.getByRole('region', { name: 'Mission 执行状态' })).toContainText('租约已过期');
    await expect(calls.dialog.getByRole('button', { name: '提交 Mission' })).toBeDisabled();
    expect(actual.evidence).toEqual([]);
  });

  test('real policy governance opens the existing Decision inbox without resolving it', async ({ page, request }) => {
    const calls = await openAssistant(page);
    const body = await submit(page);
    const actual = await advance(request, body.missionId!, 'decision');
    expect(actual.mission.status).toBe('WAITING_DECISION');
    expect(actual.decisions).toHaveLength(1);
    expect(actual.evidence).toEqual([]);
    await expect(calls.dialog.getByRole('region', { name: 'Mission 执行状态' })).toContainText('等待人工决策');
    await calls.dialog.getByRole('button', { name: '打开决策收件箱' }).click();
    await expect(page.getByRole('heading', { name: '决策收件箱' })).toBeVisible();
    expect(calls.posts).toEqual(['/api/v1/chat/mission']);
    expect((await snapshot(request, body.missionId!)).decisions[0].status).toBe('PENDING');
  });

  test('failed real cancellation remains visible until a successful server acknowledgement', async ({ page, request }) => {
    const calls = await openAssistant(page);
    const body = await submit(page);
    const pattern = `**/api/v1/missions/${body.missionId}/cancel`;
    await page.route(pattern, route => route.continue({ headers: { ...route.request().headers(), authorization: 'Bearer chat-browser-foreign' } }));
    const rejected = page.waitForResponse(response => response.url().endsWith(`/${body.missionId}/cancel`) && response.status() === 403);
    await calls.dialog.getByRole('button', { name: '取消任务' }).click();
    await rejected;
    await expect(calls.dialog.getByRole('alert')).toBeVisible();
    expect((await snapshot(request, body.missionId!)).mission.status).toBe('RUNNING');
    await page.unroute(pattern);
    await calls.dialog.getByRole('button', { name: '取消任务' }).click();
    await expect(calls.dialog.getByRole('region', { name: 'Mission 执行状态' })).toContainText('任务已取消');
    expect((await snapshot(request, body.missionId!)).mission.status).toBe('CANCELLED');
    expect(calls.posts).toEqual(['/api/v1/chat/mission', `/api/v1/missions/${body.missionId}/cancel`, `/api/v1/missions/${body.missionId}/cancel`]);
  });

  test('close pauses GET observers and reopening resumes the same Mission without duplicate rows or a new POST', async ({ page, request }) => {
    const calls = await openAssistant(page);
    const body = await submit(page);
    await advance(request, body.missionId!, 'start');
    await advance(request, body.missionId!, 'text');
    await expect(calls.dialog.getByText(TEXT, { exact: true })).toBeVisible();
    await calls.dialog.getByRole('textbox', { name: '任务要求' }).fill('关闭后保留的草稿');
    await calls.dialog.getByRole('button', { name: '关闭 Mission 助手' }).click();
    await expect(calls.trigger).toBeFocused();
    await page.waitForTimeout(200);
    const before = await (await request.get(`${ORIGIN}/fixture/health`)).json();
    const statusKey = `GET /api/v1/missions/${body.missionId}/execution-status`;
    await page.waitForTimeout(2300);
    const after = await (await request.get(`${ORIGIN}/fixture/health`)).json();
    expect(after.commandCounters[statusKey]).toBe(before.commandCounters[statusKey]);
    expect(after.commandCounters[`POST /api/v1/missions/${body.missionId}/cancel`]).toBeUndefined();
    expect((await snapshot(request, body.missionId!)).mission.status).toBe('RUNNING');
    await calls.trigger.click();
    await expect(calls.dialog.getByText(TEXT, { exact: true })).toHaveCount(1);
    await expect(calls.dialog.getByRole('textbox', { name: '任务要求' })).toHaveValue('关闭后保留的草稿');
    await expect.poll(() => calls.streams.length).toBe(2);
    expect(calls.streams[1]['last-event-id']).toBeTruthy();
    expect(calls.posts).toEqual(['/api/v1/chat/mission']);
  });

  test('real pending rule confirmation creates work only after explicit confirmation', async ({ page, request }) => {
    const calls = await openAssistant(page);
    const body = await submit(page, 'fixture-confirm inspect');
    expect(body.missionId).toBeUndefined();
    expect(body.pendingId).toBeTruthy();
    await expect(calls.dialog).toContainText('等待确认');
    expect(calls.streams).toHaveLength(0);
    const acknowledgement = page.waitForResponse(response => response.url().endsWith('/api/v1/chat/confirm') && response.request().method() === 'POST');
    await calls.dialog.getByRole('button', { name: '确认执行' }).click();
    const confirmed = await (await acknowledgement).json();
    admitted.add(confirmed.missionId);
    await expect(calls.dialog.getByRole('region', { name: 'Mission 执行状态' })).toContainText('等待 Runner');
    expect((await snapshot(request, confirmed.missionId)).workUnits[0].status).toBe('PENDING');
    expect(calls.posts).toEqual(['/api/v1/chat/mission', '/api/v1/chat/confirm']);
  });

  test('real pending rule cancellation does not create a Mission or a stream', async ({ page }) => {
    const calls = await openAssistant(page);
    const body = await submit(page, 'fixture-confirm cancel this request');
    expect(body.missionId).toBeUndefined();
    await calls.dialog.getByRole('button', { name: '取消请求' }).click();
    await expect(calls.dialog.getByRole('button', { name: '取消请求' })).toHaveCount(0);
    expect(calls.streams).toHaveLength(0);
    expect(calls.posts).toEqual(['/api/v1/chat/mission', '/api/v1/chat/cancel']);
  });

  test('real admission 403 preserves the complete draft and retry uses only the Mission endpoint', async ({ page }) => {
    const calls = await openAssistant(page);
    const pattern = '**/api/v1/chat/mission';
    await page.route(pattern, route => route.continue({ headers: { ...route.request().headers(), authorization: 'Bearer chat-browser-foreign' } }));
    const input = calls.dialog.getByRole('textbox', { name: '任务要求' });
    await input.fill('失败后保留 中文');
    const rejected = page.waitForResponse(response => response.url().endsWith('/api/v1/chat/mission') && response.status() === 403);
    await calls.dialog.getByRole('button', { name: '提交 Mission' }).click();
    await rejected;
    await expect(calls.dialog.getByRole('alert')).toBeVisible();
    await expect(input).toHaveValue('失败后保留 中文');
    await expect(calls.dialog.getByLabel('Mission 对话观察')).toContainText('尚无提交或观察');
    await page.unroute(pattern);
    await submit(page, '失败后保留 中文');
    expect(calls.posts).toEqual(['/api/v1/chat/mission', '/api/v1/chat/mission']);
  });

  test('network admission failure preserves text without optimistic assistant output', async ({ page }) => {
    const calls = await openAssistant(page);
    await page.route('**/api/v1/chat/mission', route => route.abort('connectionfailed'), { times: 1 });
    const input = calls.dialog.getByRole('textbox', { name: '任务要求' });
    await input.fill('网络失败后保留');
    await calls.dialog.getByRole('button', { name: '提交 Mission' }).click();
    await expect(calls.dialog.getByRole('alert')).toBeVisible();
    await expect(input).toHaveValue('网络失败后保留');
    await expect(calls.dialog.getByLabel('Mission 对话观察')).toContainText('尚无提交或观察');
    await submit(page, '网络失败后保留');
    expect(calls.posts).toEqual(['/api/v1/chat/mission', '/api/v1/chat/mission']);
  });

  test('switching workspace clears old bytes and draft without cancelling the old Mission', async ({ page, request }) => {
    // The workspace selector is a desktop header control hidden below 1280px.
    await page.setViewportSize({ width: 1600, height: 1000 });
    const calls = await openAssistant(page);
    const body = await submit(page, 'old workspace private request');
    await calls.dialog.getByRole('textbox', { name: '任务要求' }).fill('old workspace private draft');
    await calls.dialog.getByRole('button', { name: '关闭 Mission 助手' }).click();
    await page.getByRole('button', { name: /Chat fixture workspace/ }).click();
    await page.getByRole('button', { name: /Other fixture workspace/ }).click();
    await calls.trigger.click();
    await expect(calls.dialog.getByRole('textbox', { name: '任务要求' })).toHaveValue('');
    await expect(calls.dialog).not.toContainText('old workspace private request');
    await expect(calls.dialog).toContainText('workspace-other');
    expect((await snapshot(request, body.missionId!)).mission.status).toBe('RUNNING');
    expect(calls.posts).toEqual(['/api/v1/chat/mission']);
  });
});
