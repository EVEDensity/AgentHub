const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { EventEmitter } = require('node:events');
const { AgentHubClient, SSESubscription } = require('../out/agenthubClient');

async function serve(t, handler) {
  const server = http.createServer(handler);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  return `http://127.0.0.1:${server.address().port}`;
}

test('client sends the exact admission, pending confirmation and cancellation contracts', async t => {
  const requests = [];
  const url = await serve(t, (req, res) => {
    let raw = '';
    req.on('data', chunk => { raw += chunk; });
    req.on('end', () => {
      requests.push({ path: req.url, body: JSON.parse(raw || '{}'), authorization: req.headers.authorization });
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify(req.url.endsWith('/mission')
        ? { status: 'pending', pendingId: 'pending-1' }
        : req.url.endsWith('/confirm')
          ? { missionId: 'mission-1', streamUrl: '/api/v1/missions/mission-1/events/stream', sessionId: 'session-1' }
          : { status: 'cancelled' }));
    });
  });
  const client = new AgentHubClient(url, 'test-token', 'workspace-1');
  assert.deepEqual(await client.createMission('inspect'), { status: 'pending', pendingId: 'pending-1', ruleDescription: undefined });
  assert.equal((await client.confirmMission('pending-1')).missionId, 'mission-1');
  await client.cancelPending('pending-1');
  await client.cancelMission('mission/1');
  assert.deepEqual(requests.map(({ path, body }) => ({ path, body })), [
    { path: '/api/v1/chat/mission', body: { message: 'inspect', workspaceId: 'workspace-1', stream: true } },
    { path: '/api/v1/chat/confirm', body: { pendingId: 'pending-1' } },
    { path: '/api/v1/chat/cancel', body: { pendingId: 'pending-1' } },
    { path: '/api/v1/missions/mission%2F1/cancel', body: {} },
  ]);
  assert.ok(requests.every(req => req.authorization === 'Bearer test-token'));
});

test('an HTTP command error is never treated as admission', async t => {
  const url = await serve(t, (_req, res) => { res.writeHead(422); res.end('unsupported'); });
  await assert.rejects(new AgentHubClient(url).createMission('inspect'), /422/);
});

test('an unauthorized SSE response produces an error and no event', async t => {
  const url = await serve(t, (_req, res) => { res.writeHead(401); res.end('unauthorized'); });
  const sub = new SSESubscription(url, '');
  let events = 0;
  sub.on('event', () => { events++; });
  const error = new Promise(resolve => sub.once('error', resolve));
  sub.connect();
  assert.match((await error).message, /HTTP 401/);
  sub.close();
  assert.equal(events, 0);
});

test('interrupted rejected SSE responses are handled once without reconnecting', async t => {
  for (const status of [401, 200]) {
    await t.test(`rejected response HTTP ${status}`, async t => {
      const url = await serve(t, (_req, res) => {
        res.writeHead(status, { 'Content-Type': 'text/plain' });
        res.write('rejected response body');
        setImmediate(() => res.destroy());
      });
      const sub = new SSESubscription(url, '');
      const errors = [];
      let events = 0;
      sub.on('error', error => errors.push(error));
      sub.on('event', () => { events++; });
      sub.connect();
      await new Promise(resolve => sub.req.once('close', resolve));
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(errors.length, 1);
      assert.match(errors[0].message, new RegExp(`HTTP ${status}`));
      assert.equal(events, 0);
      assert.equal(sub.reconnectTimer, undefined);
      sub.close();
    });
  }
});

test('a disconnected HTTP response rejects the command and permits a retry', async t => {
  let attempts = 0;
  const url = await serve(t, (_req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    if (++attempts === 1) {
      res.write('{"status":');
      setImmediate(() => res.destroy());
    } else {
      res.end(JSON.stringify({ status: 'pending', pendingId: 'retry-pending' }));
    }
  });
  const { panel, messages } = panelFor(new AgentHubClient(url));
  await panel.submitPrompt('inspect');
  assert.equal(messages.at(-1).type, 'error');
  assert.match(messages.at(-1).message, /interrupted|aborted/);
  assert.equal(panel.submitting, false);
  await panel.submitPrompt('inspect');
  assert.equal(panel.pendingId, 'retry-pending');
  assert.equal(attempts, 2);
});

test('a server that never completes a command is bounded by the HTTP deadline', async t => {
  let response;
  const url = await serve(t, (_req, res) => { response = res; });
  t.after(() => response?.destroy());
  const client = new AgentHubClient(url, '', 'workspace-1', 40);
  await assert.rejects(client.createMission('inspect'), /timed out/);
});

test('a malformed successful HTTP response cannot acknowledge cancellation', async t => {
  const url = await serve(t, (_req, res) => { res.end('<html>upstream unavailable</html>'); });
  await assert.rejects(new AgentHubClient(url).cancelMission('mission-1'), /invalid JSON/);
});

