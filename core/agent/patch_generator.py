"""
PatchGenerator — converts VECTOR's in-place file modification into
a git-format unified diff suitable for SWE-bench evaluation.

SWE-bench evaluation applies predictions using `git apply`,
so the patch must be in standard unified diff format.
"""
from __future__ import annotations

import difflib
import subprocess
from pathlib import Path
from typing import Dict


class PatchGenerator:

    def from_file_contents(
        self,
        original: str,
        modified: str,
        file_path: str,
    ) -> str:
        """
        Generate a git-format unified diff from before/after file contents.

        Output format:
          diff --git a/path/to/file.py b/path/to/file.py
          --- a/path/to/file.py
          +++ b/path/to/file.py
          @@ -N,M +N,M @@
          ...
        """
        diff_lines = list(difflib.unified_diff(
            original.splitlines(True),
            modified.splitlines(True),
            fromfile = f"a/{file_path}",
            tofile   = f"b/{file_path}",
            lineterm = "",
        ))
        if not diff_lines:
            return ""   # No changes

        header = f"diff --git a/{file_path} b/{file_path}\n"
        return header + "\n".join(diff_lines) + "\n"

    def from_repo_changes(
        self,
        repo_root:      str,
        original_files: Dict[str, str],  # {rel_path: original_content}
    ) -> str:
        """
        Generate a combined patch for all modified files in the repo.
        Call this after VECTOR has made all its modifications.
        original_files: snapshot of file contents before VECTOR ran.
        """
        patches = []
        for rel_path, original in original_files.items():
            abs_path = Path(repo_root) / rel_path
            if not abs_path.exists():
                continue
            modified = abs_path.read_text(encoding="utf-8", errors="replace")
            if modified != original:
                patch = self.from_file_contents(original, modified, rel_path)
                if patch:
                    patches.append(patch)
        return "\n".join(patches)

    def validate_patch(self, patch: str, repo_root: str) -> tuple[bool, str]:
        """
        Dry-run `git apply` to verify the patch applies cleanly.
        Returns (valid, error_message).
        """
        if not patch.strip():
            return False, "Empty patch"
        try:
            result = subprocess.run(
                ["git", "apply", "--check", "-"],
                input   = patch.encode(),
                capture_output = True,
                cwd     = repo_root,
                timeout = 30,
            )
            if result.returncode == 0:
                return True, ""
            return False, result.stderr.decode()
        except Exception as e:
            return False, str(e)
