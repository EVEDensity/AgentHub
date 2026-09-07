import * as vscode from "vscode";
import { AgentHubClient, SSESubscription, SessionEvent } from "./agenthubClient";

/** One persistent webview panel for AgentHub chat.
 *
 * The panel lives as long as the extension is active; we reuse a single
 * panel and just attach/detach the current Mission event stream.
 */
export class ChatPanel implements vscode.Disposable {
  private panel: vscode.WebviewPanel | undefined;
  private client: AgentHubClient;
  private currentMissionId?: string;
  private currentSessionId?: string;
  private sse?: SSESubscription;
  private disposables: vscode.Disposable[] = [];

  constructor(
    private extensionUri: vscode.Uri,
    client: AgentHubClient
  ) {
    this.client = client;
  }

  show() {
    if (this.panel) {
      this.panel.reveal(vscode.ViewColumn.Beside);
    } else {
      this.panel = vscode.window.createWebviewPanel(
        "agenthub.chat",
        "AgentHub Chat",
        vscode.ViewColumn.Beside,
        {
          enableScripts: true,
          retainContextWhenHidden: true,
          localResourceRoots: [
            vscode.Uri.joinPath(this.extensionUri, "media"),
          ],
        }
      );
      this.panel.webview.html = this._html();
      this.panel.webview.onDidReceiveMessage(
        (msg) => this._onMessage(msg),
        undefined,
        this.disposables
      );
      this.panel.onDidDispose(() => {
        this.panel = undefined;
        this._detach();
      });
    }
  }

  async attachMission(missionId: string) {
    this._detach();
    this.currentMissionId = missionId;
    if (!this.panel) this.show();
    await this._post({ type: "attached", missionId });
    try {
      this.sse = this.client.subscribeMissionEvents(missionId);
      this.sse.on("event", (evt: SessionEvent) => {
        this._post({ type: "event", event: evt });
      });
      this.sse.on("error", (err: Error) => {
        this._post({ type: "error", message: err.message });
      });
    } catch (err) {
      this._post({ type: "error", message: (err as Error).message });
    }
  }

  sendPromptWithContext(prompt: string, codeContext?: string) {
    if (!this.panel) this.show();
    this._post({ type: "user-prompt", prompt, codeContext });
  }

  dispose() {
    this._detach();
    for (const d of this.disposables) d.dispose();
    this.disposables = [];
    if (this.panel) {
      this.panel.dispose();
      this.panel = undefined;
    }
  }

  // ── Private ─────────────────────────────────────────────────────────

  private _detach() {
    if (this.sse) {
      this.sse.close();
      this.sse = undefined;
    }
    this.currentMissionId = undefined;
    this.currentSessionId = undefined;
  }

  private _post(msg: unknown) {
    if (this.panel && this.panel.webview) {
      this.panel.webview.postMessage(msg);
    }
  }

  private _onMessage(msg: { command: string; [k: string]: unknown }) {
    switch (msg.command) {
      case "createMission": {
        vscode.commands.executeCommand("agenthub.createMission");
        break;
      }
      case "confirm": {
        const m = msg as unknown as { missionId: string; confirmId: string };
        const { missionId, confirmId } = m;
        this.client
          .confirmMission(missionId, confirmId)
          .then(() => this._post({ type: "confirmed", confirmId }))
          .catch((err) => this._post({ type: "error", message: (err as Error).message }));
        break;
      }
      case "cancel": {
        const m = msg as unknown as { missionId: string };
        const { missionId } = m;
        this.client
          .cancelMission(missionId)
          .then(() => this._post({ type: "cancelled" }))
          .catch((err) => this._post({ type: "error", message: (err as Error).message }));
        break;
      }
      case "checkHealth": {
        this.client
          .health()
          .then((h) => this._post({ type: "health", data: h }))
          .catch((err) => this._post({ type: "health", error: (err as Error).message }));
        break;
      }
    }
  }

  // ── HTML shell ───────────────────────────────────────────────────────

  private _html(): string {
    return `<!DOCTYPE html>
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
    function confirmBtn(confirmId, missionId) {
      vscode.postMessage({ command: 'confirm', missionId, confirmId });
    }
    function cancelBtn(missionId) {
      vscode.postMessage({ command: 'cancel', missionId });
    }
    function sendPrompt() {
      const el = document.getElementById('prompt');
      const value = el.value.trim();
      if (!value) return;
      vscode.postMessage({ command: 'sendPrompt', text: value });
      el.value = '';
    }

    window.addEventListener('message', (e) => {
      const msg = e.data;
      if (!msg) return;

      if (msg.type === 'health') {
        setStatus(msg.data ? 'connected' : 'no server', msg.data ? 'ok' : 'err');
      } else if (msg.type === 'health' && msg.error) {
        setStatus('disconnected', 'err');
      }

      if (msg.type === 'attached') {
        emptyEl.style.display = 'none';
        setStatus('live', 'ok');
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
  }
}
