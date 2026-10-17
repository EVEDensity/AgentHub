"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.CHAT_HTML = void 0;
// Static browser projection; command handling stays in ChatPanel.
exports.CHAT_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AgentHub Chat</title>
  <style>
    body {
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
      color: var(--vscode-foreground);
      background: var(--vscode-editor-background);
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      height: 100vh;
    }
    .header {
      padding: 10px 14px;
      border-bottom: 1px solid var(--vscode-panel-border);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .header h2 { margin: 0; font-size: 13px; font-weight: 600; }
    .status {
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 10px;
      background: var(--vscode-badge-background);
      color: var(--vscode-badge-foreground);
    }
    .status.ok   { background: #2ea043; color: white; }
    .status.err  { background: #da3633; color: white; }

    #events {
      flex: 1;
      overflow-y: auto;
      padding: 10px 14px;
    }
    .event {
      margin-bottom: 10px;
      padding: 8px 10px;
      border-radius: 6px;
      border-left: 3px solid var(--vscode-panel-border);
      background: var(--vscode-textCodeBlock-background);
      font-size: 12.5px;
      line-height: 1.5;
    }
    .event.message       { border-left-color: #58a6ff; }
    .event.artifact      { border-left-color: #a371f7; }
    .event.evidence      { border-left-color: #3fb950; }
    .event.terminal      { border-left-color: #f85149; }
    .event.workunit      { border-left-color: #d29922; }
    .event.mission       { border-left-color: #58a6ff; }
    .event.system        { border-left-color: #8b949e; font-style: italic; opacity: 0.85; }

    .actor { font-weight: 600; margin-right: 6px; }
    .ts    { font-size: 10px; opacity: 0.55; float: right; }
    .content { white-space: pre-wrap; word-wrap: break-word; margin-top: 4px; }
    .payload { font-family: var(--vscode-editor-font-family); font-size: 11px; color: var(--vscode-descriptionForeground); margin-top: 4px; }

    .composer {
      border-top: 1px solid var(--vscode-panel-border);
      padding: 10px 14px;
      display: flex;
      gap: 8px;
    }
    .composer textarea {
      flex: 1;
      min-height: 50px;
      max-height: 150px;
      resize: vertical;
      font-family: inherit;
      font-size: inherit;
      background: var(--vscode-input-background);
      color: var(--vscode-input-foreground);
      border: 1px solid var(--vscode-input-border);
      border-radius: 4px;
      padding: 6px 8px;
    }
    .composer button {
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
      border: none;
      border-radius: 4px;
      padding: 0 14px;
      cursor: pointer;
      font-size: 12px;
    }
    .composer button:hover { background: var(--vscode-button-hoverBackground); }

    .empty {
      text-align: center;
      padding: 40px 20px;
      opacity: 0.7;
    }
    .empty button {
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
      border: none;
      border-radius: 4px;
      padding: 8px 16px;
      cursor: pointer;
      margin-top: 12px;
    }
  </style>
</head>
<body>
  <div class="header">
    <h2>AgentHub</h2>
    <span id="status" class="status">connecting…</span>
  </div>

  <div id="events">
    <div class="empty" id="empty">
      <div>No active Mission yet.</div>
      <button onclick="createMission()">Create Mission…</button>
      <div style="margin-top:18px;font-size:11px;opacity:0.7">
        Tip: right-click any code selection → <code>Send Selection to Chat</code>
      </div>
    </div>
  </div>

  <div id="commands" style="padding: 8px 14px; display: none">
    <span id="confirmation"></span>
    <button id="confirm" onclick="confirmBtn()">Confirm</button>
    <button onclick="cancelBtn()">Cancel</button>
  </div>
  <div class="composer">
    <textarea id="prompt" placeholder="Type @agent to wake up an agent, e.g. @archivist summarise my diff..."></textarea>
    <button onclick="sendPrompt()">Send</button>
  </div>

  <script>
    const vscode = acquireVsCodeApi();
    const eventsEl = document.getElementById('events');
    const statusEl = document.getElementById('status');
    const emptyEl  = document.getElementById('empty');

    function setStatus(text, cls='') {
      statusEl.textContent = text;
      statusEl.className = 'status ' + cls;
    }

    function createMission() { vscode.postMessage({ command: 'createMission' }); }
    function confirmBtn() { vscode.postMessage({ command: 'confirm' }); }
    function cancelBtn() { vscode.postMessage({ command: 'cancel' }); }
    function sendPrompt() {
      const el = document.getElementById('prompt');
      const value = el.value.trim();
      if (!value) return;
      vscode.postMessage({ command: 'sendPrompt', text: value });

    }

    window.addEventListener('message', (e) => {
      const msg = e.data;
      if (!msg) return;

      if (msg.type === 'health') {
        setStatus(msg.data?.status === 'ok' ? 'server reachable' : 'server unavailable', msg.data?.status === 'ok' ? 'ok' : 'err');
      }
      if (msg.type === 'error') {
        setStatus('command or stream failed', 'err');
        appendEvent({ type: 'error', payload: { error: msg.message } });
      }
      if (msg.type === 'user-prompt') {
        document.getElementById('prompt').value = msg.prompt + (msg.codeContext ? '\\n\\n' + msg.codeContext : '');
      }
      if (msg.type === 'pending') {
        document.getElementById('commands').style.display = 'block';
        document.getElementById('confirm').style.display = 'inline-block';
        document.getElementById('confirmation').textContent = msg.description;
        setStatus('awaiting confirmation');
      }
      if (msg.type === 'cancelled') {
        document.getElementById('commands').style.display = 'none';
        setStatus('cancellation acknowledged');
      }
      if (msg.type === 'attached') {
        emptyEl.style.display = 'none';
        document.getElementById('prompt').value = '';
        document.getElementById('commands').style.display = 'block';
        document.getElementById('confirm').style.display = 'none';
        document.getElementById('confirmation').textContent = '';
        setStatus('mission accepted; awaiting events');
      }

      if (msg.type === 'event' && msg.event) {
        appendEvent(msg.event);
      }
    });

    function appendEvent(evt) {
      emptyEl.style.display = 'none';
      const cls = classifyEvent(evt.type);
      const div = document.createElement('div');
      div.className = 'event ' + cls;
      const ts = evt.timestamp ? new Date(evt.timestamp).toLocaleTimeString() : '';
      const actor = evt.actor ? evt.actor.displayName || evt.actor.id : '';

      div.innerHTML = \`
        <span class="ts">\${ts}</span>
        <span class="actor">\${escapeHtml(actor || classifyEvent(evt.type))}</span>
        <span>\${escapeHtml(evt.type)}</span>
        \${renderEventBody(evt)}
      \`;
      eventsEl.appendChild(div);
      eventsEl.scrollTop = eventsEl.scrollHeight;
    }

    function classifyEvent(t) {
      if (!t) return 'system';
      const lower = t.toLowerCase();
      if (lower.includes('message'))  return 'message';
      if (lower.includes('artifact')) return 'artifact';
      if (lower.includes('evidence')) return 'evidence';
      if (lower.includes('terminal') || lower.includes('completed') || lower.includes('failed') || lower.includes('cancelled')) return 'terminal';
      if (lower.includes('workunit')) return 'workunit';
      if (lower.includes('mission'))  return 'mission';
      return 'system';
    }

    function renderEventBody(evt) {
      const p = evt.payload || {};
      if (p.content) {
        return '<div class="content">' + escapeHtml(String(p.content)) + '</div>';
      }
      if (p.error) {
        return '<div class="content">' + escapeHtml(String(p.error)) + '</div>';
      }
      // Render structured payload as JSON (truncated)
      const simplified = {};
      for (const k of Object.keys(p)) {
        if (k === 'content') continue;
        const v = p[k];
        simplified[k] = typeof v === 'string' && v.length > 120 ? v.slice(0, 117) + '...' : v;
      }
      if (Object.keys(simplified).length) {
        return '<pre class="payload">' + escapeHtml(JSON.stringify(simplified, null, 2)) + '</pre>';
      }
      return '';
    }

    function escapeHtml(s) {
      if (!s) return '';
      return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    vscode.postMessage({ command: 'ready' });
    // Initial health probe
    vscode.postMessage({ command: 'checkHealth' });
    setInterval(() => vscode.postMessage({ command: 'checkHealth' }), 15000);

    document.getElementById('prompt').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendPrompt();
      }
    });
  </script>
</body>
</html>`;
//# sourceMappingURL=chatHtml.js.map