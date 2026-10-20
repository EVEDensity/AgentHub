import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

import { classifyProductLogin, debugEndpoint, loginChildEnvironment, productLoginReport, submitProductLogin } from '../webview2-login-support.mjs';

const digest = 'a'.repeat(64);
const desktop = dirname(fileURLToPath(new URL('../webview2-login-support.mjs', import.meta.url)));

function identity() {
  return {
    status: 'passed', executableSha256: digest, nativeWebView2: true, packagedApplication: true,
    realLoginVerified: false, sharedUiVerified: false, realServicesVerified: false,
    chineseInputVerified: false, zoomVerified: false, shutdownVerified: false,
    debugEndpoint: null, webViewPids: [40],
  };
}

function page({ token = null, status = 200 } = {}) {
  const calls = [];
  return {
    calls,
    evaluate: async fn => {
      if (Function.prototype.toString.call(fn).includes('setItem')) throw new Error('token injection');
      calls.push('read-token');
      return token;
    },
    getByLabel: name => ({ fill: async value => { calls.push(['fill', name, value]); } }),
    getByRole: (_role, options) => ({ click: async () => { calls.push(['click', options.name]); } }),
    waitForResponse: predicate => {
      const response = {
        url: () => 'http://127.0.0.1:28004/api/auth/login?unused=no',
        status: () => status,
        request: () => ({ method: () => 'POST' }),
      };
      return Promise.resolve().then(() => {
        if (!predicate(response)) throw new Error('login response did not match');
        return response;
      });
    },
  };
}

test('debugging is loopback and replaces inherited browser arguments', () => {
  assert.equal(debugEndpoint(9222), 'http://127.0.0.1:9222');
  assert.throws(() => debugEndpoint(80), /1024/);
  const env = loginChildEnvironment({ WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: '--remote-debugging-port=0.0.0.0:9222', PATH: 'kept' }, 9333);
  assert.equal(env.WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS, '--remote-debugging-port=9333 --remote-debugging-address=127.0.0.1');
  assert.equal(env.PATH, 'kept');
});

test('login acceptance requires one page request and no pre-seeded token', () => {
  assert.equal(classifyProductLogin({ requests: [{ method: 'POST', path: '/api/auth/login', status: 200, initiator: 'page' }] }).verified, true);
  assert.equal(classifyProductLogin({ requests: [{ method: 'POST', path: '/api/auth/login', status: 200, initiator: 'test' }] }).verified, false);
  assert.equal(classifyProductLogin({
    requests: [{ method: 'POST', path: '/api/auth/login', status: 200, initiator: 'page' }],
    tokenWrites: [{ key: 'agenthub_token', source: 'test', beforeLogin: true }],
  }).verified, false);
  assert.equal(classifyProductLogin({
    requests: [{ method: 'POST', path: '/api/auth/login', status: 200, initiator: 'page' }],
    tokenWrites: [{ key: 'agenthub_token', source: 'page', beforeLogin: false }],
  }).verified, true);
});

test('the login interaction uses the form and refuses a stored token', async () => {
  const signedOut = page();
  const result = await submitProductLogin(signedOut, { name: 'ada', password: 'secret' });
  assert.equal(result.verified, true);
  assert.deepEqual(signedOut.calls, ['read-token', ['fill', 'Username', 'ada'], ['fill', 'Password', 'secret'], ['click', 'Login']]);
  const signedIn = page({ token: 'already-present' });
  await assert.rejects(submitProductLogin(signedIn, { name: 'ada', password: 'secret' }), /pre-seeded/);
  assert.deepEqual(signedIn.calls, ['read-token']);
});

test('a login report stays bound to the passed identity and leaves shared UI false', () => {
  const report = productLoginReport(identity(), {
    executableSha256: digest,
    debugEndpoint: 'http://127.0.0.1:9333',
    requests: [{ method: 'POST', path: '/api/auth/login', status: 200, initiator: 'page' }],
  });
  assert.equal(report.realLoginVerified, true);
  assert.equal(report.sharedUiVerified, false);
  assert.throws(() => productLoginReport({ ...identity(), status: 'failed' }, {
    executableSha256: digest, debugEndpoint: 'http://127.0.0.1:9333', requests: [],
  }), /passed WebView2/);
  assert.throws(() => productLoginReport(identity(), {
    executableSha256: 'b'.repeat(64), debugEndpoint: 'http://127.0.0.1:9333',
    requests: [{ method: 'POST', path: '/api/auth/login', status: 200, initiator: 'page' }],
  }), /does not match/);
});

test('login tooling does not write the admin token', async () => {
  const source = await readFile(join(desktop, 'webview2-login-support.mjs'), 'utf8');
  assert.equal(source.includes('setItem'), false);
});
