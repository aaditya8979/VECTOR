/**
 * extension.ts — VECTOR VS Code Extension Entry Point
 *
 * Features:
 *   - Dedicated output channel for all logs
 *   - First-run detection → opens walkthrough if not set up
 *   - Auto-configures from detected environment
 *   - Cancellable modification progress
 *   - Smart error messages with actionable fixes
 */
import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as path from 'path';
import * as fs from 'fs';
import { CPGStatusBar } from './cpgStatus';
import { FunctionPicker } from './functionPicker';
import { detectEnvironment, formatReport, EnvironmentReport } from './envDetector';
import { runHealthCheck, quickCheck, autoConfigureFromReport } from './healthCheck';

let statusBar: CPGStatusBar;
let outputChannel: vscode.OutputChannel;
let cachedReport: EnvironmentReport | null = null;

export function activate(context: vscode.ExtensionContext) {

    // ── Output channel — single log destination ──────────────────────────────
    outputChannel = vscode.window.createOutputChannel('VECTOR');
    context.subscriptions.push(outputChannel);
    log('VECTOR extension activated.');

    // ── Status bar ───────────────────────────────────────────────────────────
    statusBar = new CPGStatusBar(outputChannel);
    context.subscriptions.push(statusBar);

    // ── First-run detection ──────────────────────────────────────────────────
    const showWelcome = vscode.workspace.getConfiguration('vector').get<boolean>('showWelcome', true);
    const hasSeenWalkthrough = context.globalState.get<boolean>('vector.hasSeenWalkthrough', false);

    if (showWelcome && !hasSeenWalkthrough) {
        // Run quick environment scan in background
        detectEnvironment().then(async (report) => {
            cachedReport = report;
            log(formatReport(report));

            // Auto-configure settings from detected environment
            if (report.bestModel || report.pythonPath) {
                await autoConfigureFromReport(report);
                log('Auto-configured settings from detected environment.');
            }

            // Show walkthrough if not fully set up
            if (!report.allGood) {
                vscode.commands.executeCommand(
                    'workbench.action.openWalkthrough',
                    'aaditya8979.vector-coder#vector.welcome',
                    false,
                );
            } else {
                // Everything is good — just show a welcome notification
                vscode.window.showInformationMessage(
                    `⚡ VECTOR is ready! ${report.models.length} model(s) detected. Press Cmd+Shift+M to modify a function.`,
                    'Got it',
                );
            }

            context.globalState.update('vector.hasSeenWalkthrough', true);
        });
    }

    // ── Command: Modify Function ─────────────────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('vector.modify', async () => {
            const editor = vscode.window.activeTextEditor;
            if (!editor) {
                vscode.window.showWarningMessage('VECTOR: Open a source file first.');
                return;
            }

            const filePath = editor.document.uri.fsPath;
            const workspaceRoot = getWorkspaceRoot(filePath);

            // Check if project is initialized
            if (!fs.existsSync(path.join(workspaceRoot, '.codeagent'))) {
                const action = await vscode.window.showWarningMessage(
                    'VECTOR: Project not initialized. Build the Code Property Graph first.',
                    'Initialize Now',
                    'Run Health Check',
                );
                if (action === 'Initialize Now') {
                    vscode.commands.executeCommand('vector.init');
                } else if (action === 'Run Health Check') {
                    vscode.commands.executeCommand('vector.healthCheck');
                }
                return;
            }

            // 1. Pick function
            const picker = new FunctionPicker(workspaceRoot, filePath);
            const funcName = await picker.pick();
            if (!funcName) { return; }

            // 2. Get modification goal
            const goal = await vscode.window.showInputBox({
                prompt: `⚡ Modify "${funcName}" — describe what to change`,
                placeHolder: 'e.g. add request timing — log duration in milliseconds before return',
                validateInput: (v) => v.trim().length < 3 ? 'Describe the change (min 3 characters)' : null,
            });
            if (!goal) { return; }

            // 3. Run VECTOR with cancellation support
            await runVector(workspaceRoot, filePath, funcName, goal.trim());
        }),
    );

    // ── Command: Init ────────────────────────────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('vector.init', async () => {
            const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
            if (!root) {
                vscode.window.showWarningMessage('VECTOR: Open a workspace folder first.');
                return;
            }

            log(`Initializing project: ${root}`);
            const terminal = vscode.window.createTerminal({ name: '⚡ VECTOR Init', cwd: root });
            terminal.show();
            terminal.sendText(`${getPython()} "${getAgentPath(root)}" init "${root}" --force`);
        }),
    );

    // ── Command: Status ──────────────────────────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('vector.status', async () => {
            const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
            if (!root) { return; }

            outputChannel.show(true);
            outputChannel.appendLine('\n─── VECTOR Status ─────────────────────────────────');

            const proc = cp.spawnSync(
                getPython(),
                [getAgentPath(root), 'status', root],
                { cwd: root, encoding: 'utf-8' },
            );
            outputChannel.appendLine(proc.stdout ?? proc.stderr ?? 'No output');
        }),
    );

    // ── Command: Resume ──────────────────────────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('vector.resume', async () => {
            const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
            if (!root) { return; }

            log('Resuming last incomplete task...');
            const terminal = vscode.window.createTerminal({ name: '⚡ VECTOR Resume', cwd: root });
            terminal.show();
            terminal.sendText(`${getPython()} "${getAgentPath(root)}" resume "${root}"`);
        }),
    );

    // ── Command: Health Check ────────────────────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('vector.healthCheck', async () => {
            cachedReport = await runHealthCheck(outputChannel);
            if (cachedReport.bestModel || cachedReport.pythonPath) {
                await autoConfigureFromReport(cachedReport);
            }
        }),
    );

    // ── Command: Detect Environment ──────────────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('vector.detectEnvironment', async () => {
            const report = await vscode.window.withProgress(
                {
                    location: vscode.ProgressLocation.Notification,
                    title: '⚡ VECTOR: Scanning for local LLMs…',
                },
                async () => detectEnvironment(),
            );

            cachedReport = report;
            outputChannel.appendLine(formatReport(report));
            outputChannel.show(true);

            // Auto-configure
            if (report.bestModel || report.pythonPath) {
                await autoConfigureFromReport(report);
            }

            if (report.models.length > 0) {
                const modelList = report.models.map(m =>
                    `${m.name} (${m.backend})${m === report.bestModel ? ' ★' : ''}`
                ).join(', ');
                vscode.window.showInformationMessage(
                    `⚡ Found ${report.models.length} model(s): ${modelList}`,
                );
            } else {
                const action = await vscode.window.showWarningMessage(
                    'VECTOR: No local LLMs found. Install one to get started.',
                    'Install Ollama',
                    'Open Docs',
                );
                if (action === 'Install Ollama') {
                    vscode.env.openExternal(vscode.Uri.parse('https://ollama.ai'));
                } else if (action === 'Open Docs') {
                    vscode.env.openExternal(vscode.Uri.parse('https://github.com/aaditya8979/tsdc-agent#setup'));
                }
            }
        }),
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
    const originalContent = fs.readFileSync(filePath, 'utf8');
    const config = vscode.workspace.getConfiguration('vector');
    const maxAttempts = config.get<number>('maxAttempts', 5);

    let finalResult = false;
    let childProcess: cp.ChildProcess | null = null;

    log(`\n═══ Modification Task ═══`);
    log(`  File:     ${relPath}`);
    log(`  Function: ${funcName}`);
    log(`  Goal:     ${goal}`);
    log(`  Max attempts: ${maxAttempts}`);

    try {
        await vscode.window.withProgress({
            location: vscode.ProgressLocation.Notification,
            title: `⚡ VECTOR: modifying \`${funcName}\``,
            cancellable: true,
        }, async (progress, token) => {
            return new Promise<void>((resolve, reject) => {
                progress.report({ message: 'Building TSDC context…' });

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
                    '--max-iterations', String(maxAttempts),
                ];

                childProcess = cp.spawn(getPython(), args, { cwd: workspaceRoot, env });
                let stdout = '';
                let stderr = '';

                // Handle cancellation
                token.onCancellationRequested(() => {
                    if (childProcess) {
                        childProcess.kill('SIGTERM');
                        log('  ⚠️ Modification cancelled by user.');
                        // Restore original file
                        fs.writeFileSync(filePath, originalContent, 'utf8');
                        resolve();
                    }
                });

                childProcess.stdout?.on('data', (d: Buffer) => {
                    const line = d.toString();
                    stdout += line;
                    log(line.trimEnd());

                    // Parse progress from stdout
                    if (line.includes('Attempt')) {
                        const m = line.match(/Attempt (\d+)\/(\d+)/);
                        if (m) {
                            progress.report({
                                message: `Attempt ${m[1]}/${m[2]} — generating & verifying…`,
                                increment: (100 / maxAttempts),
                            });
                        }
                    }
                    if (line.includes('✓ Verified') || line.includes('all 5 layers')) {
                        progress.report({ message: '✅ All 5 verification layers passed!' });
                    }
                    if (line.includes('Failed at:')) {
                        const m = line.match(/Failed at: (\w+)/);
                        if (m) {
                            progress.report({ message: `Retrying — ${m[1]} failed…` });
                        }
                    }
                });

                childProcess.stderr?.on('data', (d: Buffer) => {
                    stderr += d.toString();
                });

                childProcess.on('close', async (code) => {
                    childProcess = null;
                    if (token.isCancellationRequested) {
                        resolve();
                        return;
                    }

                    if (code === 0) {
                        finalResult = true;
                        log('  ✅ Modification succeeded.');
                        resolve();
                    } else {
                        // Parse error and show actionable message
                        const errorMsg = parseError(stderr, stdout);
                        log(`  ❌ Modification failed: ${errorMsg}`);
                        showSmartError(errorMsg, workspaceRoot);
                        resolve(); // Don't reject — we handle errors gracefully
                    }
                });
            });
        });
    } catch {
        // Progress was rejected (shouldn't happen with our resolve pattern)
    }

    if (finalResult) {
        await showDiffAndConfirm(filePath, originalContent, workspaceRoot);
        statusBar.refresh(workspaceRoot);
    }
}

