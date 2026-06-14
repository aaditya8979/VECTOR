/**
 * envDetector.ts — Smart LLM Auto-Discovery System
 *
 * Scans the user's system for:
 *   1. OS & architecture (macOS/Apple Silicon → MLX preferred)
 *   2. Ollama — installed models via `ollama list`
 *   3. MLX — Python mlx_lm package + HuggingFace cache
 *   4. GGUF files — common model directories
 *   5. Python & dependencies — tree-sitter, networkx, etc.
 *
 * Returns an EnvironmentReport that the extension uses to auto-configure.
 */
import * as cp from 'child_process';
import * as os from 'os';
import * as path from 'path';
import * as fs from 'fs';

// ── Types ────────────────────────────────────────────────────────────────────

export interface DetectedModel {
    name: string;
    backend: 'ollama' | 'mlx' | 'llamacpp';
    size?: string;        // e.g. "4.5 GB"
    priority: number;     // lower = better (1 = ideal)
    path?: string;        // for GGUF files
}

export interface EnvironmentReport {
    os: 'macos' | 'windows' | 'linux';
    arch: string;                          // arm64, x86_64
    isAppleSilicon: boolean;
    pythonPath: string | null;
    pythonVersion: string | null;
    ollamaInstalled: boolean;
    ollamaRunning: boolean;
    mlxAvailable: boolean;
    models: DetectedModel[];
    bestModel: DetectedModel | null;
    recommendedBackend: 'mlx' | 'ollama' | 'llamacpp' | null;
    missingDeps: string[];
    allGood: boolean;                      // true = ready to use
}

// ── Model priority ranking ───────────────────────────────────────────────────

const MODEL_PRIORITIES: [RegExp, number][] = [
    [/qwen2\.?5.*coder.*7b/i,      1],   // Best: VECTOR is tuned for this
    [/qwen2\.?5.*coder/i,          2],   // Same family, different size/quant
    [/deepseek.*coder/i,           3],   // Strong code model
    [/codellama/i,                 4],   // Well-known code model
    [/starcoder/i,                 5],   // Decent alternative
    [/codegemma/i,                 5],   // Google code model
    [/granite.*code/i,             6],   // IBM code model
    [/phi.*3/i,                    7],   // Microsoft small model
];

function getModelPriority(name: string): number {
    for (const [pattern, priority] of MODEL_PRIORITIES) {
        if (pattern.test(name)) { return priority; }
    }
    return 99; // Unknown model — lowest priority
}

// ── Core detection ───────────────────────────────────────────────────────────

function execSync(cmd: string, timeoutMs = 5000): string | null {
    try {
        return cp.execSync(cmd, {
            encoding: 'utf-8',
            timeout: timeoutMs,
            stdio: ['pipe', 'pipe', 'pipe'],
        }).trim();
    } catch {
        return null;
    }
}

function detectOS(): { os: 'macos' | 'windows' | 'linux'; arch: string; isAppleSilicon: boolean } {
    const platform = os.platform();
    const arch = os.arch();
    const osType = platform === 'darwin' ? 'macos' : platform === 'win32' ? 'windows' : 'linux';
    const isAppleSilicon = osType === 'macos' && arch === 'arm64';
    return { os: osType, arch, isAppleSilicon };
}

function detectPython(): { path: string | null; version: string | null } {
    // Try python3 first, then python
    for (const cmd of ['python3', 'python']) {
        const version = execSync(`${cmd} --version`);
        if (version && version.includes('Python 3.')) {
            const pythonPath = execSync(`which ${cmd}`) ?? execSync(`where ${cmd}`) ?? cmd;
            return { path: pythonPath, version: version.replace('Python ', '') };
        }
    }
    return { path: null, version: null };
}

