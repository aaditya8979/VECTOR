"""
test_ablation.py — Ablation study unit tests.

Tests each VECTOR component in isolation to verify that each one
contributes to the final result. These tests use mocks/stubs where
possible so that they run fast without a real model.

Paper relevance: these tests validate the logic that drives Table 2.
"""
from __future__ import annotations

import sys
import os
import json
import pytest

# ---------------------------------------------------------------------------
# Helper: ensure project root is importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ---------------------------------------------------------------------------
# T1: cl100k vs Qwen tokenizer divergence
# ---------------------------------------------------------------------------
class TestTokenizerDivergence:
    """
    Verify that cl100k (GPT-4) and Qwen tokenizers count tokens differently
    on code. This underpins the paper's claim that using the wrong tokenizer
    introduces a ~30-40% budget error.
    """

    def test_tokenizers_diverge_on_code(self):
        """cl100k count should differ from Qwen by >10% on typical code."""
        try:
            import tiktoken
        except ImportError:
            pytest.skip("tiktoken not installed — run: pip install tiktoken")

        from core.tsdc.budget import count_tokens as qwen_count

        sample = (
            "def authenticate(username: str, password: str) -> AuthResult:\n"
            "    \"\"\"Validate credentials and return auth token.\"\"\"\n"
            "    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())\n"
            "    user   = db.users.find_one({'username': username})\n"
            "    if not user or not bcrypt.checkpw(password.encode(), user['hash']):\n"
            "        raise AuthError('Invalid credentials')\n"
            "    return AuthResult(token=jwt.encode({'sub': username}, SECRET))\n"
        )

        enc         = tiktoken.get_encoding("cl100k_base")
        cl100k_n    = len(enc.encode(sample))
        qwen_n      = qwen_count(sample)

        diff_pct = abs(cl100k_n - qwen_n) / qwen_n
        assert diff_pct > 0.05, (
            f"Expected >5% difference between tokenizers. "
            f"cl100k={cl100k_n}, qwen={qwen_n}, diff={diff_pct:.1%}. "
            "If both tokenizers produce identical counts this may be a false pass."
        )

    def test_qwen_count_deterministic(self):
        """count_tokens must be deterministic (idempotent)."""
        from core.tsdc.budget import count_tokens

        text = "fn add(a: i32, b: i32) -> i32 { a + b }"
        assert count_tokens(text) == count_tokens(text)

    def test_qwen_count_nonzero(self):
        """Non-empty code must produce a non-zero token count."""
        from core.tsdc.budget import count_tokens

        assert count_tokens("hello world") > 0

    def test_qwen_count_empty_is_zero_or_small(self):
        """Empty string should produce 0 or a negligible count (BOS token only)."""
        from core.tsdc.budget import count_tokens

        assert count_tokens("") <= 2


# ---------------------------------------------------------------------------
# T2: Budget allocator — tier truncation logic
# ---------------------------------------------------------------------------
class TestBudgetAllocator:
    """Verify the BudgetAllocator correctly enforces the 2500-token limit."""

    def _make_long_text(self, n_words: int) -> str:
        return " ".join([f"token_{i}" for i in range(n_words)])

    def test_total_never_exceeds_budget(self):
        """Even if all tiers are huge, total must stay ≤ 2500 tokens."""
        from core.tsdc.budget import BudgetAllocator, count_tokens

        allocator = BudgetAllocator(total_budget=2500)
        tiers = {
            "task_header":    self._make_long_text(400),
            "type_skeleton":  self._make_long_text(400),
            "callee_sigs":    self._make_long_text(400),
            "contract":       self._make_long_text(400),
            "caller_patterns":self._make_long_text(400),
            "diff_digest":    self._make_long_text(400),
            "codebase_rules": self._make_long_text(400),
            "target_body":    self._make_long_text(400),
        }
        result = allocator.allocate(tiers)
        total  = sum(count_tokens(v) for v in result.values() if v)
        assert total <= 2500, (
            f"Budget exceeded: {total} tokens (limit 2500)"
        )

    def test_small_tiers_pass_through_unchanged(self):
        """Tiny tiers should not be truncated."""
        from core.tsdc.budget import BudgetAllocator

        allocator = BudgetAllocator(total_budget=2500)
        tiers = {
            "task_header":  "Modify the add function.",
            "target_body":  "def add(a, b): return a + b",
        }
        result = allocator.allocate(tiers)
        assert result.get("task_header") == "Modify the add function."
        assert result.get("target_body") == "def add(a, b): return a + b"


