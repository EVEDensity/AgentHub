import { expect, test, type Page } from '@playwright/test';

async function prepareChat(page: Page, readFailure = false) {
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
    if (path === '/api/chat/sessions') return reply([{ id: 'legacy-view', name: 'Runner waiting test', ownerId: 'alice', myRole: 'owner' }]);
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
  const composer = page.locator('textarea.composer-textarea');
  await composer.fill('@executor inspect this workspace');
  await page.locator('button.composer-send-btn').click();
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