function detectOllama(): { installed: boolean; running: boolean; models: DetectedModel[] } {
    const models: DetectedModel[] = [];

    // Check if ollama CLI exists
    const ollamaPath = execSync('which ollama') ?? execSync('where ollama');
    if (!ollamaPath) {
        return { installed: false, running: false, models };
    }

    // Check if ollama is running by listing models
    const listOutput = execSync('ollama list', 8000);
    if (!listOutput) {
        return { installed: true, running: false, models };
    }

    // Parse `ollama list` output
    // Format: NAME               ID              SIZE      MODIFIED
    const lines = listOutput.split('\n').slice(1); // Skip header
    for (const line of lines) {
        const parts = line.trim().split(/\s+/);
        if (parts.length >= 3) {
            const name = parts[0];
            const sizeIdx = parts.findIndex(p => /^\d+(\.\d+)?$/.test(p) && parts[parts.indexOf(p) + 1]?.match(/^[GMKT]B$/i));
            const size = sizeIdx >= 0 ? `${parts[sizeIdx]} ${parts[sizeIdx + 1]}` : parts[2] ?? '';
            models.push({
                name,
                backend: 'ollama',
                size,
                priority: getModelPriority(name),
            });
        }
    }

    return { installed: true, running: true, models };
}

function detectMLX(pythonPath: string | null): { available: boolean; models: DetectedModel[] } {
    const models: DetectedModel[] = [];
    if (!pythonPath) { return { available: false, models }; }

    // Check if mlx_lm is importable
    const mlxCheck = execSync(`${pythonPath} -c "import mlx_lm; print('ok')"`);
    if (!mlxCheck || mlxCheck !== 'ok') {
        return { available: false, models };
    }

    // Scan HuggingFace cache for MLX models
    const cacheDir = path.join(os.homedir(), '.cache', 'huggingface', 'hub');
    if (fs.existsSync(cacheDir)) {
        try {
            const entries = fs.readdirSync(cacheDir);
            for (const entry of entries) {
                const lower = entry.toLowerCase();
                if (lower.includes('mlx') || lower.includes('coder') || lower.includes('qwen')) {
                    const modelName = entry.replace('models--', '').replace(/--/g, '/');
                    models.push({
                        name: modelName,
                        backend: 'mlx',
                        priority: getModelPriority(modelName),
                    });
                }
            }
        } catch { /* ignore read errors */ }
    }

    return { available: true, models };
}

function detectGGUF(): DetectedModel[] {
    const models: DetectedModel[] = [];
    const home = os.homedir();

    // Common GGUF model directories
    const searchDirs = [
        path.join(home, 'models'),
        path.join(home, '.cache', 'lm-studio', 'models'),
        path.join(home, '.local', 'share', 'llama.cpp', 'models'),
        path.join(home, 'llama.cpp', 'models'),
        path.join(home, '.cache', 'llama.cpp'),
    ];

    for (const dir of searchDirs) {
        if (!fs.existsSync(dir)) { continue; }
        try {
            scanForGGUF(dir, models, 0);
        } catch { /* ignore */ }
    }

    return models;
}

function scanForGGUF(dir: string, models: DetectedModel[], depth: number): void {
    if (depth > 3) { return; } // Don't recurse too deep
    try {
        const entries = fs.readdirSync(dir, { withFileTypes: true });
        for (const entry of entries) {
            const fullPath = path.join(dir, entry.name);
            if (entry.isFile() && entry.name.endsWith('.gguf')) {
                const stats = fs.statSync(fullPath);
                const sizeGB = (stats.size / (1024 * 1024 * 1024)).toFixed(1);
                models.push({
                    name: entry.name.replace('.gguf', ''),
                    backend: 'llamacpp',
                    size: `${sizeGB} GB`,
                    priority: getModelPriority(entry.name),
                    path: fullPath,
                });
            } else if (entry.isDirectory() && !entry.name.startsWith('.')) {
                scanForGGUF(fullPath, models, depth + 1);
            }
        }
    } catch { /* ignore permission errors */ }
}

function detectMissingDeps(pythonPath: string | null): string[] {
    if (!pythonPath) { return ['python3']; }

    const required = [
        ['tree_sitter', 'tree-sitter'],
        ['networkx', 'networkx'],
        ['watchdog', 'watchdog'],
        ['click', 'click'],
        ['rich', 'rich'],
    ];

    const missing: string[] = [];
    for (const [importName, pipName] of required) {
        const check = execSync(`${pythonPath} -c "import ${importName}"`, 3000);
        if (check === null) {
            // execSync returns null on error (import failed)
            const verify = execSync(`${pythonPath} -c "import ${importName}; print('ok')"`, 3000);
            if (!verify || verify !== 'ok') {
                missing.push(pipName);
            }
        }
    }

    return missing;
}

