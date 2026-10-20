const SHA = /^[a-f0-9]{64}$/;
const LOOPBACK = /^http:\/\/127\.0\.0\.1:\d+$/;
const HAN = /\p{Script=Han}/u;
const RESOURCE_KINDS = new Set(['script', 'style', 'font', 'worker']);

function rejected(reason) {
  return { verified: false, reason };
}

export function classifyNestedFrames(frames) {
  const admin = frames.find(frame => frame.role === 'admin');
  const artifact = frames.find(frame => frame.role === 'artifact');
  if (!admin || !artifact) return rejected('Shared UI requires the admin frame and its artifact frame.');
  let adminUrl;
  let artifactUrl;
  try {
    adminUrl = new URL(admin.url);
    artifactUrl = new URL(artifact.url);
  } catch {
    return rejected('Frame URL is not absolute.');
  }
  if (adminUrl.protocol !== 'http:' || adminUrl.hostname !== '127.0.0.1' || adminUrl.pathname !== '/admin') return rejected('Admin frame must be the loopback /admin page.');
  if (artifact.parentRole !== 'admin' || artifactUrl.origin !== adminUrl.origin || artifactUrl.pathname !== '/artifact-code-frame.html') return rejected('Artifact frame must stay on the admin origin.');
  if (artifact.readOnly !== true || artifact.sandbox !== 'allow-scripts allow-same-origin') return rejected('Artifact frame must remain the read-only local preview.');
  return { verified: true, reason: 'Admin and read-only artifact frames share the loopback origin.', origin: adminUrl.origin };
}

export function classifyLocalResources(resources, origin) {
  if (!Array.isArray(resources) || resources.length === 0) return rejected('Local resource observation is empty.');
  const kinds = new Set();
  for (const resource of resources) {
    if (!RESOURCE_KINDS.has(resource.kind)) return rejected('Resource kind is outside script, style, font, and worker.');
    let url;
    try { url = new URL(resource.url); } catch { return rejected('Resource URL is not absolute.'); }
    if (url.origin !== origin || url.protocol !== 'http:' || url.hostname !== '127.0.0.1') return rejected('Shared UI resource left the loopback admin origin.');
    kinds.add(resource.kind);
  }
  if (!kinds.has('script') || !kinds.has('worker')) return rejected('Local script and worker resources were not both observed.');
  return { verified: true, reason: 'Observed UI resources stayed on the loopback admin origin.' };
}

export function classifyChineseInput(input) {
  if (input?.method !== 'keyboard' || typeof input.value !== 'string' || !HAN.test(input.value) || input.value !== input.readBack) {
    return rejected('Chinese input must be entered through the keyboard and read back unchanged.');
  }
  return { verified: true, reason: 'Keyboard entry of Chinese text matched the input value.' };
}

export function classifyZoom(zoom) {
  if (zoom?.method !== 'keyboard' || !Number.isFinite(zoom.before) || !Number.isFinite(zoom.after)) return rejected('Zoom must be a keyboard change with finite scale factors.');
  if (!(zoom.after > zoom.before) || zoom.before < 0.5 || zoom.after > 3) return rejected('Zoom change is outside the accepted scale bounds.');
  return { verified: true, reason: 'Keyboard zoom increased the observed scale.' };
}

export function classifyShutdown({ rootPid, processes }) {
  if (!Number.isInteger(rootPid) || rootPid <= 0) return rejected('Shutdown requires the launched process id.');
  if (processes.some(process => process.pid === rootPid)) return rejected('Launched desktop process is still running.');
  const leftover = processes.some(process => String(process.name).toLowerCase() === 'msedgewebview2.exe' && parentReaches(process.pid, rootPid, processes));
  if (leftover) return rejected('A WebView2 process still descends from the launched executable.');
  return { verified: true, reason: 'Launched process and its WebView2 children are gone.' };
}

function parentReaches(pid, rootPid, processes) {
  const byPid = new Map(processes.map(process => [process.pid, process]));
  const seen = new Set();
  let current = pid;
  for (let depth = 0; depth < 8; depth += 1) {
    const process = byPid.get(current);
    if (!process || seen.has(current)) return false;
    seen.add(current);
    if (!Number.isInteger(process.parentPid) || process.parentPid <= 0) return false;
    if (process.parentPid === rootPid) return true;
    current = process.parentPid;
  }
  return false;
}

export async function readNestedFrames(shell) {
  const admin = shell.locator('#admin-frame');
  const adminUrl = await admin.getAttribute('src');
  const artifact = admin.contentFrame().locator('iframe[src="/artifact-code-frame.html"]');
  const sandbox = await artifact.getAttribute('sandbox');
  const title = await artifact.getAttribute('title');
  return [
    { role: 'admin', url: adminUrl },
    {
      role: 'artifact', parentRole: 'admin', sandbox, readOnly: typeof title === 'string' && title.includes('只读'),
      url: new URL('/artifact-code-frame.html', adminUrl).href,
    },
  ];
}

export async function observeChineseInput(locator) {
  const value = '中文输入';
  await locator.pressSequentially(value);
  return { method: 'keyboard', value, readBack: await locator.inputValue() };
}

function assertPassedLogin(login) {
  if (login?.status !== 'passed' || login.scope !== 'desktop-webview2-product-login' || login.realLoginVerified !== true) {
    throw new Error('Shared UI requires a passed product-login report.');
  }
  if (!SHA.test(login.executableSha256) || !LOOPBACK.test(login.debugEndpoint)) throw new Error('Login report is missing its executable digest or loopback endpoint.');
  if (login.sharedUiVerified !== false || login.realServicesVerified !== false) throw new Error('Login report already claimed a later UI-4c check.');
}

export function sharedSurfaceReport(login, observation) {
  assertPassedLogin(login);
  if (observation.executableSha256 !== login.executableSha256) throw new Error('Shared UI observation does not match the logged-in executable.');
  const frames = classifyNestedFrames(observation.frames);
  if (!frames.verified) throw new Error(frames.reason);
  const resources = classifyLocalResources(observation.resources, frames.origin);
  if (!resources.verified) throw new Error(resources.reason);
  const input = classifyChineseInput(observation.input);
  if (!input.verified) throw new Error(input.reason);
  const zoom = classifyZoom(observation.zoom);
  if (!zoom.verified) throw new Error(zoom.reason);
  const shutdown = classifyShutdown(observation.shutdown);
  if (!shutdown.verified) throw new Error(shutdown.reason);
  return {
    schemaVersion: 1, scope: 'desktop-webview2-shared-surface', status: 'passed',
    executableSha256: login.executableSha256, debugEndpoint: login.debugEndpoint,
    nativeWebView2: true, packagedApplication: true, realLoginVerified: true,
    sharedUiVerified: true, realServicesVerified: false, chineseInputVerified: true,
    zoomVerified: true, shutdownVerified: true,
    checks: [frames.reason, resources.reason, input.reason, zoom.reason, shutdown.reason],
  };
}
