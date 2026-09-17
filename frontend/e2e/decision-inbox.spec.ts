import { expect, test, type Page } from '@playwright/test';

const pendingDecision = {
  id: 'decision-e2e-1',
  missionId: 'mission-e2e-1',
  workUnitId: 'work-unit-e2e-1',
  attempt: 2,
  contextDigest: `sha256:${'a'.repeat(64)}`,
  reasonCode: 'artifact_requirements_not_met',
  criterionIds: ['criterion-e2e-1'],
  options: ['RETRY_WORK_UNIT', 'FAIL_MISSION'],
  recommendedOption: 'RETRY_WORK_UNIT',
  riskSummary: 'The required execution evidence has not been collected.',
  status: 'PENDING',
  version: 3,
  requestedBy: { type: 'verifier', id: 'verifier-e2e' },
  requestedAt: '2026-08-16T04:00:00Z',
  expiresAt: '2026-08-16T05:00:00Z',
};

async function prepareInbox(page: Page): Promise<void> {
  await page.addInitScript(() => {
    localStorage.setItem('agenthub_workspace_id', 'workspace-e2e');
    localStorage.setItem('agenthub_token', 'e2e-token');
    localStorage.setItem('agenthub_chat_closed_at', String(Date.now()));
  });
  await page.route('**/api/v1/missions/decisions?**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ decisions: [pendingDecision] }) });
  });
}

test.describe('Decision inbox', () => {
  test('loads and resolves a real contract-shaped pending Decision', async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 1000 });
    await prepareInbox(page);

    let resolutionBody: Record<string, unknown> | null = null;
    await page.route('**/api/v1/missions/mission-e2e-1/decisions/decision-e2e-1/resolve', async (route) => {
      resolutionBody = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          decision: { ...pendingDecision, status: 'RESOLVED', resolution: 'RETRY_WORK_UNIT' },
          workUnit: {},
          mission: {},
        }),
      });
    });

    await page.goto('/admin');
    await page.getByRole('button', { name: /决策收件箱/ }).click();

    await expect(page.getByRole('heading', { name: '决策收件箱' })).toBeVisible();
    await expect(page.getByText('The required execution evidence has not been collected.')).toBeVisible();
    await expect(page.getByText('workspace-e2e', { exact: false })).toBeVisible();

    await page.getByLabel('决策依据').fill('The WorkUnit can safely collect the missing evidence again.');
    await page.getByRole('button', { name: '重试 WorkUnit' }).click();

    await expect.poll(() => resolutionBody).toEqual({
      expectedVersion: 3,
      resolution: 'RETRY_WORK_UNIT',
      rationale: 'The WorkUnit can safely collect the missing evidence again.',
    });
    await expect(page.getByText('Decision decision-e2e-1 已处理：重试 WorkUnit')).toBeVisible();
  });

  test('keeps the inbox usable in the mobile drawer layout', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await prepareInbox(page);

    await page.goto('/admin');
    await page.getByRole('button', { name: 'Open menu' }).click();
    await page.getByRole('button', { name: /决策收件箱/ }).click();

    const inbox = page.getByRole('heading', { name: '决策收件箱' });
    await expect(inbox).toBeVisible();
    await expect(page.getByText('The required execution evidence has not been collected.')).toBeVisible();
    await expect(page.getByLabel('决策依据')).toBeVisible();

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  });
});
