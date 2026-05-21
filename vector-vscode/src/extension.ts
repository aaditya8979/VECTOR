import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as path from 'path';
import * as fs from 'fs';
import { CPGStatusBar } from './cpgStatus';
import { FunctionPicker } from './functionPicker';

let statusBar: CPGStatusBar;

export function activate(context: vscode.ExtensionContext) {

    // Status bar — shows CPG node count, stale count, watch status
    statusBar = new CPGStatusBar();
    context.subscriptions.push(statusBar);

    // ── Command: Modify Function ─────────────────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('vector.modify', async () => {
            const editor = vscode.window.activeTextEditor;
            if (!editor) {
                vscode.window.showWarningMessage('VECTOR: No active editor.');
                return;
            }

            const filePath = editor.document.uri.fsPath;
            const workspaceRoot = getWorkspaceRoot(filePath);

            // 1. Pick function (auto-select if only 1 in file)
            const picker = new FunctionPicker(workspaceRoot, filePath);
            const funcName = await picker.pick();
            if (!funcName) return;

            // 2. Get modification goal from user
            const goal = await vscode.window.showInputBox({
                prompt: `Modify "${funcName}" — describe what to change`,
                placeHolder: 'e.g. add request timing — log duration in milliseconds before return',
                validateInput: (v) => v.trim().length < 3 ? 'Please describe the change (min 3 chars)' : null,
            });
            if (!goal) return;

            // 3. Run VECTOR and show diff preview
            await runVector(workspaceRoot, filePath, funcName, goal.trim());
        })
    );

    // ── Command: Init ────────────────────────────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('vector.init', async () => {
            const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
            if (!root) {
                vscode.window.showWarningMessage('VECTOR: No workspace folder open.');
                return;
            }
            const terminal = vscode.window.createTerminal('VECTOR Init');
            terminal.show();
            terminal.sendText(`${getPython()} ${getAgentPath(root)} init "${root}" --force`);
        })
    );

    // ── Command: Status ──────────────────────────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('vector.status', async () => {
            const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
            if (!root) return;
            const output = vscode.window.createOutputChannel('VECTOR Status');
            output.show();
            output.appendLine('─── VECTOR Status ───────────────────────────────────');
            const proc = cp.spawnSync(
                getPython(),
                [getAgentPath(root), 'status', root],
                { cwd: root, encoding: 'utf-8' }
            );
            output.appendLine(proc.stdout ?? proc.stderr ?? 'No output');
        })
    );

    // ── Command: Resume Last Task ────────────────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('vector.resume', async () => {
            const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
            if (!root) return;
            const terminal = vscode.window.createTerminal('VECTOR Resume');
            terminal.show();
            terminal.sendText(`${getPython()} ${getAgentPath(root)} status "${root}"`);
        })
    );
}

// ── Core modification flow ───────────────────────────────────────────────────

async function runVector(
    workspaceRoot: string,
    filePath: string,
    funcName: string,
    goal: string,
): Promise<void> {
    const relPath = path.relative(workspaceRoot, filePath);
    // Snapshot original content for diff view and revert
    const originalContent = fs.readFileSync(filePath, 'utf8');

    const config = vscode.workspace.getConfiguration('vector');
    const maxAttempts = config.get<number>('maxAttempts', 5);

    let finalResult = false;

    await vscode.window.withProgress({
        location: vscode.ProgressLocation.Notification,
        title: `⚡ VECTOR: modifying \`${funcName}\``,
        cancellable: false,
    }, async (progress) => {
        progress.report({ message: 'Building TSDC context…' });

        return new Promise<void>((resolve, reject) => {
            const env: NodeJS.ProcessEnv = {
                ...process.env,
                TSDC_BACKEND: config.get<string>('backend', 'auto'),
                TSDC_OLLAMA_URL: config.get<string>('ollamaUrl', 'http://localhost:11434'),
                TSDC_OLLAMA_MODEL: config.get<string>('ollamaModel', 'qwen2.5-coder:7b'),
            };

            const args = [
                getAgentPath(workspaceRoot),
                'modify',
                relPath,
                funcName,
                goal,
                '--project', workspaceRoot,
                '--max-attempts', String(maxAttempts),
            ];

            const proc = cp.spawn(getPython(), args, { cwd: workspaceRoot, env });
            let stdout = '';
            let stderr = '';

            proc.stdout.on('data', (d: Buffer) => {
                const line = d.toString();
                stdout += line;
                if (line.includes('Attempt')) {
                    const m = line.match(/Attempt (\d+)/);
                    progress.report({ message: `Attempt ${m?.[1] ?? '…'} — verifying…` });
                }
                if (line.includes('✓ Verified') || line.includes('all 5 layers')) {
                    progress.report({ message: 'All 5 layers passed!' });
                }
            });

            proc.stderr.on('data', (d: Buffer) => { stderr += d.toString(); });

            proc.on('close', async (code) => {
                if (code === 0) {
                    finalResult = true;
                    resolve();
                } else {
                    const msg = stderr.slice(-300) || stdout.slice(-300) || 'Unknown error';
                    vscode.window.showErrorMessage(
                        `VECTOR: modification failed.\n${msg}`
                    );
                    reject(new Error(msg));
                }
            });
        });
    });

    if (finalResult) {
        await showDiffAndConfirm(filePath, originalContent, workspaceRoot);
        statusBar.refresh(workspaceRoot);
    }
}

async function showDiffAndConfirm(
    filePath: string,
    originalContent: string,
    workspaceRoot: string,
): Promise<void> {
    const tmpPath = filePath + '.vector_orig';
    try {
        fs.writeFileSync(tmpPath, originalContent, 'utf8');

        await vscode.commands.executeCommand(
            'vscode.diff',
            vscode.Uri.file(tmpPath),
            vscode.Uri.file(filePath),
            `⚡ VECTOR: ${path.basename(filePath)} — Before → After`,
        );

        const choice = await vscode.window.showInformationMessage(
            '⚡ VECTOR modification applied and verified (all 5 layers passed).',
            { modal: false },
            'Accept ✓',
            'Revert ✗',
        );

        if (choice === 'Revert ✗') {
            fs.writeFileSync(filePath, originalContent, 'utf8');
            vscode.window.showInformationMessage('VECTOR: Reverted to original.');
        }
    } finally {
        if (fs.existsSync(tmpPath)) {
            fs.unlinkSync(tmpPath);
        }
    }
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function getPython(): string {
    return vscode.workspace.getConfiguration('vector').get<string>('pythonPath', 'python3');
}

function getAgentPath(workspaceRoot: string): string {
    const configured = vscode.workspace.getConfiguration('vector').get<string>('agentPath', '');
    if (configured) return configured;
    // Auto-detect: look for main.py relative to workspace
    const candidate = path.join(workspaceRoot, 'main.py');
    if (fs.existsSync(candidate)) return candidate;
    return 'main.py';
}

function getWorkspaceRoot(filePath: string): string {
    return (
        vscode.workspace.getWorkspaceFolder(vscode.Uri.file(filePath))?.uri.fsPath ??
        path.dirname(filePath)
    );
}

export function deactivate() {
    statusBar?.dispose();
}
