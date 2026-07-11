import { test, expect } from '@playwright/test';

test.describe('Frontend Smoke Tests', () => {
  test('homepage loads and has a title', async ({ page }) => {
    const response = await page.goto('/');
    // Accept any 2xx/3xx (SSR pages may return 200 or 304)
    expect(response?.status()).toBeLessThan(400);
    // Page should have a non-empty title
    await expect(page).toHaveTitle(/.+/);
  });

  test('layout renders visible content', async ({ page }) => {
    await page.goto('/');
    const body = page.locator('body');
    await expect(body).toBeVisible();
    // There should be at least one visible element — not a blank white page
    const visibleElements = page.locator('body *:visible');
    await expect(visibleElements.first()).toBeAttached();
  });

  test('no uncaught JavaScript errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    await page.goto('/');
    // Allow 404s from API calls (backend not running in this test)
    // but fail on genuine JS errors
    const realErrors = errors.filter(
      (e) => !e.includes('404') && !e.includes('Failed to fetch'),
    );
    expect(realErrors).toEqual([]);
  });
});
