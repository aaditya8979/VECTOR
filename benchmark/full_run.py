#!/usr/bin/env python3
"""
Full benchmark run script — generates all paper tables.
Run once. Takes 6-24 hours depending on hardware.

Usage:
    # Step 1: Complete HumanEval (required for Table 1)
    python benchmark/full_run.py --step humaneval

    # Step 2: Flask ablation — single run (quick validation)
    python benchmark/full_run.py --step flask

    # Step 2b: Flask ablation — 3 repeats with error bars (Table 2)
    python benchmark/full_run.py --step flask-repeats

    # Step 3: FastAPI ablation — cross-repo evaluation
    python benchmark/full_run.py --step fastapi

    # Step 4: SWE-bench pilot (20 instances — validate before full run)
    python benchmark/full_run.py --step swebench-pilot

    # Step 5: Full SWE-bench Lite (300 instances — the paper headline)
    python benchmark/full_run.py --step swebench-full

    # Step 6: Failure analysis on worst 10 tasks
    python benchmark/full_run.py --step failure-analysis

    # Step 7: Generate paper tables
    python benchmark/full_run.py --step tables
"""
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

import click


def _make_runner(engine, repo_root: Path):
    """Helper: build a FlaskAblationRunner for any repo root."""
    from benchmark.flask_tasks import FlaskAblationRunner
    from verification.pipeline import VerificationPipeline
    from core.memory.state_db  import StateDB
    from core.memory.knowledge import KnowledgeStore

    brain_dir = repo_root / ".codeagent"
    brain_dir.mkdir(parents=True, exist_ok=True)

    pipeline  = VerificationPipeline(str(repo_root))
    db        = StateDB(str(brain_dir / "state.db"))
    knowledge = KnowledgeStore(str(brain_dir / "knowledge"))
    runner    = FlaskAblationRunner(str(repo_root), engine, pipeline, db, knowledge)
    return runner


