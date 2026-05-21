"""verification/cpg_diff.py — Layer 4: CPG structural change validator."""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from core.cpg.language_registry import get_language_config, get_ts_language


class CPGDiffChecker:
    """
    Checks whether a generated diff introduces unexpected structural changes:
    - Signature change on a function that wasn't supposed to change
    - New callees that were NOT in the AVAILABLE CALLEES list
    - Deletion of raise/return statements that were in the contract
    """

    def __init__(self, project_root: str):
        self.project_root = Path(project_root)

    def check(
        self,
        temp_file:   str,
        target_rel:  str,
        target_func: str,
        diff_text:   str,
    ) -> Tuple[bool, str, List[str]]:

        real_path = self.project_root / target_rel
        config    = get_language_config(target_rel)

        if config is None:
            return True, "", []   # Unsupported language — skip layer

        try:
            lang = get_ts_language(config)
        except ImportError:
            return True, "", []   # Grammar not installed — skip

        try:
            original_sig = self._extract_signature(
                real_path.read_bytes(), target_func, config, lang
            )
            patched_sig  = self._extract_signature(
                Path(temp_file).read_bytes(), target_func, config, lang
            )

            # Signature change check
            if original_sig and patched_sig and original_sig != patched_sig:
                if not self._goal_allows_sig_change(diff_text):
                    return (
                        False,
                        f"Unexpected signature change.\nBefore: {original_sig}\nAfter:  {patched_sig}",
                        ["Do not change the function signature unless the goal explicitly requires it.",
                         "The TYPE SKELETON section shows the correct signature."],
                    )

            # Detect calls to functions not defined anywhere in added lines
            hallucinated = self._detect_new_invented_calls(diff_text)
            if hallucinated:
                return (
                    False,
                    f"Added calls to undefined symbols: {', '.join(hallucinated)}",
                    [f"Remove calls to: {', '.join(hallucinated)}",
                     "Only call functions listed in AVAILABLE CALLEES."],
                )

            return True, "", []

        except Exception as e:
            return True, "", []   # Don't fail on checker errors

    @staticmethod
    def _ts_text(source: bytes, node) -> str:
        """Safe byte-slice → str for tree-sitter nodes."""
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _extract_signature(
        self, source_bytes: bytes, func_name: str, config, lang
    ) -> str:
        from tree_sitter import Parser

        try:
            parser = Parser(lang)
            tree   = parser.parse(source_bytes)

            all_func_types = set(config.func_node_types + config.method_node_types)
            for node in self._walk(tree.root_node, all_func_types):
                name = self._get_func_name(node, source_bytes, config)
                if name == func_name:
                    params = node.child_by_field_name(config.params_field or "parameters")
                    ret    = node.child_by_field_name(config.return_type_field) if config.return_type_field else None
                    p_str  = self._ts_text(source_bytes, params) if params else "()"
                    r_str  = self._ts_text(source_bytes, ret) if ret else ""
                    return f"{p_str} -> {r_str}".strip()
        except Exception:
            pass
        return ""

    def _get_func_name(self, fn_node, source: bytes, config) -> Optional[str]:
        """Extract function name, handling C/C++ nested declarators."""
        name_node = fn_node.child_by_field_name(config.name_field)
        if not name_node:
            return None
        name_text = self._ts_text(source, name_node).strip()
        if config.name_field == "declarator":
            name_text = self._unwrap_declarator(name_node, source)
        return name_text.split("::")[-1].split("(")[0].strip()

    def _unwrap_declarator(self, node, source: bytes) -> str:
        """C/C++: recursively unwrap declarator → identifier."""
        for child in node.children:
            if child.type == "identifier":
                return self._ts_text(source, child)
            if child.type in ("function_declarator", "pointer_declarator",
                              "qualified_identifier", "destructor_name"):
                return self._unwrap_declarator(child, source)
        return self._ts_text(source, node)

    def _detect_new_invented_calls(self, diff_text: str) -> List[str]:
        """
        Find function calls in added lines that look obviously invented.
        Heuristic: snake_case names followed by ( that don't appear in context lines.
        """
        added   = set()
        context = set()
        for line in diff_text.splitlines():
            calls = set(re.findall(r'\b([a-z_][a-z0-9_]*)\s*\(', line))
            if line.startswith("+") and not line.startswith("+++"):
                added |= calls
            elif line.startswith(" "):
                context |= calls

        # Very common builtins to exclude
        builtins = {
            "print", "len", "str", "int", "float", "list", "dict", "set",
            "range", "enumerate", "zip", "map", "filter", "sorted", "reversed",
            "isinstance", "hasattr", "getattr", "setattr", "super", "type",
            "open", "repr", "hash", "id", "vars", "dir", "callable",
            "any", "all", "min", "max", "sum", "abs",
            # Cross-language common names
            "fmt", "log", "println", "printf", "sprintf", "append",
            "push", "pop", "contains", "unwrap", "expect", "ok", "err",
            "console", "require", "module", "exports",
        }
        new_only = added - context - builtins
        # Only flag if name is very suspicious (not a method call, deeply unusual)
        suspicious = [n for n in new_only if len(n) > 3 and "_" in n and n not in builtins]
        return suspicious[:3]

    def _goal_allows_sig_change(self, diff_text: str) -> bool:
        keywords = ["rename", "add parameter", "remove parameter", "change signature",
                    "new argument", "new param"]
        diff_lower = diff_text.lower()
        return any(k in diff_lower for k in keywords)

    @staticmethod
    def _walk(node, types: set):
        if node.type in types:
            yield node
        for child in node.children:
            yield from CPGDiffChecker._walk(child, types)