import { readFile, realpath, stat } from 'node:fs/promises';
import { createServer } from 'node:http';
import { dirname, isAbsolute, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const directory = dirname(fileURLToPath(import.meta.url));
const assets = new Map([
  ['/', ['index.html', 'text/html; charset=utf-8']],
  ['/index.html', ['index.html', 'text/html; charset=utf-8']],
  ['/main.js', ['main.js', 'text/javascript; charset=utf-8']],
  ['/default-contract.js', ['default-contract.js', 'text/javascript; charset=utf-8']],
  ['/styles.css', ['styles.css', 'text/css; charset=utf-8']],
  ['/favicon.ico', ['favicon.ico', 'image/x-icon']],
]);
const previewCsp = "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; connect-src 'none'; frame-src 'none'; worker-src 'none'; base-uri 'none'; form-action 'none'";

export function parseArgs(argv) {
  const options = { port: 18765, outputDirectory: resolve(directory, '..', 'output', 'playwright') };
  const seen = new Set();
  for (let index = 0; index < argv.length; index += 2) {
    const flag = argv[index];
    if (!['--port', '--output-directory'].includes(flag) || seen.has(flag)) throw new Error(`Unknown or duplicate argument: ${flag}`);
    seen.add(flag);
    const value = argv[index + 1];
    if (!value?.trim() || value.startsWith('--')) throw new Error(`Missing value for ${flag}`);
    if (flag === '--port') {
      if (!/^\d+$/.test(value) || Number(value) > 65535) throw new Error('Port must be an integer between 0 and 65535.');
      options.port = Number(value);
    } else options.outputDirectory = resolve(value);
  }
  return options;
}

export function isAllowedRequest(url, origin) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'http:' && parsed.origin === origin && !parsed.username && !parsed.password && !parsed.hash && assets.has(parsed.pathname);
  } catch { return false; }
}

/** Fixed public assets only; this is not a general filesystem or API server. */
export async function startStaticServer({ port = 18765, uiDirectory = resolve(directory, 'ui') } = {}) {
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('Port must be an integer between 0 and 65535.');
  const root = await realpath(uiDirectory);
  const server = createServer(async (request, response) => {
    response.setHeader('Content-Security-Policy', previewCsp);
    response.setHeader('Cache-Control', 'no-store');
    response.setHeader('X-Content-Type-Options', 'nosniff');
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      response.writeHead(405, { Allow: 'GET, HEAD' }); response.end(); return;
    }
    try {
      const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
      const asset = assets.get(pathname);
      if (!asset) { response.writeHead(404); response.end(); return; }
      const path = await realpath(resolve(root, asset[0]));
      const inside = relative(root, path);
      if (inside.startsWith('..') || isAbsolute(inside) || !(await stat(path)).isFile()) {
        response.writeHead(404); response.end(); return;
      }
      const bytes = await readFile(path);
      response.writeHead(200, { 'Content-Type': asset[1], 'Content-Length': bytes.length });
      response.end(request.method === 'HEAD' ? undefined : bytes);
    } catch {
      response.writeHead(404); response.end();
    }
  });
  await new Promise((ready, reject) => {
    server.once('error', reject);
    server.listen({ host: '127.0.0.1', port, exclusive: true }, () => { server.removeListener('error', reject); ready(); });
  });
  const origin = `http://127.0.0.1:${server.address().port}`;
  let closing;
  return { origin, close: () => {
    if (!closing) closing = new Promise((done, reject) => {
      server.close(error => error ? reject(error) : done());
      server.closeAllConnections();
    });
    return closing;
  } };
}

/** Every successful return includes successful cleanup of the owned resources. */
export async function withBrowserContext(launchBrowser, operation) {
  const browser = await launchBrowser();
  let context;
  try {
    context = await browser.newContext({ viewport: { width: 1120, height: 760 }, serviceWorkers: 'block', acceptDownloads: false });
    return await operation(context, browser);
  } finally {
    try { await context?.close(); } finally { await browser.close(); }
  }
}
