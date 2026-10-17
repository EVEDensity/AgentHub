import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { expect, it } from 'vitest';

async function rewrites(env: Record<string, string>) {
  const module = { exports: {} as { rewrites: () => Promise<Array<{ source: string; destination: string }>> } };
  runInNewContext(readFileSync(resolve('next.config.js'), 'utf8'), { module, process: { env } });
  return module.exports.rewrites();
}

it('routes Mission projections and bytes to their owner even under the Go API profile', async () => {
  const rules = await rewrites({ API_BACKEND: 'go', GO_GATEWAY_URL: 'http://gateway.test:8081', MISSION_CONTROL_URL: 'http://control.test:8000' });
  expect(rules[0]).toEqual({ source: '/api/v1/missions/:path*', destination: 'http://control.test:8000/api/v1/missions/:path*' });
  expect(rules.find(rule => rule.source === '/api/v1/chat/:path*')?.destination).toBe('http://control.test:8000/api/v1/chat/:path*');
  expect(rules.find(rule => rule.source === '/api/v1/sessions/:path*')?.destination).toBe('http://control.test:8000/api/v1/sessions/:path*');
  expect(rules.find(rule => rule.source === '/api/v1/access/:path*')?.destination).toBe('http://control.test:8000/api/v1/access/:path*');
  expect(rules.find(rule => rule.source === '/api/:path*')?.destination).toBe('http://gateway.test:8081/api/:path*');
});

it('uses the baked Desktop control-plane endpoint when no override is supplied', async () => {
  const rules = await rewrites({ API_BACKEND: 'legacy', API_BACKEND_URL: 'http://127.0.0.1:28000', GO_GATEWAY_URL: 'http://127.0.0.1:28001' });
  expect(rules[0].destination).toBe('http://127.0.0.1:28000/api/v1/missions/:path*');
});

it('pins Desktop Mission routing before the build even when a remote override is inherited', async () => {
  const script = readFileSync(resolve('../desktop/local-services/build-windows.ps1'), 'utf8');
  const beforeBuild = script.split('npm.cmd run build')[0];
  const assignments = [...beforeBuild.matchAll(/\$env:(API_BACKEND|API_BACKEND_URL|GO_GATEWAY_URL|MISSION_CONTROL_URL) = '([^']+)'/g)];
  const env = { MISSION_CONTROL_URL: 'https://foreign.test', ...Object.fromEntries(assignments.map(match => [match[1], match[2]])) };
  expect((await rewrites(env))[0].destination).toBe('http://127.0.0.1:28000/api/v1/missions/:path*');
  expect(script).toContain('$previousMissionControlUrl = $env:MISSION_CONTROL_URL');
  expect(script).toContain('$env:MISSION_CONTROL_URL = $previousMissionControlUrl');
});
