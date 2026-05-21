#!/usr/bin/env python3
"""
Full benchmark run script — generates all paper tables.
Run once. Takes 6-24 hours depending on hardware.

Usage:
    # Step 1: Complete HumanEval (required for Table 1)
    python benchmark/full_run.py --step humaneval

    # Step 2: Flask ablation (required for Table 2)
    python benchmark/full_run.py --step flask

    # Step 3: SWE-bench pilot (20 instances — validate before full run)
    python benchmark/full_run.py --step swebench-pilot

    # Step 4: Full SWE-bench Lite (300 instances — the paper headline)
    python benchmark/full_run.py --step swebench-full

    # Step 5: Generate paper tables
    python benchmark/full_run.py --step tables
"""
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

import click


@click.command()
@click.option("--step", type=click.Choice([
    "humaneval", "flask", "swebench-pilot", "swebench-full", "tables", "all"
]))
@click.option("--k", default=5, help="pass@k for HumanEval")
@click.option("--resume/--no-resume", default=True)
@click.option("--max", "max_n", default=300)
def run(step, k, resume, max_n):
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
        print("\n=== STEP 2: Running Flask dynamic ablation benchmark ===")
        import os
        from benchmark.flask_tasks import FlaskAblationRunner
        from verification.pipeline import VerificationPipeline
        from core.memory.state_db  import StateDB
        from core.memory.knowledge import KnowledgeStore

        flask_root = Path(
            os.environ.get("FLASK_ROOT",
            Path.home() / "Downloads/Offline_testing/Research/flask")
        )
        if not flask_root.exists():
            print(f"Flask root not found: {flask_root}")
            print("Set FLASK_ROOT env var or clone Flask to that path.")
            sys.exit(1)

        brain_dir = flask_root / ".codeagent"
        brain_dir.mkdir(parents=True, exist_ok=True)

        pipeline  = VerificationPipeline(str(flask_root))
        db        = StateDB(str(brain_dir / "state.db"))
        knowledge = KnowledgeStore(str(brain_dir / "knowledge"))
        runner    = FlaskAblationRunner(str(flask_root), engine, pipeline, db, knowledge)
        results   = runner.run_all(modes=("full", "no_ki", "no_sandbox", "cl100k"))

        import json
        out_path = Path("results/flask/ablation_results.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(results, indent=2))
        print(f"Ablation results written to: {out_path}")

    def do_swebench_pilot():
        print("\n=== STEP 3: Running SWE-bench 20-instance pilot cohort ===")
        from benchmark.swebench_runner import SWEBenchRunner
        runner = SWEBenchRunner(engine, output_dir="results/swebench-pilot")
        result = runner.pilot_run(n=20)
        print(f"\nPilot complete: {result['pilot_rate']:.1f}% resolve rate")
        print("If >10%: proceed to swebench-full")
        print("If <5%: fix localization before full run")

    def do_swebench_full():
        print("\n=== STEP 4: Running Full SWE-bench Lite Cohort (300 instances) ===")
        from benchmark.swebench_runner import SWEBenchRunner
        runner = SWEBenchRunner(engine, output_dir="results/swebench")
        result = runner.run_all(max_instances=max_n, resume=resume)
        print(f"\nFinal: {result['resolve_rate']:.1f}% resolve rate")

    def do_tables():
        print("\n=== STEP 5: Compiling final publication-grade LaTeX tables ===")
        from benchmark.reporter import PaperReporter
        reporter = PaperReporter()

        he_path = "results/humaneval/humaneval_results.jsonl"
        if Path(he_path).exists():
            print(reporter.table1_humaneval(he_path))

        flask_path = "results/flask/ablation_results.json"
        if Path(flask_path).exists():
            print(reporter.table2_ablation(flask_path))

        swe_path = "results/swebench/"
        if Path(swe_path).exists() and (Path(swe_path) / "predictions.jsonl").exists():
            print(reporter.table3_swebench(swe_path))

    if step == "humaneval":
        do_humaneval()
    elif step == "flask":
        do_flask()
    elif step == "swebench-pilot":
        do_swebench_pilot()
    elif step == "swebench-full":
        do_swebench_full()
    elif step == "tables":
        do_tables()
    elif step == "all":
        do_humaneval()
        do_flask()
        do_swebench_pilot()
        do_tables()


if __name__ == "__main__":
    run()
