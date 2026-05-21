"""
Symbolic extractors — pull type contracts, preconditions, postconditions,
side-effect annotations, and caller patterns from source and CPG.
Zero LLM calls. Pure AST + regex + tree-sitter analysis.

Supports Python (ast fast path) and all other languages (tree-sitter + doc comment parsing).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.cpg.language_registry import get_language_config, get_ts_language


# ─────────────────────────────────────────────────────────────────────────────
# Contract extraction
# ─────────────────────────────────────────────────────────────────────────────

class ContractExtractor:
    """
    Extracts pre/post conditions and side effects from:
    - Type annotations (source of truth)
    - Docstring sections (Args, Returns, Raises, Note)
    - Assert statements in function body
    - raise statements
    """

    def extract(self, source: str, func_name: str, file_path: str = "") -> Dict[str, List[str]]:
        contract = {
            "preconditions":  [],
            "postconditions": [],
            "side_effects":   [],
            "raises":         [],
            "invariants":     [],
        }

        ext = Path(file_path).suffix.lower() if file_path else ".py"

        if ext == ".py":
            return self._extract_python(source, func_name, contract)
        else:
            return self._extract_generic(source, func_name, file_path, contract)

    def _extract_python(self, source: str, func_name: str, contract: dict) -> dict:
        """Python fast path — uses ast module."""
        try:
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name == func_name:
                        self._from_annotations(node, contract)
                        self._from_docstring(node, contract)
                        self._from_asserts(node, contract)
                        self._from_raises(node, contract)
                        self._from_body_patterns(node, source, contract)
                        break
        except Exception:
            pass
        return contract

    @staticmethod
    def _ts_text(source: bytes, node) -> str:
        """Safe byte-slice → str for tree-sitter nodes."""
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _extract_generic(
        self, source: str, func_name: str, file_path: str, contract: dict
    ) -> dict:
        """Generic path — uses tree-sitter + doc comment parsing."""
        config = get_language_config(file_path)
        if not config:
            return contract

        try:
            lang   = get_ts_language(config)
            from tree_sitter import Parser
            parser = Parser(lang)
            source_bytes = source.encode("utf-8")
            tree   = parser.parse(source_bytes)

            all_func_types = set(config.func_node_types + config.method_node_types)
            for node in self._walk_types(tree.root_node, all_func_types):
                name = self._get_func_name_ts(node, source_bytes, config)
                if name != func_name:
                    continue

                # Extract doc comment above the function (uses line-based, so str is fine)
                doc = self._extract_doc_comment(source_bytes, node, config)
                if doc:
                    self._parse_doc_contract(doc, contract, config)

                # Extract return type as postcondition
                if config.return_type_field:
                    ret_node = node.child_by_field_name(config.return_type_field)
                    if ret_node:
                        ret_text = self._ts_text(source_bytes, ret_node).strip()
                        contract["postconditions"].append(f"returns: {ret_text}")

                # Extract parameter types as preconditions
                if config.params_field:
                    params_node = node.child_by_field_name(config.params_field)
                    if params_node:
                        params_text = self._ts_text(source_bytes, params_node).strip()
                        if len(params_text) > 2:
                            contract["preconditions"].append(f"params: {params_text[:120]}")

                break

        except (ImportError, Exception):
            pass

        return contract

    def _extract_doc_comment(self, source: bytes, fn_node, config) -> str:
        """Extract the doc comment block immediately above a function node."""
        lines = source[:fn_node.start_byte].decode("utf-8", errors="replace").splitlines()
        doc_lines = []

        prefix = config.doc_comment_prefix

        # Walk backwards from the function to find doc comments
        for line in reversed(lines):
            stripped = line.strip()
            if not stripped:
                if doc_lines:
                    break  # blank line after doc block = end
                continue

            # JSDoc style: /** ... */ or single-line /** */
            if stripped.startswith("/**") or stripped.startswith("*") or stripped.endswith("*/"):
                doc_lines.append(stripped.lstrip("/*").rstrip("*/").strip())
                if stripped.startswith("/**"):
                    break
            # Triple-slash doc comments (Rust, TS)
            elif stripped.startswith("///"):
                doc_lines.append(stripped[3:].strip())
            # Go-style: // comment above function
            elif stripped.startswith("//") and not stripped.startswith("//!"):
                doc_lines.append(stripped[2:].strip())
            # Python: # comments (handled by ast path, but fallback)
            elif stripped.startswith("#"):
                doc_lines.append(stripped[1:].strip())
            else:
                break

        doc_lines.reverse()
        return "\n".join(doc_lines)

    def _parse_doc_contract(self, doc: str, contract: dict, config) -> None:
        """Parse doc comment sections into contract structure."""
        lines = doc.splitlines()
        section = None

        for line in lines:
            stripped = line.strip()
            low = stripped.lower()

            # JSDoc tags
            if low.startswith("@param") or low.startswith("@arg"):
                section = "pre"
                contract["preconditions"].append(stripped[:120])
                continue
            elif low.startswith("@returns") or low.startswith("@return"):
                section = "post"
                contract["postconditions"].append(stripped[:120])
                continue
            elif low.startswith("@throws") or low.startswith("@raises") or low.startswith("@exception"):
                section = "raises"
                contract["raises"].append(stripped[:120])
                continue

            # Rust/Go-style sections
            if low.startswith(("args:", "parameters:", "param ", "# parameters", "# arguments")):
                section = "pre"
            elif low.startswith(("returns:", "return:", "# returns", "# return value")):
                section = "post"
            elif low.startswith(("raises:", "raise:", "errors:", "# errors", "# panics")):
                section = "raises"
            elif low.startswith(("side effects:", "side-effects:", "writes:", "mutates:",
                                "# safety", "# side effects")):
                section = "side"
            elif low.startswith(("note:", "warning:", "important:", "# note", "# warning")):
                section = "inv"
            elif stripped and section:
                if section == "pre":
                    contract["preconditions"].append(stripped[:120])
                elif section == "post":
                    contract["postconditions"].append(stripped[:120])
                elif section == "raises":
                    contract["raises"].append(stripped[:120])
                elif section == "side":
                    contract["side_effects"].append(stripped[:120])
                elif section == "inv":
                    contract["invariants"].append(stripped[:120])

    def _get_func_name_ts(self, fn_node, source: bytes, config) -> Optional[str]:
        """Extract function name from tree-sitter node.
        source must be bytes — tree-sitter byte offsets index into UTF-8 bytes."""
        name_node = fn_node.child_by_field_name(config.name_field)
        if name_node:
            text = self._ts_text(source, name_node).strip()
            if config.name_field == "declarator":
                for child in name_node.children:
                    if child.type == "identifier":
                        return self._ts_text(source, child)
            return text.split("::")[-1].split("(")[0].strip()

        # Arrow function from variable
        parent = fn_node.parent
        if parent and parent.type == "variable_declarator":
            left = parent.child_by_field_name("name")
            if left:
                return self._ts_text(source, left).strip()
        return None

    @staticmethod
    def _walk_types(node, types: set):
        if node.type in types:
            yield node
        for child in node.children:
            yield from ContractExtractor._walk_types(child, types)

    # ── Python-specific helpers (preserved) ──────────────────────────────────

    def _from_annotations(self, node: ast.FunctionDef, contract: dict):
        if node.returns:
            ret = ast.unparse(node.returns)
            contract["postconditions"].append(f"returns: {ret}")
        for arg in node.args.args:
            if arg.annotation:
                ann = ast.unparse(arg.annotation)
                if ann not in ("Any", "object"):
                    contract["preconditions"].append(f"{arg.arg}: {ann}")

    def _from_docstring(self, node: ast.FunctionDef, contract: dict):
        docstring = ast.get_docstring(node)
        if not docstring:
            return
        lines = docstring.splitlines()
        section = None
        for line in lines:
            stripped = line.strip()
            low = stripped.lower()
            if low.startswith(("args:", "parameters:", "param ")):
                section = "pre"
            elif low.startswith(("returns:", "return:")):
                section = "post"
            elif low.startswith(("raises:", "raise:")):
                section = "raises"
            elif low.startswith(("side effects:", "side-effects:", "writes:", "mutates:")):
                section = "side"
            elif low.startswith(("note:", "warning:", "important:")):
                section = "inv"
            elif stripped and section:
                if section == "pre":
                    contract["preconditions"].append(stripped[:120])
                elif section == "post":
                    contract["postconditions"].append(stripped[:120])
                elif section == "raises":
                    contract["raises"].append(stripped[:120])
                elif section == "side":
                    contract["side_effects"].append(stripped[:120])
                elif section == "inv":
                    contract["invariants"].append(stripped[:120])

    def _from_asserts(self, node: ast.FunctionDef, contract: dict):
        for child in ast.walk(node):
            if isinstance(child, ast.Assert):
                text = ast.unparse(child.test)
                contract["preconditions"].append(f"assert {text}")
                if len(contract["preconditions"]) > 5:
                    break

    def _from_raises(self, node: ast.FunctionDef, contract: dict):
        for child in ast.walk(node):
            if isinstance(child, ast.Raise) and child.exc:
                exc = ast.unparse(child.exc)
                entry = f"raises {exc}"
                if entry not in contract["raises"]:
                    contract["raises"].append(entry)
                if len(contract["raises"]) > 3:
                    break

    def _from_body_patterns(self, node: ast.FunctionDef, source: str, contract: dict):
        src = ast.unparse(node)
        # DB write patterns
        db_writes = re.findall(r'\b(session\.add|db\.execute|\.save\(|\.commit\(|cursor\.execute)', src)
        if db_writes:
            contract["side_effects"].append(f"DB write: {', '.join(set(db_writes))}")
        # File writes
        file_writes = re.findall(r'\b(open\(.+[\'"]w[\'"]\\)|\.write\(|shutil\.)', src)
        if file_writes:
            contract["side_effects"].append("file write")
        # Network
        net = re.findall(r'\b(requests\.|httpx\.|aiohttp\.|socket\.)', src)
        if net:
            contract["side_effects"].append("network call")
        # Cache / state mutation
        cache = re.findall(r'\bcache\b|\b_cache\b|\bself\.\w+\s*=', src)
        if cache:
            contract["side_effects"].append("mutates state")


# ─────────────────────────────────────────────────────────────────────────────
# Caller pattern extraction
# ─────────────────────────────────────────────────────────────────────────────

class CallerPatternExtractor:
    """
    Given a target function name and its callers from the CPG,
    extract representative call sites (how callers actually invoke it).
    """

    def extract(
        self,
        project_root: str,
        target_func: str,
        caller_nodes: list,   # List[CPGNode]
        max_sites: int = 3,
    ) -> List[str]:
        patterns: List[str] = []
        for caller in caller_nodes[:max_sites]:
            try:
                path = Path(project_root) / caller.file_path
                source = path.read_text(errors="replace")
                ext    = path.suffix.lower()
                if ext == ".py":
                    sites = self._find_call_sites_python(source, target_func, caller.file_path)
                else:
                    sites = self._find_call_sites_generic(source, target_func, caller.file_path)
                patterns.extend(sites[:2])
                if len(patterns) >= max_sites:
                    break
            except Exception:
                continue
        return patterns[:max_sites]

    def _find_call_sites_python(self, source: str, target: str, file_rel: str) -> List[str]:
        """Python: use ast to find call sites."""
        sites = []
        try:
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    call_str = ast.unparse(node)
                    func_part = ast.unparse(node.func) if hasattr(node, "func") else ""
                    if func_part.split(".")[-1] == target or func_part == target:
                        lineno = getattr(node, "lineno", 0)
                        sites.append(f"{file_rel}:{lineno} → {call_str[:120]}")
        except Exception:
            pass
        return sites

    def _find_call_sites_generic(self, source: str, target: str, file_rel: str) -> List[str]:
        """Generic: use regex to find call sites."""
        sites = []
        for i, line in enumerate(source.splitlines(), 1):
            # Match function_name( or .function_name(
            if re.search(rf'\b{re.escape(target)}\s*\(', line):
                sites.append(f"{file_rel}:{i} → {line.strip()[:120]}")
                if len(sites) >= 3:
                    break
        return sites


# ─────────────────────────────────────────────────────────────────────────────
# Body skeleton extractor (for functions >150 LOC)
# ─────────────────────────────────────────────────────────────────────────────

class BodySkeletonExtractor:
    """
    When the target function body is too large for the token budget,
    compress it to a skeleton: keep signatures of nested funcs, 
    first/last line of each block, strip long string literals.
    """

    def extract(self, source: str, func_name: str, max_lines: int = 80,
                file_path: str = "") -> str:
        ext = Path(file_path).suffix.lower() if file_path else ".py"

        if ext == ".py":
            return self._extract_python(source, func_name, max_lines)
        else:
            return self._extract_generic(source, func_name, max_lines, file_path)

    def _extract_python(self, source: str, func_name: str, max_lines: int) -> str:
        """Python: use ast to find function body."""
        try:
            tree   = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name == func_name:
                        lines  = source.splitlines()
                        body_l = node.body[0].lineno - 1 if node.body else node.lineno
                        body_e = node.end_lineno
                        body_lines = lines[body_l:body_e]
                        if len(body_lines) <= max_lines:
                            return "\n".join(body_lines)
                        return self._skeleton(body_lines, node, source)
        except Exception:
            pass
        return source

    def _extract_generic(self, source: str, func_name: str, max_lines: int,
                         file_path: str) -> str:
        """Generic: use tree-sitter to find function body."""
        config = get_language_config(file_path)
        if not config:
            return source

        try:
            lang = get_ts_language(config)
            from tree_sitter import Parser
            parser = Parser(lang)
            tree   = parser.parse(source.encode("utf-8"))

            all_func_types = set(config.func_node_types + config.method_node_types)
            for node in self._walk_types(tree.root_node, all_func_types):
                name_node = node.child_by_field_name(config.name_field)
                if not name_node:
                    continue
                name = source[name_node.start_byte:name_node.end_byte].strip()
                if config.name_field == "declarator":
                    name = name.split("::")[-1].split("(")[0].strip()
                if name != func_name:
                    continue

                lines = source.splitlines()
                start = node.start_point[0]
                end   = node.end_point[0] + 1
                body_lines = lines[start:end]
                if len(body_lines) <= max_lines:
                    return "\n".join(body_lines)
                # Simple skeleton: keep first/last lines + control flow
                return self._skeleton_generic(body_lines)

        except (ImportError, Exception):
            pass
        return source

    def _skeleton_generic(self, lines: List[str]) -> str:
        """Language-agnostic skeleton: keep control flow keywords."""
        result = []
        keep_patterns = re.compile(
            r"^\s*(def |fn |func |function |class |struct |impl |trait |"
            r"if |else\b|else if|elif |for |while |loop |match |switch |"
            r"try\b|catch|except|finally:|return |raise |throw |yield |"
            r"import |from |use |#include|pub |async |await )"
        )
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith(("//", "#", "/*", "*")):
                continue
            if keep_patterns.match(line) or i < 3 or i > len(lines) - 3:
                result.append(line.rstrip())
        result.append("    // ... [body compressed for token budget]")
        return "\n".join(result)

    def _skeleton(self, lines: List[str], node: ast.FunctionDef, source: str) -> str:
        """Python: keep control flow skeleton, strip long literals and implementation detail."""
        result = []
        keep_patterns = re.compile(
            r"^\s*(def |class |if |elif |else:|for |while |try:|except|finally:|"
            r"with |return |raise |yield |assert |import |from )"
        )
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if keep_patterns.match(line):
                cleaned = re.sub(r'(""".*?"""|\'\'\'.*?\'\'\'|"[^"]{60,}"|\'[^\']{60,}\')', '"..."', line)
                result.append(cleaned.rstrip())
            elif i < 3 or i > len(lines) - 3:
                result.append(line.rstrip())
        result.append("    # ... [body compressed for token budget]")
        return "\n".join(result)

    @staticmethod
    def _walk_types(node, types: set):
        if node.type in types:
            yield node
        for child in node.children:
            yield from BodySkeletonExtractor._walk_types(child, types)