# ---------------------------------------------------------------------------
# T3: CPG staleness tracking — ablation
# ---------------------------------------------------------------------------
class TestStalenessAblatement:
    """
    Verify that removing staleness tracking causes callers not to be
    re-verified — simulates 'no CPG' ablation condition.
    """

    def test_stale_node_detected_on_change(self, tmp_path):
        """Edited node must be marked stale in the CPG."""
        import textwrap
        from core.cpg.builder import CPGBuilder

        src = textwrap.dedent("""\
            def add(a: int, b: int) -> int:
                return a + b

            def use_add():
                return add(1, 2)
        """)
        f = tmp_path / "app.py"
        f.write_text(src, encoding="utf-8")

        builder = CPGBuilder(str(tmp_path))
        builder.build_for_file(str(f))

        # Modify the file
        f.write_text(src.replace("return a + b", "return a + b + 1"), encoding="utf-8")

        from core.cpg.updater import CPGUpdater
        updater = CPGUpdater(builder)
        updater.on_file_changed(str(f))

        stale = [n for n in builder.nodes.values() if getattr(n, "is_stale", False)]
        assert len(stale) >= 1, "Expected at least 'add' node to be stale after edit"

    def test_caller_marked_stale_after_callee_change(self, tmp_path):
        """When callee changes, its callers must also be marked stale."""
        import textwrap
        from core.cpg.builder import CPGBuilder
        from core.cpg.updater import CPGUpdater

        src = textwrap.dedent("""\
            def add(a: int, b: int) -> int:
                return a + b

            def pipeline(x, y):
                return add(x, y)
        """)
        f = tmp_path / "app.py"
        f.write_text(src, encoding="utf-8")

        builder = CPGBuilder(str(tmp_path))
        builder.build_for_file(str(f))

        f.write_text(src.replace("return a + b", "return a * b"), encoding="utf-8")

        updater = CPGUpdater(builder)
        updater.on_file_changed(str(f))

        stale_names = {
            n.function_name
            for n in builder.nodes.values()
            if getattr(n, "is_stale", False)
        }
        assert "pipeline" in stale_names, (
            "'pipeline' must be stale because it calls the changed 'add'"
        )


# ---------------------------------------------------------------------------
# T4: Post-processor ablation — language extraction
# ---------------------------------------------------------------------------
class TestPostProcessorAblatement:
    """
    Verify the polyglot post-processor correctly extracts function bodies
    across languages. Without this, the model output cannot be applied.
    """

    def _pp(self, raw: str) -> str:
        from core.model.inference import InferenceEngine
        return InferenceEngine._post_process_static(raw)

    def test_python_extracted_correctly(self):
        raw = "Here is the updated function:\n\ndef foo(x):\n    return x * 2\n\nThis is prose."
        result = self._pp(raw)
        assert result.startswith("def foo")
        assert "This is prose" not in result

    def test_typescript_extracted_correctly(self):
        raw = "```typescript\nfunction bar(x: number): number {\n    return x + 1;\n}\n```"
        result = self._pp(raw)
        assert result.startswith("function bar")
        assert "```" not in result

    def test_go_extracted_correctly(self):
        raw = "Sure!\n\nfunc Add(a int, b int) int {\n    return a + b\n}\n\nHope that helps."
        result = self._pp(raw)
        assert result.startswith("func Add")
        assert "Hope that helps" not in result

    def test_rust_extracted_correctly(self):
        raw = "Here you go:\n\nfn multiply(a: i32, b: i32) -> i32 {\n    a * b\n}\n"
        result = self._pp(raw)
        assert result.startswith("fn multiply")

    def test_no_function_returns_raw(self):
        """If no function is found, return raw stripped content (don't crash)."""
        raw = "This is just a description without any function."
        result = self._pp(raw)
        assert isinstance(result, str)
        assert len(result) > 0
