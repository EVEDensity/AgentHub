import type { ChildProcess } from 'node:child_process';
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { ownedPythonFixtureLogs, startOwnedPythonFixture, stopOwnedPythonFixture } from './helpers/ownedPythonFixture';

const ORIGIN = 'http://127.0.0.1:38100';
const WORKSPACE = 'workspace-chat-browser';
const WORKSPACE_NAME = 'Header fixture workspace';
const USER_NAME = 'Chat fixture human';
const CONFIGURED_AGENT = 'ConfiguredAgentDoesNotProveExecution';
const NOTICE = '运行指标尚未接入：实际执行模型、Token 用量和上下文占用';
const HEADERS = { Authorization: 'Bearer chat-browser-human' };
const admitted = new Set<string>();

async function openAdmin(page: Page, configurationFails = false) {
  const requests: string[] = [];
  const writes: string[] = [];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    requests.push(path);
    if (!['GET', 'HEAD'].includes(request.method()) && path.startsWith('/api/')) {
      writes.push(`${request.method()} ${path}`);
    }
  });
  await page.addInitScript(() => {
    localStorage.setItem('agenthub_workspace_id', 'workspace-chat-browser');
    localStorage.setItem('agenthub_token', 'chat-browser-human');
    localStorage.setItem('agenthub_user', JSON.stringify({ id: 'workspace-chat-browser', name: 'Chat fixture human', role: 'developer' }));
    localStorage.setItem('agenthub_chat_closed_at', String(Date.now()));
  });
  // Only unrelated identity presentation, legacy configuration and platform
  // catalog reads are fixtures. Every v1 Mission/chat/session read or command
  // continues through Next to the owned real SQLite HTTP application.
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path.startsWith('/api/v1/')) return route.fallback();
    if (path === '/api/auth/me') return reply({ id: WORKSPACE, name: USER_NAME, role: 'developer' });
    if (path === '/api/agent/registry') return reply([]);
    if (path === '/api/adapters') return reply({ adapters: [] });
    if (path === '/api/admin/chat-defaults') {
      return configurationFails ? reply({ detail: 'Explicit unrelated configuration fixture failure' }, 503)
        : reply({ agentId: CONFIGURED_AGENT });
    }
    if (path === '/api/admin/models') {
      return configurationFails ? reply({ detail: 'Explicit unrelated model configuration fixture failure' }, 503)
        : reply([{ id: 1, provider: 'configuration-only', modelName: 'ConfiguredModelDoesNotProveExecution', isActive: true }]);
    }
    return route.fallback();
  });
  await page.route('**/platform/workspaces**', route => route.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify({ workspaces: [{ id: WORKSPACE, name: WORKSPACE_NAME, member_count: 1 }] }) }));
  const configuration = page.waitForResponse(response => new URL(response.url()).pathname === '/api/admin/chat-defaults');
  await page.goto('/admin');
  expect((await configuration).status()).toBe(configurationFails ? 503 : 200);
  await expect(page.getByRole('dialog', { name: 'Mission 助手' })).toHaveCount(0);
  await expect(page.getByRole('banner', { name: '管理页头部' }).getByText(USER_NAME, { exact: true })).toBeVisible();
  return { requests, writes, errors };
}

async function assertHonestHeader(page: Page) {
  await expect(page.getByRole('region', { name: '执行指标状态' })).toHaveText(NOTICE);
  const header = page.getByRole('banner', { name: '管理页头部' });
  await expect(header.getByText(USER_NAME, { exact: true })).toBeVisible();
  await expect(header.getByRole('button', { name: new RegExp(WORKSPACE_NAME) })).toBeVisible();
  await expect(header.getByRole('link', { name: '返回', exact: true })).toHaveAttribute('href', '/');
  for (const title of ['当前模型', 'Token 用量 (今日)', '上下文窗口填充率']) {
    await expect(header.getByTitle(title, { exact: true })).toHaveCount(0);
  }
  await expect(header.getByText(/12\.4K|200K|18%|Claude Opus 4\.8|Orchestrator|ConfiguredAgentDoesNotProveExecution|ConfiguredModelDoesNotProveExecution/)).toHaveCount(0);
  await expect(header.getByRole('progressbar')).toHaveCount(0);
}

