"""
verification/feedback.py — Rule-based feedback distiller.

Converts verbose mypy/pytest stack traces into concise, actionable fix
instructions that a 7B model can actually act on.  Zero LLM calls.

This is the key to making Pass@5 > Pass@1.  The raw traces were too noisy
for the model to extract the fix — this distiller gives it a single
punchy sentence plus a fix pattern.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple


class FeedbackDistiller:
    """Rule-based distiller — zero LLM calls.

    Converts verbose mypy/pytest output into 1-3 line actionable fixes.
    Pattern-matched against known error families, with a generic fallback.
    """

    # ── mypy error patterns ──────────────────────────────────────────────────
    _MYPY_RULES: List[Tuple[re.Pattern, str, str]] = [
        # (compiled_pattern, distilled_message_template, fix_hint)
        (
            re.compile(r'Name "(\w+)" is not defined'),
            "You used `{0}` but it is not imported.",
            "Add `import {0}` at the top of the file.",
        ),
        (
            re.compile(r'"(\w+)" has no attribute "(\w+)"'),
            "Object `{0}` does not have attribute `{1}`.",
            "Check <available_callees> for valid methods on `{0}`.",
        ),
        (
            re.compile(
                r'Incompatible return value type \(got "([^"]+)", expected "([^"]+)"\)'
            ),
            "Return type mismatch: you returned `{0}` but the signature expects `{1}`.",
            "Change your return value to match the expected type `{1}`.",
        ),
        (
            re.compile(
                r'Argument (\d+) .* has incompatible type "([^"]+)"; expected "([^"]+)"'
            ),
            "Argument {0} has wrong type: got `{1}`, expected `{2}`.",
            "Cast or convert the argument to `{2}` before passing.",
        ),
        (
            re.compile(r'Incompatible types in assignment \(expression has type "([^"]+)", variable has type "([^"]+)"\)'),
            "Type mismatch in assignment: expression is `{0}`, variable expects `{1}`.",
            "Ensure the assigned value matches the variable's type `{1}`.",
        ),
        (
            re.compile(r'Missing return statement'),
            "Function is missing a return statement on at least one code path.",
            "Add a `return` statement covering all branches.",
        ),
        (
            re.compile(r'Too many arguments for "(\w+)"'),
            "Too many arguments passed to `{0}`.",
            "Check <signature> for the correct parameter count.",
        ),
        (
            re.compile(r'Too few arguments for "(\w+)"'),
            "Too few arguments passed to `{0}`.",
            "Check <signature> for required parameters.",
        ),
        (
            re.compile(r'Module "(\w+)" has no attribute "(\w+)"'),
            "Module `{0}` does not have `{1}`.",
            "Check the import — you may need `from {0} import {1}` or a different module.",
        ),
    ]

    # ── pytest error patterns ────────────────────────────────────────────────
    _PYTEST_RULES: List[Tuple[re.Pattern, str, str]] = [
        (
            re.compile(r'AssertionError:\s*assert\s+(.+?)\s*==\s*(.+)', re.IGNORECASE),
            "Assertion failed: expected `{1}` but got `{0}`.",
            "Fix the logic so the actual value matches the expected value.",
        ),
        (
            re.compile(r'AssertionError:\s*(.+)'),
            "Assertion failed: {0}.",
            "Review the test expectation and fix your logic accordingly.",
        ),
        (
            re.compile(r'TypeError:\s*(.+)'),
            "TypeError: {0}.",
            "Check argument types and return values.",
        ),
        (
            re.compile(r'AttributeError:\s*(.+)'),
            "AttributeError: {0}.",
            "You accessed an attribute that doesn't exist. Check <available_callees>.",
        ),
        (
            re.compile(r'NameError:\s*name\s+\'(\w+)\'\s+is not defined'),
            "NameError: `{0}` is not defined.",
            "Add `import {0}` or define `{0}` before using it.",
        ),
        (
            re.compile(r'ImportError:\s*cannot import name\s+\'(\w+)\''),
            "ImportError: `{0}` cannot be imported.",
            "Check that `{0}` exists in the module you're importing from.",
        ),
        (
            re.compile(r'ValueError:\s*(.+)'),
            "ValueError: {0}.",
            "Validate inputs before processing.",
        ),
        (
            re.compile(r'KeyError:\s*(.+)'),
            "KeyError: {0} — the key does not exist in the dictionary.",
            "Add a key existence check before accessing.",
        ),
    ]

    # ── Generic infrastructure errors to suppress ────────────────────────────
    _INFRA_NOISE = [
        "ImportError while loading conftest",
        "PytestUnraisableExceptionWarning",
        "coverage",
        "cacheprovider",
    ]

    def distill(
        self,
        layer: Optional[str],
        raw_error: str,
        allowed_callees: Optional[List[str]] = None,
    ) -> str:
        """Convert a raw verification error into a concise, actionable message.

        Returns a formatted string ready for injection into the TSDC Tier 1
        error_feedback field.
        
        allowed_callees: when provided and layer is symbol_check, the distiller
        re-lists all allowed functions so the model knows what to use instead.
        """
        if not raw_error:
            return ""

        # Select rule set based on which verification layer failed
        if layer == "type_check":
            return self._apply_rules(self._MYPY_RULES, raw_error, "TYPE CHECK")
        elif layer == "test_execution":
            return self._apply_rules(self._PYTEST_RULES, raw_error, "TEST")
        elif layer == "symbol_check":
            return self._distill_symbol(raw_error, allowed_callees or [])
        elif layer == "sandbox":
            return self._distill_sandbox(raw_error)
        elif layer == "diff_parse":
            return self._distill_diff_parse(raw_error)
        elif layer == "cpg_diff":
            return self._distill_cpg(raw_error)
        else:
            return self._generic_distill(raw_error)

    def _apply_rules(
        self,
        rules: List[Tuple[re.Pattern, str, str]],
        raw: str,
        prefix: str,
    ) -> str:
        """Match the first applicable rule and format the distilled message."""
        for pattern, msg_template, fix_template in rules:
            match = pattern.search(raw)
            if match:
                groups = match.groups()
                try:
                    msg = msg_template.format(*groups)
                    fix = fix_template.format(*groups)
                except (IndexError, KeyError):
                    msg = msg_template
                    fix = fix_template
                return (
                    f"[{prefix} ERROR] {msg}\n"
                    f"FIX: {fix}"
                )

        # Fallback: extract the first meaningful line
        return self._generic_distill(raw, prefix)

    def _distill_symbol(
        self,
        raw: str,
        allowed_callees: List[str] = None,
    ) -> str:
        """Distill symbol constraint violations with callee re-listing.
        
        When hallucination is detected, the model doesn't know WHAT to
        replace the bad symbol with.  Re-listing the allowed callees
        explicitly prevents it from hallucinating a different symbol.
        """
        base = (
            f"[SYMBOL ERROR] {raw[:200]}\n"
            f"FIX: Check <available_callees> for valid functions."
        )
        # Re-list allowed callees if available
        if allowed_callees:
            callee_list = "\n".join(
                f"  • {c}" for c in sorted(allowed_callees[:12])
            )
            base += (
                f"\n\nREMINDER — the ONLY functions that exist are:\n"
                f"{callee_list}\n"
                f"Use ONLY these. Nothing else exists."
            )
        return base

    def _distill_sandbox(self, raw: str) -> str:
        """Distill sandbox runtime errors."""
        # Extract the actual error type
        match = re.search(r'(NAME_ERROR|ATTR_ERROR|TYPE_ERROR|LOAD_ERROR):\s*(.+)', raw)
        if match:
            err_type = match.group(1).replace("_", " ")
            err_msg = match.group(2).strip()
            return (
                f"[RUNTIME ERROR] {err_type}: {err_msg[:150]}\n"
                f"FIX: Ensure all names are defined and attributes exist."
            )
        return f"[RUNTIME ERROR] {raw[:200]}\nFIX: Check for undefined names and None values."

    def _distill_diff_parse(self, raw: str) -> str:
        """Distill diff/parse failures."""
        if "syntax error" in raw.lower():
            return (
                f"[SYNTAX ERROR] {raw[:200]}\n"
                f"FIX: Your code has a syntax error. Check brackets, colons, and indentation."
            )
        if "'def '" in raw.lower() or "not found" in raw.lower():
            return (
                f"[PARSE ERROR] {raw[:200]}\n"
                f"FIX: Your output must start with the function definition line. "
                f"Wrap your code in <code>...</code> tags."
            )
        return f"[PARSE ERROR] {raw[:200]}\nFIX: Ensure output is a valid function body."

    def _distill_cpg(self, raw: str) -> str:
        """Distill CPG structural check failures."""
        if "signature change" in raw.lower():
            return (
                f"[SIGNATURE ERROR] {raw[:200]}\n"
                f"FIX: Do NOT change the function signature. Match <signature> exactly."
            )
        if "undefined symbols" in raw.lower():
            return (
                f"[HALLUCINATION] {raw[:200]}\n"
                f"FIX: You invented function names. Use ONLY functions from <available_callees>."
            )
        return f"[STRUCTURAL ERROR] {raw[:200]}\nFIX: Preserve the function's public interface."

    def _generic_distill(self, raw: str, prefix: str = "ERROR") -> str:
        """Fallback: extract the first non-infrastructure error line."""
        lines = raw.splitlines()
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # Skip infrastructure noise
            if any(noise in stripped for noise in self._INFRA_NOISE):
                continue
            # Skip file path lines and traceback headers
            if stripped.startswith(("  File ", "Traceback ", "    ", "E  ")):
                continue
            # Return the first meaningful line
            return f"[{prefix}] {stripped[:250]}\nFIX: Address the error described above."
        # Nothing matched — return truncated raw
        return f"[{prefix}] {raw[:250]}\nFIX: Review the error and fix accordingly."
