# 🚀 Your First Modification

You're ready! Here's how to use VECTOR:

### Quick Start
1. Open any source file (Python, TypeScript, JavaScript, Go, Rust, C/C++)
2. Press **Cmd+Shift+M** (Mac) or **Ctrl+Shift+M** (Windows/Linux)
3. Select the function you want to modify
4. Describe the change in plain English:
   > "add request timing — log duration in milliseconds before return"
5. Wait for VECTOR to generate and verify (5-30 seconds)
6. Review the diff preview → **Accept ✓** or **Revert ✗**

### Alternative: Right-Click Menu
- Right-click inside any function → **⚡ VECTOR: Modify Function**

### What VECTOR Verifies
Every modification passes through 5 deterministic layers:
1. ✅ **Syntax** — code parses without errors
2. ✅ **Symbols** — no hallucinated function names
3. ✅ **Types** — mypy strict type checking passes
4. ✅ **Tests** — existing test suite still passes
5. ✅ **Runtime** — no crashes on sandboxed execution

If any layer fails, VECTOR automatically retries with corrective feedback (up to 5 attempts).

### Tips
- Run **VECTOR: Health Check** anytime to verify your setup
- Check the **VECTOR** output channel for detailed logs
- The status bar shows your CPG stats at a glance
