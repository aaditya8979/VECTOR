/**
 * healthCheck.ts — Prerequisite Validator with Fix-It Actions
 *
 * Shows a diagnostic panel with ✅/❌ for each requirement.
 * Failed checks have actionable "Fix" buttons.
 */
import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { detectEnvironment, formatReport, EnvironmentReport } from './envDetector';

// ── Types ────────────────────────────────────────────────────────────────────

interface CheckResult {
    label: string;
    passed: boolean;
    detail: string;
    fixAction?: () => void | Promise<void>;
    fixLabel?: string;
}

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Run the full health check and display results.
 * Returns the EnvironmentReport for other modules to use.
 */
export async function runHealthCheck(
    outputChannel: vscode.OutputChannel,
): Promise<EnvironmentReport> {
    const report = await vscode.window.withProgress(
        {
            location: vscode.ProgressLocation.Notification,
            title: '⚡ VECTOR: Scanning your environment…',
            cancellable: false,
        },
        async () => detectEnvironment(),
    );

    // Log full report to output channel
    outputChannel.appendLine(formatReport(report));
    outputChannel.show(true);

    // Build check results
    const checks = buildChecks(report);

    // Show QuickPick with results
    await showHealthCheckResults(checks, report);

    return report;
}

/**
 * Quick silent check — returns true if everything is ready.
 * Used on activation to decide whether to show walkthrough.
 */
export async function quickCheck(): Promise<boolean> {
    const report = await detectEnvironment();
    return report.allGood;
}

// ── Internal ─────────────────────────────────────────────────────────────────

function buildChecks(report: EnvironmentReport): CheckResult[] {
    const checks: CheckResult[] = [];
    const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;

    // 1. Python
    checks.push({
        label: 'Python 3.10+',
        passed: report.pythonPath !== null,
        detail: report.pythonPath
            ? `✅ ${report.pythonVersion} — ${report.pythonPath}`
            : '❌ Python 3.10+ not found',
        fixAction: () => {
            vscode.env.openExternal(vscode.Uri.parse('https://www.python.org/downloads/'));
        },
        fixLabel: 'Download Python',
    });

    // 2. Ollama or MLX
    const hasBackend = report.ollamaRunning || report.mlxAvailable;
    checks.push({
        label: 'Inference Backend',
        passed: hasBackend,
        detail: hasBackend
            ? `✅ ${report.ollamaRunning ? 'Ollama running' : ''}${report.ollamaRunning && report.mlxAvailable ? ' + ' : ''}${report.mlxAvailable ? 'MLX available' : ''}`
            : '❌ No inference backend found',
        fixAction: () => {
            vscode.env.openExternal(vscode.Uri.parse('https://ollama.ai'));
        },
        fixLabel: 'Install Ollama',
    });

    // 3. Model
    checks.push({
        label: 'Code Model',
        passed: report.bestModel !== null,
        detail: report.bestModel
            ? `✅ ${report.bestModel.name} (${report.bestModel.backend})${report.bestModel.size ? ' — ' + report.bestModel.size : ''}`
            : '❌ No code model found',
        fixAction: () => {
            const terminal = vscode.window.createTerminal('VECTOR Setup');
            terminal.show();
            terminal.sendText('ollama pull qwen2.5-coder:7b');
        },
        fixLabel: 'Download Model (ollama pull)',
    });

    // 4. Python dependencies
    checks.push({
        label: 'Python Dependencies',
        passed: report.missingDeps.length === 0 && report.pythonPath !== null,
        detail: report.missingDeps.length === 0
            ? '✅ All dependencies installed'
            : `❌ Missing: ${report.missingDeps.join(', ')}`,
        fixAction: () => {
            const terminal = vscode.window.createTerminal('VECTOR Setup');
            terminal.show();
            const agentRoot = getAgentRoot();
            if (agentRoot) {
                terminal.sendText(`pip install -r "${path.join(agentRoot, 'requirements.txt')}"`);
            } else {
                terminal.sendText(`pip install ${report.missingDeps.join(' ')}`);
            }
        },
        fixLabel: 'Install Dependencies',
    });

    // 5. Project initialized
    const initialized = root ? fs.existsSync(path.join(root, '.codeagent')) : false;
    checks.push({
        label: 'Project Initialized',
        passed: initialized,
        detail: initialized
            ? '✅ .codeagent/ directory found'
            : '❌ Project not initialized — CPG needs to be built',
        fixAction: () => {
            vscode.commands.executeCommand('vector.init');
        },
        fixLabel: 'Initialize Project',
    });

    // 6. Platform info (always passes — informational)
    checks.push({
        label: 'Platform',
        passed: true,
        detail: `ℹ️ ${report.os} ${report.arch}${report.isAppleSilicon ? ' (Apple Silicon — MLX recommended)' : ''}`,
    });

    return checks;
}

