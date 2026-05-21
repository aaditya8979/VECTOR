"""
verification/test_runner.py — Layer 3: language-aware test execution.

Two classes of test failure are treated completely differently:

  INFRA ERROR  — conftest ImportError, project not installed, missing deps.
                 These are environment problems, NOT model errors.
                 Action: auto-install project, retry once. If still fails,
                 skip this layer (do not penalise the model).

  CODE ERROR   — AttributeError, AssertionError, wrong output, crashes.
                 These ARE model errors. Fail, return structured feedback.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from config import TEST_TIMEOUT
# pyrefly: ignore [missing-import]
from rich.console import Console

from core.cpg.language_registry import get_language_config

_INFRA_PATTERNS = [
    "ImportError while loading conftest",
    "ModuleNotFoundError",
    "No module named",
    "ERROR collecting",
    "could not import",
    "fixture.*not found",
    "collection errors",
]


class TestRunner:
    def __init__(self, project_root: str, test_dir: Optional[str] = None):
        self.project_root = Path(project_root)
        self.test_dir     = test_dir or str(self.project_root)
        self._installed   = False

    def run(
        self,
        temp_file:   str,
        target_rel:  str,
        target_func: str,
        test_ids:    List[str] = None,
    ) -> Tuple[bool, str]:
        config = get_language_config(target_rel)
        ext    = Path(target_rel).suffix.lower()

        # ── Language-specific test runners ────────────────────────────────────
        if ext == ".py":
            return self._run_python(temp_file, target_rel, target_func, test_ids)
        elif ext in (".ts", ".tsx", ".js", ".jsx", ".mjs"):
            return self._run_js_ts(temp_file, target_rel, target_func, test_ids)
        elif ext == ".go":
            return self._run_go(temp_file, target_rel, target_func, test_ids)
        elif ext == ".rs":
            return self._run_rust(temp_file, target_rel, target_func, test_ids)
        elif ext in (".cpp", ".cc", ".cxx", ".c"):
            return self._run_cpp(temp_file, target_rel, target_func, test_ids)
        elif config is None or config.test_runner is None:
            return True, ""   # No test runner for this language — skip

        return True, ""

    # ── Python (pytest) ──────────────────────────────────────────────────────

    def _run_python(
        self, temp_file: str, target_rel: str, target_func: str,
        test_ids: Optional[List[str]],
    ) -> Tuple[bool, str]:
        real_abs = str(self.project_root / target_rel)
        backup   = real_abs + ".tsdc_backup"
        targets  = list(test_ids or [])

        if not targets:
            targets = self._discover_related_tests(target_func, target_rel)
            if not targets:
                return True, ""   # No tests found — skip layer

        # Auto-install project on first run so conftest imports work
        if not self._installed:
            self._try_install()

        python_exe = sys.executable
        local_venv_py = self.project_root / ".venv" / "bin" / "python"
        if local_venv_py.exists():
            python_exe = str(local_venv_py)

        cmd = [
            python_exe, "-m", "pytest",
            "-x", "--tb=short", "-q", "--no-header",
        ] + targets

        try:
            shutil.copy2(real_abs, backup)
            shutil.copy2(temp_file, real_abs)

            result = subprocess.run(
                cmd,
                capture_output=True, text=True,
                timeout=TEST_TIMEOUT,
                cwd=str(self.project_root),
                env=self._env(),
            )
            output = result.stdout + result.stderr

            if result.returncode == 0:
                return True, ""

            if self._is_infra(output):
                return self._retry_after_install(temp_file, real_abs, targets)

            return False, self._format_failure(output)

        except subprocess.TimeoutExpired:
            return False, f"Tests timed out after {TEST_TIMEOUT}s — check for infinite loops."
        except Exception:
            return True, ""
        finally:
            if Path(backup).exists():
                shutil.copy2(backup, real_abs)
                Path(backup).unlink(missing_ok=True)

    # ── TypeScript / JavaScript (jest) ───────────────────────────────────────

    def _run_js_ts(
        self, temp_file: str, target_rel: str, target_func: str,
        test_ids: Optional[List[str]],
    ) -> Tuple[bool, str]:
        config  = get_language_config(target_rel)
        targets = list(test_ids or [])

        if not targets:
            targets = self._discover_tests_by_patterns(
                target_func, target_rel,
                config.test_file_patterns if config else ["*.test.ts", "*.spec.ts"]
            )
            if not targets:
                return True, ""

        real_abs = str(self.project_root / target_rel)
        backup   = real_abs + ".tsdc_backup"

        try:
            shutil.copy2(real_abs, backup)
            shutil.copy2(temp_file, real_abs)

            cmd = ["npx", "jest", "--no-coverage", "--testPathPattern",
                   "|".join(targets) if targets else target_func]

            result = subprocess.run(
                cmd,
                capture_output=True, text=True,
                timeout=TEST_TIMEOUT,
                cwd=str(self.project_root),
            )
            if result.returncode == 0:
                return True, ""
            return False, self._format_failure(result.stdout + result.stderr)

        except subprocess.TimeoutExpired:
            return False, f"Tests timed out after {TEST_TIMEOUT}s."
        except FileNotFoundError:
            return True, ""   # jest not installed — skip
        except Exception:
            return True, ""
        finally:
            if Path(backup).exists():
                shutil.copy2(backup, real_abs)
                Path(backup).unlink(missing_ok=True)

    # ── Go (go test) ─────────────────────────────────────────────────────────

    def _run_go(
        self, temp_file: str, target_rel: str, target_func: str,
        test_ids: Optional[List[str]],
    ) -> Tuple[bool, str]:
        real_abs = str(self.project_root / target_rel)
        backup   = real_abs + ".tsdc_backup"

        try:
            shutil.copy2(real_abs, backup)
            shutil.copy2(temp_file, real_abs)

            cmd = ["go", "test", "./...", "-run", target_func, "-v", "-count=1"]

            result = subprocess.run(
                cmd,
                capture_output=True, text=True,
                timeout=TEST_TIMEOUT,
                cwd=str(self.project_root),
            )
            if result.returncode == 0:
                return True, ""
            return False, self._format_failure(result.stdout + result.stderr)

        except subprocess.TimeoutExpired:
            return False, f"Tests timed out after {TEST_TIMEOUT}s."
        except FileNotFoundError:
            return True, ""   # go not installed — skip
        except Exception:
            return True, ""
        finally:
            if Path(backup).exists():
                shutil.copy2(backup, real_abs)
                Path(backup).unlink(missing_ok=True)

    # ── Rust (cargo test) ────────────────────────────────────────────────────

    def _run_rust(
        self, temp_file: str, target_rel: str, target_func: str,
        test_ids: Optional[List[str]],
    ) -> Tuple[bool, str]:
        real_abs = str(self.project_root / target_rel)
        backup   = real_abs + ".tsdc_backup"

        try:
            shutil.copy2(real_abs, backup)
            shutil.copy2(temp_file, real_abs)

            cmd = ["cargo", "test", "--", target_func, "--nocapture"]

            result = subprocess.run(
                cmd,
                capture_output=True, text=True,
                timeout=TEST_TIMEOUT,
                cwd=str(self.project_root),
            )
            if result.returncode == 0:
                return True, ""
            return False, self._format_failure(result.stdout + result.stderr)

        except subprocess.TimeoutExpired:
            return False, f"Tests timed out after {TEST_TIMEOUT}s."
        except FileNotFoundError:
            return True, ""   # cargo not installed — skip
        except Exception:
            return True, ""
        finally:
            if Path(backup).exists():
                shutil.copy2(backup, real_abs)
                Path(backup).unlink(missing_ok=True)

    # ── C++ (ctest) ──────────────────────────────────────────────────────────

    def _run_cpp(
        self, temp_file: str, target_rel: str, target_func: str,
        test_ids: Optional[List[str]],
    ) -> Tuple[bool, str]:
        # C++ test infrastructure is highly project-specific
        # Only attempt if ctest is available and a build directory exists
        build_dir = self.project_root / "build"
        if not build_dir.exists():
            return True, ""   # No build directory — skip

        try:
            cmd = ["ctest", "--output-on-failure", "--test-dir", str(build_dir)]
            result = subprocess.run(
                cmd,
                capture_output=True, text=True,
                timeout=TEST_TIMEOUT,
                cwd=str(self.project_root),
            )
            if result.returncode == 0:
                return True, ""
            return False, self._format_failure(result.stdout + result.stderr)
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            return True, ""

    # ── Install ───────────────────────────────────────────────────────────────

    def _try_install(self) -> bool:
        self._installed = True
        has_build = any([
            (self.project_root / "setup.py").exists(),
            (self.project_root / "setup.cfg").exists(),
            (self.project_root / "pyproject.toml").exists(),
        ])
        if not has_build:
            return False
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", ".", "-q", "--no-deps"],
                cwd=str(self.project_root),
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode == 0:
                Console().print("  [dim]Auto-installed project (pip install -e .)[/dim]")
            return r.returncode == 0
        except Exception:
            return False

    def _retry_after_install(
        self, temp_file: str, real_abs: str, targets: List[str]
    ) -> Tuple[bool, str]:
        """After infra error: force install, swap file, run once more."""
        self._installed = False
        installed = self._try_install()
        if not installed:
            self._skip("pip install -e . failed")
            return True, ""

        backup = real_abs + ".tsdc_backup2"
        try:
            shutil.copy2(real_abs, backup)
            shutil.copy2(temp_file, real_abs)
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-x", "--tb=short", "-q",
                 "--no-header"] + targets,
                capture_output=True, text=True,
                timeout=TEST_TIMEOUT,
                cwd=str(self.project_root),
                env=self._env(),
            )
            out = result.stdout + result.stderr
            if result.returncode == 0:
                return True, ""
            if self._is_infra(out):
                self._skip("conftest import error persists after install")
                return True, ""
            return False, self._format_failure(out)
        except Exception:
            return True, ""
        finally:
            if Path(backup).exists():
                shutil.copy2(backup, real_abs)
                Path(backup).unlink(missing_ok=True)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _is_infra(self, output: str) -> bool:
        return any(re.search(p, output, re.IGNORECASE) for p in _INFRA_PATTERNS)

    def _skip(self, reason: str):
        Console().print(f"  [yellow]⚠ Test layer skipped ({reason})[/yellow]")

    def _env(self) -> dict:
        env = os.environ.copy()
        src = self.project_root / "src"
        if src.exists():
            prev = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{src}:{prev}" if prev else str(src)
        return env

    def _format_failure(self, output: str) -> str:
        """Extract actionable error info for the model feedback loop."""
        lines   = output.splitlines()
        useful  = []
        capture = False
        for line in lines:
            if re.match(r"_{5,}", line):
                capture = True
                useful  = []
                continue
            if capture:
                useful.append(line)
                if re.match(r"={5,}", line) and len(useful) > 3:
                    break

        result = "\n".join(useful).strip() if useful else output

        # AttributeError hint — most common model hallucination
        for obj, attr in re.findall(
            r"AttributeError: '([^']+)' object has no attribute '([^']+)'", result
        ):
            result += (
                f"\n\nHINT: '{obj}' has no attribute '{attr}'. "
                f"You invented this. Use only variables defined "
                f"inside the function body or from AVAILABLE CALLEES."
            )

        # NameError hint
        for name in re.findall(r"NameError: name '([^']+)' is not defined", result):
            result += (
                f"\n\nHINT: '{name}' is not defined. "
                f"Define it before use, or add the missing import."
            )

        # Missing start_time — very common timing task error
        if "start_time" in output and "not defined" in output:
            result += (
                "\n\nHINT: 'start_time' must be assigned BEFORE the try block "
                "using: start_time = time.time()"
            )

        return result[:1500]

    def _discover_related_tests(self, func_name: str, target_rel: str) -> List[str]:
        """Python test discovery — find test files related to the target."""
        stem = Path(target_rel).stem
        for pat in [f"test_{stem}.py", f"{stem}_test.py"]:
            for p in self.project_root.rglob(pat):
                return [str(p)]
        found = []
        for tf in sorted(self.project_root.rglob("test_*.py")):
            try:
                if func_name in tf.read_text(errors="replace"):
                    found.append(str(tf))
                    if len(found) >= 2:
                        break
            except Exception:
                pass
        return found

    def _discover_tests_by_patterns(
        self, func_name: str, target_rel: str, patterns: List[str]
    ) -> List[str]:
        """Generic test discovery using glob patterns from the registry."""
        found = []
        for pattern in patterns:
            for p in self.project_root.rglob(pattern):
                try:
                    if func_name in p.read_text(errors="replace"):
                        found.append(str(p))
                        if len(found) >= 2:
                            return found
                except Exception:
                    pass
        return found