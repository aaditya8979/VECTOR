"""
test_byte_offsets.py — Parse emoji/multi-byte files in all languages, verify correct function names.

This is the core regression test for the byte-offset bug that caused garbled function names
when files contained emoji, CJK, or other multi-byte UTF-8 characters.
"""
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.cpg.builder import CPGBuilder
from core.cpg.language_registry import supported_extensions, get_language_config


# ── Test fixtures: source files with emoji + known function names ─────────────

FIXTURES = {
    ".py": {
        "source": '# 🌟 Welcome to Python! 🌟\n# ⚠️ Multi-byte test: 🚀🔥👨\u200d💻\n\ndef calculate_discount(price: float, percent: float) -> float:\n    """Apply discount."""\n    return price * (1 - percent / 100)\n',
        "expected_names": ["calculate_discount"],
    },
    ".ts": {
        "source": '// 🌟 Welcome to TypeScript! 🌟\n// ⚠️ Multi-byte test: 🚀🔥👨\u200d💻\n\nfunction calculateDiscount(price: number, percent: number): number {\n    return price * (1 - percent / 100);\n}\n',
        "expected_names": ["calculateDiscount"],
    },
    ".js": {
        "source": '// 🌟 Welcome to JavaScript! 🌟\n// ⚠️ Multi-byte test: 🚀🔥👨\u200d💻\n\nfunction calculateDiscount(price, percent) {\n    return price * (1 - percent / 100);\n}\n',
        "expected_names": ["calculateDiscount"],
    },
    ".go": {
        "source": '// 🌟 Welcome to Go! 🌟\n// ⚠️ Multi-byte test: 🚀🔥👨\u200d💻\npackage main\n\nfunc CalculateDiscount(price float64, percent float64) float64 {\n    return price * (1 - percent/100)\n}\n',
        "expected_names": ["CalculateDiscount"],
    },
    ".rs": {
        "source": '// 🌟 Welcome to Rust! 🌟\n// ⚠️ Multi-byte test: 🚀🔥👨\u200d💻\n\nfn calculate_discount(price: f64, percent: f64) -> f64 {\n    price * (1.0 - percent / 100.0)\n}\n',
        "expected_names": ["calculate_discount"],
    },
    ".c": {
        "source": '// 🌟 Welcome to C! 🌟\n// ⚠️ Multi-byte test: 🚀🔥👨\u200d💻\n\ndouble calculate_discount(double price, double percent) {\n    return price * (1.0 - percent / 100.0);\n}\n',
        "expected_names": ["calculate_discount"],
    },
    ".cpp": {
        "source": '// 🌟 Welcome to C++! 🌟\n// ⚠️ Multi-byte test: 🚀🔥👨\u200d💻\n\ndouble calculateDiscount(double price, double percent) {\n    return price * (1.0 - percent / 100.0);\n}\n',
        "expected_names": ["calculateDiscount"],
    },
}


def _has_grammar(ext: str) -> bool:
    """Check if the tree-sitter grammar for this extension is installed."""
    config = get_language_config(f"test{ext}")
    if not config:
        return False
    if ext == ".py":
        return True  # Python uses ast, not tree-sitter
    try:
        from core.cpg.language_registry import get_ts_language
        get_ts_language(config)
        return True
    except ImportError:
        return False


class TestByteOffsets:
    """Verify that multi-byte characters don't corrupt function name extraction."""

    @pytest.mark.parametrize("ext,fixture", list(FIXTURES.items()))
    def test_function_name_correct(self, ext, fixture, tmp_path):
        """Function names must be extracted correctly despite preceding emoji."""
        if not _has_grammar(ext):
            pytest.skip(f"tree-sitter grammar not installed for {ext}")

        # Write fixture to a temp file
        test_file = tmp_path / f"test_emoji{ext}"
        test_file.write_text(fixture["source"], encoding="utf-8")

        # Build CPG
        builder = CPGBuilder(str(tmp_path))
        builder.build()

        # Verify function names
        actual_names = sorted([n.function_name for n in builder.nodes.values()])
        expected = sorted(fixture["expected_names"])
        assert actual_names == expected, (
            f"Expected {expected}, got {actual_names} for {ext}"
        )

    @pytest.mark.parametrize("ext,fixture", list(FIXTURES.items()))
    def test_find_node_by_function(self, ext, fixture, tmp_path):
        """find_node_by_function must resolve names after multi-byte prefix."""
        if not _has_grammar(ext):
            pytest.skip(f"tree-sitter grammar not installed for {ext}")

        test_file = tmp_path / f"test_emoji{ext}"
        test_file.write_text(fixture["source"], encoding="utf-8")

        builder = CPGBuilder(str(tmp_path))
        builder.build()

        for name in fixture["expected_names"]:
            rel_path = f"test_emoji{ext}"
            node = builder.find_node_by_function(rel_path, name)
            assert node is not None, f"find_node_by_function failed for {name} in {ext}"
            assert node.function_name == name

    @pytest.mark.parametrize("ext,fixture", list(FIXTURES.items()))
    def test_auto_detect_single_function(self, ext, fixture, tmp_path):
        """Auto-detect must work when file has exactly one function."""
        if not _has_grammar(ext):
            pytest.skip(f"tree-sitter grammar not installed for {ext}")

        test_file = tmp_path / f"test_emoji{ext}"
        test_file.write_text(fixture["source"], encoding="utf-8")

        builder = CPGBuilder(str(tmp_path))
        builder.build()

        rel_path = f"test_emoji{ext}"
        node = builder.find_node_by_function(rel_path, "")  # empty = auto-detect
        assert node is not None, f"Auto-detect failed for {ext}"
        assert node.function_name == fixture["expected_names"][0]

    def test_byte_vs_char_divergence(self, tmp_path):
        """Verify the specific scenario: emoji makes byte count > char count."""
        source = '// 🌟🌟🌟🌟🌟\nfunction hello(): void {}\n'
        assert len(source) < len(source.encode("utf-8")), (
            "Test precondition: byte count must exceed char count"
        )

        test_file = tmp_path / "diverge.ts"
        test_file.write_text(source, encoding="utf-8")

        builder = CPGBuilder(str(tmp_path))
        builder.build()

        names = [n.function_name for n in builder.nodes.values()]
        assert "hello" in names, f"Expected 'hello', got {names}"


class TestCJKCharacters:
    """CJK characters are 3 bytes each in UTF-8 — maximum offset divergence."""

    def test_cjk_comments_python(self, tmp_path):
        source = '# 中文注释 日本語コメント 한국어주석\ndef process_data(x: int) -> int:\n    return x * 2\n'
        test_file = tmp_path / "cjk.py"
        test_file.write_text(source, encoding="utf-8")

        builder = CPGBuilder(str(tmp_path))
        builder.build()

        node = builder.find_node_by_function("cjk.py", "process_data")
        assert node is not None
        assert node.function_name == "process_data"

    def test_cjk_comments_typescript(self, tmp_path):
        source = '// 中文注释 日本語コメント 한국어주석\nfunction processData(x: number): number {\n    return x * 2;\n}\n'
        test_file = tmp_path / "cjk.ts"
        test_file.write_text(source, encoding="utf-8")

        builder = CPGBuilder(str(tmp_path))
        builder.build()

        node = builder.find_node_by_function("cjk.ts", "processData")
        assert node is not None
        assert node.function_name == "processData"
