"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.ChatPanel = void 0;
const vscode = __importStar(require("vscode"));
const chatHtml_1 = require("./chatHtml");
/** One persistent webview panel for AgentHub chat.
 *
 * The panel lives as long as the extension is active; we reuse a single
 * panel and just attach/detach the current Mission event stream.
 */
class ChatPanel {
    constructor(extensionUri, client) {
        this.extensionUri = extensionUri;
        this.submitting = false;
        this.webviewReady = false;
        this.queuedMessages = [];
        this.disposables = [];
        this.client = client;
    }
    show() {
        if (this.panel) {
            this.panel.reveal(vscode.ViewColumn.Beside);
        }
        else {
            this.panel = vscode.window.createWebviewPanel("agenthub.chat", "AgentHub Chat", vscode.ViewColumn.Beside, {
                enableScripts: true,
                retainContextWhenHidden: true,
                localResourceRoots: [
                    vscode.Uri.joinPath(this.extensionUri, "media"),
                ],
            });
            this.panel.webview.onDidReceiveMessage((msg) => this._onMessage(msg), undefined, this.disposables);
            this.panel.webview.html = chatHtml_1.CHAT_HTML;
            this.panel.onDidDispose(() => {
                this.webviewReady = false;
                this.queuedMessages = [];
                this.panel = undefined;
                this._detach();
            });
        }
    }
    async attachMission(missionId) {
        this._detach();
        this.currentMissionId = missionId;
        if (!this.panel)
            this.show();
        await this._post({ type: "attached", missionId });
        try {
            this.sse = this.client.subscribeMissionEvents(missionId);
            this.sse.on("event", (evt) => {
                this._post({ type: "event", event: evt });
            });
            this.sse.on("error", (err) => {
                this._post({ type: "error", message: err.message });
            });
        }
        catch (err) {
            this._post({ type: "error", message: err.message });
        }
    }
    async submitPrompt(text) {
        if (!text.trim() || this.submitting)
            return;
        this.submitting = true;
        try {
            await this._admit(await this.client.createMission(text.trim()));
        }
        catch (err) {
            this._post({ type: 'error', message: err.message });
        }
        finally {
            this.submitting = false;
        }
    }
    async _admit(result) {
        if (result.status === 'pending') {
            this._detach();
            this.pendingId = result.pendingId;
            this._post({ type: 'pending', description: result.ruleDescription ?? 'Confirmation required' });
        }
        else {
            this.pendingId = undefined;
            await this.attachMission(result.missionId);
        }
    }
    sendPromptWithContext(prompt, codeContext) {
        if (!this.panel)
            this.show();
        this._post({ type: "user-prompt", prompt, codeContext });
    }
    dispose() {
        this._detach();
        for (const d of this.disposables)
            d.dispose();
        this.disposables = [];
        if (this.panel) {
            this.panel.dispose();
            this.panel = undefined;
        }
    }
    // ── Private ─────────────────────────────────────────────────────────
    _detach() {
        if (this.sse) {
            this.sse.close();
            this.sse = undefined;
        }
        this.currentMissionId = undefined;
    }
    _post(msg) {
        if (this.panel && this.panel.webview) {
            if (this.webviewReady)
                this.panel.webview.postMessage(msg);
            else
                this.queuedMessages.push(msg);
        }
    }
    _onMessage(msg) {
        switch (msg.command) {
            case "ready": {
                this.webviewReady = true;
                for (const message of this.queuedMessages)
                    this._post(message);
                this.queuedMessages = [];
                break;
            }
            case "createMission": {
                vscode.commands.executeCommand("agenthub.createMission");
                break;
            }
            case "sendPrompt": {
                if (typeof msg.text === 'string')
                    void this.submitPrompt(msg.text);
                break;
            }
            case "confirm": {
                if (!this.pendingId || this.submitting)
                    break;
                const pendingId = this.pendingId;
                this.submitting = true;
                this.client.confirmMission(pendingId)
                    .then(result => this._admit(result))
                    .catch(err => this._post({ type: 'error', message: err.message }))
                    .finally(() => { this.submitting = false; });
                break;
            }
            case "cancel": {
                if (this.submitting || (!this.pendingId && !this.currentMissionId))
                    break;
                this.submitting = true;
                const command = this.pendingId ? this.client.cancelPending(this.pendingId) : this.client.cancelMission(this.currentMissionId);
                command.then(() => {
                    this.pendingId = undefined;
                    this._detach();
                    this._post({ type: 'cancelled' });
                }).catch(err => this._post({ type: 'error', message: err.message }))
                    .finally(() => { this.submitting = false; });
                break;
            }
            case "checkHealth": {
                this.client
                    .health()
                    .then((h) => this._post({ type: "health", data: h }))
                    .catch((err) => this._post({ type: "health", error: err.message }));
                break;
            }
        }
    }
}
exports.ChatPanel = ChatPanel;
//# sourceMappingURL=chatPanel.js.map