import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { createServer } from 'node:http';
import { cp, mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, isAbsolute, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import test from 'node:test';

import { isAllowedRequest, parseArgs, startStaticServer, withBrowserContext } from '../shell-browser-smoke-support.mjs';

const execute = promisify(execFile);
const desktop = fileURLToPath(new URL('../', import.meta.url));
const runner = join(desktop, 'shell-browser-smoke.mjs');

async function ownedDirectory(t) {
  const root = await mkdtemp(join(tmpdir(), 'agenthub-shell-smoke-test-'));
  t.after(async () => {
    // Only remove the freshly created private test root, never a caller path.
    assert.equal(dirname(resolve(root)), resolve(tmpdir()));
    assert.ok(root.split(sep).at(-1).startsWith('agenthub-shell-smoke-test-'));
    await rm(root, { recursive: true, force: true });
  });
  return root;
}

async function uiFiles(t) {
  const root = await ownedDirectory(t);
  const uiDirectory = join(root, 'ui');
  await mkdir(uiDirectory);
  await Promise.all([
    writeFile(join(uiDirectory, 'index.html'), '<!doctype html><title>Owned shell test</title>'),
    writeFile(join(uiDirectory, 'main.js'), 'globalThis.ownedShellTest = true;'),
    writeFile(join(uiDirectory, 'styles.css'), 'body { color: navy; }'),
    writeFile(join(uiDirectory, 'default-contract.js'), 'export const fixture = true;'),
    writeFile(join(root, 'outside-secret.txt'), 'PRIVATE-OUTSIDE-UI-ROOT'),
  ]);
  return { root, uiDirectory };
}

async function request(origin, path = '/', method = 'GET') {
  const { request: httpRequest } = await import('node:http');
  return new Promise((accept, reject) => {
    const endpoint = new URL(origin);
    const outgoing = httpRequest({ hostname: endpoint.hostname, port: endpoint.port, path, method }, response => {
      const chunks = [];
      response.on('data', value => chunks.push(value));
      response.on('end', () => accept({ status: response.statusCode, headers: response.headers, body: Buffer.concat(chunks).toString('utf8') }));
      response.on('error', reject);
    });
    outgoing.on('error', reject);
    outgoing.setTimeout(3000, () => outgoing.destroy(new Error('Owned HTTP test timed out')));
    outgoing.end();
  });
}

async function occupiedListener(t) {
  const server = createServer((_request, response) => response.end('other-listener-remains-owned'));
  await new Promise((accept, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', accept);
  });
  t.after(() => new Promise((accept, reject) => server.close(error => error ? reject(error) : accept())));
  const port = server.address().port;
  return { server, port, origin: `http://127.0.0.1:${port}` };
}

async function subprocess(command, args, options = {}) {
  try {
    const result = await execute(command, args, { timeout: 15_000, windowsHide: true, ...options });
    return { ...result, code: 0 };
  } catch (error) {
    if (error.killed || error.signal || typeof error.code !== 'number') throw error;
    return { stdout: error.stdout, stderr: error.stderr, code: error.code };
  }
}

async function failedReport(outputDirectory) {
  const entries = await readdir(outputDirectory, { withFileTypes: true });
  const directories = entries.filter(value => value.isDirectory() && value.name.startsWith('shell-browser-preview-'));
  assert.equal(directories.length, 1, 'The failed invocation must own exactly one new report directory');
  const directory = join(outputDirectory, directories[0].name);
  const report = JSON.parse(await readFile(join(directory, 'report.json'), 'utf8'));
  assert.equal(report.status, 'failed');
  assert.equal(report.scope, 'browser-shell-preview');
  assert.equal(report.nativeWebView2, false);
  assert.equal(report.packagedApplication, false);
  assert.equal(report.realServicesVerified, false);
  assert.equal((await readdir(directory)).some(value => value.endsWith('.png')), false, 'Failed startup must not leave success screenshots');
  return report;
}

test('CLI arguments preserve the existing port/output choices and reject ambiguous inputs', () => {
  const defaults = parseArgs([]);
  assert.equal(defaults.port, 18765);
  assert.ok(isAbsolute(defaults.outputDirectory));
  const chosen = parseArgs(['--port', '0', '--output-directory', 'path with spaces']);
  assert.equal(chosen.port, 0);
  assert.equal(chosen.outputDirectory, resolve('path with spaces'));
  for (const args of [
    ['--port'], ['--port', '-1'], ['--port', '65536'], ['--port', '1.5'], ['--port', 'NaN'],
    ['--port', '12', '--port', '13'], ['--output-directory'], ['--output-directory', 'a', '--output-directory', 'b'],
    ['--unknown', 'a'], ['positional'],
  ]) assert.throws(() => parseArgs(args));
});

test('actual static HTTP serves only fixed assets, HEAD and effective probe CSP', async t => {
  const { uiDirectory } = await uiFiles(t);
  const server = await startStaticServer({ port: 0, uiDirectory });
  t.after(server.close);
  assert.equal(new URL(server.origin).hostname, '127.0.0.1');
  const home = await request(server.origin);
  assert.equal(home.status, 200);
  assert.match(home.body, /Owned shell test/);
  assert.match(home.headers['content-type'], /text\/html/);
  for (const directive of ['connect-src', 'frame-src', 'worker-src', 'form-action']) {
    assert.match(home.headers['content-security-policy'], new RegExp(`${directive} 'none'`));
  }
  const script = await request(server.origin, '/main.js?cache-test=1');
  assert.equal(script.status, 200);
  assert.match(script.body, /ownedShellTest/);
  const head = await request(server.origin, '/index.html', 'HEAD');
  assert.equal(head.status, 200);
  assert.equal(head.body, '');
  assert.equal((await request(server.origin, '/', 'POST')).status, 405);
});

test('actual HTTP refuses traversal, private files and non-asset same-origin paths', async t => {
  const { uiDirectory } = await uiFiles(t);
  await writeFile(join(uiDirectory, 'private.txt'), 'PRIVATE-INSIDE-NOT-ALLOWLISTED');
  const server = await startStaticServer({ port: 0, uiDirectory });
  t.after(server.close);
  for (const path of ['/../outside-secret.txt', '/%2e%2e/outside-secret.txt', '/%2e%2e%2foutside-secret.txt',
    '/%5c..%5coutside-secret.txt', '/private.txt', '/api/health', '/main.js/extra', '/%00']) {
    const response = await request(server.origin, path);
    assert.ok([400, 403, 404].includes(response.status), `${path}: ${response.status}`);
    assert.doesNotMatch(response.body, /PRIVATE-/);
  }
});

test('allowlisted symlink cannot disclose bytes outside the real static root', async t => {
  const { root, uiDirectory } = await uiFiles(t);
  await rm(join(uiDirectory, 'main.js'));
  try {
    await symlink(join(root, 'outside-secret.txt'), join(uiDirectory, 'main.js'), 'file');
  } catch (error) {
    if (['EPERM', 'EACCES', 'ENOTSUP'].includes(error.code)) { t.skip('Host cannot create the file symlink needed for the realpath regression'); return; }
    throw error;
  }
  const server = await startStaticServer({ port: 0, uiDirectory });
  t.after(server.close);
  const response = await request(server.origin, '/main.js');
  assert.ok([403, 404].includes(response.status));
  assert.doesNotMatch(response.body, /PRIVATE-/);
});

test('occupied loopback port is refused without reusing or stopping the existing listener', async t => {
  const { uiDirectory } = await uiFiles(t);
  const occupied = await occupiedListener(t);
  await assert.rejects(startStaticServer({ port: occupied.port, uiDirectory }));
  assert.ok(occupied.server.listening);
  assert.equal((await request(occupied.origin)).body, 'other-listener-remains-owned');
});

test('close is idempotent and releases only the helper-owned listening port', async t => {
  const { uiDirectory } = await uiFiles(t);
  const first = await startStaticServer({ port: 0, uiDirectory });
  const port = Number(new URL(first.origin).port);
  await first.close();
  await first.close();
  const second = await startStaticServer({ port, uiDirectory });
  t.after(second.close);
  assert.equal((await request(second.origin)).status, 200);
});

test('browser request policy rejects external origins, credentials and API paths', () => {
  const origin = 'http://127.0.0.1:18765';
  for (const suffix of ['/', '/index.html', '/main.js', '/styles.css', '/default-contract.js', '/favicon.ico?x=1']) {
    assert.equal(isAllowedRequest(origin + suffix, origin), true, suffix);
  }
  for (const url of ['https://example.invalid/main.js', 'http://localhost:18765/main.js',
    'http://127.0.0.1:18766/main.js', 'https://127.0.0.1:18765/main.js',
    'http://user:password@127.0.0.1:18765/main.js', origin + '/main.js#fragment',
    origin + '/api/health', origin + '/private.txt', 'file:///main.js', 'data:text/plain,example', 'not a URL']) {
    assert.equal(isAllowedRequest(url, origin), false, url);
  }
});

test('launch failure propagates without pretending a context or browser succeeded', async () => {
  const failure = new Error('Intentional browser launch failure');
  let operationCalled = false;
  await assert.rejects(withBrowserContext(async () => { throw failure; }, async () => { operationCalled = true; }),
    error => error === failure);
  assert.equal(operationCalled, false);
});

test('context creation failure still closes its launched browser', async () => {
  const calls = [];
  const failure = new Error('Intentional context creation failure');
  await assert.rejects(withBrowserContext(async () => ({
    newContext: async () => { calls.push('newContext'); throw failure; },
    close: async () => { calls.push('browser.close'); },
  }), async () => { throw new Error('Operation must not run'); }), error => error === failure);
  assert.deepEqual(calls, ['newContext', 'browser.close']);
});

test('operation failure closes context before browser and retains the failure', async () => {
  const calls = [];
  const context = { close: async () => { calls.push('context.close'); } };
  const browser = { newContext: async () => context, close: async () => { calls.push('browser.close'); } };
  const failure = new Error('Intentional operation failure');
  await assert.rejects(withBrowserContext(async () => browser, async (actualContext, actualBrowser) => {
    assert.equal(actualContext, context);
    assert.equal(actualBrowser, browser);
    calls.push('operation');
    throw failure;
  }), error => error === failure);
  assert.deepEqual(calls, ['operation', 'context.close', 'browser.close']);
});

test('cleanup failure cannot turn into a passed operation or skip browser close', async () => {
  const calls = [];
  const failure = new Error('Intentional context close failure');
  await assert.rejects(withBrowserContext(async () => ({
    newContext: async () => ({ close: async () => { calls.push('context.close'); throw failure; } }),
    close: async () => { calls.push('browser.close'); },
  }), async () => 'callback-result-before-cleanup'), error => error === failure);
  assert.deepEqual(calls, ['context.close', 'browser.close']);
});

test('browser close failure also rejects after the operation has returned', async () => {
  const calls = [];
  const failure = new Error('Intentional browser close failure');
  await assert.rejects(withBrowserContext(async () => ({
    newContext: async () => ({ close: async () => { calls.push('context.close'); } }),
    close: async () => { calls.push('browser.close'); throw failure; },
  }), async () => 'callback-result-before-cleanup'), error => error === failure);
  assert.deepEqual(calls, ['context.close', 'browser.close']);
});

test('missing Playwright in an isolated copy exits nonzero and writes only scoped failed evidence', async t => {
  const root = await ownedDirectory(t);
  const copyDesktop = join(root, 'desktop');
  await mkdir(copyDesktop);
  await Promise.all(['shell-browser-smoke.mjs', 'shell-browser-smoke-support.mjs'].map(name => cp(join(desktop, name), join(copyDesktop, name))));
  await cp(join(desktop, 'ui'), join(copyDesktop, 'ui'), { recursive: true });
  await mkdir(join(root, 'frontend'));
  await Promise.all(['package.json', 'package-lock.json'].map(name => cp(join(desktop, '..', 'frontend', name), join(root, 'frontend', name))));
  const output = join(root, 'output with spaces');
  await mkdir(output);
  await writeFile(join(output, 'report.json'), JSON.stringify({ status: 'stale-other-run' }));
  const result = await subprocess(process.execPath, [join(copyDesktop, 'shell-browser-smoke.mjs'), '--port', '0', '--output-directory', output]);
  assert.notEqual(result.code, 0);
  const report = await failedReport(output);
  assert.match(report.error, /@playwright\/test.*unavailable/i);
  assert.deepEqual(JSON.parse(await readFile(join(output, 'report.json'), 'utf8')), { status: 'stale-other-run' });
});

test('CLI refuses invalid arguments with nonzero exit before reporting success', async t => {
  const root = await ownedDirectory(t);
  const result = await subprocess(process.execPath, [runner, '--port', '65536', '--output-directory', root]);
  assert.notEqual(result.code, 0);
  assert.doesNotMatch(result.stdout, /smoke passed|"status"\s*:\s*"passed"/i);
});

test('legacy PowerShell entry preserves arguments and failure exit without killing another listener', { skip: process.platform !== 'win32' }, async t => {
  const root = await ownedDirectory(t);
  const occupied = await occupiedListener(t);
  const output = join(root, 'legacy output with spaces');
  const result = await subprocess('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
    join(desktop, 'webview2-gui-smoke.ps1'), '-Port', String(occupied.port), '-OutputDirectory', output]);
  assert.notEqual(result.code, 0);
  const report = await failedReport(output);
  assert.match(report.error, /EADDRINUSE/);
  assert.equal((await request(occupied.origin)).body, 'other-listener-remains-owned');
});
