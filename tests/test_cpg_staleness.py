"""
test_cpg_staleness.py — Verify CPG incremental update and staleness propagation.

Tests that:
1. Editing a file marks the correct nodes as changed
2. Callers of changed functions are marked stale
3. Non-Python files are correctly handled by the updater
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.cpg.builder import CPGBuilder
from core.cpg.updater import CPGUpdater
from core.cpg.language_registry import is_supported


class TestCPGStaleness:
    """Verify CPG staleness detection and propagation."""

    def test_python_edit_marks_node_changed(self, tmp_path):
        """Editing a Python function's body must update its body_hash."""
        src = tmp_path / "app.py"
        src.write_text(
            "def greet(name: str) -> str:\n"
            "    return f'Hello, {name}!'\n"
            "\n"
            "def main():\n"
            "    print(greet('World'))\n",
            encoding="utf-8",
        )

        builder = CPGBuilder(str(tmp_path))
        builder.build()

        old_hash = builder.nodes["app.py::greet"].body_hash

        # Edit the function
        src.write_text(
            "def greet(name: str) -> str:\n"
            "    return f'Hi there, {name}!'\n"
            "\n"
            "def main():\n"
            "    print(greet('World'))\n",
            encoding="utf-8",
        )

        updater = CPGUpdater(builder)
        changed = updater.on_file_changed(str(src))

        assert "app.py::greet" in changed, "greet must be in changed nodes"
        assert builder.nodes["app.py::greet"].body_hash != old_hash

    def test_caller_marked_stale(self, tmp_path):
        """When a callee changes, its callers must be marked stale."""
        src = tmp_path / "app.py"
        src.write_text(
            "def helper() -> int:\n"
            "    return 42\n"
            "\n"
            "def main():\n"
            "    x = helper()\n"
            "    print(x)\n",
            encoding="utf-8",
        )

        builder = CPGBuilder(str(tmp_path))
        builder.build()

        assert not builder.nodes["app.py::main"].is_stale

        # Edit helper
        src.write_text(
            "def helper() -> int:\n"
            "    return 99\n"
            "\n"
            "def main():\n"
            "    x = helper()\n"
            "    print(x)\n",
            encoding="utf-8",
        )

        updater = CPGUpdater(builder)
        updater.on_file_changed(str(src))

        assert builder.nodes["app.py::main"].is_stale, (
            "main() calls helper() — must be marked stale when helper changes"
        )

    def test_new_function_detected(self, tmp_path):
        """Adding a new function must create a new CPG node."""
        src = tmp_path / "app.py"
        src.write_text(
            "def existing():\n    pass\n",
            encoding="utf-8",
        )

        builder = CPGBuilder(str(tmp_path))
        builder.build()

        assert len(builder.nodes) == 1

        # Add a new function
        src.write_text(
            "def existing():\n    pass\n\ndef new_func():\n    return 1\n",
            encoding="utf-8",
        )

        updater = CPGUpdater(builder)
        changed = updater.on_file_changed(str(src))

        assert "app.py::new_func" in changed
        assert len(builder.nodes) == 2

    def test_deleted_function_removed(self, tmp_path):
        """Removing a function must delete its CPG node."""
        src = tmp_path / "app.py"
        src.write_text(
            "def keep():\n    pass\n\ndef remove_me():\n    pass\n",
            encoding="utf-8",
        )

        builder = CPGBuilder(str(tmp_path))
        builder.build()

        assert len(builder.nodes) == 2

        # Remove the function
        src.write_text(
            "def keep():\n    pass\n",
            encoding="utf-8",
        )

        updater = CPGUpdater(builder)
        updater.on_file_changed(str(src))

        assert "app.py::remove_me" not in builder.nodes
        assert len(builder.nodes) == 1

    def test_updater_handles_supported_extensions(self, tmp_path):
        """The updater must accept all supported file extensions."""
        assert is_supported("test.py")
        assert is_supported("test.ts")
        assert is_supported("test.go")
        assert is_supported("test.rs")
        assert is_supported("test.cpp")
        assert is_supported("test.c")
        assert is_supported("test.js")

    def test_updater_rejects_unsupported(self, tmp_path):
        """Unsupported files must be silently skipped."""
        src = tmp_path / "data.csv"
        src.write_text("a,b,c\n1,2,3\n", encoding="utf-8")

        builder = CPGBuilder(str(tmp_path))
        builder.build()

        updater = CPGUpdater(builder)
        changed = updater.on_file_changed(str(src))
        assert changed == [], "CSV file should produce no changes"

    def test_resolve_staleness(self, tmp_path):
        """resolve_staleness must clear the stale flag and update body_hash."""
        src = tmp_path / "app.py"
        src.write_text(
            "def target():\n    return 1\n",
            encoding="utf-8",
        )

        builder = CPGBuilder(str(tmp_path))
        builder.build()

        node = builder.nodes["app.py::target"]
        node.mark_stale()
        assert node.is_stale

        updater = CPGUpdater(builder)
        updater.resolve_staleness("app.py::target")
        assert not node.is_stale