// ── Diff preview & confirm ───────────────────────────────────────────────────

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
            '⚡ VECTOR: Modification applied and verified through all 5 layers (syntax → symbols → types → tests → runtime).',
            { modal: false },
            'Accept ✓',
            'Revert ✗',
        );

        if (choice === 'Revert ✗') {
            fs.writeFileSync(filePath, originalContent, 'utf8');
            vscode.window.showInformationMessage('VECTOR: Reverted to original.');
            log('  ↩️ User reverted the modification.');
        } else {
            log('  ✅ User accepted the modification.');
        }
    } finally {
        if (fs.existsSync(tmpPath)) {
            fs.unlinkSync(tmpPath);
        }
    }
}

// ── Smart error handling ─────────────────────────────────────────────────────

function parseError(stderr: string, stdout: string): string {
    const combined = stderr + stdout;

    if (combined.includes('No module named')) {
        const m = combined.match(/No module named '(\w+)'/);
        return m ? `Missing Python module: ${m[1]}` : 'Missing Python dependency';
    }
    if (combined.includes('command not found') || combined.includes('not recognized')) {
        return 'Python not found on your system';
    }
    if (combined.includes('Connection refused') || combined.includes('ECONNREFUSED')) {
        return 'Cannot connect to Ollama — is it running?';
    }
    if (combined.includes('model') && combined.includes('not found')) {
        return 'Model not found in Ollama';
    }
    if (combined.includes('not initialised') || combined.includes('not initialized')) {
        return 'Project not initialized — run VECTOR: Initialize Project';
    }
    if (combined.includes('Could not verify modification')) {
        return 'All verification attempts exhausted — the modification could not be verified';
    }

    // Fallback: last meaningful line
    const lines = combined.split('\n').filter(l => l.trim() && !l.startsWith('Traceback'));
    return lines[lines.length - 1]?.trim().slice(0, 200) || 'Unknown error';
}