async function showHealthCheckResults(
    checks: CheckResult[],
    report: EnvironmentReport,
): Promise<void> {
    const failedChecks = checks.filter(c => !c.passed && c.fixAction);

    // Build QuickPick items
    const items: (vscode.QuickPickItem & { action?: () => void | Promise<void> })[] = [];

    for (const check of checks) {
        const icon = check.passed ? '$(check)' : '$(error)';
        items.push({
            label: `${icon} ${check.label}`,
            detail: `     ${check.detail}`,
            description: !check.passed && check.fixLabel ? `→ ${check.fixLabel}` : '',
            action: !check.passed ? check.fixAction : undefined,
        });
    }

    // Add separator + fix all option if there are failures
    if (failedChecks.length > 0) {
        items.push({
            label: '',
            kind: vscode.QuickPickItemKind.Separator,
        } as any);
        items.push({
            label: '$(tools) Fix All Issues',
            detail: `     Runs ${failedChecks.length} fix action(s) sequentially`,
            action: async () => {
                for (const check of failedChecks) {
                    if (check.fixAction) { await check.fixAction(); }
                }
            },
        });
    }

    // Add detected models section
    if (report.models.length > 0) {
        items.push({
            label: '',
            kind: vscode.QuickPickItemKind.Separator,
        } as any);
        items.push({
            label: `$(hubot) Detected ${report.models.length} model(s)`,
            detail: `     Best: ${report.bestModel?.name ?? 'none'} (${report.bestModel?.backend ?? '—'})`,
        });
    }

    const selected = await vscode.window.showQuickPick(items, {
        title: '⚡ VECTOR Health Check',
        placeHolder: report.allGood
            ? '✅ Everything looks good! VECTOR is ready to use.'
            : '❌ Some issues need fixing — select an item to fix it',
        matchOnDetail: true,
    });

    if (selected && 'action' in selected && selected.action) {
        await selected.action();
    }
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function getAgentRoot(): string | null {
    const configured = vscode.workspace.getConfiguration('vector').get<string>('agentPath', '');
    if (configured) { return path.dirname(configured); }
    // Try to find it relative to extension
    const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (root && fs.existsSync(path.join(root, 'main.py'))) {
        return root;
    }
    return null;
}

/**
 * Auto-configure VS Code settings based on detected environment.
 * Called after health check passes or user runs "Fix All".
 */
export async function autoConfigureFromReport(report: EnvironmentReport): Promise<void> {
    const config = vscode.workspace.getConfiguration('vector');

    if (report.pythonPath && !config.get<string>('pythonPath')) {
        await config.update('pythonPath', report.pythonPath, vscode.ConfigurationTarget.Global);
    }

    if (report.recommendedBackend) {
        await config.update('backend', report.recommendedBackend, vscode.ConfigurationTarget.Global);
    }

    if (report.bestModel && report.bestModel.backend === 'ollama') {
        await config.update('ollamaModel', report.bestModel.name, vscode.ConfigurationTarget.Global);
    }
}
