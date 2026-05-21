"""
SWE-bench Lite runner — downloads dataset, clones repos, runs VECTOR,
collects predictions in the format expected by the official evaluator.

Output: predictions.jsonl (one JSON line per instance)
Format: {"instance_id": "...", "model_patch": "..."}

Evaluation (after generating predictions):
  python -m swebench.harness.run_evaluation \
    --predictions_path predictions.jsonl \
    --swe_bench_tasks princeton-nlp/SWE-bench_Lite \
    --output_dir results/swebench/
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing  import Iterator

from core.agent.repo_loop import RepositoryAgentLoop, RepoLoopResult
from benchmark.metrics    import MetricsStore, TaskMetrics


SWEBENCH_CACHE = Path(".swebench_cache")
REPO_CACHE     = Path(".swebench_repos")


class SWEBenchRunner:

    def __init__(self, engine, output_dir: str = "results/swebench"):
        self.engine     = engine
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.agent_loop = RepositoryAgentLoop(engine)
        self.metrics    = MetricsStore(str(self.output_dir / "metrics"))

    # ── Dataset loading ────────────────────────────────────────────────────────

    def load_dataset(self, split: str = "test") -> list[dict]:
        """Download SWE-bench Lite from HuggingFace. Cache locally."""
        cache_file = SWEBENCH_CACHE / f"swe_bench_lite_{split}.json"
        SWEBENCH_CACHE.mkdir(exist_ok=True)

        if cache_file.exists():
            return json.loads(cache_file.read_text())

        from datasets import load_dataset
        ds      = load_dataset("princeton-nlp/SWE-bench_Lite", split=split,
                               trust_remote_code=True)
        records = [dict(row) for row in ds]
        cache_file.write_text(json.dumps(records, indent=2))
        return records

    # ── Repo management ────────────────────────────────────────────────────────

    def prepare_repo(self, instance: dict) -> str:
        """
        Clone the repository at base_commit. Returns the local repo path.
        Caches clones to avoid repeated network calls.
        """
        repo_name   = instance["repo"].replace("/", "__")
        repo_path   = REPO_CACHE / repo_name
        REPO_CACHE.mkdir(exist_ok=True)

        if not repo_path.exists():
            print(f"  [repo] Cloning {instance['repo']}...")
            subprocess.run(
                ["git", "clone", f"https://github.com/{instance['repo']}.git",
                 str(repo_path)],
                capture_output=True, check=True, timeout=120,
            )

        # Reset to base_commit for this instance
        subprocess.run(
            ["git", "checkout", instance["base_commit"]],
            cwd=str(repo_path), capture_output=True, check=False,
        )
        subprocess.run(
            ["git", "clean", "-fd"],
            cwd=str(repo_path), capture_output=True, check=False,
        )

        # Install the package in dev mode (needed for test execution)
        setup_files = ["setup.py", "setup.cfg", "pyproject.toml"]
        if any((repo_path / f).exists() for f in setup_files):
            subprocess.run(
                ["pip", "install", "-e", ".", "-q", "--no-deps"],
                cwd=str(repo_path), capture_output=True, timeout=120,
            )

        return str(repo_path)

    def cleanup_repo(self, repo_path: str, instance: dict):
        """Reset repo back to base_commit state after each attempt."""
        subprocess.run(
            ["git", "checkout", instance["base_commit"]],
            cwd=repo_path, capture_output=True,
        )
        subprocess.run(["git", "clean", "-fd"], cwd=repo_path,
                       capture_output=True)

    # ── Single instance ────────────────────────────────────────────────────────

    def run_instance(self, instance: dict) -> dict:
        """
        Run VECTOR on one SWE-bench instance.
        Returns prediction dict: {instance_id, model_patch}.
        """
        instance_id = instance["instance_id"]
        print(f"\n[SWE] {instance_id}")
        print(f"  Issue: {instance['problem_statement'][:120]}...")

        try:
            repo_path = self.prepare_repo(instance)
        except Exception as e:
            print(f"  [SKIP] Repo prep failed: {e}")
            return {"instance_id": instance_id, "model_patch": ""}

        t0 = time.time()

        # Include hints_text if available for better localization
        full_issue = instance["problem_statement"]
        hints = instance.get("hints_text", "")
        if hints:
            full_issue += f"\n\nMaintainer hint:\n{hints}"

        result: RepoLoopResult = self.agent_loop.solve(
            issue     = full_issue,
            repo_root = repo_path,
            task_id   = instance_id,
        )
        elapsed = time.time() - t0

        status = "✓ RESOLVED" if result.resolved else "✗ not resolved"
        print(f"  {status} in {elapsed:.0f}s · "
              f"localized to: {result.localized_to[:2]}")

        self.cleanup_repo(repo_path, instance)

        return {
            "instance_id":  instance_id,
            "model_patch":  result.patch,
            "resolved":     result.resolved,
            "elapsed_sec":  elapsed,
            "localized_to": result.localized_to,
            "attempts":     result.attempts,
        }

    # ── Full run ───────────────────────────────────────────────────────────────

    def run_all(
        self,
        max_instances: int    = 300,
        start_from:    int    = 0,
        resume:        bool   = True,
        split:         str    = "test",
    ) -> dict:
        """
        Run all SWE-bench Lite instances and produce predictions.jsonl.
        Supports resuming from checkpoint if interrupted.
        """
        instances = self.load_dataset(split)[start_from:start_from + max_instances]
        pred_path = self.output_dir / "predictions.jsonl"

        # Load already-completed predictions if resuming
        completed = {}
        if resume and pred_path.exists():
            for line in pred_path.read_text().splitlines():
                if line.strip():
                    p = json.loads(line)
                    completed[p["instance_id"]] = p
            print(f"[resume] {len(completed)} instances already done.")

        resolved_count = sum(1 for p in completed.values() if p.get("resolved"))
        total_done     = len(completed)

        with pred_path.open("a") as f:
            for i, instance in enumerate(instances):
                iid = instance["instance_id"]
                if iid in completed:
                    continue

                print(f"\n[{total_done+1}/{len(instances)+len(completed)}] "
                      f"{iid}")
                pred = self.run_instance(instance)

                json.dump(pred, f)
                f.write("\n")
                f.flush()

                total_done    += 1
                resolved_count += int(pred.get("resolved", False))
                rate = resolved_count / total_done * 100 if total_done else 0
                print(f"  Running resolve rate: {rate:.1f}% "
                      f"({resolved_count}/{total_done})")

        # Final summary
        final_rate = resolved_count / total_done * 100 if total_done else 0
        print(f"\n{'='*60}")
        print(f"  FINAL: {final_rate:.1f}% resolve rate "
              f"({resolved_count}/{total_done})")
        print(f"  Predictions: {pred_path}")
        print(f"{'='*60}")

        return {
            "resolve_rate":   final_rate,
            "resolved":       resolved_count,
            "total":          total_done,
            "predictions":    str(pred_path),
        }

    # ── Pilot run (validate before full run) ──────────────────────────────────

    def pilot_run(self, n: int = 20) -> dict:
        """
        Run first N instances as a sanity check before the full 300.
        Use this to validate the pipeline before committing to 8+ hours.
        Use a diverse set (different repos).
        """
        instances  = self.load_dataset()
        # Pick instances from different repos for diversity
        by_repo    = {}
        for inst in instances:
            repo = inst["repo"]
            if repo not in by_repo:
                by_repo[repo] = inst
        diverse = list(by_repo.values())[:n]
        pilot_path = self.output_dir / "pilot_predictions.jsonl"
        resolved   = 0

        with pilot_path.open("w") as f:
            for i, inst in enumerate(diverse):
                print(f"\n[PILOT {i+1}/{len(diverse)}]")
                pred = self.run_instance(inst)
                json.dump(pred, f)
                f.write("\n")
                resolved += int(pred.get("resolved", False))

        rate = resolved / len(diverse) * 100 if diverse else 0
        print(f"\nPilot resolve rate: {rate:.1f}% ({resolved}/{len(diverse)})")
        return {"pilot_rate": rate, "resolved": resolved, "total": len(diverse)}