function showSmartError(errorMsg: string, workspaceRoot: string): void {
    if (errorMsg.includes('Missing Python module')) {
        vscode.window.showErrorMessage(
            `VECTOR: ${errorMsg}`,
            'Install Dependencies',
        ).then(action => {
            if (action === 'Install Dependencies') {
                const terminal = vscode.window.createTerminal('⚡ VECTOR Setup');
                terminal.show();
                terminal.sendText(`pip install -r "${path.join(path.dirname(getAgentPath(workspaceRoot)), 'requirements.txt')}"`);
            }
        });
    } else if (errorMsg.includes('Python not found')) {
        vscode.window.showErrorMessage(
            'VECTOR: Python 3.10+ not found. Install it or set vector.pythonPath in settings.',
            'Download Python',
            'Open Settings',
        ).then(action => {
            if (action === 'Download Python') {
                vscode.env.openExternal(vscode.Uri.parse('https://www.python.org/downloads/'));
            } else if (action === 'Open Settings') {
                vscode.commands.executeCommand('workbench.action.openSettings', 'vector.pythonPath');
            }
        });
    } else if (errorMsg.includes('Ollama')) {
        vscode.window.showErrorMessage(
            'VECTOR: Cannot connect to Ollama. Make sure it\'s running.',
            'Start Ollama',
            'Health Check',
        ).then(action => {
            if (action === 'Start Ollama') {
                const terminal = vscode.window.createTerminal('Ollama');
                terminal.show();
                terminal.sendText('ollama serve');
            } else if (action === 'Health Check') {
                vscode.commands.executeCommand('vector.healthCheck');
            }
        });
    } else if (errorMsg.includes('not initialized')) {
        vscode.window.showErrorMessage(
            'VECTOR: Project not initialized.',
            'Initialize Now',
        ).then(action => {
            if (action === 'Initialize Now') {
                vscode.commands.executeCommand('vector.init');
            }
        });
    } else {
        vscode.window.showErrorMessage(`VECTOR: ${errorMsg}`, 'Open Logs').then(action => {
            if (action === 'Open Logs') {
                outputChannel.show(true);
            }
        });
    }
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function log(message: string): void {
    const timestamp = new Date().toLocaleTimeString();
    outputChannel.appendLine(`[${timestamp}] ${message}`);
}

function getPython(): string {
    return vscode.workspace.getConfiguration('vector').get<string>('pythonPath', 'python3');
}

function getAgentPath(workspaceRoot: string): string {
    const configured = vscode.workspace.getConfiguration('vector').get<string>('agentPath', '');
    if (configured) { return configured; }
    // Auto-detect: check multiple possible locations
    const candidates = [
        path.join(workspaceRoot, 'main.py'),
        path.join(workspaceRoot, 'tsdc-agent', 'main.py'),
        path.join(workspaceRoot, '..', 'tsdc-agent', 'main.py'),
    ];
    for (const candidate of candidates) {
        if (fs.existsSync(candidate)) { return candidate; }
    }
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
    outputChannel?.dispose();
}
