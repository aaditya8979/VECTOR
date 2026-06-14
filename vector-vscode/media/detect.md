# 🔍 Environment Detection

VECTOR automatically scans your system for:

- **Ollama models** — runs `ollama list` to find installed code models
- **MLX models** — checks HuggingFace cache for MLX-compatible models (Mac only)
- **GGUF files** — scans `~/models/` and common directories for model files

### Auto-configuration
Once detected, VECTOR automatically configures the best backend:
- **Apple Silicon Mac** → MLX (fastest, native Metal GPU)
- **Any platform with Ollama** → Ollama (easiest setup)
- **GGUF files found** → llama.cpp backend

Click **"Scan My System"** above to run the detection.