@click.command()
@click.option("--step", type=click.Choice([
    "humaneval", "flask", "flask-repeats", "fastapi",
    "swebench-pilot", "swebench-full",
    "failure-analysis", "tables", "all",
]))
@click.option("--k", default=5, help="pass@k for HumanEval")
@click.option("--repeats", default=3, help="Number of repeats for statistical significance")
@click.option("--resume/--no-resume", default=True)
@click.option("--max", "max_n", default=300)
def run(step, k, repeats, resume, max_n):
    from core.model.inference import InferenceEngine
    engine = InferenceEngine.get()

    def do_humaneval():
        print("\n=== STEP 1: Running HumanEval benchmark ===")
        from benchmark.humaneval import HumanEvalAdapter
        from verification.pipeline import VerificationPipeline

        pipeline = VerificationPipeline(".")
        adapter  = HumanEvalAdapter(engine, pipeline)
        results  = adapter.run_all(
            max_problems=164,
            k=k,
            output_file="results/humaneval/humaneval_results.jsonl",
        )

        pass_1 = sum(1 for r in results if r["passed_at_1"]) / len(results) if results else 0
        pass_k = sum(1 for r in results if r["passed_at_k"]) / len(results) if results else 0
        print(f"\nTable 1 row: pass@1={pass_1:.1%}  pass@{k}={pass_k:.1%}")

    def do_flask():
        print("\n=== STEP 2: Running Flask ablation (single run) ===")
        import os, json

        flask_root = Path(
            os.environ.get("FLASK_ROOT",
            Path.home() / "Downloads/Offline_testing/Research/flask")
        )
        if not flask_root.exists():
            print(f"Flask root not found: {flask_root}")
            print("Set FLASK_ROOT env var or clone Flask to that path.")
            sys.exit(1)

        runner  = _make_runner(engine, flask_root)
        results = runner.run_all(
            modes=("full", "no_ki", "no_sandbox", "cl100k"),
            checkpoint_path="results/flask/ablation_results_v3.json",
        )

        out_path = Path("results/flask/ablation_results_v3.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(results, indent=2))
        print(f"Ablation results written to: {out_path}")

    def do_flask_repeats():
        print(f"\n=== STEP 2b: Running Flask ablation ({repeats} repeats) ===")
        import os

        flask_root = Path(
            os.environ.get("FLASK_ROOT",
            Path.home() / "Downloads/Offline_testing/Research/flask")
        )
        if not flask_root.exists():
            print(f"Flask root not found: {flask_root}")
            print("Set FLASK_ROOT env var or clone Flask to that path.")
            sys.exit(1)

        runner = _make_runner(engine, flask_root)
        runner.run_all_with_repeats(
            n_repeats=repeats,
            modes=("full", "no_ki", "no_sandbox", "cl100k"),
            checkpoint_path="results/flask/ablation_results_v3_repeats.json",
        )

    def do_fastapi():
        print("\n=== STEP 3: Running FastAPI ablation ===")
        import os
        from benchmark.fastapi_tasks import FASTAPI_TASKS

        fastapi_root = Path(
            os.environ.get("FASTAPI_ROOT",
            Path.home() / "Downloads/Offline_testing/Research/fastapi")
        )
        if not fastapi_root.exists():
            print(f"FastAPI root not found: {fastapi_root}")
            print("Set FASTAPI_ROOT env var or clone FastAPI to that path.")
            print("  git clone https://github.com/tiangolo/fastapi.git")
            sys.exit(1)

        runner = _make_runner(engine, fastapi_root)
        runner.run_all_with_repeats(
            n_repeats=repeats,
            modes=("full", "no_ki", "no_sandbox", "cl100k"),
            tasks=FASTAPI_TASKS,
            checkpoint_path="results/fastapi/ablation_results_v3_repeats.json",
        )

    def do_swebench_pilot():
        print("\n=== STEP 4: Running SWE-bench 20-instance pilot cohort ===")
        from benchmark.swebench_runner import SWEBenchRunner
        runner = SWEBenchRunner(engine, output_dir="results/swebench-pilot")
        result = runner.pilot_run(n=20)
        print(f"\nPilot complete: {result['pilot_rate']:.1f}% resolve rate")
        print("If >10%: proceed to swebench-full")
        print("If <5%: fix localization before full run")

    def do_swebench_full():
        print("\n=== STEP 5: Running Full SWE-bench Lite Cohort (300 instances) ===")
        from benchmark.swebench_runner import SWEBenchRunner
        runner = SWEBenchRunner(engine, output_dir="results/swebench")
        result = runner.run_all(max_instances=max_n, resume=resume)
        print(f"\nFinal: {result['resolve_rate']:.1f}% resolve rate")

    def do_failure_analysis():
        print("\n=== STEP 6: Running failure analysis ===")
        from benchmark.failure_analysis import analyse_failures

        # Analyse Flask results
        for name, path in [
            ("Flask (v3)",   "results/flask/ablation_results_v3.json"),
            ("Flask (repeats)", "results/flask/ablation_results_v3_repeats.json"),
            ("FastAPI",      "results/fastapi/ablation_results_v3_repeats.json"),
        ]:
            if Path(path).exists():
                print(f"\n--- {name} ---")
                result = analyse_failures(
                    path,
                    n_worst=10,
                    output_path=path.replace(".json", "_failure_analysis.json"),
                )
                print(f"  Passed: {result.get('total_passed', 0)}/{result.get('total_tasks', 0)}")
                print(f"  Categories: {result.get('category_counts_worst_n', {})}")
            else:
                print(f"  [skip] {name} — no results at {path}")

    def do_tables():
        print("\n=== STEP 7: Compiling final publication-grade LaTeX tables ===")
        from benchmark.reporter import PaperReporter
        reporter = PaperReporter()

        he_path = "results/humaneval/humaneval_results.jsonl"
        if Path(he_path).exists():
            print("\n--- Table 1: HumanEval ---")
            print(reporter.table1_humaneval(he_path))

        # Prefer v3 repeats, fall back to v3, then v2
        for flask_path in [
            "results/flask/ablation_results_v3_repeats.json",
            "results/flask/ablation_results_v3.json",
            "results/flask/ablation_results_v2.json",
        ]:
            if Path(flask_path).exists():
                print(f"\n--- Table 2: Ablation ({flask_path}) ---")
                print(reporter.table2_ablation(flask_path))
                break

        swe_path = "results/swebench/"
        if Path(swe_path).exists() and (Path(swe_path) / "predictions.jsonl").exists():
            print("\n--- Table 3: SWE-bench ---")
            print(reporter.table3_swebench(swe_path))

    # ── Dispatch ─────────────────────────────────────────────────────────
    if step == "humaneval":
        do_humaneval()
    elif step == "flask":
        do_flask()
    elif step == "flask-repeats":
        do_flask_repeats()
    elif step == "fastapi":
        do_fastapi()
    elif step == "swebench-pilot":
        do_swebench_pilot()
    elif step == "swebench-full":
        do_swebench_full()
    elif step == "failure-analysis":
        do_failure_analysis()
    elif step == "tables":
        do_tables()
    elif step == "all":
        do_humaneval()
        do_flask()
        do_flask_repeats()
        do_fastapi()
        do_failure_analysis()
        do_tables()


if __name__ == "__main__":
    run()
