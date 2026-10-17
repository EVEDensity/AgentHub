// Single anonymous document read; private AgentHub transport, not an MCP server.
// Original AgentHub code. SPDX-License-Identifier: Apache-2.0
import { readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { chromium } from 'playwright';
import {
  check, parseInput, requestUrl, limitedText, safeError,
  PLAYWRIGHT_VERSION, CHROMIUM_VERSION, SCHEMA,
} from './policy.mjs';

const require = createRequire(import.meta.url);
const output = {
  schema: SCHEMA, requestId: null, success: false, errorCode: null,
  requestedUrl: null, finalUrl: null, httpStatus: null, title: '',
  ...limitedText('', 256), blockedResourceCount: 0, redirectRejected: false,
  playwrightVersion: PLAYWRIGHT_VERSION, browserVersion: '',
  workerPid: process.pid, browserPid: null, cleanupConfirmed: false,
};
let server, browser, context, input;
let deadline = 0;
let mainError = null;
let shutdown;
let navigationStarted = false;

function remaining() {
  const value = deadline - Date.now();
  check(value > 0, 'deadline_exceeded');
  return value;
}

async function closeOwned() {
  // A second caller joins the same close. No externally supplied browser IDs.
  if (shutdown) return shutdown;
  shutdown = (async () => {
    try {
      if (context) await context.close();
      if (browser) await browser.close();
      if (server) await server.close();
      const child = server?.process();
      output.cleanupConfirmed = !child || child.exitCode !== null || child.signalCode !== null;
    } catch {
      try { if (server) await server.kill(); } catch { /* Parent owns final recovery. */ }
      const child = server?.process();
      output.cleanupConfirmed = !child || child.exitCode !== null || child.signalCode !== null;
    }
  })();
  return shutdown;
}

async function routeDocument(route, page) {
  const request = route.request();
  if (request.resourceType() !== 'document' || !request.isNavigationRequest()
      || request.frame() !== page?.mainFrame() || request.method() !== 'GET') {
    output.blockedResourceCount++;
    await route.abort('blockedbyclient');
    return;
  }
  let response;
  try {
    check(!navigationStarted && requestUrl(request.url(), input.origins) === input.url, 'navigation_failed');
    navigationStarted = true;
    // Never let either the browser or APIRequestContext follow a redirect.
    response = await route.fetch({ maxRedirects: 0, maxRetries: 0, timeout: remaining() });
    output.httpStatus = response.status();
    const headers = response.headers();
    if (output.httpStatus >= 300 && output.httpStatus <= 399) {
      output.redirectRejected = true;
      throw new Error('redirect_rejected');
    }
    check(output.httpStatus >= 200 && output.httpStatus < 300, 'http_error');
    check(!headers['content-disposition'], 'download_rejected');
    check(/^text\/(html|plain)(?:;|$)/i.test(headers['content-type'] ?? ''), 'document_type_unsupported');
    await route.fulfill({ response });
  } catch (error) {
    mainError = safeError(error);
    await route.abort('blockedbyclient').catch(() => {});
  } finally {
    await response?.dispose().catch(() => {});
  }
}

async function run() {
  const raw = await readFile(process.argv[2]);
  check(raw.byteLength <= 32768);
  input = parseInput(JSON.parse(raw.toString('utf8')));
  output.requestId = input.requestId;
  output.requestedUrl = input.url;
  check(process.env.AGENTHUB_BROWSER_OWNER === input.requestId);
  check(require('playwright/package.json').version === PLAYWRIGHT_VERSION, 'runtime_mismatch');
  deadline = Date.now() + input.timeoutMs;
  try {
    server = await chromium.launchServer({ host: '127.0.0.1', headless: true, timeout: remaining() });
  } catch {
    throw new Error('browser_launch_failed');
  }
  output.browserPid = server.process().pid;
  await writeFile('started.json', JSON.stringify({ requestId: input.requestId,
    workerPid: process.pid, browserPid: output.browserPid }), { flag: 'wx', mode: 0o600 });
  browser = await chromium.connect(server.wsEndpoint(), { timeout: remaining() });
  output.browserVersion = browser.version();
  check(output.browserVersion === CHROMIUM_VERSION, 'runtime_mismatch');
  context = await browser.newContext({
    javaScriptEnabled: false, serviceWorkers: 'block', acceptDownloads: false,
    ignoreHTTPSErrors: false, permissions: [],
  });
  await context.routeWebSocket(/.*/, socket => socket.close());
  let page;
  await context.route('**/*', route => routeDocument(route, page));
  page = await context.newPage();
  context.on('page', popup => { void popup.close().catch(() => {}); });
  page.on('dialog', dialog => { void dialog.dismiss().catch(() => {}); });
  page.on('download', download => {
    mainError = 'download_rejected';
    void download.cancel().catch(() => {});
  });
  try {
    await page.goto(input.url, { waitUntil: 'domcontentloaded', timeout: remaining() });
  } catch {
    throw new Error(mainError ?? (Date.now() >= deadline ? 'deadline_exceeded' : 'navigation_failed'));
  }
  check(!mainError, mainError);
  output.finalUrl = requestUrl(page.url(), input.origins);
  check(output.finalUrl === input.url, 'origin_denied');
  const title = await page.title();
  check(Array.from(title).length <= 512, 'output_limited');
  const text = await page.locator('body').innerText({ timeout: remaining() });
  output.title = title;
  Object.assign(output, limitedText(text, input.maxTextChars));
  check(!mainError, mainError);
  check(requestUrl(page.url(), input.origins) === input.url, 'navigation_failed');
  remaining();
  output.success = true;
}

try {
  await run();
} catch (error) {
  output.errorCode = safeError(error);
} finally {
  await closeOwned();
  if (!output.cleanupConfirmed) output.errorCode = 'cleanup_failed';
  if (output.errorCode) output.success = false;
  process.stdout.write(JSON.stringify(output) + '\n');
  process.exitCode = output.success ? 0 : 1;
}
