import assert from 'node:assert/strict';
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

import { connectLoopback, runPackagedAcceptance } from '../webview2-acceptance.mjs';

const digest = 'c'.repeat(64);
const desktop = dirname(fileURLToPath(new URL('../webview2-acceptance.mjs', import.meta.url)));
const origin = 'http://127.0.0.1:28004';

function identityReport() {
  return {
    status: 'passed', scope: 'desktop-webview2-process-identity', executableSha256: digest,
    nativeWebView2: true, packagedApplication: true, realLoginVerified: false,
    sharedUiVerified: false, realServicesVerified: false, chineseInputVerified: false,
    zoomVerified: false, shutdownVerified: false, debugEndpoint: null, webViewPids: [41],
  };
}

async function ownedDirectory(t) {
  const root = await mkdtemp(join(tmpdir(), 'agenthub-webview2-acceptance-'));
  t.after(async () => {
    assert.equal(dirname(resolve(root)), resolve(tmpdir()));
    assert.ok(root.split(sep).at(-1).startsWith('agenthub-webview2-acceptance-'));
    await rm(root, { recursive: true, force: true });
  });
  return root;
}

async function saved(output, name) {
  const [run] = await readdir(output);
  return JSON.parse(await readFile(join(output, run, name), 'utf8'));
}

test('debugging connection refuses any non-loopback endpoint before fetching', async () => {
  let called = false;
  const fetchImpl = async () => { called = true; return { ok: true, json: async () => ({}) }; };
  await assert.rejects(connectLoopback('http://10.1.1.1:9333', fetchImpl), /non-loopback/);
  await assert.rejects(connectLoopback('http://127.0.0.1', fetchImpl), /non-loopback/);
  assert.equal(called, false);
  const seen = [];
  await connectLoopback('http://127.0.0.1:9333', async url => { seen.push(String(url)); return { ok: true, json: async () => ({ Browser: 'owned' }) }; });
  assert.deepEqual(seen, ['http://127.0.0.1:9333/json/version']);
});

test('acceptance runs identity before the loopback login launch and leaves services false', async (t) => {
  const root = await ownedDirectory(t);
  const executable = join(root, 'agenthub-desktop.exe');
  await writeFile(executable, 'package');
  const output = join(root, 'out');
  let identityCalls = 0;
  let stopped = false;
  const launched = [];
  const { report } = await runPackagedAcceptance({
    executable, expectedSha256: digest, outputDirectory: output, timeoutMs: 1000,
  }, {
    debugPort: 9333,
    pollIntervalMs: 0,
    baseEnv: { WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: '--remote-debugging-port=0.0.0.0:1', PATH: 'kept' },
    runIdentity: async options => {
      identityCalls += 1;
      assert.equal(options.executable, executable);
      return { report: identityReport() };
    },
    launch: (received, env) => {
      launched.push({ received, env });
      return { pid: 70 };
    },
    listProcesses: async () => (stopped ? [] : [
      { pid: 70, parentPid: 1, name: 'agenthub-desktop.exe' },
      { pid: 71, parentPid: 70, name: 'msedgewebview2.exe' },
    ]),
    fetch: async () => ({ ok: true, json: async () => ({}) }),
    openSession: async endpoint => {
      assert.equal(endpoint, 'http://127.0.0.1:9333');
      return {
        login: async () => ({ requests: [{ method: 'POST', path: '/api/auth/login', status: 200, initiator: 'page' }] }),
        surface: async () => ({
          frames: [
            { role: 'admin', url: `${origin}/admin` },
            { role: 'artifact', parentRole: 'admin', url: `${origin}/artifact-code-frame.html`, readOnly: true, sandbox: 'allow-scripts allow-same-origin' },
          ],
          resources: [
            { kind: 'script', url: `${origin}/app.js` },
            { kind: 'worker', url: `${origin}/worker.js` },
          ],
          input: { method: 'keyboard', value: '中文输入', readBack: '中文输入' },
          zoom: { method: 'keyboard', before: 1, after: 1.1 },
        }),
      };
    },
    stop: async pid => { assert.equal(pid, 70); stopped = true; },
  });
  assert.equal(identityCalls, 1);
  assert.equal(launched[0].env.WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS, '--remote-debugging-port=9333 --remote-debugging-address=127.0.0.1');
  assert.equal(launched[0].env.PATH, 'kept');
  assert.equal(report.status, 'passed');
  assert.equal(report.realServicesVerified, false);
  assert.equal(report.sharedUiVerified, true);
  assert.equal((await saved(output, 'report.json')).status, 'passed');
  assert.equal((await saved(output, 'surface.json')).realServicesVerified, false);
});

test('identity failure does not launch the login process', async (t) => {
  const root = await ownedDirectory(t);
  let launched = false;
  await assert.rejects(runPackagedAcceptance({
    executable: join(root, 'agenthub-desktop.exe'), expectedSha256: digest, outputDirectory: join(root, 'out'), timeoutMs: 1000,
  }, {
    runIdentity: async () => { throw new Error('identity unavailable'); },
    launch: () => { launched = true; return { pid: 1 }; },
  }), error => /identity unavailable/.test(error.cause?.message));
  assert.equal(launched, false);
});

test('acceptance tooling does not write an admin token', async () => {
  const source = await readFile(join(desktop, 'webview2-acceptance.mjs'), 'utf8');
  assert.equal(source.includes('setItem'), false);
  assert.equal(source.includes('agenthub_token'), false);
});
