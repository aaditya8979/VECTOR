"""
CPG Builder — zero-LLM, pure symbolic construction of Code Property Graph.
Parses the entire project with tree-sitter and builds a directed graph
of function nodes connected by CALLS / IMPORTS / INHERITS edges.

Supports Python, TypeScript, JavaScript, Go, Rust, C++, and C via the
language registry. Python uses a dedicated fast path. All other languages
use generic tree-sitter extraction.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Dict, Generator, List, Optional, Tuple

import networkx as nx

from tree_sitter import Parser, Node

from .models import CPGNode, CPGEdge, EdgeType
from .language_registry import (
    get_language_config, get_ts_language, is_supported, supported_extensions,
    LanguageConfig,
)

_SKIP_DIRS = {".venv", "venv", "__pycache__", ".git", "node_modules", ".mypy_cache",
              "dist", "build", ".next", "target", ".cargo", "pkg"}


class CPGBuilder:
    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
        self.graph        = nx.DiGraph()
        self.nodes:  Dict[str, CPGNode] = {}
        self._file_imports: Dict[str, Dict[str, str]] = {}  # file → {alias: module}
        self._parsers: Dict[str, Parser] = {}  # cache parsers by language key

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def build(self) -> "CPGBuilder":
        source_files = list(self._iter_source_files())
        for f in source_files:
            self._parse_file(f)
        for f in source_files:
            self._resolve_calls(f)
        return self

    def build_for_file(self, file_path: str) -> "CPGBuilder":
        """Parse and resolve a single file into the CPG graph.
        Useful for tests and incremental builds."""
        p = Path(file_path).resolve()
        self._parse_file(p)
        self._resolve_calls(p)
        return self

    def get_node(self, node_id: str) -> Optional[CPGNode]:
        return self.nodes.get(node_id)

    def get_direct_callees(self, node_id: str) -> List[CPGNode]:
        """1-hop downstream: functions this node calls."""
        result = []
        for _, target, data in self.graph.out_edges(node_id, data=True):
            if data.get("edge_type") == EdgeType.CALLS and target in self.nodes:
                result.append(self.nodes[target])
        result.sort(key=lambda n: self.graph[node_id][n.node_id].get("call_frequency", 1), reverse=True)
        return result

    def get_direct_callers(self, node_id: str) -> List[CPGNode]:
        """1-hop upstream: functions that call this node."""
        result = []
        for source, _, data in self.graph.in_edges(node_id, data=True):
            if data.get("edge_type") == EdgeType.CALLS and source in self.nodes:
                result.append(self.nodes[source])
        return result

    def get_stale_neighbors(self, node_id: str) -> List[str]:
        """Return node_ids of 1-hop neighbors that are marked stale."""
        nbrs = [t for _, t in self.graph.out_edges(node_id)] + \
               [s for s, _ in self.graph.in_edges(node_id)]
        return [n for n in nbrs if self.nodes.get(n, CPGNode.__new__(CPGNode)).is_stale]

    def find_node_by_function(self, file_path: str, function_name: str = "") -> Optional[CPGNode]:
        # THE BULLETPROOF MATCHER: Bypasses all pathlib absolute/relative nonsense.
        target_path = str(file_path).replace("\\", "/")

        # Collect all functions in the target file
        file_nodes: List[CPGNode] = []
        for nid, node in self.nodes.items():
            node_path = node.file_path.replace("\\", "/")
            if node_path.endswith(target_path) or target_path.endswith(node_path):
                if function_name and node.function_name == function_name:
                    return node
                file_nodes.append(node)

        # Bug 2 fix: auto-detect when function_name is empty and file has exactly 1 function
        if not function_name and len(file_nodes) == 1:
            return file_nodes[0]

        return None

    def save(self, path: str):
        data = {
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "edges": [
                {"u": u, "v": v, "data": d}
                for u, v, d in self.graph.edges(data=True)
            ],
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, path: str, project_root: str) -> "CPGBuilder":
        b = cls(project_root)
        with open(path) as f:
            data = json.load(f)
        for nid, nd in data["nodes"].items():
            node = CPGNode.from_dict(nd)
            b.nodes[nid] = node
            b.graph.add_node(nid, **nd)
        for edge in data["edges"]:
            b.graph.add_edge(edge["u"], edge["v"], **edge["data"])
        return b

    # ─────────────────────────────────────────────────────────────────────────
    # Internal: file iteration
    # ─────────────────────────────────────────────────────────────────────────

    def _iter_source_files(self) -> Generator[Path, None, None]:
        """Walk project tree yielding all files with supported extensions."""
        exts = set(supported_extensions())
        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for f in files:
                if Path(f).suffix.lower() in exts:
                    yield Path(root) / f

    # ─────────────────────────────────────────────────────────────────────────
    # Internal: parsing
    # ─────────────────────────────────────────────────────────────────────────

    def _get_parser(self, config: LanguageConfig) -> Optional[Parser]:
        """Get or create a cached tree-sitter Parser for the given language."""
        key = f"{config.tree_sitter_module}:{config.ts_sublanguage or 'default'}"
        if key in self._parsers:
            return self._parsers[key]
        try:
            lang = get_ts_language(config)
            parser = Parser(lang)
            self._parsers[key] = parser
            return parser
        except ImportError:
            return None

    def _parse_file(self, file_path: Path):
        config = get_language_config(str(file_path))
        if config is None:
            return

        parser = self._get_parser(config)
        if parser is None:
            return

        try:
            source_bytes = file_path.read_bytes()
            tree         = parser.parse(source_bytes)
            rel          = self._rel(file_path)

            self._file_imports[rel] = self._extract_imports_generic(
                tree.root_node, source_bytes, config
            )

            if config.use_python_ast:
                self._extract_functions_python(tree.root_node, source_bytes, rel, file_path)
            else:
                # Generic path: pass bytes to avoid byte-offset vs code-point mismatch
                self._extract_functions_generic(tree.root_node, source_bytes, rel, file_path, config)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # Python fast path (preserved from original)
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_functions_python(self, root: Node, source: bytes, rel: str, file_path: Path):
        """Python-specific function extraction — uses the original logic."""
        mtime = file_path.stat().st_mtime
        for fn_node in self._walk_type(root, {"function_definition"}):
            try:
                name = self._field_text(fn_node, "name", source)
                if not name:
                    continue

                class_name = self._class_context_python(fn_node, source)
                node_id    = f"{rel}::{class_name}::{name}" if class_name else f"{rel}::{name}"

                sig        = self._extract_sig_python(fn_node, source, name)
                ret_type   = self._extract_ret_python(fn_node, source)
                decorators = self._extract_decorators_python(fn_node, source)
                raises     = self._extract_raises_python(fn_node, source)
                body_hash  = hashlib.sha256(
                    source[fn_node.start_byte:fn_node.end_byte]
                ).hexdigest()[:16]

                node = CPGNode(
                    node_id       = node_id,
                    file_path     = rel,
                    function_name = name,
                    class_name    = class_name,
                    signature     = sig,
                    return_type   = ret_type,
                    decorators    = decorators,
                    raises        = raises,
                    body_hash     = body_hash,
                    start_line    = fn_node.start_point[0] + 1,
                    end_line      = fn_node.end_point[0] + 1,
                    last_modified = mtime,
                )
                self.nodes[node_id] = node
                self.graph.add_node(node_id, **node.to_dict())
            except Exception:
                continue

    # ─────────────────────────────────────────────────────────────────────────
    # Generic multi-language extraction
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_functions_generic(
        self, root: Node, source: bytes, rel: str, file_path: Path, config: LanguageConfig
    ):
        """Language-agnostic function extraction using the registry config.
        NOTE: source is bytes to match tree-sitter byte offsets. All slicing
        uses _ts_text() which decodes the byte-slice to str.
        """
        mtime = file_path.stat().st_mtime
        all_func_types = set(config.func_node_types + config.method_node_types)

        for fn_node in self._walk_type(root, all_func_types):
            try:
                name = self._get_name_generic(fn_node, source, config)
                if not name:
                    continue

                class_name = self._get_class_context_generic(fn_node, source, config)
                node_id = f"{rel}::{class_name}::{name}" if class_name else f"{rel}::{name}"

                sig       = self._get_signature_generic(fn_node, source, config, name)
                ret_type  = self._get_return_type_generic(fn_node, source, config)
                body_hash = hashlib.sha256(
                    source[fn_node.start_byte:fn_node.end_byte]
                ).hexdigest()[:16]

                node = CPGNode(
                    node_id       = node_id,
                    file_path     = rel,
                    function_name = name,
                    class_name    = class_name,
                    signature     = sig,
                    return_type   = ret_type,
                    decorators    = [],
                    raises        = [],
                    body_hash     = body_hash,
                    start_line    = fn_node.start_point[0] + 1,
                    end_line      = fn_node.end_point[0] + 1,
                    last_modified = mtime,
                )
                self.nodes[node_id] = node
                self.graph.add_node(node_id, **node.to_dict())
            except Exception:
                continue

    def _get_name_generic(self, fn_node: Node, source: bytes, config: LanguageConfig) -> Optional[str]:
        """Extract function name, handling language-specific quirks.
        source must be bytes — tree-sitter byte offsets index into UTF-8 bytes."""
        name_node = fn_node.child_by_field_name(config.name_field)
        if name_node:
            name_text = self._ts_text(source, name_node).strip()
            if config.name_field == "declarator":
                name_text = self._unwrap_cpp_declarator(name_node, source)
            return name_text.split("::")[-1].split("(")[0].strip()

        # Arrow functions / const assignments (JS/TS)
        parent = fn_node.parent
        if parent and parent.type in ("variable_declarator", "assignment_expression"):
            left = parent.child_by_field_name("name") or parent.child_by_field_name("left")
            if left:
                return self._ts_text(source, left).strip()

        # Export default arrow: export const foo = () => {}
        if parent and parent.type == "lexical_declaration":
            for child in parent.children:
                if child.type == "variable_declarator":
                    name_n = child.child_by_field_name("name")
                    if name_n:
                        return self._ts_text(source, name_n).strip()

        return None

    def _unwrap_cpp_declarator(self, decl_node: Node, source: bytes) -> str:
        """C/C++: recursively unwrap declarator → identifier."""
        for child in decl_node.children:
            if child.type == "identifier":
                return self._ts_text(source, child)
            if child.type in ("function_declarator", "pointer_declarator",
                              "qualified_identifier", "destructor_name"):
                return self._unwrap_cpp_declarator(child, source)
        return self._ts_text(source, decl_node)

    def _get_class_context_generic(
        self, fn_node: Node, source: bytes, config: LanguageConfig
    ) -> Optional[str]:
        """Walk up to find enclosing class/struct/impl."""
        parent = fn_node.parent
        while parent:
            if parent.type in config.class_node_types:
                name_node = parent.child_by_field_name("name")
                if name_node:
                    return self._ts_text(source, name_node).strip()
            if parent.type == "impl_item":
                type_node = parent.child_by_field_name("type")
                if type_node:
                    return self._ts_text(source, type_node).strip()
            parent = parent.parent
        return None

    def _get_signature_generic(
        self, fn_node: Node, source: bytes, config: LanguageConfig, name: str
    ) -> str:
        """Build a signature string from tree-sitter fields."""
        params_field = config.params_field or "parameters"
        params = fn_node.child_by_field_name(params_field)
        ret    = fn_node.child_by_field_name(config.return_type_field) if config.return_type_field else None
        p_str  = self._ts_text(source, params) if params else "()"
        r_str  = f" -> {self._ts_text(source, ret)}" if ret else ""

        prefix = config.display_name.lower()
        kw_map = {"python": "def", "rust": "fn", "go": "func"}
        prefix = kw_map.get(prefix, "function")

        return f"{prefix} {name}{p_str}{r_str}"

    def _get_return_type_generic(
        self, fn_node: Node, source: bytes, config: LanguageConfig
    ) -> str:
        """Extract return type annotation if available."""
        if not config.return_type_field:
            return ""
        ret = fn_node.child_by_field_name(config.return_type_field)
        if ret:
            text = self._ts_text(source, ret).strip()
            return text.lstrip("->").strip()
        return ""

    # ─────────────────────────────────────────────────────────────────────────
    # Import extraction (language-agnostic)
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_imports_generic(
        self, root: Node, source: bytes, config: LanguageConfig
    ) -> Dict[str, str]:
        """Extract imports using the registry's import_node_types."""
        imports: Dict[str, str] = {}

        for node in self._walk_type(root, set(config.import_node_types)):
            text = self._ts_text(source, node).strip()
            try:
                if config.use_python_ast:
                    imports.update(self._parse_python_import(text))
                else:
                    imports.update(self._parse_generic_import(text, config))
            except Exception:
                pass
        return imports

    def _parse_python_import(self, text: str) -> Dict[str, str]:
        """Parse Python import statements."""
        imports = {}
        if text.startswith("from "):
            parts  = text.split()
            module = parts[1] if len(parts) > 1 else ""
            if "import" in parts:
                idx = parts.index("import")
                names = " ".join(parts[idx + 1:]).split(",")
                for n in names:
                    n = n.strip()
                    if " as " in n:
                        alias, orig = n.split(" as ")
                        imports[alias.strip()] = f"{module}.{orig.strip()}"
                    else:
                        imports[n] = f"{module}.{n}"
        elif text.startswith("import "):
            parts = text.split()
            if "as" in parts:
                idx = parts.index("as")
                imports[parts[idx + 1]] = parts[idx - 1]
            else:
                name = parts[1].split(".")[0]
                imports[name] = parts[1]
        return imports

    def _parse_generic_import(self, text: str, config: LanguageConfig) -> Dict[str, str]:
        """Parse imports for non-Python languages (best-effort)."""
        imports = {}
        import re
        # JS/TS: import { foo, bar } from 'module'
        m = re.search(r'import\s+\{([^}]+)\}\s+from\s+[\'"]([^\'"]+)[\'"]', text)
        if m:
            names = [n.strip().split(' as ')[-1].strip() for n in m.group(1).split(',')]
            module = m.group(2)
            for n in names:
                if n:
                    imports[n] = module
            return imports

        # JS/TS: import foo from 'module'
        m = re.search(r'import\s+(\w+)\s+from\s+[\'"]([^\'"]+)[\'"]', text)
        if m:
            imports[m.group(1)] = m.group(2)
            return imports

        # Go: import "module/path"
        m = re.search(r'import\s+[\'"]([^\'"]+)[\'"]', text)
        if m:
            pkg = m.group(1).split('/')[-1]
            imports[pkg] = m.group(1)
            return imports

        # Rust: use std::collections::HashMap;
        m = re.search(r'use\s+([\w:]+)(?:::\{([^}]+)\})?', text)
        if m:
            module = m.group(1)
            if m.group(2):
                for n in m.group(2).split(','):
                    n = n.strip().split(' as ')[-1].strip()
                    if n:
                        imports[n] = module
            else:
                name = module.split('::')[-1]
                imports[name] = module
            return imports

        # C/C++: #include <header> or #include "header"
        m = re.search(r'#include\s+[<"]([^>"]+)[>"]', text)
        if m:
            header = m.group(1).split('/')[-1].split('.')[0]
            imports[header] = m.group(1)

        return imports

    # ─────────────────────────────────────────────────────────────────────────
    # Call resolution
    # ─────────────────────────────────────────────────────────────────────────

    def _resolve_calls(self, file_path: Path):
        config = get_language_config(str(file_path))
        if config is None:
            return

        parser = self._get_parser(config)
        if parser is None:
            return

        try:
            source_bytes = file_path.read_bytes()
            tree         = parser.parse(source_bytes)
            rel          = self._rel(file_path)

            all_func_types = set(config.func_node_types + config.method_node_types)
            call_types     = set(config.call_node_types)

            for fn_node in self._walk_type(tree.root_node, all_func_types):
                if config.use_python_ast:
                    fn_name    = self._field_text(fn_node, "name", source_bytes)
                    class_name = self._class_context_python(fn_node, source_bytes)
                else:
                    # Pass bytes — byte offsets must index into bytes, not str
                    fn_name    = self._get_name_generic(fn_node, source_bytes, config)
                    class_name = self._get_class_context_generic(fn_node, source_bytes, config)

                if not fn_name:
                    continue

                caller_id  = f"{rel}::{class_name}::{fn_name}" if class_name else f"{rel}::{fn_name}"
                if caller_id not in self.nodes:
                    continue

                for call in self._walk_type(fn_node, call_types):
                    if config.use_python_ast:
                        called = self._call_name(call, source_bytes)
                    else:
                        called = self._call_name_generic(call, source_bytes)
                    if not called:
                        continue
                    target = self._resolve_to_node(called, rel)
                    if target and target != caller_id:
                        if self.graph.has_edge(caller_id, target):
                            self.graph[caller_id][target]["call_frequency"] = \
                                self.graph[caller_id][target].get("call_frequency", 1) + 1
                        else:
                            self.graph.add_edge(
                                caller_id, target,
                                edge_type=EdgeType.CALLS,
                                call_frequency=1,
                            )
        except Exception:
            pass

    def _call_name_generic(self, call: Node, source: bytes) -> Optional[str]:
        """Extract the called function name from a call_expression node."""
        fn = call.child_by_field_name("function")
        if fn:
            text = self._ts_text(source, fn).strip()
            return text.split(".")[-1].split("->")[-1].split("::")[-1].strip()
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Internal: Python-specific helpers (preserved from original)
    # ─────────────────────────────────────────────────────────────────────────

    def _resolve_to_node(self, name: str, current_file: str) -> Optional[str]:
        parts     = name.split(".")
        func_name = parts[-1]
        same      = f"{current_file}::{func_name}"
        if same in self.nodes:
            return same

        candidates = [nid for nid in self.nodes if nid.endswith(f"::{func_name}")]
        if len(candidates) == 1:
            return candidates[0]

        if len(parts) == 2:
            class_n = parts[0]
            for nid in self.nodes:
                n = self.nodes[nid]
                if n.function_name == func_name and n.class_name == class_n:
                    return nid
        return None

    def _extract_sig_python(self, fn_node: Node, source: bytes, name: str) -> str:
        params = fn_node.child_by_field_name("parameters")
        ret    = fn_node.child_by_field_name("return_type")
        p_str  = source[params.start_byte:params.end_byte].decode("utf-8", errors="replace") if params else "()"
        r_str  = f" -> {source[ret.start_byte:ret.end_byte].decode('utf-8', errors='replace')}" if ret else ""
        return f"def {name}{p_str}{r_str}"

    def _extract_ret_python(self, fn_node: Node, source: bytes) -> str:
        ret = fn_node.child_by_field_name("return_type")
        if ret:
            return source[ret.start_byte:ret.end_byte].decode("utf-8", errors="replace").lstrip("->").strip()
        return "None"

    def _extract_decorators_python(self, fn_node: Node, source: bytes) -> List[str]:
        decs, node = [], fn_node.prev_sibling
        while node and node.type == "decorator":
            decs.append(source[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip())
            node = node.prev_sibling
        return list(reversed(decs))

    def _extract_raises_python(self, fn_node: Node, source: bytes) -> List[str]:
        raises = []
        for r in self._walk_type(fn_node, {"raise_statement"}):
            raises.append(source[r.start_byte:r.end_byte].decode("utf-8", errors="replace").strip())
            if len(raises) >= 3:
                break
        return raises

    def _class_context_python(self, fn_node: Node, source: bytes) -> Optional[str]:
        parent = fn_node.parent
        while parent:
            if parent.type == "class_definition":
                n = parent.child_by_field_name("name")
                return source[n.start_byte:n.end_byte].decode("utf-8", errors="replace") if n else None
            parent = parent.parent
        return None

    def _field_text(self, node: Node, field: str, source: bytes) -> Optional[str]:
        child = node.child_by_field_name(field)
        return source[child.start_byte:child.end_byte].decode("utf-8", errors="replace") if child else None

    def _call_name(self, call: Node, source: bytes) -> Optional[str]:
        fn = call.child_by_field_name("function")
        return source[fn.start_byte:fn.end_byte].decode("utf-8", errors="replace").strip() if fn else None

    def _rel(self, path: Path) -> str:
        return str(path.relative_to(self.project_root))

    @staticmethod
    def _ts_text(source: bytes, node: Node) -> str:
        """Safe byte-slice → str. tree-sitter byte offsets index into UTF-8
        bytes, NOT Python str code points. This prevents garbled names when
        source contains multi-byte characters (emoji, CJK, etc.)."""
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    @staticmethod
    def _walk_type(node: Node, types: set) -> Generator[Node, None, None]:
        if node.type in types:
            yield node
        for child in node.children:
            yield from CPGBuilder._walk_type(child, types)