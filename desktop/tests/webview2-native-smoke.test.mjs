import assert from 'node:assert/strict';
import { mkdtemp, readdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

import { parseArgs, runWebViewIdentity } from '../webview2-native-smoke.mjs';
import { childEnvironment, classifyOwnedWebView, identityAccepted, parseProcessSnapshot } from '../webview2-native-support.mjs';

const digest = 'a'.repeat(64);
const desktop = dirname(fileURLToPath(new URL('../webview2-native-smoke.mjs', import.meta.url)));

async function ownedDirectory(t) {
  const root = await mkdtemp(join(tmpdir(), 'agenthub-webview2-identity-'));
  t.after(async () => {
    assert.equal(dirname(resolve(root)), resolve(tmpdir()));
    assert.ok(root.split(sep).at(-1).startsWith('agenthub-webview2-identity-'));
    await rm(root, { recursive: true, force: true });
  });
  return root;
}

function processes(rootPid, webViewParent) {
  return [
    { pid: rootPid, parentPid: 1, name: 'agenthub-desktop.exe' },
    { pid: 40, parentPid: webViewParent, name: 'msedgewebview2.exe' },
  ];
}

test('parent chain ownership stops at the launched executable', () => {
  assert.equal(classifyOwnedWebView({ rootPid: 10, processes: processes(10, 10) }).owned, true);
  const nested = [
    { pid: 10, parentPid: 1, name: 'agenthub-desktop.exe' },
    { pid: 20, parentPid: 10, name: 'msedgewebview2.exe' },
    { pid: 21, parentPid: 20, name: 'msedgewebview2.exe' },
  ];
  assert.deepEqual(classifyOwnedWebView({ rootPid: 10, processes: nested }).webViewPids, [20, 21]);
  assert.equal(classifyOwnedWebView({ rootPid: 10, processes: processes(10, 99) }).owned, false);
  const deep = [{ pid: 10, parentPid: 1, name: 'agenthub-desktop.exe' }];
  let parent = 10;
  for (let step = 0; step < 9; step += 1) {
    const pid = 100 + step;
    deep.push({ pid, parentPid: parent, name: step === 8 ? 'msedgewebview2.exe' : 'broker.exe' });
    parent = pid;
  }
  assert.equal(classifyOwnedWebView({ rootPid: 10, processes: deep }).owned, false);
  assert.equal(classifyOwnedWebView({ rootPid: 10, processes: [{ pid: 10, parentPid: 10, name: 'agenthub-desktop.exe' }, { pid: 11, parentPid: 10, name: 'msedgewebview2.exe' }] }).owned, true);
});

test('process snapshot rejects duplicate ids and accepts Windows CIM names', () => {
  const duplicate = classifyOwnedWebView({ rootPid: 10, processes: [{ pid: 10, parentPid: 1, name: 'agenthub-desktop.exe' }, { pid: 10, parentPid: 1, name: 'other.exe' }] });
  assert.equal(duplicate.owned, false);
  assert.match(duplicate.reason, /invalid or duplicate/);
  const parsed = parseProcessSnapshot('{"ProcessId":10,"ParentProcessId":1,"Name":"agenthub-desktop.exe"}');
  assert.deepEqual(parsed, [{ pid: 10, parentPid: 1, name: 'agenthub-desktop.exe' }]);
});

test('identity environment does not inherit a WebView2 debugging argument', () => {
  const env = childEnvironment({ WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: '--remote-debugging-port=9222', PATH: 'kept' });
  assert.equal(env.WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS, undefined);
  assert.equal(env.PATH, 'kept');
});

test('a passing identity report cannot claim login or shared UI', () => {
  const report = {
    nativeWebView2: true, packagedApplication: true, realLoginVerified: false,
    sharedUiVerified: false, realServicesVerified: false, chineseInputVerified: false,
    zoomVerified: false, shutdownVerified: false, debugEndpoint: null, webViewPids: [40],
  };
  assert.equal(identityAccepted(report), true);
  assert.equal(identityAccepted({ ...report, realLoginVerified: true }), false);
  assert.equal(identityAccepted({ ...report, debugEndpoint: 'http://127.0.0.1:9222' }), false);
});

async function savedReport(outputDirectory) {
  const [run] = await readdir(outputDirectory);
  return JSON.parse(await readFile(join(outputDirectory, run, 'report.json'), 'utf8'));
}

test('digest mismatch does not launch the executable', async (t) => {
  const root = await ownedDirectory(t);
  const executable = join(root, 'agenthub-desktop.exe');
  await writeFile(executable, 'not-the-package');
  let launched = false;
  await assert.rejects(runWebViewIdentity({
    executable, expectedSha256: digest, outputDirectory: join(root, 'out'), timeoutMs: 1000,
  }, {
    hashFile: async () => 'b'.repeat(64),
    launch: () => { launched = true; return { pid: 10 }; },
  }), /process identity failed/);
  assert.equal(launched, false);
  const report = await savedReport(join(root, 'out'));
  assert.equal(report.status, 'failed');
  assert.equal(report.nativeWebView2, false);
  assert.equal(report.packagedApplication, false);
});

test('owned WebView2 passes identity and stops only the launched process', async (t) => {
  const root = await ownedDirectory(t);
  const executable = join(root, 'agenthub-desktop.exe');
  await writeFile(executable, 'package-bytes');
  const stopped = [];
  const seen = [];
  const output = join(root, 'out');
  const { report } = await runWebViewIdentity({
    executable, expectedSha256: digest, outputDirectory: output, timeoutMs: 1000,
  }, {
    hashFile: async () => digest,
    launch: received => {
      seen.push(received);
      return { pid: 10 };
    },
    listProcesses: async () => processes(10, 10),
    stop: async pid => { stopped.push(pid); },
    pollIntervalMs: 0,
  });
  assert.equal(report.status, 'passed');
  assert.equal(report.nativeWebView2, true);
  assert.equal(report.realLoginVerified, false);
  assert.equal(report.debugEndpoint, null);
  assert.deepEqual(report.webViewPids, [40]);
  assert.deepEqual(stopped, [10]);
  assert.deepEqual(seen, [executable]);
  assert.equal((await savedReport(output)).status, 'passed');
});

test('missing executable writes a failed report and does not launch', async (t) => {
  const root = await ownedDirectory(t);
  const output = join(root, 'out');
  await assert.rejects(runWebViewIdentity({
    executable: join(root, 'agenthub-desktop.exe'), expectedSha256: digest, outputDirectory: output, timeoutMs: 1000,
  }, { launch: () => { throw new Error('launch must not run'); } }), error => /nonempty regular file/.test(error.cause?.message));
  const report = await savedReport(output);
  assert.equal(report.status, 'failed');
  assert.equal(report.nativeWebView2, false);
  assert.equal(report.packagedApplication, false);
});

test('failure to stop the launched process fails the identity report', async (t) => {
  const root = await ownedDirectory(t);
  const executable = join(root, 'agenthub-desktop.exe');
  await writeFile(executable, 'package-bytes');
  await assert.rejects(runWebViewIdentity({
    executable, expectedSha256: digest, outputDirectory: join(root, 'out'), timeoutMs: 1000,
  }, {
    hashFile: async () => digest,
    launch: () => ({ pid: 10 }),
    listProcesses: async () => processes(10, 10),
    stop: async () => { throw new Error('stop failed'); },
    pollIntervalMs: 0,
  }), /process identity failed/);
  assert.equal((await savedReport(join(root, 'out'))).status, 'failed');
});

test('command line requires the executable and selected digest', () => {
  assert.throws(() => parseArgs([]), /Requires --executable/);
  const options = parseArgs(['--executable', 'desktop\\agenthub-desktop.exe', '--expected-sha256', digest, '--timeout-ms', '1000']);
  assert.equal(options.timeoutMs, 1000);
  assert.equal(options.expectedSha256, digest);
});

test('identity tooling does not inject an admin token', async () => {
  const source = await readFile(join(desktop, 'webview2-native-smoke.mjs'), 'utf8');
  const support = await readFile(join(desktop, 'webview2-native-support.mjs'), 'utf8');
  assert.equal(source.includes('agenthub_token'), false);
  assert.equal(support.includes('localStorage'), false);
});
