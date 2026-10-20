import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

import { classifyShutdown, observeChineseInput, readNestedFrames, sharedSurfaceReport } from '../webview2-surface-support.mjs';

const digest = 'a'.repeat(64);
const origin = 'http://127.0.0.1:28004';
const desktop = dirname(fileURLToPath(new URL('../webview2-surface-support.mjs', import.meta.url)));

function login() {
  return {
    status: 'passed', scope: 'desktop-webview2-product-login', executableSha256: digest,
    debugEndpoint: 'http://127.0.0.1:9333', nativeWebView2: true, packagedApplication: true,
    realLoginVerified: true, sharedUiVerified: false, realServicesVerified: false,
    chineseInputVerified: false, zoomVerified: false, shutdownVerified: false,
  };
}

function observation(overrides = {}) {
  return {
    executableSha256: digest,
    frames: [
      { role: 'admin', url: `${origin}/admin?menu=通用` },
      { role: 'artifact', parentRole: 'admin', url: `${origin}/artifact-code-frame.html`, readOnly: true, sandbox: 'allow-scripts allow-same-origin' },
    ],
    resources: [
      { kind: 'script', url: `${origin}/_next/static/chunks/app.js` },
      { kind: 'worker', url: `${origin}/monaco/worker.js` },
    ],
    input: { method: 'keyboard', value: '中文输入', readBack: '中文输入' },
    zoom: { method: 'keyboard', before: 1, after: 1.25 },
    shutdown: { rootPid: 10, processes: [] },
    ...overrides,
  };
}

test('shared surface accepts nested loopback frames and then requires shutdown', () => {
  const report = sharedSurfaceReport(login(), observation());
  assert.equal(report.sharedUiVerified, true);
  assert.equal(report.chineseInputVerified, true);
  assert.equal(report.zoomVerified, true);
  assert.equal(report.shutdownVerified, true);
  assert.equal(report.realServicesVerified, false);
  assert.throws(() => sharedSurfaceReport(login(), observation({
    resources: [{ kind: 'script', url: 'https://cdn.example/app.js' }, { kind: 'worker', url: `${origin}/worker.js` }],
  })), /loopback admin origin/);
  assert.throws(() => sharedSurfaceReport(login(), observation({
    input: { method: 'evaluate', value: '中文输入', readBack: '中文输入' },
  })), /keyboard/);
  assert.throws(() => sharedSurfaceReport({ ...login(), realLoginVerified: false }), /product-login/);
});

test('a leftover WebView2 child fails shutdown', () => {
  const result = classifyShutdown({
    rootPid: 10,
    processes: [{ pid: 40, parentPid: 10, name: 'msedgewebview2.exe' }],
  });
  assert.equal(result.verified, false);
  assert.match(result.reason, /WebView2/);
});

test('frame reader and Chinese keyboard entry use the product controls', async () => {
  const shell = {
    locator: selector => {
      assert.equal(selector, '#admin-frame');
      return {
        getAttribute: async () => `${origin}/admin`,
        contentFrame: () => ({
          locator: () => ({
            getAttribute: async name => (name === 'sandbox' ? 'allow-scripts allow-same-origin' : '产物 registered-1 的只读代码'),
          }),
        }),
      };
    },
  };
  const frames = await readNestedFrames(shell);
  assert.equal(frames[1].url, `${origin}/artifact-code-frame.html`);
  assert.equal(frames[1].readOnly, true);
  const calls = [];
  const input = await observeChineseInput({
    pressSequentially: async value => { calls.push(value); },
    inputValue: async () => '中文输入',
  });
  assert.deepEqual(calls, ['中文输入']);
  assert.equal(input.readBack, '中文输入');
});

test('surface tooling does not write an admin token', async () => {
  const source = await readFile(join(desktop, 'webview2-surface-support.mjs'), 'utf8');
  assert.equal(source.includes('setItem'), false);
  assert.equal(source.includes('agenthub_token'), false);
});