test('an interrupted SSE response emits one error and never fabricates completion', async t => {
  const url = await serve(t, (_req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write('data: {"type":"mission.started"}\n\n');
    setImmediate(() => res.destroy());
  });
  const sub = new SSESubscription(url, '');
  const errors = [];
  const events = [];
  const disconnected = new Promise(resolve => sub.once('error', resolve));
  sub.on('error', error => errors.push(error));
  sub.on('event', event => events.push(event));
  sub.connect();
  await disconnected;
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(errors.length, 1);
  assert.match(errors[0].message, /interrupted|aborted/);
  assert.deepEqual(events.map(event => event.type), ['mission.started']);
  assert.ok(sub.reconnectTimer, 'a dropped response must schedule reconnection');
  sub.close();
});

test('SSE EOF reports connection loss without manufacturing a terminal event', async t => {
  const url = await serve(t, (_req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.end('data: {"type":"work_unit.running"}\n\n');
  });
  const sub = new SSESubscription(url, '');
  const events = [];
  sub.on('event', event => events.push(event));
  const disconnected = new Promise(resolve => sub.once('error', resolve));
  sub.connect();
  assert.match((await disconnected).message, /connection closed/);
  sub.close();
  assert.deepEqual(events.map(event => event.type), ['work_unit.running']);
});

// Load the extension adapter with only the VS Code surface supplied by its host.
const Module = require('node:module');
const originalLoad = Module._load;
Module._load = function (name, ...args) {
  if (name === 'vscode') return { window: {}, commands: {}, Uri: {} };
  return originalLoad.call(this, name, ...args);
};
const { ChatPanel } = require('../out/chatPanel');
Module._load = originalLoad;
const tick = () => new Promise(resolve => setImmediate(resolve));
function panelFor(client) {
  const panel = new ChatPanel({}, client);
  const messages = [];
  panel.webviewReady = true;
  panel.panel = { webview: { postMessage: msg => messages.push(msg) } };
  return { panel, messages };
}

test('webview Send submits, waits for pending confirmation, then attaches the accepted Mission', async () => {
  const calls = [];
  const sub = new EventEmitter(); sub.close = () => {};
  const { panel, messages } = panelFor({
    createMission: async text => { calls.push(['create', text]); return { status: 'pending', pendingId: 'pending-1' }; },
    confirmMission: async id => { calls.push(['confirm', id]); return { status: 'accepted', missionId: 'mission-1' }; },
    subscribeMissionEvents: id => { calls.push(['subscribe', id]); return sub; },
    cancelMission: async id => { calls.push(['cancel', id]); },
  });
  panel._onMessage({ command: 'sendPrompt', text: 'inspect' });
  await tick();
  assert.deepEqual(calls, [['create', 'inspect']]);
  assert.equal(messages.at(-1).type, 'pending');
  panel._onMessage({ command: 'confirm', pendingId: 'untrusted-other-id' });
  await tick();
  assert.deepEqual(calls.slice(1), [['confirm', 'pending-1'], ['subscribe', 'mission-1']]);
  panel._onMessage({ command: 'cancel' });
  await tick();
  assert.deepEqual(calls.at(-1), ['cancel', 'mission-1']);
  assert.equal(messages.at(-1).type, 'cancelled');
});

test('failed cancellation keeps the pending command available for retry', async () => {
  let attempts = 0;
  const { panel, messages } = panelFor({
    createMission: async () => ({ status: 'pending', pendingId: 'pending-1' }),
    cancelPending: async id => { assert.equal(id, 'pending-1'); if (++attempts === 1) throw Error('offline'); },
  });
  await panel.submitPrompt('inspect');
  panel._onMessage({ command: 'cancel' }); await tick();
  assert.equal(messages.at(-1).type, 'error');
  assert.equal(panel.pendingId, 'pending-1');
  panel._onMessage({ command: 'cancel' }); await tick();
  assert.equal(messages.at(-1).type, 'cancelled');
});

test('the webview script parses and provides explicit error and draft rendering', () => {
  const { CHAT_HTML } = require('../out/chatHtml');
  const script = CHAT_HTML.match(/<script>([\s\S]*)<\/script>/)[1];
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { style: {}, value: '', textContent: '', addEventListener() {} });
    return elements.get(id);
  };
  let onMessage;
  const vm = require('node:vm');
  vm.runInNewContext(script, {
    acquireVsCodeApi: () => ({ postMessage() {} }),
    document: { getElementById: element },
    window: { addEventListener: (_name, handler) => { onMessage = handler; } },
    setInterval() {},
  });
  onMessage({ data: { type: 'user-prompt', prompt: 'review', codeContext: 'const x = 1;' } });
  assert.equal(element('prompt').value, 'review\n\nconst x = 1;');
  onMessage({ data: { type: 'health', error: 'offline' } });
  assert.equal(element('status').textContent, 'server unavailable');
});

test('initial selection and admission messages wait for the webview ready handshake', () => {
  const { panel, messages } = panelFor({});
  panel.webviewReady = false;
  panel._post({ type: 'user-prompt', prompt: 'review' });
  assert.equal(messages.length, 0);
  panel._onMessage({ command: 'ready' });
  assert.deepEqual(messages, [{ type: 'user-prompt', prompt: 'review' }]);
});
