"""
CLI commands — the user-facing interface for the TSDC agent.

Commands:
  tsdc init      — Build CPG for a project, create .codeagent/
  tsdc watch     — Start real-time file watcher (background)
  tsdc modify    — Modify a specific function (the main command)
  tsdc status    — Show project state, CPG stats, metrics summary
  tsdc resume    — Resume last incomplete task
  tsdc benchmark — Run benchmark suite
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import click
from rich.console import Console
from rich.table   import Table
from rich.panel   import Panel
from rich         import print as rprint

console = Console()


def _get_brain_paths(project_root: str):
    from config import BRAIN_DIR, DB_FILE, CPG_FILE, KNOWLEDGE_DIR
    brain  = Path(project_root) / BRAIN_DIR
    return {
        "brain":     brain,
        "db":        brain / DB_FILE,
        "cpg":       brain / CPG_FILE,
        "knowledge": brain / KNOWLEDGE_DIR,
    }


def _load_system(project_root: str):
    """Load CPG, StateDB, KnowledgeStore. Returns (builder, db, knowledge)."""
    from core.cpg.builder    import CPGBuilder
    from core.memory.state_db import StateDB
    from core.memory.knowledge import KnowledgeStore

    paths = _get_brain_paths(project_root)
    if not paths["cpg"].exists():
        console.print("[red]Project not initialised. Run: tsdc init[/red]")
        sys.exit(1)

    builder   = CPGBuilder.load(str(paths["cpg"]), project_root)
    db        = StateDB(str(paths["db"]))
    knowledge = KnowledgeStore(str(paths["knowledge"]))
    return builder, db, knowledge


# ── init ──────────────────────────────────────────────────────────────────────

@click.command()
@click.argument("project_root", default=".", type=click.Path(exists=True))
@click.option("--force", is_flag=True, help="Rebuild CPG even if it already exists.")
def init(project_root: str, force: bool):
    """Initialise TSDC for a project. Builds the Code Property Graph."""
    from core.cpg.builder import CPGBuilder
    from config           import BRAIN_DIR, CPG_FILE

    project_root = str(Path(project_root).resolve())
    paths        = _get_brain_paths(project_root)
    paths["brain"].mkdir(parents=True, exist_ok=True)
    paths["knowledge"].mkdir(parents=True, exist_ok=True)
    (paths["brain"] / "diffs").mkdir(exist_ok=True)

    cpg_path = paths["cpg"]
    if cpg_path.exists() and not force:
        console.print(f"[yellow]CPG already exists at {cpg_path}. Use --force to rebuild.[/yellow]")
        return

    console.print(f"\n[bold blue]Building Code Property Graph for:[/bold blue] {project_root}")
    t0      = time.time()
    builder = CPGBuilder(project_root)

    with console.status("[bold green]Parsing source files..."):
        builder.build()

    elapsed = time.time() - t0
    n_nodes = len(builder.nodes)
    n_edges = builder.graph.number_of_edges()

    console.print(f"  [green]✓[/green] {n_nodes} function nodes")
    console.print(f"  [green]✓[/green] {n_edges} call graph edges")
    console.print(f"  [green]✓[/green] Built in {elapsed:.1f}s")

    with console.status("Saving CPG..."):
        builder.save(str(cpg_path))

    console.print(f"\n[bold green]✓ Project initialised.[/bold green] 🎉")
    console.print(f"  Brain directory: {paths['brain']}")
    console.print(f"  Next step: [bold]tsdc watch[/bold] to start real-time sync\n")


# ── watch ─────────────────────────────────────────────────────────────────────

@click.command()
@click.argument("project_root", default=".", type=click.Path(exists=True))
def watch(project_root: str):
    """Start real-time CPG synchronisation (runs until Ctrl+C)."""
    from core.cpg.builder    import CPGBuilder
    from core.watcher.fs_watcher import FSWatcher
    from config              import BRAIN_DIR, CPG_FILE

    project_root = str(Path(project_root).resolve())
    paths        = _get_brain_paths(project_root)
    builder      = CPGBuilder.load(str(paths["cpg"]), project_root)

    def on_change(changed_file: str, changed_nodes: list):
        rel = Path(changed_file).relative_to(project_root)
        console.print(f"  [cyan]↺[/cyan] {rel} — {len(changed_nodes)} node(s) updated")

    console.print(f"\n[bold blue]Watching[/bold blue] {project_root}")
    console.print("Press Ctrl+C to stop.\n")

    with FSWatcher(builder, project_root, on_change=on_change):
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    console.print("\n[yellow]Watcher stopped.[/yellow]")


# ── modify ────────────────────────────────────────────────────────────────────

@click.command()
@click.argument("file_path")
@click.argument("func_name")
@click.argument("goal")
@click.option("--project", "-p", default=".", type=click.Path(exists=True))
@click.option("--test-guard", "-t", default="", help="Space-separated pytest node IDs that must pass.")
@click.option("--max-iterations", "-n", default=5, help="Max regeneration attempts.")
@click.option("--dry-run", is_flag=True, help="Show TSDC document without running model.")
def modify(
    file_path:      str,
    func_name:      str,
    goal:           str,
    project:        str,
    test_guard:     str,
    max_iterations: int,
    dry_run:        bool,
):
    """
    Modify a function in the codebase.

    \b
    FILE_PATH  — relative path, e.g. src/services/user.py
    FUNC_NAME  — function to modify, e.g. authenticate
    GOAL       — plain-English goal, e.g. "add OAuth2 validation before password check"
    """
    from core.tsdc.generator  import TSDCGenerator
    from core.model.inference import InferenceEngine
    from verification.pipeline import VerificationPipeline
    from benchmark.metrics     import MetricsStore, TaskMetrics
    from core.tsdc.budget      import count_tokens
    from config                import MAX_ITERATIONS, BENCHMARK_OUTPUT_DIR

    project_root = str(Path(project).resolve())
    builder, db, knowledge = _load_system(project_root)
    paths  = _get_brain_paths(project_root)

    # ── Resolve function name (auto-detect for single-function files) ────
    node = builder.find_node_by_function(file_path, func_name)
    if node is None:
        file_nodes = [n for n in builder.nodes.values() if n.file_path == file_path]
        if not file_nodes:
            console.print(f"[red]No functions found in {file_path}. Run: tsdc init --force[/red]")
            sys.exit(1)
        elif len(file_nodes) == 1:
            node = file_nodes[0]
            console.print(f"  [dim]Auto-detected function: {node.function_name}[/dim]")
            func_name = node.function_name
        else:
            console.print(f"[yellow]Function '{func_name}' not found. Available in {file_path}:[/yellow]")
            for n in sorted(file_nodes, key=lambda x: x.start_line):
                console.print(f"  {n.start_line:4d}  {n.signature}")
            console.print(f"\nRun: tsdc modify {file_path} <function_name> \"goal\"")
            sys.exit(1)

    max_iter = max_iterations or MAX_ITERATIONS
    task_id  = db.create_task(goal, file_path, func_name)
    db.update_task(task_id, status="in_progress")

    console.print(f"\n[bold blue]TSDC Modify[/bold blue]")
    console.print(f"  Target : [cyan]{file_path}::{func_name}[/cyan]")
    console.print(f"  Goal   : {goal}")
    console.print(f"  Task ID: #{task_id}\n")

    # Generate TSDC document
    generator = TSDCGenerator(builder, db, knowledge)
    try:
        abs_file_path = str(Path(project_root) / file_path)
        tsdc_doc = generator.generate(
            file_path  = file_path,
            func_name  = func_name,
            task_goal  = goal,
            test_guard = test_guard,
        )
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        db.update_task(task_id, status="failed", result=str(e))
        sys.exit(1)

    tsdc_tokens = count_tokens(tsdc_doc)
    console.print(f"  TSDC document: [green]{tsdc_tokens} tokens[/green] (budget: 2500)")

    if dry_run:
        console.print("\n[bold yellow]── DRY RUN — TSDC Document ──[/bold yellow]")
        console.print(Panel(tsdc_doc, title="TSDC Context", border_style="blue"))
        return

    # Load model
    with console.status("[bold green]Loading model (Metal inference)..."):
        engine = InferenceEngine.get()

    pipeline   = VerificationPipeline(project_root)
    store      = MetricsStore(str(paths["brain"] / "metrics"))
    metrics    = TaskMetrics(task_id=task_id, func_name=func_name, file_path=file_path,
                             tsdc_tokens_used=tsdc_tokens)
    t_total    = time.time()
    error_feed = None
    prev_error_type = None  # v2.1: track for fresh-start retry

    # v2.1: Extract allowed callees from CPG for hallucination checking
    allowed_callees = []
    if node:
        callee_nodes = builder.get_direct_callees(node.node_id)
        allowed_callees = [c.function_name for c in callee_nodes]

    for attempt in range(1, max_iter + 1):
        console.print(f"  [bold]Attempt {attempt}/{max_iter}[/bold]", end=" ")

        if error_feed or attempt > 1:
            tsdc_doc = generator.generate(
                file_path       = file_path,
                func_name       = func_name,
                task_goal       = goal,
                test_guard      = test_guard,
                error_feedback  = error_feed,
                attempt         = attempt,
                prev_error_type = prev_error_type,
            )

        # Inference — T=0.0 on attempt 1, T=0.3 on retries (v2.1)
        retry_temp = 0.0 if attempt == 1 else 0.3
        t_inf  = time.time()
        func_code, inf_stats = engine.generate_function(
            tsdc_doc, grammar_mode="relaxed", temperature=retry_temp
        )
        metrics.inference_ms += (time.time() - t_inf) * 1000
        console.print(f"[dim]({inf_stats['tokens_per_sec']} tok/s)[/dim]")

        # Verification — with allowed_callees for AST-based hallucination check
        t_ver  = time.time()
        result = pipeline.run(
            func_code, file_path, func_name, test_guard,
            allowed_callees=allowed_callees,
        )
        metrics.verification_ms += (time.time() - t_ver) * 1000
        metrics.layer_results    = result.layer_results

        if result.passed:
            metrics.passed_final   = True
            metrics.passed_first_shot = (attempt == 1)
            metrics.iterations     = attempt
            metrics.total_ms       = (time.time() - t_total) * 1000

            diffs_dir = paths["brain"] / "diffs"
            diffs_dir.mkdir(parents=True, exist_ok=True)

            # 3. Archive the generated function for audit trail
            diff_archive = diffs_dir / f"task{task_id}_{Path(file_path).stem}{Path(file_path).suffix}"
            diff_archive.write_text(func_code, encoding="utf-8")

            # Log change and extract knowledge
            db.log_change(
                node_id     = f"{file_path}::{func_name}",
                file_path   = file_path,
                func_name   = func_name,
                description = goal,
                task_id     = task_id,
            )
            ki_rules = knowledge.extract_rules_from_diff(func_code, func_name, file_path, {})
            db.update_task(task_id, status="completed", iterations=attempt)
            store.record(metrics)

            console.print(f"\n  [bold green]✓ Verified and committed in {attempt} attempt(s)[/bold green]")
            console.print(f"  Verification layers: {result.layer_results}")
            if ki_rules:
                console.print(f"  Knowledge Items extracted: {len(ki_rules)}")
            console.print(f"  Total time: {metrics.total_ms/1000:.1f}s\n")
            return

        # Failed — track error type and prepare feedback
        prev_error_type = result.layer_failed
        error_feed = result.feedback_for_model(allowed_callees=allowed_callees)
        metrics.failed_layer = result.layer_failed
        console.print(f"  [red]✗ Failed at: {result.layer_failed}[/red]")
        console.print(f"    {result.error_message[:120]}")
        console.print("\n[dim]Generated Output (for debugging):[/dim]")
        console.print(Panel(func_code, border_style="red"))

    # All attempts exhausted
    metrics.iterations = max_iter
    metrics.total_ms   = (time.time() - t_total) * 1000
    store.record(metrics)
    db.update_task(task_id, status="failed", result=error_feed)
    console.print(f"\n  [red]✗ Could not verify modification after {max_iter} attempts.[/red]")
    console.print("  The original file is unchanged.\n")
    sys.exit(1)


# ── status ────────────────────────────────────────────────────────────────────

@click.command()
@click.argument("project_root", default=".", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True, default=False,
              help="Output status as JSON (for editor integration).")
def status(project_root: str, as_json: bool):
    """Show CPG statistics, task history, and metrics summary."""
    from benchmark.metrics import MetricsStore

    project_root = str(Path(project_root).resolve())
    builder, db, knowledge = _load_system(project_root)
    paths = _get_brain_paths(project_root)

    stale    = sum(1 for n in builder.nodes.values() if n.is_stale)
    ki_stats = knowledge.stats()
    last     = db.get_last_incomplete_task()

    # ── JSON output for VS Code extension ────────────────────────────────
    if as_json:
        import json as _json
        data = {
            "nodes":           len(builder.nodes),
            "edges":           builder.graph.number_of_edges(),
            "stale_nodes":     stale,
            "knowledge_items": ki_stats["total_rules"],
            "watching":        False,   # Can't tell from CLI — watcher is a daemon
            "last_task":       last["description"] if last else None,
        }
        print(_json.dumps(data))
        return

    # ── Rich terminal output ─────────────────────────────────────────────
    console.print(f"\n[bold blue]TSDC Project Status[/bold blue]")
    console.print(f"  Root: {project_root}\n")

    # CPG stats
    table = Table(title="Code Property Graph", show_header=True, header_style="bold cyan")
    table.add_column("Metric",  style="dim")
    table.add_column("Value",   justify="right")
    table.add_row("Function nodes",   str(len(builder.nodes)))
    table.add_row("Call graph edges", str(builder.graph.number_of_edges()))
    table.add_row("Stale nodes",      f"[yellow]{stale}[/yellow]" if stale else "0")
    console.print(table)

    # Knowledge
    console.print(f"\n  Knowledge Items: {ki_stats['total_rules']}")
    console.print(f"  Avg KI hit rate: {ki_stats['avg_hit_rate']*100:.1f}%")

    # Last task
    if last:
        console.print(f"\n  [yellow]Incomplete task #{last['id']}: {last['description']}[/yellow]")
        console.print(f"  Target: {last['target_file']}::{last['target_func']}")
        console.print("  Run [bold]tsdc resume[/bold] to continue.")

    # Metrics summary
    store   = MetricsStore(str(paths["brain"] / "metrics"))
    summary = store.summary()
    if "error" not in summary:
        console.print(f"\n  Tasks run:     {summary['total_tasks']}")
        console.print(f"  pass@1:        {summary['M4_pass_at_1']}")
        console.print(f"  pass@k:        {summary['M4_pass_at_k']}")
        console.print(f"  Compression:   {summary['M2_avg_compression']}")
        console.print(f"  Hallucination: {summary['M7_hallucination_rate']}")

    console.print()


# ── resume ────────────────────────────────────────────────────────────────────

@click.command()
@click.argument("project_root", default=".", type=click.Path(exists=True))
def resume(project_root: str):
    """Resume the last incomplete modification task."""
    project_root = str(Path(project_root).resolve())
    _, db, _ = _load_system(project_root)

    last = db.get_last_incomplete_task()
    if not last:
        console.print("[green]No incomplete tasks. Everything is up to date.[/green]")
        return

    console.print(f"\n[bold yellow]Resuming task #{last['id']}[/bold yellow]")
    console.print(f"  {last['description']}")
    console.print(f"  {last['target_file']}::{last['target_func']}\n")

    # Invoke modify with the same parameters
    from click.testing import CliRunner
    runner = CliRunner()
    runner.invoke(modify, [
        last["target_file"],
        last["target_func"],
        last["description"],
        "--project", project_root,
    ])


# ── benchmark ─────────────────────────────────────────────────────────────────

@click.command()
@click.option("--tier", default="1", type=click.Choice(["1", "2", "3"]),
              help="Benchmark tier to run.")
@click.option("--max-problems", "-n", default=164, help="Max problems to evaluate.")
@click.option("--k", default=5, help="Max regeneration attempts per problem.")
@click.option("--project", "-p", default=".", type=click.Path(exists=True))
def benchmark(tier: str, max_problems: int, k: int, project: str):
    """Run the benchmark suite (Tier 1=HumanEval, 2=RepoBench, 3=SWE-bench)."""
    from benchmark.harness import BenchmarkHarness

    harness = BenchmarkHarness()
    if tier == "1":
        harness.run_humaneval(max_problems=max_problems, k_attempts=k)
    elif tier == "2":
        harness.run_repobench(max_problems=max_problems)
    elif tier == "3":
        harness.run_swebench(max_instances=max_problems)

    console.print(harness.full_report())


# ── list-functions ────────────────────────────────────────────────────────────

@click.command("list-functions")
@click.argument("file_path", type=click.Path(exists=False))
@click.option("--project", "-p", default=".", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True, default=False)
def list_functions(file_path: str, project: str, as_json: bool):
    """List all functions in a file (for editor integration)."""
    project_root = str(Path(project).resolve())
    builder, _, _ = _load_system(project_root)
    
    # Path in CPG is absolute
    abs_path = str(Path(project_root) / file_path)
    
    funcs = []
    for node in builder.nodes.values():
        # CPG may store relative or absolute — match either way
        if (node.file_path == file_path or
            node.file_path.endswith(file_path) or
            file_path.endswith(node.file_path)):
            funcs.append({
                "name": node.function_name,
                "signature": node.signature,
                "line": node.start_line,
                "class_name": node.class_name,
            })
            
    # Sort by line number
    funcs.sort(key=lambda x: x["line"])
    
    if as_json:
        import json
        print(json.dumps(funcs))
    else:
        for f in funcs:
            cls_part = f"{f['class_name']}::" if f['class_name'] else ""
            console.print(f"{f['line']:>4} | {cls_part}{f['name']}")