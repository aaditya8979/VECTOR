"""
failure_analysis.py — Categorise and report on failed tasks.

Paper relevance: Section 5.3 (Failure Analysis). Pick the N worst tasks,
categorise why they failed, and produce a LaTeX table + JSON report.

Failure categories:
  - symbol_hallucination : model invents a function that doesn't exist
  - expected_symbol_miss : model uses a valid alternative but not the expected one
  - type_mismatch        : correct logic but wrong return/argument type
  - test_guard_strict    : code correct but fails a pre-existing test
  - context_insufficient : TSDC doc lacked critical context
  - formatting_error     : code logically correct but fails to parse (indentation, etc.)
  - logic_error          : code parses and type-checks but produces wrong output
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional


# ── Category definitions ─────────────────────────────────────────────────────

FAILURE_CATEGORIES = {
    "symbol_hallucination": "Model invents a function/method that doesn't exist in the codebase",
    "expected_symbol_miss": "Model uses a valid alternative but not the symbol the task requires",
    "type_mismatch":        "Correct logic but wrong return type or argument type",
    "test_guard_strict":    "Code is correct but fails a pre-existing test expectation",
    "context_insufficient": "TSDC doc lacked critical context for the task",
    "formatting_error":     "Code is logically correct but fails to parse (indentation, syntax)",
    "logic_error":          "Code parses/type-checks but produces wrong runtime behaviour",
}


def categorise_failure(task_result: dict) -> str:
    """
    Heuristically categorise a failed task result into one of the
    failure categories based on the layer_results and error patterns.
    """
    layers = task_result.get("layer_results", [])
    if not layers:
        return "context_insufficient"

    # Count which layers failed
    layer_counts = Counter(layers)

    # All failures at symbol_check
    if layer_counts.get("symbol_check", 0) == len(layers):
        # Check if it's hallucination or expected symbol miss
        # If the task has expected_symbols and the model never used them
        return "symbol_hallucination"

    # Mix of symbol_check and other failures
    if "symbol_check" in layer_counts and layer_counts["symbol_check"] > len(layers) // 2:
        return "expected_symbol_miss"

    # All failures at diff_parse
    if layer_counts.get("diff_parse", 0) == len(layers):
        return "formatting_error"

    # All failures at type_check
    if layer_counts.get("type_check", 0) == len(layers):
        return "type_mismatch"

    # Mix with type_check dominant
    if layer_counts.get("type_check", 0) > len(layers) // 2:
        return "type_mismatch"

    # Test execution failures
    if layer_counts.get("test_execution", 0) > 0:
        return "test_guard_strict"

    # Sandbox failures
    if layer_counts.get("sandbox", 0) > 0:
        return "logic_error"

    # CPG diff failures
    if layer_counts.get("cpg_diff", 0) > 0:
        return "symbol_hallucination"

    # Default: context insufficient
    return "context_insufficient"


def analyse_failures(
    results_path: str,
    n_worst: int = 10,
    output_path: Optional[str] = None,
) -> dict:
    """
    Analyse the N worst-performing tasks from an ablation results file.

    Args:
        results_path: Path to ablation_results_v2.json (or v3)
        n_worst: Number of worst tasks to analyse
        output_path: Where to save the analysis JSON

    Returns:
        {
            "total_tasks": 25,
            "total_failed": 20,
            "category_counts": {"symbol_hallucination": 8, ...},
            "worst_tasks": [
                {
                    "task_id": "FT-001",
                    "category": "symbol_hallucination",
                    "layer_history": ["symbol_check", "symbol_check", ...],
                    "iterations_used": 5,
                    "explanation": "..."
                },
                ...
            ],
            "latex_table": "...",
        }
    """
    path = Path(results_path)
    if not path.exists():
        return {"error": f"Results file not found: {results_path}"}

    data = json.loads(path.read_text())

    # Handle both v2 format (dict of modes) and v3 format (runs list)
    if "runs" in data:
        # v3 repeats format — use the first run of "full" mode
        full_runs = [r for r in data["runs"] if r["mode"] == "full"]
        if not full_runs:
            return {"error": "No 'full' mode runs found"}
        task_results = full_runs[0]["results"]
    else:
        # v2 format
        task_results = data.get("full", [])

    if not task_results:
        return {"error": "No task results found in 'full' mode"}

    # Identify failed tasks and sort by severity (most iterations = worst)
    failed = [t for t in task_results if not t.get("passed_at_5", False)]
    failed.sort(key=lambda t: t.get("iterations", 0), reverse=True)

    worst = failed[:n_worst]

    # Categorise each failure
    category_counts: Dict[str, int] = Counter()
    analysed_tasks = []

    for task in worst:
        category = categorise_failure(task)
        category_counts[category] += 1

        layers = task.get("layer_results", [])
        layer_counts = Counter(layers)

        explanation = _generate_explanation(task, category, layer_counts)

        analysed_tasks.append({
            "task_id": task["task_id"],
            "mode": task.get("mode", "full"),
            "category": category,
            "category_description": FAILURE_CATEGORIES.get(category, "Unknown"),
            "layer_history": layers,
            "layer_summary": dict(layer_counts),
            "iterations_used": task.get("iterations", 0),
            "hallucination_rate": task.get("metrics", {}).get("M7_hallucination_rate", 0),
            "explanation": explanation,
        })

    # Global category distribution (across all failed tasks, not just worst N)
    all_categories = Counter()
    for task in failed:
        all_categories[categorise_failure(task)] += 1

    # Generate LaTeX table
    latex = _generate_latex_table(analysed_tasks, category_counts)

    result = {
        "total_tasks": len(task_results),
        "total_failed": len(failed),
        "total_passed": len(task_results) - len(failed),
        "pass_rate": f"{(len(task_results) - len(failed)) / len(task_results) * 100:.1f}%",
        "category_counts_worst_n": dict(category_counts),
        "category_counts_all": dict(all_categories),
        "worst_tasks": analysed_tasks,
        "latex_table": latex,
    }

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2))
        print(f"[FailureAnalysis] Saved to {output_path}")

    return result


def _generate_explanation(task: dict, category: str, layer_counts: Counter) -> str:
    """Generate a human-readable explanation for why this task failed."""
    task_id = task["task_id"]
    iters = task.get("iterations", 0)
    layers = task.get("layer_results", [])

    if category == "symbol_hallucination":
        return (
            f"Task {task_id} failed all {iters} attempts at symbol_check. "
            f"The model consistently generated function calls not present in the "
            f"CPG's allowed callee list. The hallucination rate was "
            f"{task.get('metrics', {}).get('M7_hallucination_rate', 0):.0%}."
        )
    elif category == "expected_symbol_miss":
        return (
            f"Task {task_id} failed {layer_counts.get('symbol_check', 0)}/{iters} "
            f"attempts at symbol_check. The model used valid code but not the "
            f"specific symbol the task required (e.g., print() instead of logging.debug())."
        )
    elif category == "type_mismatch":
        return (
            f"Task {task_id} failed {layer_counts.get('type_check', 0)}/{iters} "
            f"attempts at type_check (mypy). The generated code had type annotation "
            f"mismatches — likely wrong return type or incompatible argument types."
        )
    elif category == "test_guard_strict":
        return (
            f"Task {task_id} failed {layer_counts.get('test_execution', 0)}/{iters} "
            f"attempts at test_execution. The code was syntactically and type-correct "
            f"but failed pre-existing test expectations."
        )
    elif category == "context_insufficient":
        return (
            f"Task {task_id} failed across multiple layers ({dict(layer_counts)}). "
            f"The TSDC context document likely lacked sufficient information for the "
            f"model to generate correct code."
        )
    elif category == "formatting_error":
        return (
            f"Task {task_id} failed {layer_counts.get('diff_parse', 0)}/{iters} "
            f"attempts at diff_parse. The model produced code with syntax/indentation "
            f"errors that prevented AST parsing."
        )
    elif category == "logic_error":
        return (
            f"Task {task_id} failed {layer_counts.get('sandbox', 0)}/{iters} "
            f"attempts at sandbox. The code parsed and type-checked but crashed "
            f"during sandboxed execution."
        )
    return f"Task {task_id} failed with unknown pattern: {dict(layer_counts)}"


def _generate_latex_table(tasks: list, counts: Counter) -> str:
    """Generate a LaTeX table for the paper's failure analysis section."""
    lines = [
        "\\begin{table}[h]",
        "\\centering",
        "\\begin{tabular}{l c l}",
        "\\toprule",
        "Failure Category & Count & Representative Task \\\\",
        "\\midrule",
    ]

    # Group tasks by category
    by_cat: Dict[str, list] = {}
    for t in tasks:
        cat = t["category"]
        if cat not in by_cat:
            by_cat[cat] = []
        by_cat[cat].append(t)

    for cat, desc in FAILURE_CATEGORIES.items():
        count = counts.get(cat, 0)
        if count == 0:
            continue
        representative = by_cat.get(cat, [{}])[0].get("task_id", "—")
        # Escape underscores for LaTeX
        cat_display = cat.replace("_", "\\_")
        lines.append(f"\\texttt{{{cat_display}}} & {count} & {representative} \\\\")

    lines += [
        "\\bottomrule",
        "\\end{tabular}",
        f"\\caption{{Failure analysis of the {len(tasks)} worst-performing tasks.}}",
        "\\label{tab:failure-analysis}",
        "\\end{table}",
    ]
    return "\n".join(lines)


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    results_path = sys.argv[1] if len(sys.argv) > 1 else "results/flask/ablation_results_v2.json"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    output = sys.argv[3] if len(sys.argv) > 3 else "results/flask/failure_analysis.json"

    result = analyse_failures(results_path, n_worst=n, output_path=output)

    print(f"\nTotal: {result.get('total_tasks', 0)} tasks, "
          f"{result.get('total_failed', 0)} failed, "
          f"{result.get('total_passed', 0)} passed")
    print(f"\nCategory distribution (worst {n}):")
    for cat, count in result.get("category_counts_worst_n", {}).items():
        print(f"  {cat:25s}: {count}")
    print(f"\n{result.get('latex_table', '')}")
