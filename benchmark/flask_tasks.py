"""
flask_tasks.py — 25 pre-defined Flask modification tasks for controlled ablation.

Paper relevance: This module drives Table 2. It defines 25 real-world modification
tasks across the Flask codebase with automated assertions (expected symbols, forbidden
symbols, and test guards) to measure hallucination rates, iteration efficiency, and pass@1.
"""
from __future__ import annotations

import os
import json
import time
from pathlib import Path


FLASK_TASKS = [
    # ── app.py tasks (10) ────────────────────────────────────────────────────
    {
        "id":       "FT-001",
        "category": "timing",
        "file_path": "src/flask/app.py",
        "func_name": "full_dispatch_request",
        "goal":     "add request timing — log duration in milliseconds before return",
        "expected_symbols": ["time.time", "self.logger.debug"],
        "forbidden_symbols": ["ctx.request.start_time", "logging.info"],
        "test_guard": "",
        "difficulty": "easy",
    },
    {
        "id":       "FT-002",
        "category": "error_handling",
        "file_path": "src/flask/app.py",
        "func_name": "handle_user_exception",
        "goal":     "add structured logging of exception type and message before handling",
        "expected_symbols": ["self.logger"],
        "forbidden_symbols": ["print("],
        "test_guard": "",
        "difficulty": "easy",
    },
    {
        "id":       "FT-003",
        "category": "validation",
        "file_path": "src/flask/app.py",
        "func_name": "make_response",
        "goal":     "add a guard that raises ValueError if rv is None",
        "expected_symbols": ["ValueError"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "medium",
    },
    {
        "id":       "FT-004",
        "category": "logging",
        "file_path": "src/flask/app.py",
        "func_name": "handle_exception",
        "goal":     "log the exception traceback using self.logger.exception before re-raising",
        "expected_symbols": ["self.logger.exception"],
        "forbidden_symbols": ["traceback.print_exc"],
        "test_guard": "",
        "difficulty": "easy",
    },
    {
        "id":       "FT-005",
        "category": "security",
        "file_path": "src/flask/app.py",
        "func_name": "dispatch_request",
        "goal":     "add a check that raises 403 if the request method is in a configurable BLOCKED_METHODS list on the app config",
        "expected_symbols": ["self.config", "403"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "medium",
    },
    {
        "id":       "FT-006",
        "category": "observability",
        "file_path": "src/flask/app.py",
        "func_name": "preprocess_request",
        "goal":     "log the request path and method at DEBUG level at the start of preprocessing",
        "expected_symbols": ["self.logger.debug"],
        "forbidden_symbols": ["print("],
        "test_guard": "",
        "difficulty": "easy",
    },
    {
        "id":       "FT-007",
        "category": "validation",
        "file_path": "src/flask/app.py",
        "func_name": "process_response",
        "goal":     "add an X-Request-ID header to every response using a uuid if not already present",
        "expected_symbols": ["uuid", "X-Request-ID"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "medium",
    },
    {
        "id":       "FT-008",
        "category": "error_handling",
        "file_path": "src/flask/app.py",
        "func_name": "handle_http_exception",
        "goal":     "log the HTTP exception status code and description at WARNING level",
        "expected_symbols": ["self.logger.warning"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "easy",
    },
    {
        "id":       "FT-009",
        "category": "timing",
        "file_path": "src/flask/app.py",
        "func_name": "wsgi_app",
        "goal":     "record total WSGI request duration in milliseconds into the environ dict under 'flask.duration_ms'",
        "expected_symbols": ["time.time", "flask.duration_ms"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "medium",
    },
    {
        "id":       "FT-010",
        "category": "validation",
        "file_path": "src/flask/app.py",
        "func_name": "finalize_request",
        "goal":     "raise a RuntimeError if the finalized response has no content-type header",
        "expected_symbols": ["RuntimeError", "Content-Type"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "medium",
    },

    # ── config.py tasks (4) ──────────────────────────────────────────────────
    {
        "id":       "FT-011",
        "category": "validation",
        "file_path": "src/flask/config.py",
        "func_name": "from_object",
        "goal":     "log a warning if the object has no uppercase attributes (likely a wrong config object)",
        "expected_symbols": ["warnings.warn"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "medium",
    },
    {
        "id":       "FT-012",
        "category": "logging",
        "file_path": "src/flask/config.py",
        "func_name": "from_pyfile",
        "goal":     "log the absolute path of the loaded config file at DEBUG level",
        "expected_symbols": ["logging.debug"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "easy",
    },
    {
        "id":       "FT-013",
        "category": "validation",
        "file_path": "src/flask/config.py",
        "func_name": "from_mapping",
        "goal":     "raise TypeError if any key in the mapping is not a string",
        "expected_symbols": ["TypeError"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "easy",
    },
    {
        "id":       "FT-014",
        "category": "observability",
        "file_path": "src/flask/config.py",
        "func_name": "from_envvar",
        "goal":     "log the environment variable name and whether it was found at DEBUG level",
        "expected_symbols": ["logging.debug"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "easy",
    },

    # ── ctx.py tasks (4) ─────────────────────────────────────────────────────
    {
        "id":       "FT-015",
        "category": "observability",
        "file_path": "src/flask/ctx.py",
        "func_name": "push",
        "goal":     "log the context push event at DEBUG level including the app name",
        "expected_symbols": ["logging.debug"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "easy",
    },
    {
        "id":       "FT-016",
        "category": "error_handling",
        "file_path": "src/flask/ctx.py",
        "func_name": "pop",
        "goal":     "log a warning if exc is not None when popping the context",
        "expected_symbols": ["logging.warning"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "easy",
    },
    {
        "id":       "FT-017",
        "category": "validation",
        "file_path": "src/flask/ctx.py",
        "func_name": "match_request",
        "goal":     "catch routing exceptions and log the URL that failed to match at WARNING level",
        "expected_symbols": ["logging.warning"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "medium",
    },
    {
        "id":       "FT-018",
        "category": "timing",
        "file_path": "src/flask/ctx.py",
        "func_name": "copy",
        "goal":     "add a timestamp attribute '_copied_at' to the copied context using time.time()",
        "expected_symbols": ["time.time", "_copied_at"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "easy",
    },

    # ── sessions.py tasks (3) ─────────────────────────────────────────────────
    {
        "id":       "FT-019",
        "category": "security",
        "file_path": "src/flask/sessions.py",
        "func_name": "open_session",
        "goal":     "log a warning if the session secret key is shorter than 16 characters",
        "expected_symbols": ["warnings.warn"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "medium",
    },
    {
        "id":       "FT-020",
        "category": "validation",
        "file_path": "src/flask/sessions.py",
        "func_name": "save_session",
        "goal":     "raise ValueError if the session cookie name is empty",
        "expected_symbols": ["ValueError"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "easy",
    },
    {
        "id":       "FT-021",
        "category": "observability",
        "file_path": "src/flask/sessions.py",
        "func_name": "get_expiration_time",
        "goal":     "log the computed expiration time at DEBUG level before returning",
        "expected_symbols": ["logging.debug"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "easy",
    },

    # ── helpers.py tasks (2) ─────────────────────────────────────────────────
    {
        "id":       "FT-022",
        "category": "validation",
        "file_path": "src/flask/helpers.py",
        "func_name": "send_file",
        "goal":     "raise ValueError if the path argument is an empty string",
        "expected_symbols": ["ValueError"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "easy",
    },
    {
        "id":       "FT-023",
        "category": "observability",
        "file_path": "src/flask/helpers.py",
        "func_name": "url_for",
        "goal":     "log the generated URL at DEBUG level before returning it",
        "expected_symbols": ["logging.debug"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "easy",
    },

    # ── views.py tasks (2) ────────────────────────────────────────────────────
    {
        "id":       "FT-024",
        "category": "security",
        "file_path": "src/flask/views.py",
        "func_name": "dispatch_request",
        "goal":     "raise MethodNotAllowed if the HTTP method is not in self.methods",
        "expected_symbols": ["MethodNotAllowed"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "medium",
    },
    {
        "id":       "FT-025",
        "category": "observability",
        "file_path": "src/flask/views.py",
        "func_name": "as_view",
        "goal":     "log the view name and allowed methods at DEBUG level when the view is created",
        "expected_symbols": ["logging.debug"],
        "forbidden_symbols": [],
        "test_guard": "",
        "difficulty": "easy",
    },
]

class FlaskAblationRunner:
    """
    Runs Flask tasks under 4 conditions:
    1. full        — Full VECTOR pipeline (baseline)
    2. no_ki       — No Knowledge Items (T7 removed)
    3. no_sandbox  — No sandbox layer (layer 5 disabled)
    4. cl100k      — cl100k tokenizer instead of Qwen tokenizer
    
    Records M1-M7 metrics for each task under each condition.
    """
    
    def __init__(self, flask_root: str, engine, pipeline, db, knowledge):
        self.flask_root = Path(flask_root).resolve()
        self.engine     = engine
        self.pipeline   = pipeline
        self.db         = db
        self.knowledge  = knowledge
        
        # Build CPG for target repo
        from core.cpg.builder import CPGBuilder
        self.builder = CPGBuilder(str(self.flask_root))
        self.builder.build()

    def run_task(self, task: dict, mode: str = "full") -> dict:
        """
        Run a single task under a specific ablation condition.
        Returns a result dictionary mimicking the TaskMetrics schema.
        """
        import core.tsdc.budget
        import tiktoken
        from core.tsdc.generator import TSDCGenerator

        file_path = task["file_path"]
        func_name = task["func_name"]
        goal      = task["goal"]
        
        abs_path = self.flask_root / file_path
        if not abs_path.exists():
            raise FileNotFoundError(f"Flask file {file_path} not found at {abs_path}")

        original_content = abs_path.read_text(encoding="utf-8")

        # Set up dynamic modifications based on mode
        orig_count = core.tsdc.budget.count_tokens
        orig_get_rules = self.knowledge.get_rules_for
        orig_sandbox_run = self.pipeline.sandbox.run

        if mode == "cl100k":
            enc = tiktoken.get_encoding("cl100k_base")
            core.tsdc.budget.count_tokens = lambda text: len(enc.encode(text))
        
        if mode == "no_ki":
            self.knowledge.get_rules_for = lambda *args, **kwargs: []

        if mode == "no_sandbox":
            self.pipeline.sandbox.run = lambda *args, **kwargs: (True, "")

        passed_at_1 = False
        passed_at_5 = False
        iterations = 0
        hallucination = False
        layer_results = []
        prompt_tokens_recorded = 0
        hallucination_attempts = 0

        # Task-level symbol constraints — now passed INTO the pipeline
        expected_symbols  = task.get("expected_symbols", [])
        forbidden_symbols = task.get("forbidden_symbols", [])

        # Construct generator
        generator = TSDCGenerator(self.builder, self.db, self.knowledge)
        error_feed = None
        prev_error_type = None  # v2.1: track error type for fresh-start retry

        # v2.1: Extract allowed callees from CPG for hallucination checking
        allowed_callees = []
        node = self.builder.find_node_by_function(file_path, func_name)
        if node:
            callee_nodes = self.builder.get_direct_callees(node.node_id)
            allowed_callees = [c.function_name for c in callee_nodes]

        try:
            for attempt in range(1, 6):
                iterations = attempt
                
                # Restore original content before generating/applying in-place
                abs_path.write_text(original_content, encoding="utf-8")
                
                tsdc_doc = generator.generate(
                    file_path=file_path,
                    func_name=func_name,
                    task_goal=goal,
                    error_feedback=error_feed,
                    attempt=attempt,
                    prev_error_type=prev_error_type,
                )

                if attempt == 1:
                    prompt_tokens_recorded = core.tsdc.budget.count_tokens(tsdc_doc)

                # Generate code from engine
                # Gap 1: T=0.0 on attempt 1 (deterministic), T=0.3 on retries
                # to break out of repetitive failure loops
                retry_temp = 0.0 if attempt == 1 else 0.3
                func_code, stats = self.engine.generate_function(
                    tsdc_doc, temperature=retry_temp
                )

                # Run verification pipeline with symbol constraints + callee list
                ver_result = self.pipeline.run(
                    diff_text=func_code,
                    target_file=file_path,
                    target_func=func_name,
                    allowed_callees=allowed_callees,
                    expected_symbols=expected_symbols,
                    forbidden_symbols=forbidden_symbols,
                )

                # Track hallucinations (symbol_check failures)
                if ver_result.layer_failed == "symbol_check":
                    hallucination = True
                    hallucination_attempts += 1

                layer_results.append(ver_result.layer_failed or "passed")

                if ver_result.passed:
                    if attempt == 1:
                        passed_at_1 = True
                    passed_at_5 = True
                    break

                # Track error type for fresh-start retry (Fix 1)
                prev_error_type = ver_result.layer_failed

                # Distilled feedback with callee re-listing (Fix 4)
                error_feed = ver_result.feedback_for_model(
                    allowed_callees=allowed_callees
                )

        finally:
            # RESTORE ORIGINAL FILE CONTENTS TO AVOID CONTAMINATING NEXT RUNS
            abs_path.write_text(original_content, encoding="utf-8")

            # Restore original functions
            core.tsdc.budget.count_tokens = orig_count
            self.knowledge.get_rules_for = orig_get_rules
            self.pipeline.sandbox.run = orig_sandbox_run

        # Compute hallucination rate for this mode
        hallucination_rate = hallucination_attempts / max(iterations, 1)

        return {
            "task_id": task["id"],
            "mode": mode,
            "passed_at_1": passed_at_1,
            "passed_at_5": passed_at_5,
            "iterations": iterations,
            "hallucination": hallucination,
            "tsdc_tiers": {"codebase_rules": "" if mode == "no_ki" else "rule1"},
            "layer_results": layer_results,
            "metrics": {
                "M1_prompt_tokens": prompt_tokens_recorded,
                "M7_hallucination_rate": hallucination_rate,
            }
        }
        
    def run_all(
        self,
        modes: tuple = ("full", "no_ki", "no_sandbox", "cl100k"),
        checkpoint_path: str = "results/flask/ablation_results_v2.json",
    ) -> dict:
        """
        Run all 25 tasks under each ablation mode.
        v2: saves to ablation_results_v2.json to preserve v1 baseline data.
        Saves a checkpoint after every task×mode so crashes don't lose progress.
        On restart, loads existing results and skips completed entries.

        Available modes:
          - full        : Full VECTOR v2 pipeline (XML framing + distilled feedback)
          - no_ki       : No Knowledge Items (T7 removed)
          - no_sandbox  : No sandbox layer (layer 5 disabled)
          - cl100k      : cl100k tokenizer instead of Qwen tokenizer
        """
        import json as _json
        from pathlib import Path as _Path

        ckpt = _Path(checkpoint_path)
        ckpt.parent.mkdir(parents=True, exist_ok=True)

        # Load existing checkpoint if present
        results: dict = {mode: [] for mode in modes}
        if ckpt.exists():
            try:
                saved = _json.loads(ckpt.read_text())
                for m in modes:
                    results[m] = saved.get(m, [])
                completed = sum(len(v) for v in results.values())
                print(f"[Checkpoint] Resuming — {completed} entries already done.")
            except Exception:
                pass  # corrupt checkpoint — start fresh

        # Build set of (task_id, mode) already completed
        done: set = set()
        for mode in modes:
            for r in results[mode]:
                done.add((r["task_id"], r["mode"]))

        total = len(FLASK_TASKS) * len(modes)
        finished = len(done)

        for task in FLASK_TASKS:
            print(f"\n[Ablation v2] Task {task['id']} ({task['func_name']}) — "
                  f"{task['difficulty']} — {task['file_path']}")
            for mode in modes:
                if (task["id"], mode) in done:
                    print(f"  [skip] {mode} (already in checkpoint)")
                    continue

                finished += 1
                print(f"  [{finished}/{total}] Mode: {mode}")
                res = self.run_task(task, mode)
                results[mode].append(res)

                # Save checkpoint after each entry
                ckpt.write_text(_json.dumps(results, indent=2))

        print(f"\n[Ablation v2] Complete — {total} runs across {len(FLASK_TASKS)} tasks × {len(modes)} modes.")
        return results
