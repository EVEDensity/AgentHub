import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, rename, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

import { listWindowsProcesses, parseArgs, runWebViewIdentity, stopProcess } from './webview2-native-smoke.mjs';
import { classifyOwnedWebView, identityAccepted } from './webview2-native-support.mjs';
import { debugEndpoint, loginChildEnvironment, productLoginReport } from './webview2-login-support.mjs';
import { sharedSurfaceReport } from './webview2-surface-support.mjs';

export async function connectLoopback(endpoint, fetchImpl = globalThis.fetch) {
  const url = new URL(endpoint);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.port === '') throw new Error('Refusing a non-loopback WebView2 debugging endpoint.');
  const target = new URL('/json/version', url);
  if (target.hostname !== '127.0.0.1') throw new Error('Refusing a non-loopback WebView2 debugging endpoint.');
  const response = await fetchImpl(target, { redirect: 'error' });
  if (!response?.ok) throw new Error('WebView2 debugging endpoint did not respond.');
  return response.json();
}

function launchWithEnvironment(executable, env) {
  return spawn(executable, [], { cwd: dirname(executable), env, windowsHide: false, stdio: 'ignore' });
}

async function ownedWebView(pid, listProcesses, timeoutMs, pollIntervalMs) {
  const deadline = Date.now() + timeoutMs;
  let classification = { owned: false, reason: 'WebView2 process was not observed.' };
  while (Date.now() <= deadline) {
    classification = classifyOwnedWebView({ rootPid: pid, processes: await listProcesses() });
    if (classification.owned) return classification;
    await delay(pollIntervalMs);
  }
  throw new Error(classification.reason);
}

async function saveReport(reportPath, report) {
  await writeFile(`${reportPath}.tmp`, `${JSON.stringify(report, null, 2)}\n`);
  await rename(`${reportPath}.tmp`, reportPath);
}

export async function runPackagedAcceptance(options, dependencies = {}) {
  await mkdir(options.outputDirectory, { recursive: true });
  const output = await mkdtemp(resolve(options.outputDirectory, 'webview2-acceptance-'));
  const reportPath = resolve(output, 'report.json');
  const report = {
    schemaVersion: 1, scope: 'desktop-webview2-packaged-acceptance', status: 'running',
    executableSha256: null, nativeWebView2: false, packagedApplication: false,
    realLoginVerified: false, sharedUiVerified: false, realServicesVerified: false,
    chineseInputVerified: false, zoomVerified: false, shutdownVerified: false, debugEndpoint: null, checks: [],
  };
  await saveReport(reportPath, report);
  let child = null;
  let failure;
  try {
    const identity = await (dependencies.runIdentity ?? (identityOptions => runWebViewIdentity(identityOptions, dependencies.identityDependencies ?? {})))(options);
    if (identity.report?.status !== 'passed' || !identityAccepted(identity.report) || identity.report.debugEndpoint !== null) {
      throw new Error('Packaged acceptance requires a passed process-identity report with no debugging endpoint.');
    }
    report.executableSha256 = identity.report.executableSha256;
    report.nativeWebView2 = true;
    report.packagedApplication = true;
    report.checks.push('process identity passed without a debugging endpoint');
    const endpoint = debugEndpoint(dependencies.debugPort ?? 9333);
    report.debugEndpoint = endpoint;
    const env = loginChildEnvironment(dependencies.baseEnv ?? process.env, dependencies.debugPort ?? 9333);
    child = (dependencies.launch ?? launchWithEnvironment)(options.executable, env);
    if (!Number.isInteger(child.pid) || child.pid <= 0) throw new Error('Login launch did not receive a process id.');
    await ownedWebView(child.pid, dependencies.listProcesses ?? listWindowsProcesses, options.timeoutMs, dependencies.pollIntervalMs ?? 500);
    report.checks.push('login launch owns its WebView2 process');
    await connectLoopback(endpoint, dependencies.fetch);
    if (!dependencies.openSession) throw new Error('Packaged WebView2 product session is not connected.');
    const session = await dependencies.openSession(endpoint);
    const loginReport = productLoginReport(identity.report, {
      ...(await session.login()), executableSha256: identity.report.executableSha256, debugEndpoint: endpoint,
    });
    await saveReport(resolve(output, 'login.json'), loginReport);
    const surfaceObservation = await session.surface();
    const rootPid = child.pid;
    await (dependencies.stop ?? stopProcess)(rootPid);
    child = null;
    const surfaceReport = sharedSurfaceReport(loginReport, {
      ...surfaceObservation, executableSha256: identity.report.executableSha256,
      shutdown: { rootPid, processes: await (dependencies.listProcesses ?? listWindowsProcesses)() },
    });
    if (surfaceReport.realServicesVerified !== false) throw new Error('Acceptance cannot claim a live service.');
    await saveReport(resolve(output, 'surface.json'), surfaceReport);
    report.realLoginVerified = true;
    report.sharedUiVerified = true;
    report.chineseInputVerified = true;
    report.zoomVerified = true;
    report.shutdownVerified = true;
    report.realServicesVerified = false;
    report.checks.push('login and shared surface passed for the identified executable');
    report.status = 'passed';
  } catch (error) {
    failure = error;
    report.status = 'failed';
    report.error = error instanceof Error ? error.message : String(error);
  } finally {
    if (child?.pid) {
      try { await (dependencies.stop ?? stopProcess)(child.pid); }
      catch (error) {
        failure = failure ?? error;
        report.status = 'failed';
        report.error = error instanceof Error ? error.message : String(error);
      }
    }
    report.finishedAt = new Date().toISOString();
    await saveReport(reportPath, report);
  }
  if (failure) {
    const error = new Error(`Packaged WebView2 acceptance failed. Report: ${reportPath}`, { cause: failure });
    error.reportPath = reportPath;
    throw error;
  }
  return { report, reportPath };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const { reportPath } = await runPackagedAcceptance(parseArgs(process.argv.slice(2)));
    console.log(`Packaged WebView2 acceptance passed. Live services remain unverified. Report: ${reportPath}`);
  } catch (error) {
    console.error(JSON.stringify({ scope: 'desktop-webview2-packaged-acceptance', status: 'failed', error: error.message, cause: error.cause?.message }));
    process.exitCode = 1;
  }
}
