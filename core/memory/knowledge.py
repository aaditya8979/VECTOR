"""
Knowledge Store — project-local Knowledge Items (KIs).
KIs are written ONLY after a diff passes all 5 verification layers.
Never written from model output alone. This prevents hallucination laundering.

Storage: <project_root>/.codeagent/knowledge/<hash>.json
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Dict, List, Optional


class KnowledgeItem:
    def __init__(
        self,
        rule:        str,
        source_func: str,
        source_file: str,
        confidence:  float   = 1.0,
        tags:        List[str] = None,
        created_at:  float   = None,
        used_count:  int     = 0,
        helped_count: int    = 0,
    ):
        self.rule         = rule
        self.source_func  = source_func
        self.source_file  = source_file
        self.confidence   = confidence
        self.tags         = tags or []
        self.created_at   = created_at or time.time()
        self.used_count   = used_count
        self.helped_count = helped_count
        self.ki_id        = hashlib.sha256(rule.encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        return self.__dict__

    @classmethod
    def from_dict(cls, d: dict) -> "KnowledgeItem":
        ki = cls.__new__(cls)
        ki.__dict__.update(d)
        return ki

    @property
    def hit_rate(self) -> float:
        if self.used_count == 0:
            return 0.0
        return self.helped_count / self.used_count


class KnowledgeStore:
    def __init__(self, knowledge_dir: str):
        self.dir = Path(knowledge_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, KnowledgeItem] = {}
        self._load_all()

    def _load_all(self):
        for f in self.dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                ki   = KnowledgeItem.from_dict(data)
                self._cache[ki.ki_id] = ki
            except Exception:
                pass

    def add_rule(
        self,
        rule:        str,
        source_func: str,
        source_file: str,
        tags:        List[str] = None,
    ) -> KnowledgeItem:
        """
        Add a new codebase rule from a VERIFIED edit.
        Deduplicates by rule text.
        """
        ki_id = hashlib.sha256(rule.encode()).hexdigest()[:12]
        if ki_id in self._cache:
            return self._cache[ki_id]

        ki = KnowledgeItem(
            rule        = rule,
            source_func = source_func,
            source_file = source_file,
            tags        = tags or self._infer_tags(rule),
        )
        self._cache[ki.ki_id] = ki
        self._save(ki)
        return ki

    def get_rules_for(
        self, func_name: str, file_path: str, max_rules: int = 10
    ) -> List[str]:
        """
        Retrieve the most relevant rules for a given function + file context.
        Ranks by confidence * hit_rate, then recency.
        """
        candidates = list(self._cache.values())

        # Filter by tag relevance
        file_stem  = Path(file_path).stem
        func_lower = func_name.lower()

        def score(ki: KnowledgeItem) -> float:
            s = ki.confidence
            if ki.source_func == func_name:
                s += 2.0
            if ki.source_file == file_path:
                s += 1.5
            if any(file_stem in t or func_lower in t for t in ki.tags):
                s += 1.0
            s += ki.hit_rate * 0.5
            return s

        ranked = sorted(candidates, key=score, reverse=True)
        return [ki.rule for ki in ranked[:max_rules]]

    def mark_used(self, rule: str, helped: bool):
        ki_id = hashlib.sha256(rule.encode()).hexdigest()[:12]
        if ki_id in self._cache:
            self._cache[ki_id].used_count   += 1
            if helped:
                self._cache[ki_id].helped_count += 1
            self._save(self._cache[ki_id])

    def extract_rules_from_diff(
        self,
        diff: str,
        func_name: str,
        file_path: str,
        verification_result: dict,
    ) -> List[KnowledgeItem]:
        """
        Called ONLY after all 5 verification layers pass.
        Extracts candidate rules from the verified diff.
        """
        rules = []
        for extractor in [
            self._extract_none_guard_rule,
            self._extract_exception_rule,
            self._extract_pattern_rule,
            self._extract_rust_rule,
            self._extract_go_rule,
            self._extract_cpp_rule,
            self._extract_js_ts_rule,
        ]:
            rule = extractor(diff)
            if rule:
                ki = self.add_rule(rule, func_name, file_path)
                rules.append(ki)
        return rules

    def _extract_none_guard_rule(self, diff: str) -> Optional[str]:
        if "is None" in diff or "is not None" in diff:
            return "Always check for None before dereferencing object attributes or indexing return values."
        return None

    def _extract_exception_rule(self, diff: str) -> Optional[str]:
        m = re.search(r'raise (\w+Error|\w+Exception)', diff)
        if m:
            return f"Use {m.group(1)} for error conditions — not generic Exception or RuntimeError."
        return None

    def _extract_pattern_rule(self, diff: str) -> Optional[str]:
        if "with " in diff and "open(" in diff:
            return "Always use context managers (with statement) for file and resource operations."
        if "async def" in diff or "await " in diff:
            return "Async functions must be awaited — do not call them without await."
        return None

    def _extract_rust_rule(self, diff: str) -> Optional[str]:
        if ".unwrap()" in diff:
            return "Use the ? operator instead of .unwrap() — unwrap panics on None/Err in production."
        if "unsafe {" in diff or "unsafe " in diff:
            return "Minimize unsafe blocks. Document safety invariants with // SAFETY: comments."
        return None

    def _extract_go_rule(self, diff: str) -> Optional[str]:
        # Detect unhandled errors: function call result not checked
        if re.search(r'\w+\(.*\)\s*$', diff, re.MULTILINE) and "err" not in diff:
            if "if err != nil" not in diff and "func " in diff:
                return "Always check error returns in Go — use if err != nil { return err } pattern."
        return None

    def _extract_cpp_rule(self, diff: str) -> Optional[str]:
        if re.search(r'\bnew\s+\w+', diff) and "unique_ptr" not in diff and "shared_ptr" not in diff:
            return "Use smart pointers (std::unique_ptr, std::shared_ptr) instead of raw new/delete."
        if "delete " in diff:
            return "Prefer RAII and smart pointers over manual delete to prevent memory leaks."
        return None

    def _extract_js_ts_rule(self, diff: str) -> Optional[str]:
        if "=== null" in diff or "!== null" in diff or "=== undefined" in diff:
            return "Use optional chaining (?.) and nullish coalescing (??) for null/undefined checks."
        if "any" in diff and re.search(r':\s*any\b', diff):
            return "Avoid 'any' type — use specific types or generics for type safety."
        return None

    def _infer_tags(self, rule: str) -> List[str]:
        tags   = []
        lower  = rule.lower()
        checks = [
            ("None", "null_safety"), ("null", "null_safety"),
            ("undefined", "null_safety"), ("exception", "error_handling"),
            ("error", "error_handling"), ("unwrap", "error_handling"),
            ("async", "concurrency"), ("await", "concurrency"),
            ("db", "database"), ("file", "io"), ("type", "typing"),
            ("test", "testing"), ("unsafe", "safety"),
            ("memory", "memory"), ("smart pointer", "memory"),
        ]
        for keyword, tag in checks:
            if keyword.lower() in lower:
                tags.append(tag)
        return tags

    def _save(self, ki: KnowledgeItem):
        path = self.dir / f"{ki.ki_id}.json"
        path.write_text(json.dumps(ki.to_dict(), indent=2))

    def stats(self) -> dict:
        items = list(self._cache.values())
        return {
            "total_rules":   len(items),
            "avg_hit_rate":  sum(ki.hit_rate for ki in items) / max(len(items), 1),
            "total_uses":    sum(ki.used_count for ki in items),
        }