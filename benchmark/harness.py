"""
harness.py — Benchmark execution harness.

Wraps the different benchmark adapters (HumanEval, RepoBench, SWE-bench)
into a unified interface for the CLI.
"""
from __future__ import annotations

import os
from pathlib import Path

from core.model.inference import InferenceEngine
from verification.pipeline import VerificationPipeline

class BenchmarkHarness:
    def __init__(self):
        self.engine = InferenceEngine.get()
        self.report_data = []

    def run_humaneval(self, max_problems: int = 164, k_attempts: int = 5):
        from benchmark.humaneval import HumanEvalAdapter
        
        print(f"Initializing HumanEval benchmark (max_problems={max_problems}, k={k_attempts})...")
        pipeline = VerificationPipeline(".") # Dummy pipeline for HumanEval
        adapter = HumanEvalAdapter(self.engine, pipeline)
        
        results = adapter.run_all(max_problems=max_problems, k=k_attempts)
        self.report_data.append(("HumanEval", results))

    def run_repobench(self, max_problems: int = 50):
        print("RepoBench adapter not yet implemented.")
        pass

    def run_swebench(self, max_instances: int = 50):
        print("SWE-bench adapter not yet implemented.")
        pass

    def full_report(self) -> str:
        if not self.report_data:
            return "No benchmarks run."
            
        report = "\\n[bold blue]Benchmark Report[/bold blue]\\n"
        for name, results in self.report_data:
            report += f"\\n{name}: {len(results)} items evaluated."
            
        return report