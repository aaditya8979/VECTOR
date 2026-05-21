import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as path from 'path';
import * as fs from 'fs';

export class CPGStatusBar implements vscode.Disposable {
    private item: vscode.StatusBarItem;
    private timer: NodeJS.Timeout | undefined;

    constructor() {
        this.item = vscode.window.createStatusBarItem(
            vscode.StatusBarAlignment.Left,
            100,
        );
        this.item.command = 'vector.status';
        this.item.text = '$(database) VECTOR…';
        this.item.tooltip = 'Click to show VECTOR status';
        this.item.show();

        // Refresh on editor focus change and every 30s
        this.timer = setInterval(() => this.refreshForActiveEditor(), 30_000);
        vscode.window.onDidChangeActiveTextEditor(() => this.refreshForActiveEditor());

        this.refreshForActiveEditor();
    }

    /** Manually trigger a refresh (e.g. after a modify command succeeds). */
    refresh(workspaceRoot: string): void {
        this.readStatus(workspaceRoot);
    }

    private refreshForActiveEditor(): void {
        const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        if (root) this.readStatus(root);
    }

    private readStatus(root: string): void {
        const brainDir = path.join(root, '.codeagent');

        if (!fs.existsSync(brainDir)) {
            this.item.text = '$(database) VECTOR: not initialized';
            this.item.tooltip = 'Run "VECTOR: Initialize Project" to set up (Cmd+Shift+P)';
            this.item.backgroundColor = undefined;
            return;
        }

        const python = vscode.workspace.getConfiguration('vector').get<string>('pythonPath', 'python3');
        const agentPath = this.resolveAgentPath(root);

        cp.exec(
            `${python} "${agentPath}" status "${root}" --json`,
            { cwd: root },
            (err, stdout) => {
                if (err) {
                    this.item.text = '$(database) VECTOR: error';
                    this.item.tooltip = err.message;
                    return;
                }

                try {
                    const data = JSON.parse(stdout);
                    const nodes = data.nodes ?? 0;
                    const stale = data.stale_nodes ?? 0;
                    const edges = data.edges ?? 0;
                    const ki    = data.knowledge_items ?? 0;
                    const watching = data.watching ? '●' : '○';
                    const lastTask = data.last_task ?? 'none';

                    this.item.text = `$(database) VECTOR · ${nodes} fn · ${stale} stale · ${watching}`;
                    this.item.tooltip = new vscode.MarkdownString(
                        `**VECTOR Brain Status**\n\n` +
                        `- Functions: ${nodes}\n` +
                        `- Edges: ${edges}\n` +
                        `- Stale: ${stale}\n` +
                        `- Knowledge Items: ${ki}\n` +
                        `- Last task: \`${lastTask}\`\n\n` +
                        `_Click to open status panel_`
                    );
                    this.item.tooltip.isTrusted = true;

                    // Warn visually if too many stale nodes
                    this.item.backgroundColor = stale > 10
                        ? new vscode.ThemeColor('statusBarItem.warningBackground')
                        : undefined;
                } catch {
                    this.item.text = '$(database) VECTOR: ready';
                    this.item.backgroundColor = undefined;
                }
            }
        );
    }

    private resolveAgentPath(root: string): string {
        const configured = vscode.workspace.getConfiguration('vector').get<string>('agentPath', '');
        if (configured) return configured;
        const candidate = path.join(root, 'main.py');
        return fs.existsSync(candidate) ? candidate : 'main.py';
    }

    dispose(): void {
        if (this.timer) clearInterval(this.timer);
        this.item.dispose();
    }
}
