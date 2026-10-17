import * as vscode from "vscode";
import { AgentHubClient } from "./agenthubClient";

/** Command shortcuts; Mission listing is not yet supported. */
export class MissionTreeProvider implements vscode.TreeDataProvider<TreeItem> {
  private _onDidChangeTreeData = new vscode.EventEmitter<TreeItem | undefined | void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  constructor(private client: AgentHubClient) {}

  refresh(): void {
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(element: TreeItem): vscode.TreeItem {
    return element;
  }

  async getChildren(): Promise<vscode.TreeItem[]> {
    let healthOk = false;
    try { healthOk = (await this.client.health()).status === 'ok'; } catch { /* Render failed probe below. */ }
    if (!healthOk) {
      return [
        new TreeItem(
          "Server not reachable",
          {
            command: "agenthub.openChat",
            title: "Open Chat",
          },
          vscode.TreeItemCollapsibleState.None,
          { description: "Open AgentHub chat to see connection details", tooltip: "" }
        ),
      ];
    }
    return [
      new TreeItem("Mission listing unavailable", undefined, vscode.TreeItemCollapsibleState.None,
        { description: "Server reachable; execution status is shown through Mission events" }),
      new TreeItem(
        "Create Mission…",
        {
          command: "agenthub.createMission",
          title: "Create Mission",
        },
        vscode.TreeItemCollapsibleState.None,
        { description: "Submit a new Mission", tooltip: "" }
      ),
      new TreeItem(
        "Open Chat Panel",
        {
          command: "agenthub.openChat",
          title: "Open Chat",
        },
        vscode.TreeItemCollapsibleState.None,
        { description: "Open the AgentHub Webview chat", tooltip: "" }
      ),
    ];
  }


}

interface ItemMeta {
  description?: string;
  tooltip?: string;
}

class TreeItem extends vscode.TreeItem {
  constructor(
    label: string,
    command: vscode.Command | undefined,
    collapsible: vscode.TreeItemCollapsibleState,
    meta: ItemMeta = {}
  ) {
    super(label, collapsible);
    this.command = command;
    this.description = meta.description;
    this.tooltip = meta.tooltip ?? label;
    this.iconPath = new vscode.ThemeIcon("play-circle");
  }
}
