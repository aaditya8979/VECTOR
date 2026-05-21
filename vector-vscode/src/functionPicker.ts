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
        } catch (err) {
            vscode.window.showErrorMessage(`VECTOR: Failed to query CPG — ${err}`);
            return undefined;
        }

        if (functions.length === 0) {
            // Fall back to cursor-position detection
            return this.detectFromCursor();
        }

        if (functions.length === 1) {
            vscode.window.setStatusBarMessage(
                `⚡ VECTOR: auto-selected \`${functions[0].name}\``,
                3000
            );
            return functions[0].name;
        }

        // Multiple functions — show QuickPick
        const items: vscode.QuickPickItem[] = functions.map(f => ({
            label: f.name,
            description: f.class_name ? `(in ${f.class_name})` : '',
            detail: `  Line ${f.line}   ${f.signature}`,
        }));

        const selected = await vscode.window.showQuickPick(items, {
            placeHolder: 'Select function to modify',
            matchOnDetail: true,
            matchOnDescription: true,
        });

        return selected?.label;
    }

    /**
     * Falls back to detecting a function name from the cursor position
     * using a simple regex over the current line range.
     */
    private detectFromCursor(): Promise<string | undefined> {
        return new Promise((resolve) => {
            const editor = vscode.window.activeTextEditor;
            if (!editor) { resolve(undefined); return; }

            const cursorLine = editor.selection.active.line;
            const doc = editor.document;

            // Scan upward from cursor to find the nearest function definition
            const funcPatterns: RegExp[] = [
                /^\s*def\s+(\w+)\s*\(/,                          // Python
                /^\s*(?:async\s+)?function\s+(\w+)\s*\(/,        // JS/TS
                /^\s*export\s+(?:async\s+)?function\s+(\w+)\s*\(/, // TS export
                /^\s*func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(/,        // Go
                /^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*\(/,   // Rust
            ];

            for (let i = cursorLine; i >= Math.max(0, cursorLine - 50); i--) {
                const lineText = doc.lineAt(i).text;
                for (const re of funcPatterns) {
                    const m = lineText.match(re);
                    if (m) {
                        vscode.window.setStatusBarMessage(
                            `⚡ VECTOR: detected \`${m[1]}\` from cursor`,
                            3000
                        );
                        resolve(m[1]);
                        return;
                    }
                }
            }

            // Prompt manually as a last resort
            vscode.window.showInputBox({
                prompt: 'CPG not initialized. Enter function name manually:',
                placeHolder: 'my_function',
            }).then(resolve);
        });
    }

    private getFunctionsFromCPG(relPath: string): Promise<FunctionNode[]> {
        return new Promise((resolve, reject) => {
            const python = vscode.workspace.getConfiguration('vector')
                .get<string>('pythonPath', 'python3');
            const agentPath = this.resolveAgentPath();

            cp.exec(
                `${python} "${agentPath}" list-functions "${relPath}" --project "${this.workspaceRoot}" --json`,
                { cwd: this.workspaceRoot, timeout: 10_000 },
                (err, stdout, stderr) => {
                    if (err) {
                        // Non-fatal: fall back to cursor detection
                        resolve([]);
                        return;
                    }
                    try {
                        resolve(JSON.parse(stdout) as FunctionNode[]);
                    } catch {
                        resolve([]);
                    }
                }
            );
        });
    }

    private resolveAgentPath(): string {
        const configured = vscode.workspace.getConfiguration('vector')
            .get<string>('agentPath', '');
        if (configured) return configured;
        const candidate = path.join(this.workspaceRoot, 'main.py');
        return fs.existsSync(candidate) ? candidate : 'main.py';
    }
}
