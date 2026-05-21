"""
Token Budget Allocator — ensures the final TSDC document never exceeds
the configured token limit. Drops lowest-priority tiers first, then
compresses within tiers. Zero LLM calls.
"""
from __future__ import annotations

import os
from typing import Dict, List, Tuple

from transformers import AutoTokenizer

_ENC = AutoTokenizer.from_pretrained(
    os.environ.get("TSDC_MLX_MODEL_PATH",
                    os.path.expanduser("~/models/qwen25-coder-mlx/")),
    trust_remote_code=True,
)


def count_tokens(text: str) -> int:
    return len(_ENC.encode(text, add_special_tokens=False))


class BudgetAllocator:
    """
    Given the raw tier strings, trim them to fit within `total_budget` tokens.
    Priority order (drop last first):
      T7 rules → T6 digest → T5 callers → T3 callees → T8 skeleton → T2+T4 (never drop)
    """

    def __init__(self, total_budget: int = 2500):
        self.total_budget = total_budget

    def allocate(self, tiers: Dict[str, str], is_retry: bool = False) -> Dict[str, str]:
        """
        tiers keys: task_header, type_skeleton, callee_sigs, contract,
                    caller_patterns, diff_digest, codebase_rules, target_body
        Returns trimmed tiers that fit in budget.
        
        is_retry: on attempts 2-5, rebalance budget to prioritize error feedback.
                  Gives +300 tokens to task_header (contains distilled error feedback),
                  takes -150 each from diff_digest and codebase_rules.
        """
        result = dict(tiers)

        # ── Retry-aware budget rebalancing ────────────────────────────────────
        # On retries, the error feedback in task_header is MORE important than
        # historical diffs and rules.  Pre-trim the low-priority tiers so the
        # budget allocator gives the feedback room to breathe.
        if is_retry:
            for deprioritize_key in ("diff_digest", "codebase_rules"):
                val = result.get(deprioritize_key, "")
                if val:
                    tok_count = count_tokens(val)
                    if tok_count > 150:
                        # Hard-cap each to 150 fewer tokens
                        result[deprioritize_key] = self._truncate_to_budget(
                            val, max(0, tok_count - 150)
                        )

        used = self._total(result)

        if used <= self.total_budget:
            return result

        # Phase 1 — soft trim (bullet-list aware)
        trim_order = [
            ("codebase_rules",  self._trim_bullet_list,   100),
            ("diff_digest",     self._trim_bullet_list,    60),
            ("caller_patterns", self._trim_bullet_list,    80),
            ("callee_sigs",     self._trim_sig_list,      100),
        ]

        for key, trim_fn, step in trim_order:
            while used > self.total_budget and key in result and result[key]:
                result[key] = trim_fn(result[key], step)
                used = self._total(result)

        # Phase 2 — hard truncation of any remaining tier (priority: least → most critical)
        hard_order = [
            "codebase_rules", "diff_digest", "caller_patterns",
            "callee_sigs", "type_skeleton", "contract",
            "task_header", "target_body",
        ]
        for key in hard_order:
            if used <= self.total_budget:
                break
            if key in result and result[key]:
                allowance = self.total_budget - self._total_excluding(result, key)
                result[key] = self._truncate_to_budget(result[key], max(0, allowance))
                used = self._total(result)

        return result


    def _total(self, tiers: Dict[str, str]) -> int:
        return sum(count_tokens(v) for v in tiers.values() if v)

    def _total_excluding(self, tiers: Dict[str, str], exclude: str) -> int:
        return sum(count_tokens(v) for k, v in tiers.items() if k != exclude and v)

    def _trim_bullet_list(self, text: str, step: int) -> str:
        """Remove the last `step` tokens worth of lines."""
        lines = text.splitlines()
        if len(lines) <= 1:
            return ""
        removed = 0
        while lines and removed < step:
            removed += count_tokens(lines[-1])
            lines.pop()
        return "\n".join(lines)

    def _trim_sig_list(self, text: str, step: int) -> str:
        """Remove the last callee signature entry."""
        lines = [l for l in text.splitlines() if l.strip()]
        if not lines:
            return ""
        # Each signature entry starts with 'def ' or is indented
        # Remove the last non-empty block
        removed = 0
        while lines and removed < step:
            removed += count_tokens(lines[-1])
            lines.pop()
        return "\n".join(lines)

    def _truncate_to_budget(self, text: str, token_limit: int) -> str:
        if token_limit <= 15:
            return "# [target body omitted — budget exhausted]"
        tokens = _ENC.encode(text)
        if len(tokens) <= token_limit:
            return text
        
        # Deduct space for the disclaimer string
        actual_limit = max(0, token_limit - 15)
        truncated = _ENC.decode(tokens[:actual_limit])
        return truncated + "\n    # ... [truncated: body exceeds token budget]"

    def report(self, tiers: Dict[str, str]) -> Dict[str, int]:
        return {k: count_tokens(v) for k, v in tiers.items() if v}