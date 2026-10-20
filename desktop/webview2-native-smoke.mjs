import { execFile, spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { lstat, mkdir, mkdtemp, open, rename, writeFile } from 'node:fs/promises';
import { basename, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { promisify } from 'node:util';

import { assertApplicationDigest, childEnvironment, classifyOwnedWebView, identityAccepted, parseProcessSnapshot } from './webview2-native-support.mjs';

const execute = promisify(execFile);
const executableName = 'agenthub-desktop.exe';

function fail(message) {
  const error = new Error(message);
  error.identityFailure = true;
  throw error;
}

export function parseArgs(argv) {
  const options = { outputDirectory: resolve('desktop/.runtime/webview2-identity'), timeoutMs: 20_000 };
  for (let index = 0; index < argv.length; index += 1) {
    const name = argv[index];
    const value = argv[index + 1];
    if (!name.startsWith('--') || value === undefined || value.startsWith('--')) fail(`Missing value for ${name}.`);
    if (name === '--executable') options.executable = resolve(value);
    else if (name === '--expected-sha256') options.expectedSha256 = value;
    else if (name === '--output') options.outputDirectory = resolve(value);
    else if (name === '--timeout-ms') options.timeoutMs = Number(value);
    else fail(`Unknown argument ${name}.`);
    index += 1;
  }
  if (!options.executable || !options.expectedSha256) fail('Requires --executable and --expected-sha256.');
  if (!Number.isInteger(options.timeoutMs) || options.timeoutMs < 1000 || options.timeoutMs > 120_000) fail('Timeout must be an integer from 1000 to 120000 milliseconds.');
  return options;
}

async function hashFile(path) {
  const handle = await open(path, 'r');
  const hash = createHash('sha256');
  try {
    const stream = handle.createReadStream();
    for await (const chunk of stream) hash.update(chunk);
  } finally { await handle.close(); }
  return hash.digest('hex');
}

async function assertRegularExecutable(path) {
  let info;
  try { info = await lstat(path); }
  catch (error) {
    if (error.code === 'ENOENT') fail('Desktop executable must be a nonempty regular file.');
    throw error;
  }
  if (info.isSymbolicLink() || !info.isFile() || info.size <= 0) fail('Desktop executable must be a nonempty regular file.');
  if (basename(path).toLowerCase() !== executableName) fail('Identity launch accepts only agenthub-desktop.exe.');
}

export async function listWindowsProcesses() {
  if (process.platform !== 'win32') fail('Native WebView2 identity requires Windows.');
  const command = "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name | ConvertTo-Json -Compress";
  const { stdout } = await execute('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', command], { windowsHide: true, timeout: 30_000, maxBuffer: 32 * 1024 * 1024 });
  return parseProcessSnapshot(stdout);
}

function launchDesktop(executable) {
  const child = spawn(executable, [], { cwd: dirname(executable), env: childEnvironment(process.env), windowsHide: false, stdio: 'ignore' });
  return child;
}

async function stopProcess(pid) {
  if (!Number.isInteger(pid) || pid <= 0) fail('Refusing to stop an invalid process id.');
  try {
    await execute('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true, timeout: 15_000 });
  } catch (error) {
    if (error.code !== 128) fail(`Unable to stop launched desktop process ${pid}.`);
  }
}

function blankReport(expectedSha256) {
  return {
    schemaVersion: 1, scope: 'desktop-webview2-process-identity', status: 'running',
    executableName, executableSha256: null, expectedSha256,
    nativeWebView2: false, packagedApplication: false, realLoginVerified: false,
    sharedUiVerified: false, realServicesVerified: false, chineseInputVerified: false,
    zoomVerified: false, shutdownVerified: false, debugEndpoint: null,
    rootPid: null, webViewPids: [], checks: [],
  };
}

export async function runWebViewIdentity(options, dependencies = {}) {
  await mkdir(options.outputDirectory, { recursive: true });
  const output = await mkdtemp(resolve(options.outputDirectory, 'webview2-identity-'));
  const reportPath = resolve(output, 'report.json');
  const report = blankReport(options.expectedSha256);
  const save = async () => {
    await writeFile(`${reportPath}.tmp`, `${JSON.stringify(report, null, 2)}\n`);
    await rename(`${reportPath}.tmp`, reportPath);
  };
  await save();
  let child = null;
  let failure;
  try {
    await (dependencies.assertExecutable ?? assertRegularExecutable)(options.executable);
    report.executableSha256 = await (dependencies.hashFile ?? hashFile)(options.executable);
    assertApplicationDigest(report.executableSha256, options.expectedSha256);
    report.packagedApplication = true;
    report.checks.push('executable digest matches the selected package build');
    child = (dependencies.launch ?? launchDesktop)(options.executable);
    if (!Number.isInteger(child.pid) || child.pid <= 0) fail('Launched desktop process did not receive a process id.');
    report.rootPid = child.pid;
    const deadline = Date.now() + options.timeoutMs;
    let classification = { owned: false, reason: 'WebView2 process was not observed.', webViewPids: [] };
    while (Date.now() <= deadline) {
      classification = classifyOwnedWebView({ rootPid: child.pid, processes: await (dependencies.listProcesses ?? listWindowsProcesses)() });
      if (classification.owned) break;
      await delay(dependencies.pollIntervalMs ?? 500);
    }
    if (!classification.owned) fail(classification.reason);
    report.nativeWebView2 = true;
    report.webViewPids = classification.webViewPids;
    report.checks.push(classification.reason);
    if (!identityAccepted(report)) fail('Identity report claimed a later UI-4c check.');
    report.status = 'passed';
  } catch (error) {
    failure = error;
    report.status = 'failed';
    report.error = error instanceof Error ? error.message : String(error);
  } finally {
    if (child?.pid) {
      try { await (dependencies.stop ?? stopProcess)(child.pid); report.checks.push('launched process tree was stopped'); }
      catch (error) {
        failure = failure ?? error;
        report.status = 'failed';
        report.error = error instanceof Error ? error.message : String(error);
      }
    }
    report.finishedAt = new Date().toISOString();
    await save();
  }
  if (failure) {
    const error = new Error(`Native WebView2 process identity failed. Report: ${reportPath}`, { cause: failure });
    error.reportPath = reportPath;
    throw error;
  }
  return { report, reportPath };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const { reportPath } = await runWebViewIdentity(parseArgs(process.argv.slice(2)));
    console.log(`Packaged desktop WebView2 process identity passed. Login, shared UI, services, input, zoom, and shutdown remain unverified. Report: ${reportPath}`);
  } catch (error) {
    console.error(JSON.stringify({ scope: 'desktop-webview2-process-identity', status: 'failed', error: error.message, cause: error.cause?.message }));
    process.exitCode = 1;
  }
}
