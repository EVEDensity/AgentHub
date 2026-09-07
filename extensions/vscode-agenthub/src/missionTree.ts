import * as vscode from "vscode";
import { AgentHubClient, MissionSummary } from "./agenthubClient";

/** Sidebar tree view showing known Missions (placeholder for now —
 *  real Mission listing API will land in a follow-up). */
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

  getChildren(): vscode.TreeItem[] {
    const healthOk = this._checkHealthSync();
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
      new TreeItem(
        "Create Mission…",
        {
          command: "agenthub.createMission",
          title: "Create Mission",
        },
        vscode.TreeItemCollapsibleState.None,
        { description: "Start a new multi-agent task", tooltip: "" }
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

  private _checkHealthSync(): boolean {
    // Fire-and-forget — the sidebar renders optimistically.  Health probe
    // happens inside the chat Webview every 15s anyway.
    void this.client
      .health()
      .then(() => this._onDidChangeTreeData.fire())
      .catch(() => this._onDidChangeTreeData.fire());
    return true;
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
