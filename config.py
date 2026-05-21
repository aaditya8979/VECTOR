from pathlib import Path
import os

# ── Model ─────────────────────────────────────────────────────────────────────
MODEL_PATH = os.environ.get(
    "TSDC_MODEL_PATH",
    "/Users/aadityaagarwal/Downloads/Offline_testing/llama.cpp/models/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
)
N_GPU_LAYERS   = int(os.environ.get("TSDC_GPU_LAYERS", "-1"))   # All layers to Metal
N_CTX          = int(os.environ.get("TSDC_N_CTX", "8192"))      # Model context window
N_THREADS      = int(os.environ.get("TSDC_THREADS", "4"))       # M4 Performance cores
TEMPERATURE    = 0.0                                              # Deterministic
MAX_TOKENS_OUT = 1024                                             # Diff output budget

# ── TSDC token budget ─────────────────────────────────────────────────────────
TSDC_BUDGET          = 2500   # Total token budget for context document
TIER_BUDGETS = {
    "task_header":      80,
    "type_skeleton":   150,
    "callee_sigs":     250,
    "contract":        200,
    "caller_patterns": 150,
    "diff_digest":     150,
    "codebase_rules":  200,
    # tier 8 (target body) gets the remainder
}
MAX_CALLEES  = 8
MAX_CALLERS  = 3
MAX_RULES    = 10
DIGEST_DAYS  = 14

# ── Verification ──────────────────────────────────────────────────────────────
MAX_ITERATIONS       = 5     # Maximum regeneration attempts in verification loop
SANDBOX_TIMEOUT      = 10    # seconds
TYPE_CHECK_TIMEOUT   = 30
TEST_TIMEOUT         = 60

# ── Project paths ─────────────────────────────────────────────────────────────
BRAIN_DIR    = ".codeagent"
DB_FILE      = "state.db"
CPG_FILE     = "cpg.json"
KNOWLEDGE_DIR = "knowledge"
TASKS_DIR    = "tasks"
DIFFS_DIR    = "diffs"

# ── Benchmark ─────────────────────────────────────────────────────────────────
BENCHMARK_OUTPUT_DIR = "benchmark_results"
HUMANEVAL_CACHE      = ".humaneval_cache"