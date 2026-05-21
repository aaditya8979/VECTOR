"""
RepositoryAgentLoop — the full agentic loop for SWE-bench resolution.

Algorithm:
  1. Build CPG for the target repository
  2. Localize: find top-3 candidate functions from issue text
  3. For each candidate (in confidence order):
     a. Take a snapshot of the file before modification
     b. Run VECTOR's TSDC + modify + 5-layer verify pipeline
     c. If all layers pass → collect patch and return success
     d. If verify fails → restore original, try next candidate
  4. If all candidates fail → attempt multi-file: follow test failure
     to identify the next candidate and retry
  5. Return: RepoLoopResult with patch, metrics, and resolution status
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib     import Path
from typing      import Dict, List, Optional

from core.cpg.builder    import CPGBuilder
from core.cpg.models     import CPGNode
from core.agent.localizer import IssueLocalizer
from core.agent.patch_generator import PatchGenerator
from core.tsdc.generator  import TSDCGenerator
from core.memory.state_db import StateDB
from core.memory.knowledge import KnowledgeStore
from verification.pipeline import VerificationPipeline


@dataclass
class RepoLoopResult:
    resolved:       bool
    patch:          str          = ""
    modified_files: List[str]    = field(default_factory=list)
    attempts:       int          = 0
    error_message:  str          = ""
    localized_to:   List[str]    = field(default_factory=list)
    metrics:        Dict         = field(default_factory=dict)
    elapsed_sec:    float        = 0.0


class RepositoryAgentLoop:

    def __init__(
        self,
        engine,              # InferenceEngine
        max_functions:  int = 3,    # max candidates to try
        max_iterations: int = 5,    # VECTOR retries per candidate
    ):
        self.engine         = engine
        self.max_functions  = max_functions
        self.max_iterations = max_iterations
        self.patch_gen      = PatchGenerator()

    def solve(
        self,
        issue:     str,
        repo_root: str,
        task_id:   str = "swe_task",
    ) -> RepoLoopResult:

        t0      = time.time()
        result  = RepoLoopResult(resolved=False)

        # 1. Build CPG
        builder = CPGBuilder(repo_root)
        try:
            builder.build()
        except Exception as e:
            result.error_message = f"CPG build failed: {e}"
            result.elapsed_sec   = time.time() - t0
            return result

        # 2. Localize
        brain_dir = Path(repo_root) / ".codeagent"
        brain_dir.mkdir(parents=True, exist_ok=True)

        db        = StateDB(str(brain_dir / "state.db"))
        knowledge = KnowledgeStore(str(brain_dir / "knowledge"))
        localizer = IssueLocalizer(builder, self.engine)
        candidates = localizer.localize(issue, top_k=self.max_functions)

        result.localized_to = [n.node_id for n, _ in candidates]

        if not candidates:
            result.error_message = "Localization found zero candidates."
            result.elapsed_sec   = time.time() - t0
            return result

        # 3. Try each candidate
        for node, confidence in candidates:
            result.attempts += 1
            abs_path = Path(repo_root) / node.file_path

            # Snapshot original content
            try:
                original_content = abs_path.read_text(encoding="utf-8")
            except Exception:
                continue

            # Build a SWE-specific goal that includes the issue context
            goal = (
                f"Fix the following issue:\n"
                f"{issue[:800]}\n\n"
                f"Modify this function to resolve the bug described above."
            )

            # Run VECTOR modification pipeline
            generator  = TSDCGenerator(builder, db, knowledge)
            pipeline   = VerificationPipeline(repo_root)
            error_feed: Optional[str] = None

            for attempt in range(1, self.max_iterations + 1):
                try:
                    # Inject issue into T1 via error_feedback slot
                    tsdc_doc = generator.generate(
                        file_path      = node.file_path,
                        func_name      = node.function_name,
                        task_goal      = goal,
                        error_feedback = error_feed,
                    )
                except ValueError:
                    break

                func_code, stats = self.engine.generate_function(tsdc_doc)

                ver_result = pipeline.run(
                    diff_text   = func_code,
                    target_file = node.file_path,
                    target_func = node.function_name,
                )

                if ver_result.passed:
                    # Generate the patch from original → modified
                    try:
                        modified_content = abs_path.read_text(encoding="utf-8")
                    except Exception:
                        break

                    patch = self.patch_gen.from_file_contents(
                        original_content, modified_content, node.file_path
                    )

                    if patch:
                        result.resolved       = True
                        result.patch          = patch
                        result.modified_files = [node.file_path]
                        result.metrics        = {
                            "confidence":     confidence,
                            "attempt":        attempt,
                            "tsdc_tokens":    stats.get("prompt_tokens", 0),
                            "tokens_per_sec": stats.get("tokens_per_sec", 0),
                        }
                        result.elapsed_sec = time.time() - t0
                        return result
                    break   # Patch empty = no actual changes

                error_feed = ver_result.feedback_for_model()
                # Restore file for next attempt if needed
                abs_path.write_text(original_content, encoding="utf-8")

            # Candidate exhausted — restore file before trying next
            if abs_path.exists():
                abs_path.write_text(original_content, encoding="utf-8")

        result.elapsed_sec = time.time() - t0
        return result
