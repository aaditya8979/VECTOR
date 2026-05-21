"""
Verification Pipeline — 5-layer deterministic anti-hallucination engine.
Layers: grammar check → type check → test execution → CPG diff → sandbox.
Each layer is deterministic. No LLM involved in verification.
On any failure: returns structured error → fed back into TSDC Tier 1 → regenerate.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from verification.type_check  import TypeChecker
from verification.test_runner import TestRunner
from verification.cpg_diff    import CPGDiffChecker
from verification.sandbox     import SandboxRunner


@dataclass
class VerificationResult:
    passed:        bool
    layer_failed:  Optional[str]   = None
    error_message: str             = ""
    layer_results: Dict[str, bool] = field(default_factory=dict)
    suggestions:   List[str]       = field(default_factory=list)
    elapsed_sec:   float           = 0.0

    def feedback_for_model(self, allowed_callees: list = None) -> str:
        """Format the error into concise, actionable feedback via the distiller.
        This is the key to making Pass@5 > Pass@1.
        
        allowed_callees: when provided, symbol-check failures will re-list
        the valid functions so the model knows what to use instead.
        """
        if self.passed:
            return ""
        from verification.feedback import FeedbackDistiller
        distiller = FeedbackDistiller()
        return distiller.distill(
            self.layer_failed, self.error_message,
            allowed_callees=allowed_callees,
        )


class VerificationPipeline:
    """
    Orchestrates all 5 verification layers for a generated diff.
    Applies the diff to a temp copy of the file, runs each check,
    and only writes to the real file if all layers pass.
    """

    def __init__(
        self,
        project_root: str,
        test_dir:     Optional[str] = None,
    ):
        self.project_root = Path(project_root)
        self.type_checker = TypeChecker(project_root)
        self.test_runner  = TestRunner(project_root, test_dir)
        self.cpg_checker  = CPGDiffChecker(project_root)
        self.sandbox      = SandboxRunner()

    def run(
        self,
        diff_text:    str,
        target_file:  str,   # relative to project root
        target_func:  str,
        test_guard:   str = "",  # space-separated pytest nodeids
        allowed_callees:   list = None,  # from CPG T3 — names of allowed functions
        expected_symbols:  list = None,  # symbols that MUST be present
        forbidden_symbols: list = None,  # symbols that MUST NOT be present
    ) -> VerificationResult:

        t0      = time.time()
        results = {}

        # ── Layer 0: Parse the generated function and apply via AST ───────────
        ok, patch, err = self._apply_function_replacement(diff_text, target_file, target_func)
        if not ok:
            return VerificationResult(
                passed       = False,
                layer_failed = "diff_parse",
                error_message = err,
                layer_results = {"diff_parse": False},
                elapsed_sec  = time.time() - t0,
            )
        results["diff_parse"] = True
        temp_file, patched_source = patch

        # ── Layer 1: AST-based symbol & constraint check ─────────────────────
        ok, err, suggestions = self._check_symbol_constraints(
            diff_text,
            allowed_callees  = allowed_callees or [],
            expected_symbols = expected_symbols or [],
            forbidden_symbols = forbidden_symbols or [],
        )
        results["symbol_check"] = ok
        if not ok:
            self._cleanup(temp_file)
            return VerificationResult(
                passed       = False,
                layer_failed = "symbol_check",
                error_message = err,
                layer_results = results,
                suggestions  = suggestions,
                elapsed_sec  = time.time() - t0,
            )

        from rich.console import Console
        console = Console()

        # ── Layer 1.5: Normalize formatting (ruff) ─────────────────────────────
        if Path(target_file).suffix.lower() == ".py":
            patched_source = self._normalize_formatting(patched_source, target_file)
            # Re-write the temp file with formatted source
            Path(temp_file).write_text(patched_source, encoding="utf-8")

        # ── Layer 2: Static type check (mypy) ─────────────────────────────────
        console.print("    [yellow]→ Running static type check (mypy)...[/yellow]")
        ok, err = self.type_checker.check(temp_file, target_file)
        results["type_check"] = ok
        if not ok:
            console.print("    [red]✗ Static type check (mypy) failed[/red]")
            self._cleanup(temp_file)
            return VerificationResult(
                passed       = False,
                layer_failed = "type_check",
                error_message = err,
                layer_results = results,
                suggestions  = ["Fix type annotation mismatches shown in the error above."],
                elapsed_sec  = time.time() - t0,
            )
        console.print("    [green]✓ Static type check (mypy) passed[/green]")

        # ── Layer 3: Test execution ────────────────────────────────────────────
        console.print("    [yellow]→ Running unit tests (pytest)...[/yellow]")
        test_ids = test_guard.split() if test_guard else []
        ok, err  = self.test_runner.run(temp_file, target_file, target_func, test_ids)
        results["test_execution"] = ok
        if not ok:
            console.print("    [red]✗ Unit tests (pytest) failed[/red]")
            self._cleanup(temp_file)
            return VerificationResult(
                passed       = False,
                layer_failed = "test_execution",
                error_message = err,
                layer_results = results,
                suggestions  = [
                    "The failing test output is above. Fix the logic to pass it.",
                    "Do not change the test — change only the target function.",
                ],
                elapsed_sec  = time.time() - t0,
            )
        console.print("    [green]✓ Unit tests (pytest) passed[/green]")

        # ── Layer 4: CPG structural diff check ────────────────────────────────
        console.print("    [yellow]→ Running CPG structural check...[/yellow]")
        ok, err, suggestions = self.cpg_checker.check(
            temp_file, target_file, target_func, diff_text
        )
        results["cpg_diff"] = ok
        if not ok:
            console.print("    [red]✗ CPG structural check failed[/red]")
            self._cleanup(temp_file)
            return VerificationResult(
                passed       = False,
                layer_failed = "cpg_diff",
                error_message = err,
                layer_results = results,
                suggestions  = suggestions,
                elapsed_sec  = time.time() - t0,
            )
        console.print("    [green]✓ CPG structural check passed[/green]")

        # ── Layer 5: Sandbox runtime check ────────────────────────────────────
        console.print("    [yellow]→ Running sandbox imports check...[/yellow]")
        ok, err = self.sandbox.run(temp_file, target_func, target_file)
        results["sandbox"] = ok
        if not ok:
            console.print("    [red]✗ Sandbox check failed[/red]")
            self._cleanup(temp_file)
            return VerificationResult(
                passed       = False,
                layer_failed = "sandbox",
                error_message = err,
                layer_results = results,
                suggestions  = ["Fix the runtime error shown above. Check None guards and type casts."],
                elapsed_sec  = time.time() - t0,
            )
        console.print("    [green]✓ Sandbox check passed[/green]")

        # ── All layers passed — backup original, commit patched file ─────────
        real_path   = self.project_root / target_file
        brain_diffs = self.project_root / ".codeagent" / "diffs"
        brain_diffs.mkdir(parents=True, exist_ok=True)

        # Save original as .orig backup so user can always revert
        stem        = Path(target_file).stem
        task_num    = int(time.time()) % 100000
        backup_path = brain_diffs / f"task{task_num}_{stem}.orig"
        backup_path.write_text(real_path.read_text(encoding="utf-8"), encoding="utf-8")

        real_path.write_text(patched_source, encoding="utf-8")
        self._cleanup(temp_file)

        # pyrefly: ignore [missing-import]
        from rich.console import Console
        Console().print(f"  [dim]Backup saved to: {backup_path}[/dim]")

        return VerificationResult(
            passed        = True,
            layer_results = results,
            elapsed_sec   = time.time() - t0,
        )

    # ── Function Replacement ─────────────────────────────────────────────────

    def _apply_function_replacement(
        self, model_output: str, target_file: str, target_func: str
    ) -> Tuple[bool, Optional[Tuple[str, str]], str]:
        """
        Replaces the target function in the original file with the new code from the model.
        Uses Python's ast module for .py files (fast, exact).
        Uses tree-sitter for all other languages.
        """
        real_path = self.project_root / target_file
        if not real_path.exists():
            return False, None, f"Target file not found: {target_file}"

        original = real_path.read_text(encoding="utf-8")
        ext      = Path(target_file).suffix.lower()

        if ext == ".py":
            ok, patched, err = self._replace_py_ast(original, model_output, target_func)
        else:
            ok, patched, err = self._replace_ts_generic(
                original, model_output, target_func, target_file
            )

        if not ok:
            return False, None, err

        # Validate the full patched file parses (language-specific)
        ok, err = self._validate_syntax(patched, target_file)
        if not ok:
            return False, None, f"Patched file has syntax error: {err}"

        # ── Auto-inject missing stdlib imports ────────────────────────────────
        # The model generates function bodies and often uses stdlib names
        # (e.g. time.time(), hashlib, re) without adding a module-level import.
        # We detect these and inject them so mypy doesn't burn all retries on
        # a trivially fixable cosmetic issue.
        if Path(target_file).suffix.lower() == ".py":
            patched = self._auto_inject_imports(patched, model_output)

        # Write to temp file for downstream verification layers
        suffix = Path(target_file).suffix
        tmp    = tempfile.NamedTemporaryFile(
            mode="w", suffix=suffix, delete=False, encoding="utf-8"
        )
        tmp.write(patched)
        tmp.flush()
        tmp.close()

        # pyrefly: ignore [missing-import]
        from rich.console import Console
        Console().print(f"  [green]✓ AST Function Replacement applied to:[/green] {target_file}")

        return True, (tmp.name, patched), ""

    # ── Auto-import injection ─────────────────────────────────────────────────

    # Stdlib modules the model commonly uses in function bodies without importing
    _STDLIB_MODULES = {
        "time", "re", "os", "sys", "json", "hashlib", "logging", "math",
        "copy", "datetime", "collections", "functools", "itertools",
        "pathlib", "traceback", "threading", "weakref", "uuid", "random",
        "string", "io", "contextlib", "typing", "dataclasses", "enum",
        "abc", "inspect", "warnings", "struct", "base64", "urllib",
        "http", "socket", "signal", "subprocess", "shutil", "glob",
        "fnmatch", "tempfile", "stat", "errno", "platform", "textwrap",
    }

    def _auto_inject_imports(self, source: str, model_output: str) -> str:
        """
        Detect stdlib names used in model_output that aren't imported in source,
        then inject `import X` lines at the top of the file (after existing imports).
        """
        import re as _re
        import ast as _ast

        # Find all `X.something` dotted usages in the new function code
        used_modules = set(_re.findall(r'\b([a-z_][a-z0-9_]*)\.\w+', model_output))
        # Also find bare module names used as calls/references
        used_modules.update(_re.findall(r'\b([a-z_][a-z0-9_]*)\s*\(', model_output))

        # Filter to known stdlib modules only
        to_inject = used_modules & self._STDLIB_MODULES

        if not to_inject:
            return source

        # Find what's already imported at module level
        try:
            tree = _ast.parse(source)
        except SyntaxError:
            return source

        already_imported: set = set()
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for alias in node.names:
                    already_imported.add(alias.name.split(".")[0])
            elif isinstance(node, _ast.ImportFrom):
                if node.module:
                    already_imported.add(node.module.split(".")[0])

        missing = to_inject - already_imported
        if not missing:
            return source

        # Build injection lines
        inject_lines = "\n".join(f"import {m}" for m in sorted(missing))

        # Insert after the last existing import statement
        lines = source.splitlines(keepends=True)
        last_import_line = 0
        try:
            for node in _ast.walk(tree):
                if isinstance(node, (_ast.Import, _ast.ImportFrom)):
                    last_import_line = max(last_import_line, node.end_lineno or node.lineno)
        except Exception:
            pass

        if last_import_line > 0:
            lines.insert(last_import_line, inject_lines + "\n")
        else:
            lines.insert(0, inject_lines + "\n")

        return "".join(lines)

    # ── Python fast path ─────────────────────────────────────────────────────

    def _replace_py_ast(
        self, original: str, model_output: str, target_func: str
    ) -> Tuple[bool, str, str]:
        """Python fast path — uses ast.parse for exact line boundaries."""
        import ast

        try:
            tree = ast.parse(original)
        except SyntaxError as e:
            return False, "", f"Original file has a syntax error: {e}"

        target_node = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == target_func:
                    target_node = node
                    break

        if not target_node:
            return False, "", f"Function '{target_func}' not found in Python source."

        start_line = target_node.lineno
        end_line   = target_node.end_lineno
        original_lines = original.splitlines(keepends=True)

        # Find actual 'def ' line (lineno might point to decorator)
        def_line_idx = start_line - 1
        while def_line_idx < end_line and \
              not original_lines[def_line_idx].lstrip().startswith("def ") and \
              not original_lines[def_line_idx].lstrip().startswith("async def "):
            def_line_idx += 1

        if def_line_idx >= end_line:
            def_line_idx = start_line - 1

        def_line         = original_lines[def_line_idx]
        base_indent_str  = def_line[:len(def_line) - len(def_line.lstrip())]

        # Extract and align model output
        model_lines = model_output.splitlines(keepends=True)
        mod_start   = None
        for i, line in enumerate(model_lines):
            if line.lstrip().startswith("def ") or line.lstrip().startswith("async def "):
                mod_start = i
                break

        if mod_start is None:
            return False, "", "Could not find a 'def ' statement in the generated output."

        mod_def_line        = model_lines[mod_start]
        mod_base_indent_str = mod_def_line[:len(mod_def_line) - len(mod_def_line.lstrip())]

        adjusted_model_lines = []
        for line in model_lines[mod_start:]:
            if line.strip() == "":
                adjusted_model_lines.append(line)
                continue
            if line.startswith(mod_base_indent_str):
                adjusted_model_lines.append(base_indent_str + line[len(mod_base_indent_str):])
            else:
                adjusted_model_lines.append(line)

        adjusted_model_code = "".join(adjusted_model_lines)
        if not adjusted_model_code.endswith("\n"):
            adjusted_model_code += "\n"

        # Validate the replacement snippet parses
        try:
            import textwrap
            ast.parse(textwrap.dedent(adjusted_model_code))
        except SyntaxError as e:
            return False, "", f"Generated function contains a syntax error: {e}"

        # Replace the function lines
        new_source_lines = original_lines[:start_line - 1] + [adjusted_model_code]
        if end_line < len(original_lines):
            new_source_lines += original_lines[end_line:]

        patched_source = "".join(new_source_lines)

        # Full file parse check
        try:
            ast.parse(patched_source)
        except SyntaxError as e:
            return False, "", f"Applying the generated function caused a file-level syntax error: {e}"

        return True, patched_source, ""

    # ── Generic tree-sitter path ─────────────────────────────────────────────

    def _replace_ts_generic(
        self, original: str, model_output: str, target_func: str, target_file: str
    ) -> Tuple[bool, str, str]:
        """
        Generic tree-sitter replacement for TypeScript, Go, Rust, C++, JavaScript.
        Finds the function node by name, replaces its line range.
        """
        from core.cpg.language_registry import get_language_config, get_ts_language
        from tree_sitter import Parser

        config = get_language_config(target_file)
        if config is None:
            return False, "", f"Unsupported file type: {Path(target_file).suffix}"

        try:
            lang   = get_ts_language(config)
            parser = Parser(lang)
            src_bytes = original.encode("utf-8")
            tree   = parser.parse(src_bytes)
            lines  = original.splitlines(True)

            all_func_types = set(config.func_node_types + config.method_node_types)

            for node in self._walk_types(tree.root_node, all_func_types):
                name = self._extract_name_from_ts_node(node, src_bytes, config)
                if name != target_func:
                    continue

                start = node.start_point[0]   # 0-indexed line
                end   = node.end_point[0] + 1  # exclusive

                # Include preceding attributes (#[...] in Rust, decorators, etc.)
                start = self._include_preceding_attrs(lines, start)

                indent     = self._detect_indent(lines[start] if start < len(lines) else "")
                normalized = self._normalize_indent(model_output, indent)

                # Detect and preserve original line ending style
                eol = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
                new_lines = lines[:start] + [normalized + eol] + lines[end:]
                return True, "".join(new_lines), ""

            return False, "", f"Function '{target_func}' not found via tree-sitter in {target_file}."

        except ImportError as e:
            return False, "", str(e)
        except Exception as e:
            return False, "", f"tree-sitter replacement failed: {e}"

    # ── Syntax validation ────────────────────────────────────────────────────

    def _validate_syntax(self, source: str, target_file: str) -> Tuple[bool, str]:
        """Validate that the patched source parses without errors."""
        ext = Path(target_file).suffix.lower()

        if ext == ".py":
            import ast
            try:
                ast.parse(source)
                return True, ""
            except SyntaxError as e:
                return False, f"line {e.lineno}: {e.msg}"

        # For other languages: use tree-sitter and check for ERROR nodes
        from core.cpg.language_registry import get_language_config, get_ts_language
        from tree_sitter import Parser

        config = get_language_config(target_file)
        if not config:
            return True, ""  # Unknown language — skip validation

        try:
            lang   = get_ts_language(config)
            parser = Parser(lang)
            tree   = parser.parse(source.encode("utf-8"))
            errors = list(self._walk_types(tree.root_node, {"ERROR"}))
            if errors:
                first = errors[0]
                return False, f"Parse error at line {first.start_point[0] + 1}"
            return True, ""
        except Exception:
            return True, ""  # Don't block on validation errors

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _ts_text(source: bytes, node) -> str:
        """Safe byte-slice → str for tree-sitter nodes."""
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _extract_name_from_ts_node(self, fn_node, source: bytes, config) -> Optional[str]:
        """Extract the function name from a tree-sitter node, handling language quirks.
        source must be bytes — tree-sitter byte offsets index into UTF-8 bytes."""
        name_node = fn_node.child_by_field_name(config.name_field)
        if name_node:
            name_text = self._ts_text(source, name_node).strip()
            if config.name_field == "declarator":
                name_text = self._unwrap_cpp_declarator(name_node, source)
            return name_text.split("::")[-1].split("(")[0].strip()

        # Arrow functions / anonymous: try to get from parent assignment
        parent = fn_node.parent
        if parent and parent.type in ("variable_declarator", "lexical_declaration",
                                       "assignment_expression", "export_statement"):
            if parent.type == "export_statement":
                for child in parent.children:
                    if child.type in ("lexical_declaration",):
                        parent = child
                        break
            if parent.type == "lexical_declaration":
                for child in parent.children:
                    if child.type == "variable_declarator":
                        parent = child
                        break
            left = parent.child_by_field_name("name") or parent.child_by_field_name("left")
            if left:
                return self._ts_text(source, left).strip()

        return None

    def _unwrap_cpp_declarator(self, decl_node, source: bytes) -> str:
        """C/C++ declarator nodes are nested: function_declarator → identifier."""
        for child in decl_node.children:
            if child.type == "identifier":
                return self._ts_text(source, child)
            if child.type in ("function_declarator", "pointer_declarator",
                              "qualified_identifier", "destructor_name"):
                return self._unwrap_cpp_declarator(child, source)
        return self._ts_text(source, decl_node)

    def _include_preceding_attrs(self, lines: list, start: int) -> int:
        """Walk backwards to include preceding attributes/decorators."""
        attr_pattern = re.compile(r'^\s*(#\[|@|///)') 
        i = start - 1
        while i >= 0 and attr_pattern.match(lines[i]):
            i -= 1
        return i + 1

    def _detect_indent(self, line: str) -> str:
        """Return the leading whitespace of a line."""
        return line[:len(line) - len(line.lstrip())]

    def _normalize_indent(self, code: str, target_indent: str) -> str:
        """
        Normalize the model's output indentation to match the target file's style.
        """
        lines = code.splitlines()
        if not lines:
            return code

        # Find the actual indent of the first non-empty line
        src_indent = ""
        for line in lines:
            if line.strip():
                src_indent = line[:len(line) - len(line.lstrip())]
                break

        if src_indent == target_indent:
            return code

        result = []
        for line in lines:
            if not line.strip():
                result.append("")
            elif line.startswith(src_indent):
                result.append(target_indent + line[len(src_indent):])
            else:
                result.append(target_indent + line.lstrip())
        return "\n".join(result)

    def _check_symbol_constraints(
        self,
        function_code: str,
        allowed_callees:   List[str] = None,
        expected_symbols:  List[str] = None,
        forbidden_symbols: List[str] = None,
    ) -> Tuple[bool, str, List[str]]:
        """
        Layer 1: AST-based symbol constraint checker (v2.1).

        Parses the generated code and walks ast.Call + ast.Attribute nodes.
        Ignores comments and string literals — only checks executable code.
        This eliminates false positives from symbols in comments/strings
        and false negatives from aliased imports.

        Returns (passed, error_message, suggestions).
        """
        import ast as _ast

        allowed_callees  = allowed_callees or []
        expected_symbols = expected_symbols or []
        forbidden_symbols = forbidden_symbols or []

        # ── Step 1: Parse the generated code ───────────────────────────────
        try:
            tree = _ast.parse(function_code)
        except SyntaxError:
            # Can't parse — fall back to string matching for this run
            return self._check_symbol_constraints_fallback(
                function_code, expected_symbols, forbidden_symbols
            )

        # ── Step 2: Extract all called names from AST ─────────────────────
        called_names: set = set()       # short names: e.g. "debug", "time"
        attribute_calls: set = set()    # dotted paths: e.g. "self.logger.debug"

        for node in _ast.walk(tree):
            if isinstance(node, _ast.Call):
                # Direct call: func()
                if isinstance(node.func, _ast.Name):
                    called_names.add(node.func.id)
                # Method call: obj.method()
                elif isinstance(node.func, _ast.Attribute):
                    called_names.add(node.func.attr)
                    # Record full dotted path for precise matching
                    if isinstance(node.func.value, _ast.Name):
                        attribute_calls.add(
                            f"{node.func.value.id}.{node.func.attr}"
                        )
                    elif isinstance(node.func.value, _ast.Attribute):
                        # self.something.method()
                        if isinstance(node.func.value.value, _ast.Name):
                            attribute_calls.add(
                                f"{node.func.value.value.id}."
                                f"{node.func.value.attr}."
                                f"{node.func.attr}"
                            )

        all_used = called_names | attribute_calls

        # ── Step 3: Check forbidden symbols ───────────────────────────────
        for forbidden in forbidden_symbols:
            short = forbidden.split(".")[-1].split("(")[0]
            if short in called_names or any(
                forbidden in a for a in attribute_calls
            ):
                return (
                    False,
                    f"[SYMBOL ERROR] Used forbidden symbol: '{forbidden}'. "
                    f"Remove all uses of '{forbidden}' from your code.",
                    [f"Replace '{forbidden}' with an equivalent from <available_callees>."]
                )

        # ── Step 4: Check expected symbols ────────────────────────────────
        for expected in expected_symbols:
            short = expected.split(".")[-1].split("(")[0]
            if short not in called_names and not any(
                expected in a for a in attribute_calls
            ):
                return (
                    False,
                    f"[SYMBOL ERROR] Missing required symbol: '{expected}'. "
                    f"Your code MUST call '{expected}'.",
                    [f"Add a call to '{expected}' in your implementation."]
                )

        # ── Step 5: Check against allowed callees list (if provided) ──────
        if allowed_callees:
            allowed_short = {c.split(".")[-1].split("(")[0] for c in allowed_callees}
            # Common builtins that are always allowed
            always_allowed = {
                "print", "len", "str", "int", "float", "list", "dict", "set",
                "tuple", "bool", "bytes", "bytearray", "memoryview",
                "range", "enumerate", "zip", "map", "filter", "sorted", "type",
                "isinstance", "issubclass", "hasattr", "getattr", "setattr",
                "delattr", "super", "repr", "hash", "id", "callable",
                "iter", "next", "reversed", "all", "any",
                "time", "round", "abs", "min", "max", "sum", "open", "format",
                "vars", "dir", "property", "staticmethod", "classmethod",
                "ValueError", "TypeError", "KeyError", "AttributeError",
                "RuntimeError", "NotImplementedError", "ImportError",
                "StopIteration", "OSError", "IOError", "Exception",
            }
            allowed_short |= always_allowed

            # Find calls NOT in the allowed list
            suspicious = {
                name for name in called_names
                if name not in allowed_short
                and not name.startswith("_")  # ignore private methods
                and len(name) > 2             # ignore single-char names
            }

            if suspicious:
                top = sorted(suspicious)[:3]
                return (
                    False,
                    f"[HALLUCINATION] Called functions not in <available_callees>: "
                    f"{', '.join(top)}. "
                    f"These do not exist in this codebase.",
                    [
                        f"Remove: {', '.join(top)}",
                        f"Allowed functions: {', '.join(sorted(allowed_callees)[:6])}",
                    ]
                )

        return True, "", []

    def _check_symbol_constraints_fallback(
        self,
        function_code: str,
        expected_symbols: List[str],
        forbidden_symbols: List[str],
    ) -> Tuple[bool, str, List[str]]:
        """
        Fallback string-matching checker when AST parsing fails (syntax errors).
        Less accurate but ensures we still catch obvious constraint violations.
        """
        errors = []
        suggestions = []

        for sym in expected_symbols:
            if sym not in function_code:
                errors.append(f"Missing required symbol: `{sym}`")
                suggestions.append(f"Your code MUST contain `{sym}`. Add it.")

        for sym in forbidden_symbols:
            if sym in function_code:
                errors.append(f"Forbidden symbol found: `{sym}`")
                suggestions.append(f"Remove all uses of `{sym}`. It is forbidden.")

        if errors:
            return False, " | ".join(errors), suggestions

        return True, "", []

    # ── Formatting normalization ──────────────────────────────────────────────

    def _normalize_formatting(self, source: str, target_file: str) -> str:
        """Run ruff format on patched source to normalize whitespace before mypy.
        Silently falls back to unformatted source if ruff is not installed."""
        try:
            result = subprocess.run(
                ["ruff", "format", "--stdin-filename", target_file, "-"],
                input=source,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            pass  # ruff not installed or timed out — skip gracefully
        return source

    def _cleanup(self, temp_file: Optional[str]):
        if temp_file:
            Path(temp_file).unlink(missing_ok=True)

    @staticmethod
    def _walk_types(node, types: set):
        """Recursively walk a tree-sitter node tree, yielding nodes of given types."""
        if node.type in types:
            yield node
        for child in node.children:
            yield from VerificationPipeline._walk_types(child, types)