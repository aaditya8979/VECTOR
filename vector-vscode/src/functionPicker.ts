/**
 * functionPicker.ts — Function Selection with Icons and Class Context
 *
 * Shows a QuickPick of all functions in the file from the CPG.
 * Auto-selects if only one function exists.
 * Falls back to cursor-position detection if CPG isn't available.
 */
import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as path from 'path';
import * as fs from 'fs';

interface FunctionNode {
    name: string;
    signature: string;
    line: number;
    class_name: string | null;
}

export class FunctionPicker {
    constructor(
        private workspaceRoot: string,
        private filePath: string,
    ) {}

    /**
     * Shows a QuickPick of all functions in the file from the CPG.
     * Auto-selects if only one function exists.
     * Returns the selected function name or undefined if cancelled.
     */
    async pick(): Promise<string | undefined> {
        const relPath = path.relative(this.workspaceRoot, this.filePath);
        let functions: FunctionNode[];

        try {
            functions = await this.getFunctionsFromCPG(relPath);
        } catch {
            functions = [];
        }

        if (functions.length === 0) {
            return this.detectFromCursor();
        }

        if (functions.length === 1) {
            vscode.window.setStatusBarMessage(
                `⚡ VECTOR: auto-selected \`${functions[0].name}\``,
                3000,
            );
            return functions[0].name;
        }

        // Multiple functions — show QuickPick with icons
        const items: vscode.QuickPickItem[] = functions.map(f => {
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

        if (!selected) { return undefined; }

        // Extract function name from label (remove icon prefix and class context)
        const label = selected.label;
        const match = label.match(/\)\s*(?:\w+\.)?(\w+)$/);
        if (match) { return match[1]; }

        // Fallback: find the original function node
        const idx = items.indexOf(selected);
        return idx >= 0 ? functions[idx].name : undefined;
    }

    /**
     * Falls back to detecting a function name from the cursor position
     * using regex over the current line range.
     */
    private detectFromCursor(): Promise<string | undefined> {
        return new Promise((resolve) => {
            const editor = vscode.window.activeTextEditor;
            if (!editor) { resolve(undefined); return; }

            const cursorLine = editor.selection.active.line;
            const doc = editor.document;

            const funcPatterns: RegExp[] = [
                /^\s*(?:async\s+)?def\s+(\w+)\s*\(/,                  // Python
                /^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(/, // JS/TS
                /^\s*(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(/, // Arrow functions
                /^\s*func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(/,              // Go
                /^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*[<(]/,      // Rust
                /^\s*(?:virtual\s+|static\s+)?[\w:]+\s+(\w+)\s*\(/,   // C/C++ (loose)
            ];

            for (let i = cursorLine; i >= Math.max(0, cursorLine - 50); i--) {
                const lineText = doc.lineAt(i).text;
                for (const re of funcPatterns) {
                    const m = lineText.match(re);
                    if (m) {
                        vscode.window.setStatusBarMessage(
                            `⚡ VECTOR: detected \`${m[1]}\` from cursor position`,
                            3000,
                        );
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

    private getFunctionsFromCPG(relPath: string): Promise<FunctionNode[]> {
        return new Promise((resolve) => {
            const python = vscode.workspace.getConfiguration('vector')
                .get<string>('pythonPath', 'python3');
            const agentPath = this.resolveAgentPath();

            if (!fs.existsSync(agentPath)) {
                resolve([]);
                return;
            }

            cp.exec(
                `"${python}" "${agentPath}" list-functions "${relPath}" --project "${this.workspaceRoot}" --json`,
                { cwd: this.workspaceRoot, timeout: 10_000 },
                (err, stdout) => {
                    if (err) { resolve([]); return; }
                    try {
                        resolve(JSON.parse(stdout) as FunctionNode[]);
                    } catch {
                        resolve([]);
                    }
                },
            );
        });
    }

    private resolveAgentPath(): string {
        const configured = vscode.workspace.getConfiguration('vector')
            .get<string>('agentPath', '');
        if (configured) { return configured; }
        const candidates = [
            path.join(this.workspaceRoot, 'main.py'),
            path.join(this.workspaceRoot, 'tsdc-agent', 'main.py'),
            path.join(this.workspaceRoot, '..', 'tsdc-agent', 'main.py'),
        ];
        for (const c of candidates) {
            if (fs.existsSync(c)) { return c; }
        }
        return path.join(this.workspaceRoot, 'main.py');
    }
}
