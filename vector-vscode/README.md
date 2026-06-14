# ⚡ VECTOR — Verified Local Code Modifier

**Right-click any function → describe changes in plain English → VECTOR modifies, verifies through 5 layers, and shows a diff. Accept or revert with one click.**

100% local. Zero cloud. Zero API keys. Zero hallucinations reach your code.

---

## ✨ Features

- **⚡ One-Click Modify** — Right-click any function or press `Cmd+Shift+M` to modify it with natural language
- **🔍 Smart LLM Auto-Detection** — Automatically finds Ollama models, MLX models, and GGUF files on your system
- **🛡️ 5-Layer Verification** — Every modification is verified through syntax, symbol, type, test, and runtime checks before applying
- **📊 Live CPG Status** — Status bar shows your codebase graph stats at a glance
- **🌐 8 Languages** — Python, TypeScript, JavaScript, Go, Rust, C++, C, TSX
- **🔄 Diff Preview** — Review before/after diff and accept or revert
- **💡 Knowledge Learning** — VECTOR learns patterns from verified edits to improve future modifications

---

## 🚀 Quick Start (2 minutes)

### 1. Install a Model

**Recommended (all platforms):**
```bash
ollama pull qwen2.5-coder:7b
```

**Mac with Apple Silicon (fastest):**
```bash
pip install mlx-lm
```

### 2. Install Python Dependencies

```bash
pip install tree-sitter tree-sitter-python tree-sitter-typescript \
            tree-sitter-javascript tree-sitter-go tree-sitter-rust \
            tree-sitter-cpp tree-sitter-c \
            networkx watchdog rich click mypy pytest
```

### 3. Initialize Your Project

Open VS Code Command Palette (`Cmd+Shift+P`) → **VECTOR: Initialize Project**

### 4. Modify a Function

- Press `Cmd+Shift+M` (Mac) or `Ctrl+Shift+M` (Windows/Linux)
- Or right-click inside a function → **⚡ VECTOR: Modify Function**
- Describe the change → Review diff → Accept ✓

---

## 🔍 How VECTOR Modifies Code

```
┌──────────────────────────────────────────────────────────┐
│  You: "add request timing — log duration before return"  │
└──────────────┬───────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────┐
│  1. CODE PROPERTY GRAPH                                  │
│     Finds the function, its callees, callers, and types  │
│     from a pre-built graph of your entire codebase       │
└──────────────┬───────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────┐
│  2. CONTEXT COMPRESSION                                  │
│     Extracts only what the LLM needs — compressed to     │
│     ≤2,500 tokens from potentially 50,000+ token repos   │
└──────────────┬───────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────┐
│  3. LOCAL LLM GENERATION                                 │
│     Your local model generates the modified function     │
│     (Ollama, MLX, or llama.cpp — zero cloud)             │
└──────────────┬───────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────┐
│  4. 5-LAYER VERIFICATION                                 │
│     ✅ Syntax check (AST parse)                          │
│     ✅ Symbol check (no hallucinated function names)      │
│     ✅ Type check (mypy strict)                          │
│     ✅ Test execution (pytest)                           │
│     ✅ Runtime sandbox (no crashes)                      │
│                                                          │
│     If any layer fails → auto-retry with feedback        │
│     (up to 5 attempts)                                   │
└──────────────┬───────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────┐
│  5. DIFF PREVIEW                                         │
│     You review the before/after diff                     │
│     → Accept ✓  or  Revert ✗                            │
└──────────────────────────────────────────────────────────┘
```

---

## 🌐 Supported Languages

| Language | Code Parsing | Type Checking | Test Runner | File Extensions |
|:---|:---:|:---:|:---:|:---|
| **Python** | ✅ Tree-Sitter + ast | mypy | pytest | `.py` |
| **TypeScript** | ✅ Tree-Sitter | tsc | jest | `.ts` |
| **TSX/React** | ✅ Tree-Sitter | tsc | jest | `.tsx` |
| **JavaScript** | ✅ Tree-Sitter | — | jest | `.js`, `.jsx`, `.mjs` |
| **Go** | ✅ Tree-Sitter | go vet | go test | `.go` |
| **Rust** | ✅ Tree-Sitter | cargo check | cargo test | `.rs` |
| **C++** | ✅ Tree-Sitter | — | ctest | `.cpp`, `.cc`, `.hpp` |
| **C** | ✅ Tree-Sitter | — | — | `.c`, `.h` |

---

## 🔧 Smart Environment Detection

VECTOR automatically scans your system when you first install it:

