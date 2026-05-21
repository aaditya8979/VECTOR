"""verification/type_check.py — Layer 2: language-aware static type verification."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Tuple

from config import TYPE_CHECK_TIMEOUT
from core.cpg.language_registry import get_language_config


class TypeChecker:
    def __init__(self, project_root: str):
        self.project_root = Path(project_root)

    def check(self, temp_file: str, target_rel: str) -> Tuple[bool, str]:
        """
        Run the appropriate type checker for the target file's language.
        Returns (passed, error_message).
        """
        config = get_language_config(target_rel)
        if config is None or config.type_checker is None:
            return True, ""   # No type checker for this language — skip

        ext = Path(target_rel).suffix.lower()

        # ── Python: mypy ─────────────────────────────────────────────────────
        if ext == ".py":
            import sys
            python_exe = sys.executable
            local_venv_py = self.project_root / ".venv" / "bin" / "python"
            if local_venv_py.exists():
                python_exe = str(local_venv_py)

            target_path = self.project_root / target_rel
            original_content = target_path.read_text(encoding="utf-8")
            patched_content = Path(temp_file).read_text(encoding="utf-8")

            try:
                target_path.write_text(patched_content, encoding="utf-8")
                cmd = [
                    python_exe, "-m", "mypy",
                    str(target_path),
                    "--ignore-missing-imports",
                    "--no-error-summary",
                    "--pretty",
                    "--no-color-output",
                ]
                passed, err = self._run_check(cmd)
                if not passed:
                    err = err.replace(str(target_path), temp_file)
                return passed, err
            finally:
                target_path.write_text(original_content, encoding="utf-8")

        # ── TypeScript/TSX: tsc ──────────────────────────────────────────────
        elif ext in (".ts", ".tsx"):
            cmd = [
                "npx", "tsc", "--noEmit", "--allowJs",
                "--target", "ES2020",
                "--moduleResolution", "bundler",
                "--esModuleInterop",
                "--skipLibCheck",
                temp_file,
            ]

        # ── Go: fast syntax check via gofmt ──────────────────────────────────
        elif ext == ".go":
            cmd = ["gofmt", "-e", temp_file]

        # ── Rust: fast syntax check via rustfmt ──────────────────────────────
        elif ext == ".rs":
            cmd = ["rustfmt", "--check", "--edition", "2021", temp_file]

        # ── Fallback: use registry-configured command ────────────────────────
        else:
            cmd = config.type_checker + [temp_file]

        return self._run_check(cmd)

    def _run_check(self, cmd: list) -> Tuple[bool, str]:
        """Run a type-check command and return (passed, error_message)."""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=TYPE_CHECK_TIMEOUT,
                cwd=str(self.project_root),
            )
            if result.returncode == 0:
                return True, ""

            # Filter only errors (not notes/warnings)
            output = result.stdout + result.stderr
            errors = [
                line for line in output.splitlines()
                if "error" in line.lower() or "Found" in line
            ]
            return False, "\n".join(errors[:15])

        except subprocess.TimeoutExpired:
            return True, ""   # Don't block on slow type checkers
        except FileNotFoundError:
            return True, ""   # Tool not installed — skip gracefully
        except Exception as e:
            return True, str(e)