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
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = __importStar(require("vscode"));
const agenthubClient_1 = require("./agenthubClient");
const chatPanel_1 = require("./chatPanel");
const missionTree_1 = require("./missionTree");
let client;
let chatPanel;
let missionTreeProvider;
function activate(context) {
    const serverUrl = vscode.workspace.getConfiguration("agenthub").get("serverUrl") ??
        "http://localhost:8000";
    const apiKey = vscode.workspace.getConfiguration("agenthub").get("apiKey") ?? "";
    client = new agenthubClient_1.AgentHubClient(serverUrl, apiKey);
    missionTreeProvider = new missionTree_1.MissionTreeProvider(client);
    // Sidebar mission tree
    const treeView = vscode.window.createTreeView("agenthub.missions", {
        treeDataProvider: missionTreeProvider,
        showCollapseAll: true,
    });
    // Chat webview panel
    chatPanel = new chatPanel_1.ChatPanel(context.extensionUri, client);
    // ── Commands ──────────────────────────────────────────────────────────
    context.subscriptions.push(vscode.commands.registerCommand("agenthub.openChat", () => {
        chatPanel?.show();
    }));
    context.subscriptions.push(vscode.commands.registerCommand("agenthub.createMission", async () => {
        const objective = await vscode.window.showInputBox({
            prompt: "What should the Agent do?",
            placeHolder: "e.g. Fix the failing login test",
            validateInput: (v) => (v && v.trim().length > 0 ? undefined : "Mission objective required"),
        });
        if (!objective)
            return;
        try {
            const result = await client.createMission(objective.trim());
            vscode.window.showInformationMessage(`Mission ${result.missionId} created — agent ${result.assignedAgentId} is on it`);
            chatPanel?.show();
            chatPanel?.attachMission(result.missionId);
            missionTreeProvider?.refresh();
        }
        catch (err) {
            vscode.window.showErrorMessage(`Failed to create mission: ${err.message}`);
        }
    }));
    context.subscriptions.push(vscode.commands.registerCommand("agenthub.refreshMissions", () => {
        missionTreeProvider?.refresh();
    }));
    context.subscriptions.push(vscode.commands.registerCommand("agenthub.sendSelection", async () => {
        const editor = vscode.window.activeTextEditor;
        if (!editor) {
            vscode.window.showWarningMessage("No active editor");
            return;
        }
        const selection = editor.document.getText(editor.selection);
        if (!selection.trim()) {
            vscode.window.showWarningMessage("Selection is empty");
            return;
        }
        chatPanel?.show();
        chatPanel?.sendPromptWithContext(`Review this ${editor.document.languageId} and suggest improvements.`, selection);
    }));
    // Open chat on activate so users see it immediately
    chatPanel.show();
}
function deactivate() {
    chatPanel?.dispose();
}
//# sourceMappingURL=extension.js.map