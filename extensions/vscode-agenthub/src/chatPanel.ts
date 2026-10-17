import * as vscode from "vscode";
import { CHAT_HTML } from "./chatHtml";
import { AgentHubClient, SSESubscription, SessionEvent, CreateMissionResult } from "./agenthubClient";

/** One persistent webview panel for AgentHub chat.
 *
 * The panel lives as long as the extension is active; we reuse a single
 * panel and just attach/detach the current Mission event stream.
 */
export class ChatPanel implements vscode.Disposable {
  private panel: vscode.WebviewPanel | undefined;
  private client: AgentHubClient;
  private currentMissionId?: string;
  private pendingId?: string;
  private submitting = false;
  private webviewReady = false;
  private queuedMessages: unknown[] = [];
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
      this.panel.webview.onDidReceiveMessage(
        (msg) => this._onMessage(msg),
        undefined,
        this.disposables
      );
      this.panel.webview.html = CHAT_HTML;
      this.panel.onDidDispose(() => {
        this.webviewReady = false;
        this.queuedMessages = [];
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

  async submitPrompt(text: string) {
    if (!text.trim() || this.submitting) return;
    this.submitting = true;
    try { await this._admit(await this.client.createMission(text.trim())); }
    catch (err) { this._post({ type: 'error', message: (err as Error).message }); }
    finally { this.submitting = false; }
  }

  private async _admit(result: CreateMissionResult) {
    if (result.status === 'pending') {
      this._detach();
      this.pendingId = result.pendingId;
      this._post({ type: 'pending', description: result.ruleDescription ?? 'Confirmation required' });
    } else {
      this.pendingId = undefined;
      await this.attachMission(result.missionId);
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

  }

  private _post(msg: unknown) {
    if (this.panel && this.panel.webview) {
      if (this.webviewReady) this.panel.webview.postMessage(msg);
      else this.queuedMessages.push(msg);
    }
  }

  private _onMessage(msg: { command: string; [k: string]: unknown }) {
    switch (msg.command) {
      case "ready": {
        this.webviewReady = true;
        for (const message of this.queuedMessages) this._post(message);
        this.queuedMessages = [];
        break;
      }
      case "createMission": {
        vscode.commands.executeCommand("agenthub.createMission");
        break;
      }
      case "sendPrompt": {
        if (typeof msg.text === 'string') void this.submitPrompt(msg.text);
        break;
      }
      case "confirm": {
        if (!this.pendingId || this.submitting) break;
        const pendingId = this.pendingId;
        this.submitting = true;
        this.client.confirmMission(pendingId)
          .then(result => this._admit(result))
          .catch(err => this._post({ type: 'error', message: (err as Error).message }))
          .finally(() => { this.submitting = false; });
        break;
      }
      case "cancel": {
        if (this.submitting || (!this.pendingId && !this.currentMissionId)) break;
        this.submitting = true;
        const command = this.pendingId ? this.client.cancelPending(this.pendingId) : this.client.cancelMission(this.currentMissionId!);
        command.then(() => {
          this.pendingId = undefined;
          this._detach();
          this._post({ type: 'cancelled' });
        }).catch(err => this._post({ type: 'error', message: (err as Error).message }))
          .finally(() => { this.submitting = false; });
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

}