// ── Public API ───────────────────────────────────────────────────────────────

export async function detectEnvironment(): Promise<EnvironmentReport> {
    const osInfo = detectOS();
    const python = detectPython();
    const ollama = detectOllama();
    const mlx = osInfo.isAppleSilicon ? detectMLX(python.path) : { available: false, models: [] };
    const ggufModels = detectGGUF();
    const missingDeps = detectMissingDeps(python.path);

    // Merge all detected models
    const allModels = [...ollama.models, ...mlx.models, ...ggufModels];
    allModels.sort((a, b) => a.priority - b.priority);

    // Pick best model
    const bestModel = allModels.length > 0 ? allModels[0] : null;

    // Determine recommended backend
    let recommendedBackend: 'mlx' | 'ollama' | 'llamacpp' | null = null;
    if (bestModel) {
        recommendedBackend = bestModel.backend;
    } else if (osInfo.isAppleSilicon && mlx.available) {
        recommendedBackend = 'mlx';
    } else if (ollama.installed) {
        recommendedBackend = 'ollama';
    }

    const allGood = (
        python.path !== null &&
        bestModel !== null &&
        missingDeps.length === 0
    );

    return {
        os: osInfo.os,
        arch: osInfo.arch,
        isAppleSilicon: osInfo.isAppleSilicon,
        pythonPath: python.path,
        pythonVersion: python.version,
        ollamaInstalled: ollama.installed,
        ollamaRunning: ollama.running,
        mlxAvailable: mlx.available,
        models: allModels,
        bestModel,
        recommendedBackend,
        missingDeps,
        allGood,
    };
}

/**
 * Format the environment report as a human-readable string for the output channel.
 */
export function formatReport(report: EnvironmentReport): string {
    const lines: string[] = [
        '═══════════════════════════════════════════════',
        '         ⚡ VECTOR Environment Report           ',
        '═══════════════════════════════════════════════',
        '',
        `  Platform:       ${report.os} ${report.arch}${report.isAppleSilicon ? ' (Apple Silicon)' : ''}`,
        `  Python:         ${report.pythonVersion ? `✅ ${report.pythonVersion} (${report.pythonPath})` : '❌ Not found'}`,
        `  Ollama:         ${report.ollamaInstalled ? (report.ollamaRunning ? '✅ Running' : '⚠️ Installed but not running') : '❌ Not installed'}`,
        `  MLX:            ${report.mlxAvailable ? '✅ Available' : report.isAppleSilicon ? '⚠️ Not installed (recommended for Apple Silicon)' : '— (macOS ARM only)'}`,
        '',
    ];

    if (report.models.length > 0) {
        lines.push('  Detected Models:');
        for (const model of report.models) {
            const star = model === report.bestModel ? ' ★ SELECTED' : '';
            const size = model.size ? ` (${model.size})` : '';
            lines.push(`    ${model.backend.padEnd(8)} │ ${model.name}${size}${star}`);
        }
    } else {
        lines.push('  Detected Models: ❌ None found');
        lines.push('    → Run: ollama pull qwen2.5-coder:7b');
    }

    lines.push('');

    if (report.missingDeps.length > 0) {
        lines.push(`  Missing Dependencies: ❌ ${report.missingDeps.join(', ')}`);
        lines.push(`    → Run: pip install ${report.missingDeps.join(' ')}`);
    } else if (report.pythonPath) {
        lines.push('  Dependencies:   ✅ All installed');
    }

    lines.push('');
    lines.push(`  Recommended Backend: ${report.recommendedBackend ?? 'None — install a model first'}`);
    lines.push(`  Ready to use:        ${report.allGood ? '✅ Yes!' : '❌ See issues above'}`);
    lines.push('');
    lines.push('═══════════════════════════════════════════════');

    return lines.join('\n');
}
