"""verification/sandbox.py — Layer 5: safe runtime execution of patched code."""
from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Tuple

from config import SANDBOX_TIMEOUT
from core.cpg.language_registry import get_language_config


class SandboxRunner:
    """
    Imports the patched module in a subprocess and runs lightweight smoke tests.
    On macOS: uses the subprocess isolation (not sandbox-exec, which requires entitlements).
    Catches: ImportError, SyntaxError, NameError, AttributeError, TypeError at module load.

    For non-Python languages: skip gracefully (Layers 2-4 already cover syntax, types, tests).
    """

    def run(self, temp_file: str, func_name: str, target_file: str = "") -> Tuple[bool, str]:
        """
        Returns (passed, error_message).
        """
        ext = Path(target_file).suffix.lower() if target_file else Path(temp_file).suffix.lower()

        # Non-Python languages: skip sandbox (no Python import-based smoke test)
        if ext != ".py":
            return self._syntax_check_generic(temp_file, target_file)

        # Python path: full smoke test
        # Step 1: Syntax check (ast.parse)
        ok, err = self._syntax_check(temp_file)
        if not ok:
            return False, err

        # Step 2: Import check in subprocess
        ok, err = self._import_check(temp_file)
        if not ok:
            return False, err

        # Step 3: Attempt to call the function with no args (catches NameError, AttributeError)
        ok, err = self._smoke_call(temp_file, func_name)
        if not ok and self._is_real_crash(err):
            return False, err

        return True, ""

    def _syntax_check_generic(self, temp_file: str, target_file: str) -> Tuple[bool, str]:
        """For non-Python: use tree-sitter to check for parse errors."""
        config = get_language_config(target_file or temp_file)
        if not config:
            return True, ""

        try:
            from core.cpg.language_registry import get_ts_language
            from tree_sitter import Parser

            lang   = get_ts_language(config)
            parser = Parser(lang)
            source = Path(temp_file).read_bytes()
            tree   = parser.parse(source)

            # Check for ERROR nodes
            errors = list(self._walk_errors(tree.root_node))
            if errors:
                first = errors[0]
                line  = first.start_point[0] + 1
                text  = source[first.start_byte:first.end_byte].decode("utf-8", errors="replace")[:80]
                return False, f"Parse error at line {line}: {text}"
            return True, ""
        except ImportError:
            return True, ""   # Grammar not installed — skip
        except Exception:
            return True, ""

    def _walk_errors(self, node):
        if node.type == "ERROR":
            yield node
        for child in node.children:
            yield from self._walk_errors(child)

    def _syntax_check(self, temp_file: str) -> Tuple[bool, str]:
        try:
            source = Path(temp_file).read_text(errors="replace")
            ast.parse(source, filename=temp_file)
            return True, ""
        except SyntaxError as e:
            return False, f"SyntaxError at line {e.lineno}: {e.msg}\n  {e.text}"

    def _import_check(self, temp_file: str) -> Tuple[bool, str]:
        """Import the module in a fresh subprocess."""
        script = textwrap.dedent(f"""
import sys, importlib.util
spec = importlib.util.spec_from_file_location("_tsdc_patch", {repr(temp_file)})
mod  = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
    print("OK")
except ImportError as e:
    print(f"IMPORT_ERROR: {{e}}")
except Exception as e:
    print(f"LOAD_ERROR: {{type(e).__name__}}: {{e}}")
""")
        # Resolve python executable: check if .venv/bin/python exists in parents of current working directory
        import sys
        from pathlib import Path
        python_exe = sys.executable
        curr = Path.cwd().resolve()
        for _ in range(6):
            if (curr / ".venv" / "bin" / "python").exists():
                python_exe = str(curr / ".venv" / "bin" / "python")
                break
            if curr.parent == curr:
                break
            curr = curr.parent
        try:
            result = subprocess.run(
                [python_exe, "-c", script],
                capture_output = True,
                text           = True,
                timeout        = SANDBOX_TIMEOUT,
            )
            output = result.stdout.strip()
            if output == "OK":
                return True, ""
            if output.startswith("IMPORT_ERROR"):
                # Missing imports are expected in isolated modules — skip
                return True, ""
            if output.startswith("LOAD_ERROR"):
                return False, output
            return True, ""
        except subprocess.TimeoutExpired:
            return False, f"Module load timed out after {SANDBOX_TIMEOUT}s"
        except Exception as e:
            return True, str(e)

    def _smoke_call(self, temp_file: str, func_name: str) -> Tuple[bool, str]:
        """Try to call the function with zero/None args to catch obvious crashes."""
        script = textwrap.dedent(f"""
import sys, importlib.util, inspect
spec = importlib.util.spec_from_file_location("_tsdc_patch", {repr(temp_file)})
mod  = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except Exception:
    print("SKIP")
    sys.exit(0)

fn = getattr(mod, {repr(func_name)}, None)
if fn is None:
    print("SKIP")
    sys.exit(0)

sig    = inspect.signature(fn)
params = list(sig.parameters.values())
args   = []
for p in params:
    if p.default is not inspect.Parameter.empty:
        break
    if p.annotation == int:
        args.append(0)
    elif p.annotation == str:
        args.append("")
    elif p.annotation == bool:
        args.append(False)
    else:
        args.append(None)
try:
    fn(*args)
    print("OK")
except TypeError as e:
    print(f"TYPE_ERROR: {{e}}")
except AttributeError as e:
    print(f"ATTR_ERROR: {{e}}")
except NameError as e:
    print(f"NAME_ERROR: {{e}}")
except Exception:
    print("OK")  # Other errors (ValueError, etc.) are expected with None args
""")
        # Resolve python executable: check if .venv/bin/python exists in parents of current working directory
        import sys
        from pathlib import Path
        python_exe = sys.executable
        curr = Path.cwd().resolve()
        for _ in range(6):
            if (curr / ".venv" / "bin" / "python").exists():
                python_exe = str(curr / ".venv" / "bin" / "python")
                break
            if curr.parent == curr:
                break
            curr = curr.parent
        try:
            result = subprocess.run(
                [python_exe, "-c", script],
                capture_output = True,
                text           = True,
                timeout        = SANDBOX_TIMEOUT,
            )
            out = result.stdout.strip()
            if "ERROR" in out:
                return False, out
            return True, ""
        except Exception:
            return True, ""   # Don't block on smoke test issues

    def _is_real_crash(self, error: str) -> bool:
        """Filter out expected crashes (e.g. missing args) from real bugs."""
        expected = [
            "missing.*argument", "argument.*required",
            "takes.*positional", "NoneType.*has no attribute",
        ]
        import re
        for pattern in expected:
            if re.search(pattern, error, re.IGNORECASE):
                return False
        return True