import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { once } from 'node:events';
import net from 'node:net';
import path from 'node:path';
import { expect, test, type APIRequestContext, type Frame, type Page } from '@playwright/test';

const FIXTURE_ORIGIN = 'http://127.0.0.1:38100';
const MISSION_ID = 'mis-preview-browser';
const MISSION_PATH = `/api/v1/missions/${MISSION_ID}`;
const JSON_TEXT = '{"note":"产物 中文","html":"<img src=x onerror=window.PREVIEW_EXECUTED=true>"}';
const HUMAN_HEADERS = { Authorization: 'Bearer artifact-browser-human' };
type MonacoWindow = Window & { monaco: typeof import('monaco-editor'); PREVIEW_EXECUTED?: unknown };

async function fixturePortOccupied(): Promise<boolean> {
  return new Promise((resolve, reject) => {
    const socket = net.connect({ host: '127.0.0.1', port: 38100 });
    socket.setTimeout(2000);
    socket.once('connect', () => { socket.destroy(); resolve(true); });
    socket.once('error', (error: NodeJS.ErrnoException) => {
      socket.destroy();
      if (error.code === 'ECONNREFUSED') resolve(false); else reject(error);
    });
    socket.once('timeout', () => { socket.destroy(); reject(new Error('Fixture port ownership probe timed out')); });
  });
}

async function shutdownOwnedChild(child: ChildProcess | undefined): Promise<void> {
  if (!child?.pid || child.exitCode !== null || child.signalCode !== null) return;
  const closed = once(child, 'close').then(() => true);
  const waitClosed = async (milliseconds: number) => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      return await Promise.race([closed, new Promise<false>(resolve => { timer = setTimeout(() => resolve(false), milliseconds); })]);
    } finally { if (timer) clearTimeout(timer); }
  };
  child.kill('SIGTERM');
  if (await waitClosed(12000)) return;
  if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
  if (!await waitClosed(3000)) throw new Error('Owned API fixture did not exit after bounded termination');
}

async function openMission(page: Page) {
  const previewRequests: string[] = [];
  const missionWrites: string[] = [];
  const requests: string[] = [];
  const pageErrors: string[] = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('request', request => {
    requests.push(request.url());
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/v1/missions')) {
      if (url.pathname.endsWith('/preview')) previewRequests.push(url.pathname);
      if (request.method() !== 'GET') missionWrites.push(`${request.method()} ${url.pathname}`);
    }
  });
  await page.addInitScript(() => {
    localStorage.setItem('agenthub_workspace_id', 'workspace-preview');
    localStorage.setItem('agenthub_token', 'artifact-browser-human');
    localStorage.setItem('agenthub_user', JSON.stringify({ id: 'workspace-preview', name: 'Fixture human', role: 'developer' }));
    localStorage.setItem('agenthub_chat_closed_at', String(Date.now()));
  });
  // Only unrelated background catalog/platform requests are fixtures. Every
  // Mission route continues through Next rewrites to the real SQLite/CAS API.
  await page.route('**/api/**', route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.startsWith('/api/v1/missions')) return route.fallback();
    const body = pathname === '/api/auth/me'
      ? { id: 'workspace-preview', name: 'Fixture human', role: 'developer' } : [];
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.route('**/platform/**', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ workspaces: [{ id: 'workspace-preview', name: 'Preview fixture' }] }) }));
  await page.goto('/admin');
  await expect(page.getByText('运行指标尚未接入', { exact: true })).toBeVisible();
  await expect(page.getByText('系统正常', { exact: true })).toHaveCount(0);
  await expect(page.getByText('98.7%', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: /^.*Mission Control/ }).click();
  const panel = page.getByRole('region', { name: 'Mission Control' });
  await expect(panel.getByRole('heading', { name: 'Artifact preview browser fixture' })).toBeVisible();
  await expect(panel.getByRole('button', { name: '预览 artifact-browser-json' })).toBeEnabled();
  expect(previewRequests).toEqual([]);
  return { panel, previewRequests, missionWrites, requests, pageErrors };
}

