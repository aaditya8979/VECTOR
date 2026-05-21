"""
Metrics tracker — records all 7 system-specific metrics per task.
These are your research paper numbers. Every task run writes here.

M1 — TSDC budget utilisation      (tokens used / 2500 budget)
M2 — Compression ratio            (original context tokens / TSDC tokens)
M3 — Verification loop iterations (attempts before pass)
M4 — Pass@1 and Pass@k            (first-shot vs post-loop accuracy)
M5 — Knowledge Item hit rate      (KI retrieved and helped / KI retrieved)
M6 — CPG freshness                (stale nodes at task start / nodes queried)
M7 — Hallucination rate           (invented symbols caught / total added symbols)
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from statistics import mean, median, stdev
from typing import Dict, List, Optional


@dataclass
class TaskMetrics:
    task_id:             int
    func_name:           str
    file_path:           str
    timestamp:           float = field(default_factory=time.time)

    # M1 — Budget
    tsdc_tokens_used:    int   = 0
    tsdc_budget:         int   = 2500
    budget_utilisation:  float = 0.0   # tsdc_tokens_used / tsdc_budget

    # M2 — Compression
    original_tokens:     int   = 0
    compression_ratio:   float = 0.0   # original / tsdc

    # M3 — Loop iterations
    iterations:          int   = 0
    max_iterations:      int   = 5

    # M4 — Accuracy
    passed_first_shot:   bool  = False
    passed_final:        bool  = False
    failed_layer:        Optional[str] = None

    # M5 — KI
    ki_retrieved:        int   = 0
    ki_helped:           int   = 0

    # M6 — CPG freshness
    stale_nodes_at_start: int  = 0
    nodes_queried:        int  = 0

    # M7 — Hallucination
    invented_symbols_caught: int = 0
    total_added_symbols:     int = 0

    # Timing
    tsdc_gen_ms:         float = 0.0
    inference_ms:        float = 0.0
    verification_ms:     float = 0.0
    total_ms:            float = 0.0

    # Layer breakdown
    layer_results:       Dict[str, bool] = field(default_factory=dict)

    def finalise(self):
        """Compute derived metrics after all fields are populated."""
        if self.tsdc_budget > 0:
            self.budget_utilisation = round(self.tsdc_tokens_used / self.tsdc_budget, 3)
        if self.tsdc_tokens_used > 0:
            self.compression_ratio = round(self.original_tokens / self.tsdc_tokens_used, 2)

    def to_dict(self) -> dict:
        return asdict(self)


class MetricsStore:
    """
    Persists all TaskMetrics to a JSON-lines file.
    Provides aggregation methods for the paper's result tables.
    """

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_path   = self.output_dir / "metrics.jsonl"
        self._records:  List[TaskMetrics] = []
        self._load()

    def record(self, m: TaskMetrics):
        m.finalise()
        self._records.append(m)
        with self.log_path.open("a") as f:
            f.write(json.dumps(m.to_dict()) + "\n")

    def summary(self) -> dict:
        if not self._records:
            return {"error": "No tasks recorded yet."}

        completed = [r for r in self._records if r.passed_final]
        n         = len(self._records)
        n_pass    = len(completed)

        def safe_mean(vals):
            v = [x for x in vals if x is not None]
            return round(mean(v), 3) if v else 0.0

        def safe_stdev(vals):
            v = [x for x in vals if x is not None]
            return round(stdev(v), 3) if len(v) > 1 else 0.0

        iters        = [r.iterations for r in self._records]
        budgets      = [r.budget_utilisation for r in self._records if r.budget_utilisation]
        compressions = [r.compression_ratio for r in self._records if r.compression_ratio > 0]
        ki_rates     = [r.ki_helped / r.ki_retrieved for r in self._records
                        if r.ki_retrieved > 0]
        stale_rates  = [r.stale_nodes_at_start / r.nodes_queried for r in self._records
                        if r.nodes_queried > 0]
        hall_rates   = [r.invented_symbols_caught / r.total_added_symbols for r in self._records
                        if r.total_added_symbols > 0]
        first_shot   = [r for r in self._records if r.passed_first_shot]

        # Layer failure breakdown
        layer_fails: Dict[str, int] = {}
        for r in self._records:
            if r.failed_layer:
                layer_fails[r.failed_layer] = layer_fails.get(r.failed_layer, 0) + 1

        return {
            "total_tasks":          n,
            "M4_pass_at_1":         f"{len(first_shot)/n*100:.1f}% ({len(first_shot)}/{n})",
            "M4_pass_at_k":         f"{n_pass/n*100:.1f}% ({n_pass}/{n})",
            "M3_avg_iterations":    safe_mean(iters),
            "M3_p50_iterations":    round(median(iters), 1) if iters else 0,
            "M3_stdev_iterations":  safe_stdev(iters),
            "M1_avg_budget_util":   f"{safe_mean(budgets)*100:.1f}%",
            "M2_avg_compression":   f"{safe_mean(compressions):.1f}x",
            "M5_ki_hit_rate":       f"{safe_mean(ki_rates)*100:.1f}%" if ki_rates else "N/A (no KIs yet)",
            "M6_cpg_stale_rate":    f"{safe_mean(stale_rates)*100:.1f}%" if stale_rates else "0%",
            "M7_hallucination_rate":f"{safe_mean(hall_rates)*100:.1f}%" if hall_rates else "0%",
            "layer_failure_counts": layer_fails,
            "avg_total_ms":         safe_mean([r.total_ms for r in self._records]),
        }

    def ablation_table(self) -> str:
        """
        Generates the ablation study table for the paper.
        Shows pass@k degradation as each verification layer is disabled.
        """
        if not self._records:
            return "No data yet."

        layers = ["diff_parse", "symbol_check", "type_check", "test_execution",
                  "cpg_diff", "sandbox"]
        n      = len(self._records)
        lines  = [
            "Ablation Study — Verification Layer Contribution",
            "=" * 60,
            f"{'Layer removed':<25} {'Pass@k':<12} {'Delta vs full':<15}",
            "-" * 60,
        ]

        full_pass = len([r for r in self._records if r.passed_final])
        full_rate = full_pass / n if n > 0 else 0

        lines.append(f"{'Full pipeline':<25} {full_rate*100:.1f}%        baseline")

        for layer in layers:
            # Simulate removing this layer: count tasks that would have
            # passed even without it (i.e. they didn't fail at this layer)
            would_pass = len([
                r for r in self._records
                if r.passed_final or r.failed_layer != layer
            ])
            rate  = would_pass / n if n > 0 else 0
            delta = (rate - full_rate) * 100
            sign  = "+" if delta >= 0 else ""
            lines.append(f"{'- ' + layer:<25} {rate*100:.1f}%        {sign}{delta:.1f}%")

        lines.append("=" * 60)
        return "\n".join(lines)

    def export_csv(self, path: str):
        """Export all records to CSV for paper graphs."""
        import csv
        if not self._records:
            return
        fieldnames = list(self._records[0].to_dict().keys())
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in self._records:
                w.writerow(r.to_dict())

    def _load(self):
        if self.log_path.exists():
            for line in self.log_path.read_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        d  = json.loads(line)
                        tm = TaskMetrics(**d)
                        self._records.append(tm)
                    except Exception:
                        pass