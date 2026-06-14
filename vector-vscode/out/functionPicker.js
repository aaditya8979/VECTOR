"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.FunctionPicker = void 0;
/**
 * functionPicker.ts — Function Selection with Icons and Class Context
 *
 * Shows a QuickPick of all functions in the file from the CPG.
 * Auto-selects if only one function exists.
 * Falls back to cursor-position detection if CPG isn't available.
 */
const vscode = require("vscode");
const cp = require("child_process");
const path = require("path");
const fs = require("fs");
class FunctionPicker {
    workspaceRoot;
    filePath;
    constructor(workspaceRoot, filePath) {
        this.workspaceRoot = workspaceRoot;
        this.filePath = filePath;
    }
    /**
     * Shows a QuickPick of all functions in the file from the CPG.
     * Auto-selects if only one function exists.
     * Returns the selected function name or undefined if cancelled.
     */
    async pick() {
        const relPath = path.relative(this.workspaceRoot, this.filePath);
        let functions;
        try {
            functions = await this.getFunctionsFromCPG(relPath);
        }
        catch {
            functions = [];
        }
        if (functions.length === 0) {
            return this.detectFromCursor();
        }
        if (functions.length === 1) {
            vscode.window.setStatusBarMessage(`⚡ VECTOR: auto-selected \`${functions[0].name}\``, 3000);
            return functions[0].name;
        }
        // Multiple functions — show QuickPick with icons
        const items = functions.map(f => {
            const icon = f.class_name ? '$(symbol-method)' : '$(symbol-function)';
            const classCtx = f.class_name ? `${f.class_name}.` : '';
            return {
                label: `${icon} ${classCtx}${f.name}`,
                description: `line ${f.line}`,
                detail: `    ${f.signature}`,
            };
        });
        const selected = await vscode.window.showQuickPick(items, {
            title: '⚡ VECTOR — Select Function to Modify',
            placeHolder: 'Choose a function (type to filter)',
            matchOnDetail: true,
            matchOnDescription: true,
        });
        if (!selected) {
            return undefined;
        }
        // Extract function name from label (remove icon prefix and class context)
        const label = selected.label;
        const match = label.match(/\)\s*(?:\w+\.)?(\w+)$/);
        if (match) {
            return match[1];
        }
        // Fallback: find the original function node
        const idx = items.indexOf(selected);
        return idx >= 0 ? functions[idx].name : undefined;
    }
    /**
     * Falls back to detecting a function name from the cursor position
     * using regex over the current line range.
     */
    detectFromCursor() {
        return new Promise((resolve) => {
            const editor = vscode.window.activeTextEditor;
            if (!editor) {
                resolve(undefined);
                return;
            }
            const cursorLine = editor.selection.active.line;
            const doc = editor.document;
            const funcPatterns = [
                /^\s*(?:async\s+)?def\s+(\w+)\s*\(/, // Python
                /^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(/, // JS/TS
                /^\s*(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(/, // Arrow functions
                /^\s*func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(/, // Go
                /^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*[<(]/, // Rust
                /^\s*(?:virtual\s+|static\s+)?[\w:]+\s+(\w+)\s*\(/, // C/C++ (loose)
            ];
            for (let i = cursorLine; i >= Math.max(0, cursorLine - 50); i--) {
                const lineText = doc.lineAt(i).text;
                for (const re of funcPatterns) {
                    const m = lineText.match(re);
                    if (m) {
                        vscode.window.setStatusBarMessage(`⚡ VECTOR: detected \`${m[1]}\` from cursor position`, 3000);
                        resolve(m[1]);
                        return;
                    }
                }
            }
            // Last resort: ask manually
            vscode.window.showInputBox({
                title: '⚡ VECTOR — Function Name',
                prompt: 'CPG not available. Enter the function name manually:',
                placeHolder: 'my_function',
            }).then(resolve);
        });
    }
    getFunctionsFromCPG(relPath) {
        return new Promise((resolve) => {
            const python = vscode.workspace.getConfiguration('vector')
                .get('pythonPath', 'python3');
            const agentPath = this.resolveAgentPath();
            if (!fs.existsSync(agentPath)) {
                resolve([]);
                return;
            }
            cp.exec(`"${python}" "${agentPath}" list-functions "${relPath}" --project "${this.workspaceRoot}" --json`, { cwd: this.workspaceRoot, timeout: 10_000 }, (err, stdout) => {
                if (err) {
                    resolve([]);
                    return;
                }
                try {
                    resolve(JSON.parse(stdout));
                }
                catch {
                    resolve([]);
                }
            });
        });
    }
    resolveAgentPath() {
        const configured = vscode.workspace.getConfiguration('vector')
            .get('agentPath', '');
        if (configured) {
            return configured;
        }
        const candidates = [
            path.join(this.workspaceRoot, 'main.py'),
            path.join(this.workspaceRoot, 'tsdc-agent', 'main.py'),
            path.join(this.workspaceRoot, '..', 'tsdc-agent', 'main.py'),
        ];
        for (const c of candidates) {
            if (fs.existsSync(c)) {
                return c;
            }
        }
        return path.join(this.workspaceRoot, 'main.py');
    }
}
exports.FunctionPicker = FunctionPicker;
//# sourceMappingURL=functionPicker.js.map