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
exports.MissionTreeProvider = void 0;
const vscode = __importStar(require("vscode"));
/** Command shortcuts; Mission listing is not yet supported. */
class MissionTreeProvider {
    constructor(client) {
        this.client = client;
        this._onDidChangeTreeData = new vscode.EventEmitter();
        this.onDidChangeTreeData = this._onDidChangeTreeData.event;
    }
    refresh() {
        this._onDidChangeTreeData.fire();
    }
    getTreeItem(element) {
        return element;
    }
    async getChildren() {
        let healthOk = false;
        try {
            healthOk = (await this.client.health()).status === 'ok';
        }
        catch { /* Render failed probe below. */ }
        if (!healthOk) {
            return [
                new TreeItem("Server not reachable", {
                    command: "agenthub.openChat",
                    title: "Open Chat",
                }, vscode.TreeItemCollapsibleState.None, { description: "Open AgentHub chat to see connection details", tooltip: "" }),
            ];
        }
        return [
            new TreeItem("Mission listing unavailable", undefined, vscode.TreeItemCollapsibleState.None, { description: "Server reachable; execution status is shown through Mission events" }),
            new TreeItem("Create Mission…", {
                command: "agenthub.createMission",
                title: "Create Mission",
            }, vscode.TreeItemCollapsibleState.None, { description: "Submit a new Mission", tooltip: "" }),
            new TreeItem("Open Chat Panel", {
                command: "agenthub.openChat",
                title: "Open Chat",
            }, vscode.TreeItemCollapsibleState.None, { description: "Open the AgentHub Webview chat", tooltip: "" }),
        ];
    }
}
exports.MissionTreeProvider = MissionTreeProvider;
class TreeItem extends vscode.TreeItem {
    constructor(label, command, collapsible, meta = {}) {
        super(label, collapsible);
        this.command = command;
        this.description = meta.description;
        this.tooltip = meta.tooltip ?? label;
        this.iconPath = new vscode.ThemeIcon("play-circle");
    }
}
//# sourceMappingURL=missionTree.js.map