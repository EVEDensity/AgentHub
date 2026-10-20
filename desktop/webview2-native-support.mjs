const SHA = /^[a-f0-9]{64}$/;
const EXECUTABLE = 'agenthub-desktop.exe';
const WEBVIEW = 'msedgewebview2.exe';
const MAX_PARENT_DEPTH = 8;

export function assertApplicationDigest(actual, expected) {
  if (!SHA.test(actual) || !SHA.test(expected) || actual !== expected) {
    throw new Error('Packaged desktop executable SHA-256 does not match the selected build digest.');
  }
}

export function childEnvironment(base) {
  const env = { ...base };
  delete env.WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS;
  return env;
}

export function parseProcessSnapshot(payload) {
  const parsed = typeof payload === 'string' ? JSON.parse(payload || 'null') : payload;
  const rows = Array.isArray(parsed) ? parsed : parsed ? [parsed] : [];
  return rows.map(row => ({
    pid: Number(row.pid ?? row.ProcessId),
    parentPid: Number(row.parentPid ?? row.ParentProcessId),
    name: String(row.name ?? row.Name ?? ''),
  }));
}

export function classifyOwnedWebView({ rootPid, processes }) {
  if (!Number.isInteger(rootPid) || rootPid <= 0) return rejected('Launched process id is missing.');
  const byPid = new Map();
  for (const process of processes) {
    if (!Number.isInteger(process.pid) || process.pid <= 0 || byPid.has(process.pid)) return rejected('Process snapshot contains an invalid or duplicate id.');
    byPid.set(process.pid, process);
  }
  const root = byPid.get(rootPid);
  if (!root || root.name.toLowerCase() !== EXECUTABLE) return rejected('Launched process image does not match agenthub-desktop.exe.');
  const owned = [];
  for (const process of byPid.values()) {
    if (process.pid !== rootPid && process.name.toLowerCase() === WEBVIEW && reaches(process.pid, rootPid, byPid)) owned.push(process.pid);
  }
  if (owned.length === 0) return rejected('No msedgewebview2.exe process descends from the launched executable.');
  return { owned: true, reason: 'WebView2 parent chain reaches the launched executable.', webViewPids: owned.sort((a, b) => a - b) };
}

function reaches(pid, rootPid, byPid) {
  const seen = new Set();
  let current = pid;
  for (let depth = 0; depth < MAX_PARENT_DEPTH; depth += 1) {
    const process = byPid.get(current);
    if (!process || seen.has(current)) return false;
    seen.add(current);
    if (!Number.isInteger(process.parentPid) || process.parentPid <= 0) return false;
    if (process.parentPid === rootPid) return true;
    current = process.parentPid;
  }
  return false;
}

function rejected(reason) {
  return { owned: false, reason, webViewPids: [] };
}

export function identityAccepted(report) {
  return report.nativeWebView2 === true
    && report.packagedApplication === true
    && report.realLoginVerified === false
    && report.sharedUiVerified === false
    && report.realServicesVerified === false
    && report.chineseInputVerified === false
    && report.zoomVerified === false
    && report.shutdownVerified === false
    && report.debugEndpoint === null
    && Array.isArray(report.webViewPids)
    && report.webViewPids.length > 0;
}
