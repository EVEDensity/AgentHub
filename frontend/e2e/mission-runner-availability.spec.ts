import { expect, test, type Page } from '@playwright/test';

async function prepareChat(page: Page, readFailure = false, submit = true) {
  let submissions = 0;
  let reads = 0;
  let cancelled = false;
  let submittedBody: Record<string, unknown> | undefined;
  await page.addInitScript(() => {
    localStorage.setItem('agenthub_token', 'browser-contract-token');
    localStorage.setItem('agenthub_user', JSON.stringify({ id: 'alice', name: 'Alice', role: 'user' }));
    localStorage.setItem('agenthub_chat_closed_at', String(Date.now()));
  });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/access/whoami') return reply({ userId: 'alice', name: 'Alice', role: 'user', workspaceId: 'alice', isAdmin: false, canVerify: false });
    if (path === '/api/v1/sessions') return reply({ schemaVersion: 1, workspaceId: 'alice', sessions: [], hasMore: false, nextOffset: null });
    if (path === '/api/v1/sessions/native-browser-session/missions') return reply({ schemaVersion: 1, workspaceId: 'alice', sessionId: 'native-browser-session', missions: [], hasMore: false, nextBeforeId: null });
    if (path === '/api/v1/workspaces/alice/members') return reply({ scopeId: 'alice', members: [{ memberId: 'executor', name: 'Executor', kind: 'agent', role: 'agent', enabled: true, adapterType: 'function-calling', capabilities: [] }] });
    if (path === '/api/v1/skills') return reply({ skills: [] });
    if (path === '/api/v1/chat/mission') {
      submissions += 1;
      submittedBody = route.request().postDataJSON();
      return reply({
        missionId: 'mission-browser-1', sessionId: 'native-browser-session', status: 'RUNNING',
        dispatch: { workUnitId: 'unit-browser-1', status: 'PENDING', assignedAgentId: 'executor', assignedAdapter: 'function-calling' },
        mentions: { resolved: [{ agentId: 'executor', adapterType: 'function-calling', capabilities: [] }], unresolved: [] },
        streamUrl: '/api/v1/missions/mission-browser-1/events/stream',
      }, 202);
    }
    if (path.endsWith('/events/stream')) return route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': heartbeat\n\n' });
    if (path === '/api/v1/missions/mission-browser-1/cancel') {
      cancelled = true;
      return reply({ status: 'CANCELLED' });
    }
    if (path === '/api/v1/missions/mission-browser-1/execution-status') {
      reads += 1;
      if (readFailure && reads === 1) return reply({ detail: 'database unavailable' }, 503);
      return reply({
        schemaVersion: 1, missionId: 'mission-browser-1', workspaceId: 'alice',
        missionStatus: cancelled ? 'CANCELLED' : 'RUNNING', observedAt: new Date().toISOString(),
        workUnits: [{
          workUnitId: 'unit-browser-1', status: cancelled ? 'CANCELLED' : 'PENDING', assignedAgentId: 'executor', assignedAdapter: 'function-calling',
          reason: cancelled ? 'cancelled' : 'waiting_runner', matchingRunnerAvailable: false, availabilitySource: 'none',
          lastSeenAt: null, availabilityExpiresAt: null,
        }],
      });
    }
    return reply([]);
  });
  await page.goto('/');
  await page.getByRole('combobox', { name: '工作空间' }).selectOption('alice');
  const composer = page.getByRole('textbox', { name: '任务要求' });
  await composer.fill('@executor inspect this workspace');
  if (submit) await page.getByRole('button', { name: '提交 Mission', exact: true }).click();
  return { submissions: () => submissions, body: () => submittedBody, cancelled: () => cancelled };
}

test('selected Agent without a Runner visibly waits and cancellation is a server command', async ({ page }) => {
  const calls = await prepareChat(page);
  const status = page.getByRole('region', { name: 'Mission 执行状态' });
  await expect(status.getByText('executor · 等待 Runner 连接，任务尚未执行')).toBeVisible();
  await expect(status.getByText('PENDING')).toBeVisible();
  await expect(page.getByText('正在理解你的需求...')).toHaveCount(0);
  await expect(status.getByText('正在执行', { exact: true })).toHaveCount(0);
  expect(calls.submissions()).toBe(1);
  expect(calls.body()).toMatchObject({ workspaceId: 'alice', sessionId: null });
  await status.getByRole('button', { name: '取消任务' }).click();
  await expect.poll(calls.cancelled).toBe(true);
  await expect(status.getByText('executor · 任务已取消')).toBeVisible();
});

