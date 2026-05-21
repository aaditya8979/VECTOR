#!/usr/bin/env python3
"""
TSDC Agent — Task-Scoped Deterministic Context for 7B code models.

Usage:
    tsdc init    [PROJECT_ROOT]               — build CPG, create .codeagent/
    tsdc watch   [PROJECT_ROOT]               — start real-time CPG sync
    tsdc modify  FILE FUNC "GOAL"             — modify a function with TSDC pipeline
    tsdc status  [PROJECT_ROOT]               — show CPG stats and metrics
    tsdc resume  [PROJECT_ROOT]               — resume last incomplete task
    tsdc benchmark --tier 1                   — run HumanEval benchmark

Environment variables:
    TSDC_MODEL_PATH    path to Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf
    TSDC_GPU_LAYERS    GPU layers to offload (default 99 = all, Metal)
    TSDC_N_CTX         context window (default 8192)
"""
import sys
from pathlib import Path

# Ensure the project root is on sys.path when run directly
sys.path.insert(0, str(Path(__file__).parent))

import click
from cli.commands import init, watch, modify, status, resume, benchmark, list_functions


@click.group()
@click.version_option("1.0.0", prog_name="tsdc")
def cli():
    """
    TSDC — deterministic 7B code agent with live CPG and hallucination elimination.
    """
    pass


cli.add_command(init)
cli.add_command(watch)
cli.add_command(modify)
cli.add_command(status)
cli.add_command(resume)
cli.add_command(benchmark)
cli.add_command(list_functions)


if __name__ == "__main__":
    cli()