async function openMissionPanel(page: Page) {
  await page.getByRole('button', { name: /^.*Mission Control/ }).click();
  const panel = page.getByRole('region', { name: 'Mission Control' });
  await expect(panel.getByRole('heading', { name: 'Mission Control', exact: true })).toBeVisible();
  return panel;
}

async function exerciseHeaderNavigation(page: Page) {
  const header = page.getByRole('banner', { name: '管理页头部' });
  await header.getByRole('button', { name: new RegExp(WORKSPACE_NAME) }).click();
  await header.getByRole('button', { name: new RegExp(`${WORKSPACE_NAME}.*1 成员`) }).click();
  await header.getByRole('button', { name: 'Collapse sidebar' }).click();
  await expect(header.getByRole('button', { name: 'Expand sidebar' })).toBeVisible();
  await header.getByRole('button', { name: 'Expand sidebar' }).click();
}

function assertNoInventedTelemetry(calls: { requests: string[]; writes: string[]; errors: string[] }) {
  expect(calls.writes).toEqual([]);
  expect(calls.requests.filter(path => /^\/api\//.test(path)
    && /(?:^|\/)(?:metrics|telemetry|checkpoints|budget|budgets|token-usage|usage|context|context-manifest)(?:\/|$)/.test(path))).toEqual([]);
  expect(calls.errors).toEqual([]);
}

async function snapshot(request: APIRequestContext, missionId: string) {
  const response = await request.get(`${ORIGIN}/fixture/missions/${missionId}`, { headers: HEADERS });
  expect(response.status()).toBe(200);
  return response.json();
}

async function admit(request: APIRequestContext, message: string) {
  const response = await request.post(`${ORIGIN}/api/v1/chat/mission`, { headers: HEADERS, data: { workspaceId: WORKSPACE, message } });
  expect(response.status()).toBe(202);
  const body = await response.json() as { missionId: string };
  admitted.add(body.missionId);
  return body.missionId;
}

async function mutationCounters(request: APIRequestContext) {
  const response = await request.get(`${ORIGIN}/fixture/health`);
  expect(response.status()).toBe(200);
  const body = await response.json() as { commandCounters: Record<string, number> };
  return Object.fromEntries(Object.entries(body.commandCounters).filter(([key]) => /^(POST|PUT|PATCH|DELETE) /.test(key)));
}

