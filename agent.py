"""
TSDCAgent — high-level programmatic API.
Use this to drive the full pipeline from Python code,
or to integrate into editors, scripts, or other tools.

Example:
    from agent import TSDCAgent

    agent = TSDCAgent("/path/to/your/project")
    agent.init()

    result = agent.modify(
        file_path  = "src/auth/service.py",
        func_name  = "authenticate",
        goal       = "add rate limiting — max 5 attempts per IP per minute",
        test_guard = "tests/test_auth.py::test_login_rate_limit",
    )
    print(result.passed, result.iterations)
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib     import Path
from typing      import Optional

from config import BRAIN_DIR, CPG_FILE, DB_FILE, KNOWLEDGE_DIR, MAX_ITERATIONS


@dataclass
class ModifyResult:
    passed:        bool
    iterations:    int
    diff_applied:  str          = ""
    error_message: str          = ""
    failed_layer:  Optional[str] = None
    tsdc_tokens:   int          = 0
    total_sec:     float        = 0.0
    ki_extracted:  int          = 0


class TSDCAgent:
    """
    Full TSDC pipeline in a single class.
    Manages CPG lifecycle, file watching, inference, and verification.
    """

    def __init__(self, project_root: str, auto_watch: bool = False):
        self.project_root = str(Path(project_root).resolve())
        self._paths       = self._build_paths()
        self._builder     = None
        self._db          = None
        self._knowledge   = None
        self._engine      = None
        self._watcher     = None
        self._loaded      = False
        self._auto_watch  = auto_watch

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def init(self, force: bool = False) -> "TSDCAgent":
        """Build CPG and initialise .codeagent/ directory."""
        from core.cpg.builder     import CPGBuilder
        from core.memory.state_db  import StateDB
        from core.memory.knowledge import KnowledgeStore

        for d in self._paths.values():
            Path(d).mkdir(parents=True, exist_ok=True)

        cpg_path = self._paths["cpg"]
        if Path(cpg_path).exists() and not force:
            self._builder = CPGBuilder.load(cpg_path, self.project_root)
        else:
            print(f"[tsdc] Building CPG for {self.project_root} ...")
            t0 = time.time()
            self._builder = CPGBuilder(self.project_root)
            self._builder.build()
            self._builder.save(cpg_path)
            print(f"[tsdc] CPG built: {len(self._builder.nodes)} nodes, {time.time()-t0:.1f}s")

        self._db        = StateDB(self._paths["db"])
        self._knowledge = KnowledgeStore(self._paths["knowledge"])
        self._loaded    = True

        if self._auto_watch:
            self.start_watch()

        return self

    def start_watch(self) -> "TSDCAgent":
        """Start background file watcher for live CPG sync."""
        from core.watcher.fs_watcher import FSWatcher
        self._ensure_loaded()
        self._watcher = FSWatcher(self._builder, self.project_root)
        self._watcher.start()
        return self

    def stop_watch(self):
        if self._watcher:
            self._watcher.stop()

    def __enter__(self):
        return self.init()

    def __exit__(self, *_):
        self.stop_watch()
        if self._db:
            self._db.close()

    # ── Core: modify ──────────────────────────────────────────────────────────

    def modify(
        self,
        file_path:      str,
        func_name:      str,
        goal:           str,
        test_guard:     str = "",
        max_iterations: int = MAX_ITERATIONS,
    ) -> ModifyResult:
        """
        The main entry point. Modifies `func_name` in `file_path` to achieve `goal`.
        Returns ModifyResult with pass/fail status and metrics.
        """
        self._ensure_loaded()
        self._ensure_engine()

        from core.tsdc.generator   import TSDCGenerator
        from core.tsdc.budget       import count_tokens
        from verification.pipeline  import VerificationPipeline
        from benchmark.metrics      import MetricsStore, TaskMetrics

        generator  = TSDCGenerator(self._builder, self._db, self._knowledge)
        pipeline   = VerificationPipeline(self.project_root)
        task_id    = self._db.create_task(goal, file_path, func_name)
        self._db.update_task(task_id, status="in_progress")

        t_total    = time.time()
        error_feed = None
        last_func  = ""
        metrics    = TaskMetrics(task_id=task_id, func_name=func_name, file_path=file_path)
        store      = MetricsStore(self._paths["metrics"])

        for attempt in range(1, max_iterations + 1):
            try:
                tsdc_doc = generator.generate(
                    file_path      = file_path,
                    func_name      = func_name,
                    task_goal      = goal,
                    test_guard     = test_guard,
                    error_feedback = error_feed,
                )
            except ValueError as e:
                return ModifyResult(passed=False, iterations=attempt, error_message=str(e))

            metrics.tsdc_tokens_used = count_tokens(tsdc_doc)

            t_inf  = time.time()
            func_code, _ = self._engine.generate_function(tsdc_doc)
            metrics.inference_ms += (time.time() - t_inf) * 1000
            last_func = func_code

            t_ver  = time.time()
            result = pipeline.run(func_code, file_path, func_name, test_guard)
            metrics.verification_ms += (time.time() - t_ver) * 1000

            if result.passed:
                self._db.log_change(
                    f"{file_path}::{func_name}", file_path, func_name, goal, task_id=task_id
                )
                ki_rules = self._knowledge.extract_rules_from_diff(func_code, func_name, file_path, {})
                self._db.update_task(task_id, status="completed", iterations=attempt)

                metrics.passed_final      = True
                metrics.passed_first_shot = (attempt == 1)
                metrics.iterations        = attempt
                metrics.total_ms          = (time.time() - t_total) * 1000
                store.record(metrics)

                return ModifyResult(
                    passed       = True,
                    iterations   = attempt,
                    diff_applied = func_code,
                    tsdc_tokens  = metrics.tsdc_tokens_used,
                    total_sec    = metrics.total_ms / 1000,
                    ki_extracted = len(ki_rules),
                )

            error_feed            = result.feedback_for_model()
            metrics.failed_layer  = result.layer_failed
            metrics.iterations    = attempt

        self._db.update_task(task_id, status="failed", result=error_feed or "")
        metrics.total_ms = (time.time() - t_total) * 1000
        store.record(metrics)

        return ModifyResult(
            passed        = False,
            iterations    = max_iterations,
            error_message = error_feed or "Max iterations reached",
            failed_layer  = metrics.failed_layer,
            tsdc_tokens   = metrics.tsdc_tokens_used,
            total_sec     = metrics.total_ms / 1000,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _ensure_loaded(self):
        if not self._loaded:
            self.init()

    def _ensure_engine(self):
        if self._engine is None:
            from core.model.inference import InferenceEngine
            self._engine = InferenceEngine.get()

    def _build_paths(self) -> dict:
        brain = str(Path(self.project_root) / BRAIN_DIR)
        return {
            "brain":     brain,
            "db":        str(Path(brain) / DB_FILE),
            "cpg":       str(Path(brain) / CPG_FILE),
            "knowledge": str(Path(brain) / KNOWLEDGE_DIR),
            "metrics":   str(Path(brain) / "metrics"),
        }