| What It Scans | How |
|:---|:---|
| **Ollama models** | Runs `ollama list` — finds all installed models |
| **MLX models** | Checks `~/.cache/huggingface/hub/` for MLX models |
| **GGUF files** | Scans `~/models/`, `~/.cache/lm-studio/` for `.gguf` files |
| **Python** | Finds `python3` or `python` on your PATH |
| **Dependencies** | Checks if tree-sitter, networkx, etc. are importable |
| **Platform** | Detects macOS/Apple Silicon for MLX recommendation |

Run **VECTOR: Detect Local Models** anytime to re-scan.

### Model Priority
VECTOR auto-selects the best model:
1. 🥇 `qwen2.5-coder:7b` — best accuracy with VECTOR
2. 🥈 `deepseek-coder-v2` — strong alternative
3. 🥉 `codellama` — well-known code model
4. Any other model — works but accuracy may vary

---

## ⚙️ Settings

| Setting | Default | Description |
|:---|:---|:---|
| `vector.pythonPath` | `python3` | Python 3.10+ interpreter path. Auto-detected. |
| `vector.agentPath` | (auto) | Path to `tsdc-agent/main.py`. Auto-detected. |
| `vector.backend` | `auto` | `auto` / `mlx` / `ollama` / `llamacpp`. Auto scans your system. |
| `vector.ollamaUrl` | `http://localhost:11434` | Ollama server URL. |
| `vector.ollamaModel` | `qwen2.5-coder:7b` | Which Ollama model to use. Auto-detected. |
| `vector.maxAttempts` | `5` | Max verification attempts per modification. |
| `vector.showWelcome` | `true` | Show guided setup on first install. |

---

## 📋 Commands

| Command | Shortcut | Description |
|:---|:---|:---|
| **⚡ VECTOR: Modify Function** | `Cmd+Shift+M` | Modify the selected function with natural language |
| **VECTOR: Initialize Project** | — | Build the Code Property Graph for your workspace |
| **VECTOR: Health Check** | — | Verify all prerequisites are installed |
| **VECTOR: Detect Local Models** | — | Scan for Ollama, MLX, and GGUF models |
| **VECTOR: Show Status** | — | Display CPG stats, task history, metrics |
| **VECTOR: Resume Last Task** | — | Resume an incomplete modification |

---

## 🏥 Troubleshooting

### "Python not found"
```bash
# macOS
brew install python@3.12

# Ubuntu/Debian
sudo apt install python3.12

# Windows
# Download from https://www.python.org/downloads/
```
Then set `vector.pythonPath` in VS Code settings.

### "Cannot connect to Ollama"
```bash
# Make sure Ollama is running
ollama serve

# Verify it's working
ollama list
```

### "Model not found"
```bash
# Download the recommended model
ollama pull qwen2.5-coder:7b

# Or run VECTOR: Detect Local Models to find existing models
```

### "Project not initialized"
Open Command Palette → **VECTOR: Initialize Project**

This builds the Code Property Graph. Takes 2-10 seconds for most projects.

### "Missing Python dependencies"
```bash
pip install tree-sitter tree-sitter-python networkx watchdog rich click mypy pytest
```

### Modification fails after 5 attempts
This means the model couldn't generate code that passes all 5 verification layers. Try:
- Being more specific in your goal description
- Breaking the change into smaller steps
- Using a larger model (`ollama pull qwen2.5-coder:14b`)

---

## 🏗️ Building from Source

```bash
cd vector-vscode
npm install
npm run compile
npm run package        # produces vector-coder-1.0.0.vsix
code --install-extension vector-coder-1.0.0.vsix
```

---

## 📊 System Requirements

| Requirement | Minimum | Recommended |
|:---|:---|:---|
| OS | macOS, Windows, Linux | macOS (Apple Silicon) |
| RAM | 8 GB | 16 GB |
| Python | 3.10+ | 3.12+ |
| Disk | 5 GB (for model) | 10 GB |
| GPU | Not required | Apple Silicon Metal |

---

## 🔬 How It Works (Technical)

VECTOR (Verification-Enhanced Code Transformation with Optimised Retrieval) uses a **Code Property Graph** built with Tree-Sitter to map every function and its relationships (callees, callers, types). When you request a modification, it compresses the relevant context into a **Task-Scoped Deterministic Context (TSDC)** document of ≤2,500 tokens — extracting only the symbols the current task needs. This compressed context is sent to a local 7B LLM, and the output is verified through 5 deterministic layers (AST parse, symbol check, mypy type check, pytest execution, sandboxed runtime). If any layer fails, the error is distilled into a 1-3 line actionable fix and fed back for retry. Only modifications that pass all 5 layers reach your code.

---

## 📄 License

MIT — [Aaditya Agarwal](https://github.com/aaditya8979)
