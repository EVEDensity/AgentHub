import * as vscode from "vscode";
import { AgentHubClient, MissionSummary } from "./agenthubClient";
import { ChatPanel } from "./chatPanel";
import { MissionTreeProvider } from "./missionTree";

let client: AgentHubClient;
let chatPanel: ChatPanel | undefined;
let missionTreeProvider: MissionTreeProvider | undefined;

export function activate(context: vscode.ExtensionContext) {
  const serverUrl =
    vscode.workspace.getConfiguration("agenthub").get<string>("serverUrl") ??
    "http://localhost:8000";
  const apiKey =
    vscode.workspace.getConfiguration("agenthub").get<string>("apiKey") ?? "";

  client = new AgentHubClient(serverUrl, apiKey);
  missionTreeProvider = new MissionTreeProvider(client);

  // Sidebar mission tree
  const treeView = vscode.window.createTreeView("agenthub.missions", {
    treeDataProvider: missionTreeProvider,
    showCollapseAll: true,
  });

  // Chat webview panel
  chatPanel = new ChatPanel(context.extensionUri, client);

  // ── Commands ──────────────────────────────────────────────────────────

  context.subscriptions.push(
    vscode.commands.registerCommand("agenthub.openChat", () => {
      chatPanel?.show();
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("agenthub.createMission", async () => {
      const objective = await vscode.window.showInputBox({
        prompt: "What should the Agent do?",
        placeHolder: "e.g. Fix the failing login test",
        validateInput: (v) => (v && v.trim().length > 0 ? undefined : "Mission objective required"),
      });
      if (!objective) return;
      try {
        const result = await client.createMission(objective.trim());
        vscode.window.showInformationMessage(
          `Mission ${result.missionId} created — agent ${result.assignedAgentId} is on it`
        );
        chatPanel?.show();
        chatPanel?.attachMission(result.missionId);
        missionTreeProvider?.refresh();
      } catch (err) {
        vscode.window.showErrorMessage(
          `Failed to create mission: ${(err as Error).message}`
        );
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("agenthub.refreshMissions", () => {
      missionTreeProvider?.refresh();
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("agenthub.sendSelection", async () => {
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
      chatPanel?.sendPromptWithContext(
        `Review this ${editor.document.languageId} and suggest improvements.`,
        selection
      );
    })
  );

  // Open chat on activate so users see it immediately
  chatPanel.show();
}

export function deactivate() {
  chatPanel?.dispose();
}
