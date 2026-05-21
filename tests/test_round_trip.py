"""
test_round_trip.py — init → build CPG → generate TSDC → verify pipeline.

End-to-end tests that validate the full TSDC pipeline works for
Python, TypeScript, Go, and Rust without requiring the LLM model.
Tests everything except inference: CPG construction, TSDC generation,
function replacement, syntax validation.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.cpg.builder import CPGBuilder
from core.cpg.models import CPGNode
from core.cpg.language_registry import get_language_config


# ── Multi-language source fixtures ────────────────────────────────────────────

PYTHON_SOURCE = '''
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

def multiply(a: int, b: int) -> int:
    """Multiply using add."""
    result = 0
    for _ in range(b):
        result = add(result, a)
    return result
'''.strip()

TYPESCRIPT_SOURCE = '''
function add(a: number, b: number): number {
    return a + b;
}

function multiply(a: number, b: number): number {
    let result = 0;
    for (let i = 0; i < b; i++) {
        result = add(result, a);
    }
    return result;
}
'''.strip()

GO_SOURCE = '''package main

func Add(a int, b int) int {
    return a + b
}

func Multiply(a int, b int) int {
    result := 0
    for i := 0; i < b; i++ {
        result = Add(result, a)
    }
    return result
}
'''.strip()

RUST_SOURCE = '''
fn add(a: i32, b: i32) -> i32 {
    a + b
}

fn multiply(a: i32, b: i32) -> i32 {
    let mut result = 0;
    for _ in 0..b {
        result = add(result, a);
    }
    result
}
'''.strip()


def _has_grammar(ext: str) -> bool:
    config = get_language_config(f"test{ext}")
    if not config:
        return False
    if ext == ".py":
        return True
    try:
        from core.cpg.language_registry import get_ts_language
        get_ts_language(config)
        return True
    except ImportError:
        return False


class TestCPGConstruction:
    """Verify CPG builds correctly for each language."""

    @pytest.mark.parametrize("ext,source,expected_funcs", [
        (".py", PYTHON_SOURCE, ["add", "multiply"]),
        (".ts", TYPESCRIPT_SOURCE, ["add", "multiply"]),
        (".go", GO_SOURCE, ["Add", "Multiply"]),
        (".rs", RUST_SOURCE, ["add", "multiply"]),
    ])
    def test_cpg_nodes_created(self, ext, source, expected_funcs, tmp_path):
        if not _has_grammar(ext):
            pytest.skip(f"tree-sitter grammar not installed for {ext}")

        src_file = tmp_path / f"math{ext}"
        src_file.write_text(source, encoding="utf-8")

        builder = CPGBuilder(str(tmp_path))
        builder.build()

        actual_names = sorted([n.function_name for n in builder.nodes.values()])
        assert actual_names == sorted(expected_funcs), (
            f"Expected {sorted(expected_funcs)}, got {actual_names}"
        )

    @pytest.mark.parametrize("ext,source", [
        (".py", PYTHON_SOURCE),
        (".ts", TYPESCRIPT_SOURCE),
        (".go", GO_SOURCE),
        (".rs", RUST_SOURCE),
    ])
    def test_call_edges_detected(self, ext, source, tmp_path):
        """multiply() calls add() — edge must exist."""
        if not _has_grammar(ext):
            pytest.skip(f"tree-sitter grammar not installed for {ext}")

        src_file = tmp_path / f"math{ext}"
        src_file.write_text(source, encoding="utf-8")

        builder = CPGBuilder(str(tmp_path))
        builder.build()

        # multiply must have add as a callee
        multiply_id = None
        add_id = None
        for nid, node in builder.nodes.items():
            if node.function_name.lower() == "multiply":
                multiply_id = nid
            elif node.function_name.lower() == "add":
                add_id = nid

        if multiply_id and add_id:
            callees = builder.get_direct_callees(multiply_id)
            callee_ids = [c.node_id for c in callees]
            assert add_id in callee_ids, (
                f"multiply should call add. Callees: {callee_ids}"
            )


class TestCPGSerialization:
    """Verify CPG save/load round-trip."""

    def test_save_load_roundtrip(self, tmp_path):
        src = tmp_path / "app.py"
        src.write_text(PYTHON_SOURCE, encoding="utf-8")

        builder = CPGBuilder(str(tmp_path))
        builder.build()

        cpg_path = str(tmp_path / "cpg.json")
        builder.save(cpg_path)

        loaded = CPGBuilder.load(cpg_path, str(tmp_path))

        assert len(loaded.nodes) == len(builder.nodes)
        for nid in builder.nodes:
            assert nid in loaded.nodes
            assert loaded.nodes[nid].function_name == builder.nodes[nid].function_name
            assert loaded.nodes[nid].signature == builder.nodes[nid].signature

    def test_schema_evolution_safety(self, tmp_path):
        """from_dict must handle unknown fields gracefully."""
        data = {
            "node_id": "test.py::func",
            "file_path": "test.py",
            "function_name": "func",
            "class_name": None,
            "signature": "def func()",
            "return_type": "None",
            "decorators": [],
            "raises": [],
            "body_hash": "abc123",
            "start_line": 1,
            "end_line": 3,
            "unknown_future_field": "should be ignored",
            "another_new_field": 42,
        }
        node = CPGNode.from_dict(data)
        assert node.function_name == "func"
        assert not hasattr(node, "unknown_future_field")


class TestTSDCGeneration:
    """Verify TSDC document generation for different languages."""

    def _make_generator(self, tmp_path, source, ext):
        from core.memory.state_db import StateDB
        from core.memory.knowledge import KnowledgeStore
        from core.tsdc.generator import TSDCGenerator

        src_file = tmp_path / f"math{ext}"
        src_file.write_text(source, encoding="utf-8")

        builder = CPGBuilder(str(tmp_path))
        builder.build()

        db_path = str(tmp_path / "state.db")
        ki_path = str(tmp_path / "knowledge")

        db = StateDB(db_path)
        knowledge = KnowledgeStore(ki_path)
        generator = TSDCGenerator(builder, db, knowledge)
        return generator, builder

    def test_python_tsdc_generation(self, tmp_path):
        gen, _ = self._make_generator(tmp_path, PYTHON_SOURCE, ".py")
        doc = gen.generate("math.py", "multiply", "optimize using bit shifts")

        assert "━━━ TASK ━━━" in doc
        assert "multiply" in doc
        assert "Python" in doc
        assert "━━━ OUTPUT INSTRUCTIONS ━━━" in doc

    def test_typescript_tsdc_generation(self, tmp_path):
        if not _has_grammar(".ts"):
            pytest.skip("tree-sitter-typescript not installed")

        gen, _ = self._make_generator(tmp_path, TYPESCRIPT_SOURCE, ".ts")
        doc = gen.generate("math.ts", "multiply", "optimize using bit shifts")

        assert "━━━ TASK ━━━" in doc
        assert "multiply" in doc
        assert "TypeScript" in doc
        assert "function " in doc  # output instructions must use TS keyword

    def test_tsdc_budget_respected(self, tmp_path):
        from core.tsdc.budget import count_tokens

        gen, _ = self._make_generator(tmp_path, PYTHON_SOURCE, ".py")
        doc = gen.generate("math.py", "multiply", "add error handling")

        token_count = count_tokens(doc)
        assert token_count <= 2500, (
            f"TSDC document is {token_count} tokens, exceeds 2500 budget"
        )


class TestFunctionReplacement:
    """Verify function replacement works across languages."""

    def test_python_replacement(self, tmp_path):
        from verification.pipeline import VerificationPipeline

        src_file = tmp_path / "app.py"
        src_file.write_text(PYTHON_SOURCE, encoding="utf-8")

        pipeline = VerificationPipeline(str(tmp_path))

        new_func = "def add(a: int, b: int) -> int:\n    return a + b + 0  # modified\n"
        ok, result, err = pipeline._apply_function_replacement(new_func, "app.py", "add")

        assert ok, f"Replacement failed: {err}"
        _, patched = result  # (tmp_path, patched_source)
        assert "a + b + 0" in patched
        assert "multiply" in patched  # other function preserved

    def test_typescript_replacement(self, tmp_path):
        if not _has_grammar(".ts"):
            pytest.skip("tree-sitter-typescript not installed")

        from verification.pipeline import VerificationPipeline

        src_file = tmp_path / "app.ts"
        src_file.write_text(TYPESCRIPT_SOURCE, encoding="utf-8")

        pipeline = VerificationPipeline(str(tmp_path))

        new_func = "function add(a: number, b: number): number {\n    return a + b + 0; // modified\n}\n"
        ok, result, err = pipeline._apply_function_replacement(new_func, "app.ts", "add")

        assert ok, f"Replacement failed: {err}"
        _, patched = result
        assert "a + b + 0" in patched
        assert "multiply" in patched
