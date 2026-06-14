"""
reporter.py — Benchmark report generator for the VECTOR research paper.

Reads results JSON/JSONL files from benchmark runs and generates LaTeX tables
and pass@k plots for the paper.
"""
from __future__ import annotations

import json
from pathlib import Path


class PaperReporter:
    """
    Generates Table 1, Table 2, and Figure 1/2 from benchmark result files.
    """

    def table1_humaneval(self, results_path: str) -> str:
        """
        Generates Table 1: HumanEval performance compared to baselines.
        
        Expected output format:
        | System              | Model  | Fine-tune | pass@1 | pass@5 |
        |---------------------|--------|-----------|--------|--------|
        | VECTOR (ours)       | 7B     | No        | X%     | Y%     |
        """
        path = Path(results_path)
        if not path.exists():
            return "[Error: Results file not found]"
            
        lines = path.read_text().strip().split('\n')
        results = [json.loads(line) for line in lines if line]
        
        pass_1 = sum(1 for r in results if r.get("passed_at_1")) / len(results) if results else 0
        pass_k = sum(1 for r in results if r.get("passed_at_k")) / len(results) if results else 0
        
        table = (
            "\\begin{table}[h]\n"
            "\\centering\n"
            "\\begin{tabular}{l l c c c}\n"
            "\\toprule\n"
            "System & Model & Fine-tune & pass@1 & pass@5 \\\\\n"
            "\\midrule\n"
            f"\\textbf{{VECTOR (ours)}} & 7B & No & {pass_1:.1%} & {pass_k:.1%} \\\\\n"
            "Qwen2.5-Coder raw & 7B & No & 88.4\\% & -- \\\\\n"
            "SWE-Dev & 7B & Yes & -- & -- \\\\\n"
            "\\bottomrule\n"
            "\\end{tabular}\n"
            "\\caption{HumanEval zero-shot performance.}\n"
            "\\label{tab:humaneval}\n"
            "\\end{table}"
        )
        return table

    def table2_ablation(self, results_path: str) -> str:
        """
        Generates Table 2: Ablation study.
        
        Supports both:
          - v2 format (single run):  {mode: [results...]}
          - v3 format (with repeats): {runs: [...], summary: {...}}
        
        v3 format shows mean ± std across runs.
        """
        path = Path(results_path)
        if not path.exists():
            return "[Error: Ablation results file not found]"
            
        data = json.loads(path.read_text())

        # ── v3 repeats format ────────────────────────────────────────────
        if "summary" in data:
            summary = data["summary"]
            n_tasks = 0
            if data.get("runs"):
                n_tasks = len(data["runs"][0].get("results", []))

            table = (
                "\\begin{table}[h]\n"
                "\\centering\n"
                "\\begin{tabular}{l c c c c}\n"
                "\\toprule\n"
                "Condition & pass@1 & pass@5 & Hallucination & Avg Iters \\\\\n"
                "\\midrule\n"
            )

            for mode, s in summary.items():
                name = {
                    "full": "\\textbf{Full VECTOR}",
                    "no_ki": "No KI (Tier 7)",
                    "no_sandbox": "No Sandbox (Layer 5)",
                    "cl100k": "GPT-4 Tokenizer (cl100k)",
                }.get(mode, mode)

                p1 = f"{s['pass_at_1_mean']:.1%} $\\pm$ {s['pass_at_1_std']:.1%}"
                p5 = f"{s['pass_at_5_mean']:.1%} $\\pm$ {s['pass_at_5_std']:.1%}"
                hall = f"{s['halluc_mean']:.1%} $\\pm$ {s['halluc_std']:.1%}"
                iters = f"{s['iters_mean']:.1f} $\\pm$ {s['iters_std']:.1f}"

                table += f"{name} & {p1} & {p5} & {hall} & {iters} \\\\\n"

            n_runs = next(iter(summary.values()), {}).get("n_runs", "?")
            table += (
                "\\bottomrule\n"
                "\\end{tabular}\n"
                f"\\caption{{Ablation study on {n_tasks} tasks "
                f"({n_runs} runs, mean $\\pm$ std).}}\n"
                "\\label{tab:ablation}\n"
                "\\end{table}"
            )
            return table

        # ── v2 single-run format ─────────────────────────────────────────
        table = (
            "\\begin{table}[h]\n"
            "\\centering\n"
            "\\begin{tabular}{l c c c c}\n"
            "\\toprule\n"
            "Condition & pass@1 & pass@5 & Hallucination & Avg Iters \\\\\n"
            "\\midrule\n"
        )
        
        for mode, results in data.items():
            pass_1 = sum(1 for r in results if r.get("passed_at_1")) / len(results)
            pass_5 = sum(1 for r in results if r.get("passed_at_5")) / len(results)
            halluc = sum(r.get("metrics", {}).get("M7_hallucination_rate", 0) for r in results) / len(results)
            iters  = sum(r.get("iterations", 1) for r in results) / len(results)
            
            name = {
                "full": "\\textbf{Full VECTOR}",
                "no_ki": "No KI (Tier 7)",
                "no_sandbox": "No Sandbox (Layer 5)",
                "cl100k": "GPT-4 Tokenizer (cl100k)",
            }.get(mode, mode)
            
            table += f"{name} & {pass_1:.1%} & {pass_5:.1%} & {halluc:.1%} & {iters:.1f} \\\\\n"
            
        table += (
            "\\bottomrule\n"
            "\\end{tabular}\n"
            f"\\caption{{Ablation study on {len(next(iter(data.values())))} tasks.}}\n"
            "\\label{tab:ablation}\n"
            "\\end{table}"
        )
        
        return table

    def table3_swebench(self, results_dir: str) -> str:
        """
        Table 3: SWE-bench Lite Resolve Rate

        | System              | Model  | Fine-tune | Local | Resolve% |
        |---------------------|--------|-----------|-------|----------|
        | VECTOR (ours)       | 7B     | No        | Yes   | X.X%     |
        | SWE-Dev 7B          | 7B     | Yes       | No    | 23.4%    |
        | Agentless           | GPT-4  | No        | No    | ~17%     |
        | Raw Qwen2.5-Coder   | 7B     | No        | Yes   | ~5%      |
        """
        pred_path = Path(results_dir) / "predictions.jsonl"
        if not pred_path.exists():
            return "SWE-bench results not found. Run: python benchmark/full_run.py --step swebench-full"

        predictions = [json.loads(l) for l in pred_path.read_text().splitlines() if l.strip()]
        resolved    = sum(1 for p in predictions if p.get("resolved", False))
        total       = len(predictions)
        rate        = resolved / total * 100 if total else 0

        lines = [
            "\nTable 3: SWE-bench Lite Resolve Rate",
            "=" * 70,
            f"{'System':<25} {'Model':<8} {'Fine-tune':<12} {'Local':<8} {'Resolve%'}",
            "-" * 70,
            f"{'VECTOR (ours)':<25} {'7B':<8} {'No':<12} {'Yes':<8} {rate:.1f}%  ←",
            f"{'SWE-Dev 7B':<25} {'7B':<8} {'Yes':<12} {'No':<8} 23.4%",
            f"{'Agentless + GPT-4':<25} {'175B+':<8} {'No':<12} {'No':<8} ~17%",
            f"{'Raw Qwen2.5-Coder':<25} {'7B':<8} {'No':<12} {'Yes':<8} ~5%",
            "=" * 70,
            f"VECTOR resolves {resolved}/{total} instances without fine-tuning,",
            f"on consumer hardware (M4 MacBook Air), fully offline.",
        ]
        return "\n".join(lines)

    def figure2_pass_at_k(self, results_path: str, output_image: str = "pass_at_k.png"):
        """
        Generates pass@k curve (k=1 to 5) plot.
        Requires matplotlib.
        """
        try:
            import matplotlib.pyplot as plt
        except ImportError:
            print("matplotlib not installed. Skipping plot generation.")
            return

        # In a real implementation, this would compute pass@k from attempts
        # and plot a logarithmic or linear curve showing performance gain
        # from iterative generation.
        pass