async function openCode(page: Page, id: string): Promise<Frame> {
  await page.getByRole('button', { name: `预览 artifact-browser-${id}` }).click();
  const element = page.getByTitle(`产物 artifact-browser-${id} 的只读代码`);
  await expect(element).toBeVisible();
  const frame = await (await element.elementHandle())!.contentFrame();
  if (!frame) throw new Error('Owned Artifact code frame did not load');
  await frame.waitForFunction(() => {
    const api = (window as unknown as MonacoWindow).monaco;
    return api?.editor.getModels().length === 1 && api.editor.getEditors().length === 1;
  });
  return frame;
}

async function assertMissionUnchanged(request: APIRequestContext) {
  const mission = await request.get(`${FIXTURE_ORIGIN}${MISSION_PATH}`, { headers: HUMAN_HEADERS });
  expect(mission.status()).toBe(200);
  expect(await mission.json()).toMatchObject({ id: MISSION_ID, status: 'RUNNING' });
  const evidence = await request.get(`${FIXTURE_ORIGIN}${MISSION_PATH}/evidence`, { headers: HUMAN_HEADERS });
  expect(evidence.status()).toBe(200);
  expect(await evidence.json()).toEqual({ evidence: [] });
}

test.describe('Artifact preview real API', () => {
  test.describe.configure({ mode: 'serial', retries: 0, timeout: 60000 });
  let child: ChildProcess | undefined;
  let fixtureOutput = '';
  let fixtureError = '';

  test.beforeAll(async ({ request }) => {
    test.setTimeout(60000);
    if (await fixturePortOccupied()) throw new Error('Refusing to use or stop an existing service on fixture port 38100');
    let python = process.env.AGENTHUB_TEST_PYTHON || 'python';
    let args = ['-m', 'tests.integration.artifact_preview_http_fixture'];
    if (process.platform === 'win32') {
      // A Windows venv python.exe is a launcher which starts another process.
      // Launch its real interpreter directly so our owned handle/PID is the
      // server; retain the same reviewed venv dependency paths explicitly.
      const probe = spawnSync(python, ['-c', 'import json,site,sys; print(json.dumps({"executable":sys._base_executable,"sites":site.getsitepackages()}))'], { windowsHide: true, encoding: 'utf8', timeout: 10000 });
      if (probe.status !== 0 || probe.error) throw new Error('Could not identify the Windows Python interpreter');
      const runtime = JSON.parse(probe.stdout) as { executable: string; sites: string[] };
      if (!path.isAbsolute(runtime.executable) || !Array.isArray(runtime.sites) || runtime.sites.some(directory => !path.isAbsolute(directory))) throw new Error('Python interpreter metadata is invalid');
      python = runtime.executable;
      args = ['-c', 'import runpy,site,sys; [site.addsitedir(p) for p in sys.argv[1:]]; runpy.run_module("tests.integration.artifact_preview_http_fixture",run_name="__main__")', ...runtime.sites];
    }
    child = spawn(python, args, {
      cwd: path.resolve(process.cwd(), '..'), windowsHide: true,
      env: { ...process.env, PYTHONUNBUFFERED: '1' }, stdio: ['ignore', 'pipe', 'pipe'],
    });
    child.stdout?.on('data', data => { fixtureOutput = (fixtureOutput + data.toString()).slice(-16000); });
    child.stderr?.on('data', data => { fixtureError = (fixtureError + data.toString()).slice(-16000); });
    let spawnError: Error | undefined;
    child.on('error', error => { spawnError = error; });
    try {
      await expect.poll(async () => {
        if (spawnError) throw spawnError;
        if (child?.exitCode !== null || child?.signalCode !== null) throw new Error(`Owned API fixture exited: ${fixtureError}`);
        if (!fixtureOutput.includes(MISSION_ID)) return false;
        try {
          const response = await request.get(`${FIXTURE_ORIGIN}/fixture/health`, { timeout: 1000 });
          if (!response.ok()) return false;
          const body = await response.json();
          return body.ready === true && body.fixturePid === child.pid;
        } catch { return false; }
      }, { timeout: 30000, intervals: [100, 250, 500] }).toBe(true);
    } catch (error) {
      await shutdownOwnedChild(child);
      throw new Error(`Owned API fixture did not become ready (owned PID ${child.pid}): ${String(error)}\n${fixtureOutput}\n${fixtureError}`);
    }
  });

  test.afterAll(async () => { await shutdownOwnedChild(child); });

  test('renders actual CJK bytes read-only with local Monaco and releases its workers', async ({ page, request }, testInfo) => {
    const calls = await openMission(page);
    const workers = new Set();
    let workerCloses = 0;
    page.on('worker', worker => { workers.add(worker); worker.on('close', () => { workers.delete(worker); workerCloses += 1; }); });
    const frame = await openCode(page, 'json');
    expect(await frame.evaluate(() => (window as unknown as MonacoWindow).monaco.editor.getModels()[0].getValue())).toBe(JSON_TEXT);
    expect(await frame.evaluate(() => {
      const api = (window as unknown as MonacoWindow).monaco;
      return api.editor.getEditors()[0].getOption(api.editor.EditorOption.readOnly);
    })).toBe(true);
    await frame.locator('textarea').focus();
    await page.keyboard.type('must never edit');
    expect(await frame.evaluate(() => (window as unknown as MonacoWindow).monaco.editor.getModels()[0].getValue())).toBe(JSON_TEXT);
    // Exercise the real bundled language worker even when read-only diagnostics
    // are disabled; no schema URL or remote language service is configured.
    await frame.evaluate(async () => {
      const api = (window as unknown as MonacoWindow).monaco;
      const worker = await api.json.getWorker();
      await worker(api.editor.getModels()[0].uri);
    });
    await expect.poll(() => workers.size).toBeGreaterThan(0);
    await page.screenshot({ path: testInfo.outputPath('artifact-preview-monaco.png'), fullPage: true });
    const trigger = page.getByRole('button', { name: '预览 artifact-browser-json' });
    await page.getByRole('button', { name: '关闭产物预览' }).click();
    await expect(page.locator('iframe[src="/artifact-code-frame.html"]')).toHaveCount(0);
    await expect.poll(() => workers.size).toBe(0);
    expect(workerCloses).toBeGreaterThan(0);
    await expect(trigger).toBeFocused();
    expect(calls.requests.filter(url => !['http://localhost:3000', 'http://127.0.0.1:3000'].includes(new URL(url).origin) && !url.startsWith('blob:'))).toEqual([]);
    expect(calls.pageErrors).toEqual([]);
    expect(calls.previewRequests).toEqual([`${MISSION_PATH}/artifacts/artifact-browser-json/preview`]);
    expect(calls.missionWrites).toEqual([]);
    await assertMissionUnchanged(request);
  });

  test('renders HTML as source without running scripts or loading referenced images, and Escape restores focus', async ({ page, request }) => {
    const calls = await openMission(page);
    const frame = await openCode(page, 'html');
    expect(await frame.evaluate(() => (window as unknown as MonacoWindow).monaco.editor.getModels()[0].getValue())).toContain('<script>window.PREVIEW_EXECUTED=true</script>');
    expect(await page.evaluate(() => (window as unknown as MonacoWindow).PREVIEW_EXECUTED)).toBeUndefined();
    expect(await frame.evaluate(() => (window as unknown as MonacoWindow).PREVIEW_EXECUTED)).toBeUndefined();
    expect(calls.requests.filter(url => url.includes('/fixture/should-never-load.png'))).toEqual([]);
    await frame.locator('textarea').focus();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: 'Artifact 只读预览' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: '预览 artifact-browser-html' })).toBeFocused();
    expect(calls.pageErrors).toEqual([]);
    expect(calls.missionWrites).toEqual([]);
    await assertMissionUnchanged(request);
  });

  test('shows empty bytes honestly and rejects large and confidential objects before any byte read', async ({ page, request }) => {
    const calls = await openMission(page);
    const health = await request.get(`${FIXTURE_ORIGIN}/fixture/health`);
    const before = await health.json();
    await expect(page.getByRole('button', { name: '预览 artifact-browser-large' })).toBeDisabled();
    await expect(page.getByRole('button', { name: '预览 artifact-browser-denied' })).toBeDisabled();
    await expect(page.getByText('超过 256 KiB，暂不支持预览')).toBeVisible();
    await expect(page.getByText('此敏感级别暂不支持预览')).toBeVisible();
    await page.getByRole('button', { name: '预览 artifact-browser-empty' }).click();
    await expect(page.getByText('空产物（0 字节）')).toBeVisible();
    await expect(page.locator('iframe[src="/artifact-code-frame.html"]')).toHaveCount(0);
    await page.getByRole('button', { name: '关闭产物预览' }).click();
    const after = await (await request.get(`${FIXTURE_ORIGIN}/fixture/health`)).json();
    expect(after.reads['artifact-browser-empty']).toBe((before.reads['artifact-browser-empty'] ?? 0) + 1);
    expect(after.reads).not.toHaveProperty('artifact-browser-large');
    expect(after.reads).not.toHaveProperty('artifact-browser-denied');
    expect(calls.previewRequests).toEqual([`${MISSION_PATH}/artifacts/artifact-browser-empty/preview`]);
    expect(calls.missionWrites).toEqual([]);
    await assertMissionUnchanged(request);
  });

  test('renders a real cross-workspace 403 and retries only the byte read after authorization is restored', async ({ page, request }) => {
    const calls = await openMission(page);
    const preview = `**${MISSION_PATH}/artifacts/artifact-browser-code/preview`;
    await page.route(preview, route => route.continue({ headers: { ...route.request().headers(), authorization: 'Bearer artifact-browser-foreign' } }));
    const denied = page.waitForResponse(response => response.url().endsWith('/artifacts/artifact-browser-code/preview') && response.status() === 403);
    await page.getByRole('button', { name: '预览 artifact-browser-code' }).click();
    await denied;
    await expect(page.getByRole('alert')).toContainText('没有权限预览此产物');
    await expect(page.locator('iframe[src="/artifact-code-frame.html"]')).toHaveCount(0);
    await page.unroute(preview);
    await page.getByRole('button', { name: '重试读取产物' }).click();
    await expect(page.getByText(/字节校验通过/)).toBeVisible();
    await expect(page.getByTitle('产物 artifact-browser-code 的只读代码')).toBeVisible();
    expect(calls.previewRequests).toHaveLength(2);
    expect(calls.missionWrites).toEqual([]);
    await assertMissionUnchanged(request);
  });

  test('keeps network failures visible and retry issues one GET without creating work', async ({ page, request }) => {
    const calls = await openMission(page);
    const preview = `**${MISSION_PATH}/artifacts/artifact-browser-code/preview`;
    await page.route(preview, route => route.abort('connectionfailed'), { times: 1 });
    await page.getByRole('button', { name: '预览 artifact-browser-code' }).click();
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(page.locator('iframe[src="/artifact-code-frame.html"]')).toHaveCount(0);
    await page.getByRole('button', { name: '重试读取产物' }).click();
    await expect(page.getByText(/字节校验通过/)).toBeVisible();
    expect(calls.previewRequests).toHaveLength(2);
    expect(calls.missionWrites).toEqual([]);
    await assertMissionUnchanged(request);
  });
});
