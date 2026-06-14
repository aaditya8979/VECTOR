# 📦 Install Dependencies

VECTOR needs a few Python packages for code analysis and verification.

### Quick Install
```bash
pip install tree-sitter tree-sitter-python tree-sitter-typescript \
            tree-sitter-javascript tree-sitter-go tree-sitter-rust \
            tree-sitter-cpp tree-sitter-c \
            networkx watchdog rich click mypy pytest
```

### Or from requirements.txt
```bash
pip install -r /path/to/tsdc-agent/requirements.txt
```

### What each package does
| Package | Purpose |
|:---|:---|
| `tree-sitter-*` | Parse source code into ASTs (8 languages) |
| `networkx` | Build the Code Property Graph |
| `watchdog` | Monitor file changes for live CPG updates |
| `mypy` | Type checking (verification layer 2) |
| `pytest` | Test execution (verification layer 3) |
| `rich` | Beautiful terminal output |
| `click` | CLI framework |