test.describe('Admin header uses explicit unavailable execution metrics', () => {
  test.describe.configure({ mode: 'serial', retries: 0, timeout: 60000 });
  test.use({ viewport: { width: 1800, height: 1000 } });
  let fixture: ChildProcess | undefined;
  test.beforeAll(async ({ request }) => {
    fixture = await startOwnedPythonFixture(request, 'tests.integration.mission_chat_http_fixture', WORKSPACE);
  });
  test.afterEach(async ({ request }, testInfo) => {
    if (testInfo.status !== testInfo.expectedStatus) {
      const logs = ownedPythonFixtureLogs(fixture);
      if (logs) await testInfo.attach('owned-admin-header-api-log', { body: JSON.stringify(logs), contentType: 'application/json' });
    }
    for (const missionId of admitted) {
      const current = await snapshot(request, missionId);
      if (!['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(current.mission.status)) {
        expect((await request.post(`${ORIGIN}/api/v1/missions/${missionId}/cancel`, { headers: HEADERS, data: {} })).ok()).toBe(true);
      }
    }
    admitted.clear();
  });
  test.afterAll(async () => { await stopOwnedPythonFixture(fixture); });

  test('empty real workspace shows unavailable metrics and retains identity, workspace and navigation', async ({ page, request }, testInfo) => {
    const before = await mutationCounters(request);
    const calls = await openAdmin(page);
    const panel = await openMissionPanel(page);
    await expect(panel.getByText('当前工作空间暂无 Mission', { exact: true })).toBeVisible();
    await assertHonestHeader(page);
    await exerciseHeaderNavigation(page);
    await assertHonestHeader(page);
    expect(await mutationCounters(request)).toEqual(before);
    assertNoInventedTelemetry(calls);
    await page.screenshot({ path: testInfo.outputPath('admin-header-without-mission-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    const notice = page.getByRole('region', { name: '执行指标状态' });
    await expect(notice).toBeVisible();
    await expect(notice).toHaveText(NOTICE);
    const geometry = await notice.evaluate(element => {
      const rect = element.getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(element);
      return {
        clientWidth: element.clientWidth, scrollWidth: element.scrollWidth,
        clientHeight: element.clientHeight, scrollHeight: element.scrollHeight,
        viewportWidth: window.innerWidth,
        bounds: { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom },
        textRects: Array.from(range.getClientRects()).map(value => ({ left: value.left, right: value.right, top: value.top, bottom: value.bottom })),
      };
    });
    expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth + 1);
    expect(geometry.scrollHeight).toBeLessThanOrEqual(geometry.clientHeight + 1);
    expect(geometry.bounds.left).toBeGreaterThanOrEqual(-1);
    expect(geometry.bounds.right).toBeLessThanOrEqual(geometry.viewportWidth + 1);
    expect(geometry.textRects.length).toBeGreaterThan(0);
    for (const rect of geometry.textRects) {
      expect(rect.left).toBeGreaterThanOrEqual(geometry.bounds.left - 1);
      expect(rect.right).toBeLessThanOrEqual(geometry.bounds.right + 1);
      expect(rect.top).toBeGreaterThanOrEqual(geometry.bounds.top - 1);
      expect(rect.bottom).toBeLessThanOrEqual(geometry.bounds.bottom + 1);
    }
    await page.screenshot({ path: testInfo.outputPath('admin-header-without-mission-mobile.png'), fullPage: true });
    expect(await mutationCounters(request)).toEqual(before);
    assertNoInventedTelemetry(calls);
  });

  test('switching two actual Missions never turns configuration or task selection into execution metrics', async ({ page, request }, testInfo) => {
    const first = await admit(request, 'Header pending Mission 中文');
    const second = await admit(request, 'Header verifying Mission 中文');
    const completed = await request.post(`${ORIGIN}/fixture/missions/${second}/advance`, { headers: HEADERS, data: { action: 'complete' } });
    expect(completed.status()).toBe(200);
    const original = await Promise.all([snapshot(request, first), snapshot(request, second)]);
    expect(original.map(value => value.workUnits[0].status)).toEqual(['PENDING', 'VERIFYING']);
    const before = await mutationCounters(request);
    const calls = await openAdmin(page);
    const panel = await openMissionPanel(page);
    for (const actual of original) {
      await panel.getByRole('button', { name: new RegExp(actual.mission.id) }).click();
      await expect(panel.getByRole('heading', { name: actual.mission.title, exact: true })).toBeVisible();
      await expect(panel.getByText(new RegExp(`${actual.workUnits[0].id}.*${actual.workUnits[0].status}`))).toBeVisible();
      await assertHonestHeader(page);
    }
    expect(await Promise.all([snapshot(request, first), snapshot(request, second)])).toEqual(original);
    expect(await mutationCounters(request)).toEqual(before);
    assertNoInventedTelemetry(calls);
    await page.screenshot({ path: testInfo.outputPath('admin-header-real-mission-selection.png'), fullPage: true });
  });

  test('an unrelated configuration read failure cannot create a fallback executed-model claim', async ({ page, request }, testInfo) => {
    const before = await mutationCounters(request);
    const calls = await openAdmin(page, true);
    await assertHonestHeader(page);
    await exerciseHeaderNavigation(page);
    await openMissionPanel(page);
    await assertHonestHeader(page);
    expect(await mutationCounters(request)).toEqual(before);
    assertNoInventedTelemetry(calls);
    await page.screenshot({ path: testInfo.outputPath('admin-header-configuration-unavailable.png'), fullPage: true });
  });
});
