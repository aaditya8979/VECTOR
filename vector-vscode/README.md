# VECTOR — VS Code Extension

**Local, offline, hallucination-free code modification using Qwen 2.5 Coder 7B.**

---

## Features

- **⚡ Modify Function** — Right-click any function → describe what to change → VECTOR applies, verifies (5 layers), and shows a diff preview
- **CPG Status Bar** — Live brain stats: function count, stale nodes, watch status
- **Zero cloud** — All inference is local (Apple Silicon MLX or Ollama)
- **Polyglot** — Python, TypeScript, JavaScript, Go, Rust, C/C++

## Setup

### Option A: Apple Silicon Mac (MLX — fastest, ~100 TPS)

```bash
pip install mlx-lm transformers
python3 tsdc-agent/main.py init .
```

Then set in VS Code settings:
```json
{
  "vector.pythonPath": "/path/to/python3",
  "vector.agentPath": "/path/to/tsdc-agent/main.py",
  "vector.backend": "mlx"
}
```

### Option B: Windows / Linux (Ollama — cross-platform)

```bash
# 1. Install Ollama from https://ollama.ai
ollama pull qwen2.5-coder:7b
ollama serve

# 2. Install VECTOR Python dependencies
pip install tree-sitter tree-sitter-python tree-sitter-typescript \
            tree-sitter-go tree-sitter-rust tree-sitter-cpp \
            networkx rich click
```

Then set:
```json
{
  "vector.backend": "ollama",
  "vector.ollamaUrl": "http://localhost:11434",
  "vector.ollamaModel": "qwen2.5-coder:7b"
}
```

## Usage

1. Open any supported file
2. Press `Cmd+Shift+V` (Mac) or `Ctrl+Shift+V` (Windows/Linux)  
   — or right-click → **⚡ VECTOR: Modify Function**
3. Select the function to modify (auto-detected if only one)
4. Describe the change in plain English
5. Review the diff preview → Accept or Revert

## Building the Extension

```bash
cd vector-vscode
npm install
npm run compile
npm run package   # produces vector-coder-1.0.0.vsix
code --install-extension vector-coder-1.0.0.vsix
```

## Settings

| Setting | Default | Description |
|---|---|---|
| `vector.pythonPath` | `python3` | Python interpreter with VECTOR installed |
| `vector.agentPath` | (auto) | Path to `tsdc-agent/main.py` |
| `vector.backend` | `auto` | `mlx`, `ollama`, `llamacpp`, or `auto` |
| `vector.ollamaUrl` | `http://localhost:11434` | Ollama server URL |
| `vector.ollamaModel` | `qwen2.5-coder:7b` | Model name in Ollama |
| `vector.maxAttempts` | `5` | Max verification attempts per task |
