"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.CPGStatusBar = void 0;
/**
 * cpgStatus.ts — Status Bar with Graceful Fallback
 *
 * Shows CPG stats (nodes, stale, watch status) when initialized.
 * Shows "Setup needed" with click-to-initialize when not.
 * Never shows raw errors to the user.
 */
const vscode = require("vscode");
const cp = require("child_process");
const path = require("path");
const fs = require("fs");
class CPGStatusBar {
    item;
    timer;
    outputChannel;
    constructor(outputChannel) {
        this.outputChannel = outputChannel;
        this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
        this.item.text = '$(zap) VECTOR';
        this.item.tooltip = 'Click to show VECTOR status';
        this.item.show();
        // Refresh on editor change and every 30s
        this.timer = setInterval(() => this.refreshForActiveEditor(), 30_000);
        vscode.window.onDidChangeActiveTextEditor(() => this.refreshForActiveEditor());
        this.refreshForActiveEditor();
    }
    /** Manually trigger refresh (e.g. after modify succeeds). */
    refresh(workspaceRoot) {
        this.readStatus(workspaceRoot);
    }
    refreshForActiveEditor() {
        const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        if (root) {
            this.readStatus(root);
        }
    }
    readStatus(root) {
        const brainDir = path.join(root, '.codeagent');
        // ── Not initialized — show setup prompt ─────────────────────────────
        if (!fs.existsSync(brainDir)) {
            this.item.text = '$(zap) VECTOR: Setup needed';
            this.item.tooltip = new vscode.MarkdownString('**VECTOR** — Not initialized\n\n' +
                'Click to run **VECTOR: Initialize Project** and build the Code Property Graph.\n\n' +
                '_Or run `Cmd+Shift+P` → VECTOR: Health Check_');
            this.item.tooltip.isTrusted = true;
            this.item.command = 'vector.init';
            this.item.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
            return;
        }
        // ── Initialized — try to read status via JSON ───────────────────────
        const python = vscode.workspace.getConfiguration('vector').get('pythonPath', 'python3');
        const agentPath = this.resolveAgentPath(root);
        // Check if agent file exists first
        if (!fs.existsSync(agentPath)) {
            this.item.text = '$(zap) VECTOR: Ready';
            this.item.tooltip = 'VECTOR initialized. Set vector.agentPath in settings for full status.';
            this.item.command = 'vector.status';
            this.item.backgroundColor = undefined;
            return;
        }
        cp.exec(`"${python}" "${agentPath}" status "${root}" --json`, { cwd: root, timeout: 10_000 }, (err, stdout) => {
            if (err) {
                // Graceful fallback — don't show raw errors
                this.item.text = '$(zap) VECTOR: Ready';
                this.item.tooltip = 'VECTOR initialized. Click for details.';
                this.item.command = 'vector.status';
                this.item.backgroundColor = undefined;
                return;
            }
            try {
                const data = JSON.parse(stdout);
                const nodes = data.nodes ?? 0;
                const stale = data.stale_nodes ?? 0;
                const edges = data.edges ?? 0;
                const ki = data.knowledge_items ?? 0;
                const watching = data.watching ? '●' : '○';
                const lastTask = data.last_task ?? 'none';
                this.item.text = `$(zap) VECTOR · ${nodes} fn · ${stale} stale · ${watching}`;
                this.item.command = 'vector.status';
                const tooltip = new vscode.MarkdownString(`**⚡ VECTOR Brain Status**\n\n` +
                    `| Metric | Value |\n` +
                    `|:---|:---|\n` +
                    `| Functions | ${nodes} |\n` +
                    `| Call edges | ${edges} |\n` +
                    `| Stale nodes | ${stale} |\n` +
                    `| Knowledge Items | ${ki} |\n` +
                    `| Last task | \`${lastTask}\` |\n\n` +
                    `_Click for full status_`);
                tooltip.isTrusted = true;
                this.item.tooltip = tooltip;
                // Visual warning if too many stale nodes
                this.item.backgroundColor = stale > 10
                    ? new vscode.ThemeColor('statusBarItem.warningBackground')
                    : undefined;
            }
            catch {
                this.item.text = '$(zap) VECTOR: Ready';
                this.item.command = 'vector.status';
                this.item.backgroundColor = undefined;
            }
        });
    }
    resolveAgentPath(root) {
        const configured = vscode.workspace.getConfiguration('vector').get('agentPath', '');
        if (configured) {
            return configured;
        }
        const candidates = [
            path.join(root, 'main.py'),
            path.join(root, 'tsdc-agent', 'main.py'),
            path.join(root, '..', 'tsdc-agent', 'main.py'),
        ];
        for (const c of candidates) {
            if (fs.existsSync(c)) {
                return c;
            }
        }
        return path.join(root, 'main.py');
    }
    dispose() {
        if (this.timer) {
            clearInterval(this.timer);
        }
        this.item.dispose();
    }
}
exports.CPGStatusBar = CPGStatusBar;
//# sourceMappingURL=cpgStatus.js.map