test('status outage is visible and read retry does not submit a duplicate Mission', async ({ page }) => {
  const calls = await prepareChat(page, true);
  const status = page.getByRole('region', { name: 'Mission 执行状态' });
  await expect(status.getByRole('alert')).toContainText('HTTP 503');
  await status.getByRole('button', { name: '重试读取状态' }).click();
  await expect(status.getByText('executor · 等待 Runner 连接，任务尚未执行')).toBeVisible();
  expect(calls.submissions()).toBe(1);
});

test('unsupported attachments remain in the composer and never become a partial text Mission', async ({ page }) => {
  const calls = await prepareChat(page, false, false);
  const composer = page.getByRole('textbox', { name: '任务要求' });
  await page.locator('input[type=file]').setInputFiles({ name: 'context.txt', mimeType: 'text/plain', buffer: Buffer.from('required context') });
  const remove = page.getByRole('button', { name: '移除 context.txt' });
  await expect(remove).toBeVisible();
  await page.getByRole('button', { name: '提交 Mission', exact: true }).click();
  await expect(page.getByText('当前 Mission 聊天暂不支持附件，请移除附件后发送文本。', { exact: true })).toBeVisible();
  expect(calls.submissions()).toBe(0);
  await expect(composer).toHaveValue('@executor inspect this workspace');
  await expect(remove).toBeVisible();
  await remove.click();
  await page.getByRole('button', { name: '提交 Mission', exact: true }).click();
  await expect.poll(calls.submissions).toBe(1);
  expect(calls.body()).toEqual({ message: '@executor inspect this workspace', workspaceId: 'alice', sessionId: null, stream: true });
});

test('failed admission keeps the complete text draft for retry', async ({ page }) => {
  await prepareChat(page, false, false);
  await page.route('**/api/v1/chat/mission', route => route.fulfill({
    status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'admission unavailable' }),
  }));
  const composer = page.getByRole('textbox', { name: '任务要求' });
  await page.getByRole('button', { name: '提交 Mission', exact: true }).click();
  await expect(page.getByRole('region', { name: '原生 Mission 对话' }).getByRole('alert').filter({ hasText: 'admission unavailable' })).toBeVisible();
  await expect(composer).toHaveValue('@executor inspect this workspace');
});

test('late admission preserves new text and attachments entered while submitting', async ({ page }) => {
  await prepareChat(page, false, false);
  let releaseAdmission!: () => void;
  const held = new Promise<void>(resolve => { releaseAdmission = resolve; });
  let received = false;
  await page.route('**/api/v1/chat/mission', async route => {
    received = true;
    await held;
    await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({
      missionId: 'mission-browser-1', sessionId: 'native-browser-session',
      streamUrl: '/api/v1/missions/mission-browser-1/events/stream',
    }) });
  });
  const composer = page.getByRole('textbox', { name: '任务要求' });
  await page.getByRole('button', { name: '提交 Mission', exact: true }).click();
  await expect.poll(() => received).toBe(true);
  await composer.fill('next task draft');
  await page.locator('input[type=file]').setInputFiles({ name: 'next-task.txt', mimeType: 'text/plain', buffer: Buffer.from('new task context') });
  const remove = page.getByRole('button', { name: '移除 next-task.txt' });
  await expect(remove).toBeVisible();
  releaseAdmission();
  await expect(page.getByRole('region', { name: 'Mission 执行状态' })).toBeVisible();
  await expect(composer).toHaveValue('next task draft');
  await expect(remove).toBeVisible();
});

test('successful admission clears the unchanged submitted draft', async ({ page }) => {
  await prepareChat(page, false, false);
  await page.getByRole('textbox', { name: '任务要求' }).press('Control+Enter');
  await expect(page.getByRole('region', { name: 'Mission 执行状态' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '任务要求' })).toHaveValue('');
});
