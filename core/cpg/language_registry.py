"""
Language Registry — single source of truth for all language-specific configuration.

Every module that needs language-aware behavior imports from here.
Adding a new language = adding one entry to REGISTRY.
All tree-sitter grammars are lazily loaded and cached.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class LanguageConfig:
    """Configuration for a single programming language."""

    # ── Tree-sitter ──────────────────────────────────────────────────────────
    tree_sitter_package: str          # pip package name
    tree_sitter_module:  str          # import name (may differ from package)
    ts_sublanguage:      Optional[str] = None  # e.g. "typescript" or "tsx" for TS

    # ── Node types for CPG construction ──────────────────────────────────────
    func_node_types:   List[str] = field(default_factory=list)
    method_node_types: List[str] = field(default_factory=list)
    call_node_types:   List[str] = field(default_factory=list)
    import_node_types: List[str] = field(default_factory=list)
    class_node_types:  List[str] = field(default_factory=list)

    # ── Field names within function nodes ────────────────────────────────────
    name_field:        str            = "name"
    body_field:        Optional[str]  = "body"
    params_field:      Optional[str]  = "parameters"
    return_type_field: Optional[str]  = "return_type"

    # ── Verification tools ───────────────────────────────────────────────────
    type_checker:       Optional[List[str]] = None
    test_runner:        Optional[List[str]] = None
    test_file_patterns: List[str]           = field(default_factory=list)

    # ── Source file extensions ────────────────────────────────────────────────
    extensions: List[str] = field(default_factory=list)

    # ── Comment style ────────────────────────────────────────────────────────
    doc_comment_prefix: str  = "#"

    # ── Python ast fast path ─────────────────────────────────────────────────
    use_python_ast: bool = False

    # ── Display ──────────────────────────────────────────────────────────────
    display_name: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# REGISTRY — one entry per canonical extension
# ─────────────────────────────────────────────────────────────────────────────

REGISTRY: Dict[str, LanguageConfig] = {

    # ── Python ───────────────────────────────────────────────────────────────
    ".py": LanguageConfig(
        tree_sitter_package   = "tree-sitter-python",
        tree_sitter_module    = "tree_sitter_python",
        func_node_types       = ["function_definition"],
        method_node_types     = ["function_definition"],
        call_node_types       = ["call"],
        import_node_types     = ["import_statement", "import_from_statement"],
        class_node_types      = ["class_definition"],
        name_field            = "name",
        body_field            = "body",
        params_field          = "parameters",
        return_type_field     = "return_type",
        type_checker          = ["python", "-m", "mypy",
                                 "--ignore-missing-imports", "--no-error-summary"],
        test_runner           = ["python", "-m", "pytest", "-x", "--tb=short", "-q"],
        test_file_patterns    = ["test_*.py", "*_test.py"],
        extensions            = [".py"],
        doc_comment_prefix    = "#",
        use_python_ast        = True,
        display_name          = "Python",
    ),

    # ── TypeScript ───────────────────────────────────────────────────────────
    ".ts": LanguageConfig(
        tree_sitter_package   = "tree-sitter-typescript",
        tree_sitter_module    = "tree_sitter_typescript",
        ts_sublanguage        = "language_typescript",
        func_node_types       = ["function_declaration", "function_expression",
                                 "arrow_function", "generator_function_declaration"],
        method_node_types     = ["method_definition"],
        call_node_types       = ["call_expression"],
        import_node_types     = ["import_statement", "import_declaration"],
        class_node_types      = ["class_declaration", "class"],
        name_field            = "name",
        body_field            = "body",
        params_field          = "parameters",
        return_type_field     = "return_type",
        type_checker          = ["npx", "tsc", "--noEmit", "--strict"],
        test_runner           = ["npx", "jest", "--no-coverage"],
        test_file_patterns    = ["*.test.ts", "*.spec.ts", "__tests__/*.ts"],
        extensions            = [".ts"],
        doc_comment_prefix    = "///",
        display_name          = "TypeScript",
    ),

    # ── TypeScript/React (TSX) ───────────────────────────────────────────────
    ".tsx": LanguageConfig(
        tree_sitter_package   = "tree-sitter-typescript",
        tree_sitter_module    = "tree_sitter_typescript",
        ts_sublanguage        = "language_tsx",
        func_node_types       = ["function_declaration", "function_expression",
                                 "arrow_function"],
        method_node_types     = ["method_definition"],
        call_node_types       = ["call_expression"],
        import_node_types     = ["import_statement"],
        class_node_types      = ["class_declaration"],
        name_field            = "name",
        body_field            = "body",
        params_field          = "parameters",
        return_type_field     = "return_type",
        type_checker          = ["npx", "tsc", "--noEmit"],
        test_runner           = ["npx", "jest", "--no-coverage"],
        test_file_patterns    = ["*.test.tsx", "*.spec.tsx"],
        extensions            = [".tsx"],
        doc_comment_prefix    = "///",
        display_name          = "TypeScript/React",
    ),

    # ── JavaScript ───────────────────────────────────────────────────────────
    ".js": LanguageConfig(
        tree_sitter_package   = "tree-sitter-javascript",
        tree_sitter_module    = "tree_sitter_javascript",
        func_node_types       = ["function_declaration", "function_expression",
                                 "arrow_function", "generator_function_declaration"],
        method_node_types     = ["method_definition"],
        call_node_types       = ["call_expression"],
        import_node_types     = ["import_statement"],
        class_node_types      = ["class_declaration", "class"],
        name_field            = "name",
        body_field            = "body",
        params_field          = "parameters",
        return_type_field     = None,
        type_checker          = None,
        test_runner           = ["npx", "jest", "--no-coverage"],
        test_file_patterns    = ["*.test.js", "*.spec.js", "__tests__/*.js"],
        extensions            = [".js", ".jsx", ".mjs"],
        doc_comment_prefix    = "/**",
        display_name          = "JavaScript",
    ),

    # ── Go ───────────────────────────────────────────────────────────────────
    ".go": LanguageConfig(
        tree_sitter_package   = "tree-sitter-go",
        tree_sitter_module    = "tree_sitter_go",
        func_node_types       = ["function_declaration"],
        method_node_types     = ["method_declaration"],
        call_node_types       = ["call_expression"],
        import_node_types     = ["import_declaration", "import_spec"],
        class_node_types      = ["type_declaration"],
        name_field            = "name",
        body_field            = "body",
        params_field          = "parameters",
        return_type_field     = "result",
        type_checker          = ["go", "vet", "./..."],
        test_runner           = ["go", "test", "./...", "-v", "-run"],
        test_file_patterns    = ["*_test.go"],
        extensions            = [".go"],
        doc_comment_prefix    = "//",
        display_name          = "Go",
    ),

    # ── Rust ─────────────────────────────────────────────────────────────────
    ".rs": LanguageConfig(
        tree_sitter_package   = "tree-sitter-rust",
        tree_sitter_module    = "tree_sitter_rust",
        func_node_types       = ["function_item"],
        method_node_types     = ["function_item"],
        call_node_types       = ["call_expression", "method_call_expression"],
        import_node_types     = ["use_declaration"],
        class_node_types      = ["struct_item", "impl_item", "trait_item"],
        name_field            = "name",
        body_field            = "body",
        params_field          = "parameters",
        return_type_field     = "return_type",
        type_checker          = ["cargo", "check", "--message-format=short"],
        test_runner           = ["cargo", "test", "--", "--nocapture"],
        test_file_patterns    = ["**/tests/*.rs", "src/**/*_test.rs"],
        extensions            = [".rs"],
        doc_comment_prefix    = "///",
        display_name          = "Rust",
    ),

    # ── C++ ──────────────────────────────────────────────────────────────────
    ".cpp": LanguageConfig(
        tree_sitter_package   = "tree-sitter-cpp",
        tree_sitter_module    = "tree_sitter_cpp",
        func_node_types       = ["function_definition"],
        method_node_types     = ["function_definition"],
        call_node_types       = ["call_expression"],
        import_node_types     = ["preproc_include"],
        class_node_types      = ["class_specifier", "struct_specifier"],
        name_field            = "declarator",
        body_field            = "body",
        params_field          = "parameters",
        return_type_field     = "type",
        type_checker          = None,
        test_runner           = ["ctest", "--output-on-failure"],
        test_file_patterns    = ["test_*.cpp", "*_test.cpp", "*Test.cpp"],
        extensions            = [".cpp", ".cc", ".cxx", ".hpp"],
        doc_comment_prefix    = "///",
        display_name          = "C++",
    ),

    # ── C ────────────────────────────────────────────────────────────────────
    ".c": LanguageConfig(
        tree_sitter_package   = "tree-sitter-c",
        tree_sitter_module    = "tree_sitter_c",
        func_node_types       = ["function_definition"],
        method_node_types     = ["function_definition"],
        call_node_types       = ["call_expression"],
        import_node_types     = ["preproc_include"],
        class_node_types      = ["struct_specifier"],
        name_field            = "declarator",
        body_field            = "body",
        params_field          = "parameters",
        return_type_field     = "type",
        type_checker          = None,
        test_runner           = None,
        test_file_patterns    = ["test_*.c"],
        extensions            = [".c", ".h"],
        doc_comment_prefix    = "/**",
        display_name          = "C",
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# Reverse map: extension → config (built once at import time)
# ─────────────────────────────────────────────────────────────────────────────

_EXT_MAP: Dict[str, LanguageConfig] = {}
for _cfg in REGISTRY.values():
    for _ext in _cfg.extensions:
        _EXT_MAP[_ext] = _cfg

# Cache loaded tree-sitter Language objects
_LOADED: Dict[str, Any] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_language_config(file_path: str) -> Optional[LanguageConfig]:
    """Return the LanguageConfig for a given file path, or None if unsupported."""
    ext = Path(file_path).suffix.lower()
    return _EXT_MAP.get(ext)


def get_ts_language(config: LanguageConfig):
    """
    Lazily load and cache the tree-sitter Language object for the given config.
    Raises ImportError with install instructions if the package is missing.
    """
    from tree_sitter import Language

    # Cache key includes sublanguage for TS/TSX differentiation
    key = f"{config.tree_sitter_module}:{config.ts_sublanguage or 'default'}"
    if key in _LOADED:
        return _LOADED[key]

    try:
        mod = __import__(config.tree_sitter_module)

        # Handle tree-sitter-typescript which exposes language_typescript() / language_tsx()
        if config.ts_sublanguage:
            lang_fn = getattr(mod, config.ts_sublanguage, None)
            if lang_fn is None:
                raise ImportError(
                    f"Module {config.tree_sitter_module} has no attribute {config.ts_sublanguage}"
                )
            lang = Language(lang_fn())
        else:
            lang = Language(mod.language())

        _LOADED[key] = lang
        return lang

    except ImportError:
        raise ImportError(
            f"tree-sitter grammar for {config.display_name} not installed.\n"
            f"Run: pip install {config.tree_sitter_package}"
        )


def supported_extensions() -> List[str]:
    """Return all file extensions that TSDC can process."""
    return list(_EXT_MAP.keys())


def is_supported(file_path: str) -> bool:
    """Check if TSDC supports the given file type."""
    return get_language_config(file_path) is not None
