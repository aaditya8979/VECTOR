# 🏗️ Initialize Your Project

Building the Code Property Graph (CPG) maps every function in your codebase and their relationships — who calls what, type signatures, and import chains.

### Steps
1. Open the Command Palette: **Cmd+Shift+P** (Mac) or **Ctrl+Shift+P** (Windows/Linux)
2. Type: **VECTOR: Initialize Project**
3. Wait for the CPG to build (usually 2-10 seconds)

### What happens
- Creates a `.codeagent/` directory in your workspace
- Scans all source files in supported languages
- Builds a directed graph of function nodes and call edges
- Stores the graph for instant lookup during modifications

### Re-initialize
If you add new files or make major structural changes:
- Run **VECTOR: Initialize Project** again (it rebuilds from scratch)
- Or enable the file watcher: `tsdc watch .` for live updates
