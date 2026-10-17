import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { once } from 'node:events';
import net from 'node:net';
import path from 'node:path';
import { expect, type APIRequestContext } from '@playwright/test';

const fixtureLogs = new WeakMap<ChildProcess, () => { stdout: string; stderr: string }>();
export const ownedPythonFixtureLogs = (child: ChildProcess | undefined) => child ? fixtureLogs.get(child)?.() : undefined;

export async function stopOwnedPythonFixture(child: ChildProcess | undefined): Promise<void> {
  if (!child?.pid || child.exitCode !== null || child.signalCode !== null) return;
  const closed = once(child, 'close').then(() => true);
  const waitClosed = async (milliseconds: number) => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try { return await Promise.race([closed, new Promise<false>(resolve => { timer = setTimeout(() => resolve(false), milliseconds); })]); }
    finally { if (timer) clearTimeout(timer); }
  };
  child.kill('SIGTERM');
  if (await waitClosed(12000)) return;
  if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
  if (!await waitClosed(3000)) throw new Error('Owned Python fixture did not stop');
}

export async function startOwnedPythonFixture(request: APIRequestContext, moduleName: string, workspaceId: string): Promise<ChildProcess> {
  const occupied = await new Promise<boolean>((resolve, reject) => {
    const socket = net.connect({ host: '127.0.0.1', port: 38100 });
    socket.setTimeout(2000);
    socket.once('connect', () => { socket.destroy(); resolve(true); });
    socket.once('error', (error: NodeJS.ErrnoException) => { socket.destroy(); if (error.code === 'ECONNREFUSED') resolve(false); else reject(error); });
    socket.once('timeout', () => { socket.destroy(); reject(new Error('Fixture port ownership probe timed out')); });
  });
  if (occupied) throw new Error('Refusing to use or stop an existing service on fixture port 38100');
  let python = process.env.AGENTHUB_TEST_PYTHON || 'python';
  let args = ['-m', moduleName];
  if (process.platform === 'win32') {
    const probe = spawnSync(python, ['-c', 'import json,site,sys; print(json.dumps({"executable":sys._base_executable,"sites":site.getsitepackages()}))'], { windowsHide: true, encoding: 'utf8', timeout: 10000 });
    if (probe.status !== 0 || probe.error) throw new Error('Could not identify Windows Python interpreter');
    const runtime = JSON.parse(probe.stdout) as { executable: string; sites: string[] };
    if (!path.isAbsolute(runtime.executable) || !Array.isArray(runtime.sites) || runtime.sites.some(directory => !path.isAbsolute(directory))) throw new Error('Invalid Python interpreter metadata');
    python = runtime.executable;
    // -S excludes base-install packages; load exactly the selected venv's sites.
    args = ['-S', '-c', 'import runpy,site,sys; [site.addsitedir(p) for p in sys.argv[2:]]; runpy.run_module(sys.argv[1],run_name="__main__",alter_sys=True)', moduleName, ...runtime.sites];
  }
  const child = spawn(python, args, { cwd: path.resolve(process.cwd(), '..'), windowsHide: true, env: { ...process.env, PYTHONUNBUFFERED: '1' }, stdio: ['ignore', 'pipe', 'pipe'] });
  let stdout = '';
  let stderr = '';
  let spawnError: Error | undefined;
  fixtureLogs.set(child, () => ({ stdout, stderr }));
  child.stdout?.on('data', data => { stdout = (stdout + data.toString()).slice(-16000); });
  child.stderr?.on('data', data => { stderr = (stderr + data.toString()).slice(-16000); });
  child.on('error', error => { spawnError = error; });
  try {
    await expect.poll(async () => {
      if (spawnError) throw spawnError;
      if (child.exitCode !== null || child.signalCode !== null) throw new Error(`Owned fixture exited: ${stderr}`);
      const seeded = stdout.split(/\r?\n/).some(line => {
        try { const metadata = JSON.parse(line); return metadata.workspaceId === workspaceId && metadata.port === 38100; } catch { return false; }
      });
      if (!seeded) return false;
      try {
        const response = await request.get('http://127.0.0.1:38100/fixture/health', { timeout: 1000 });
        if (!response.ok()) return false;
        const body = await response.json();
        return body.ready === true && body.fixturePid === child.pid
          && body.workspaceId === workspaceId;
      } catch { return false; }
    }, { timeout: 30000, intervals: [100, 250, 500] }).toBe(true);
    return child;
  } catch (error) {
    await stopOwnedPythonFixture(child);
    throw new Error(`Owned fixture failed readiness (PID ${child.pid}): ${String(error)}\n${stdout}\n${stderr}`);
  }
}
