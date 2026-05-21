"""
IssueLocalizer — finds which CPG nodes to modify given a GitHub issue.

Pipeline:
  1. EXTRACT: Pull symbols from issue text (class names, function names,
     error messages, file names mentioned) using regex + NLP patterns
  2. CPG LOOKUP: Exact-match these symbols against CPG node_ids and signatures
  3. BM25 FALLBACK: If no exact matches, BM25 over all function signatures
     and docstrings using the issue as the query
  4. MODEL RERANK: For top-10 BM25 candidates, use the 7B model to score
     relevance to the issue (0-10 scale). Take top 3.
  5. RETURN: Ordered list of (CPGNode, confidence_score) tuples
"""
from __future__ import annotations

import re
import math
from collections import defaultdict
from typing import List, Tuple, Optional
from pathlib import Path

from core.cpg.builder  import CPGBuilder
from core.cpg.models   import CPGNode


class IssueLocalizer:

    # Patterns that indicate relevant code locations in issue text
    _ERROR_PATTERN   = re.compile(r'(\w+Error|\w+Exception|\w+Warning)', re.I)
    _FUNC_PATTERN    = re.compile(r'`([a-z_]\w+)\(\)`|`(\w+\.\w+)\(\)`', re.M)
    _CLASS_PATTERN   = re.compile(r'`([A-Z]\w+)`|class\s+([A-Z]\w+)')
    _FILE_PATTERN    = re.compile(r'`([\w/]+\.py)`|in\s+([\w/]+\.py)')
    _METHOD_PATTERN  = re.compile(r'\.([a-z_]\w+)\(')

    def __init__(self, builder: CPGBuilder, engine=None):
        self.builder = builder
        self.engine  = engine   # InferenceEngine — used for re-ranking
        self._build_bm25_index()

    # ── Public API ────────────────────────────────────────────────────────────

    def localize(
        self,
        issue:       str,
        top_k:       int = 3,
        max_files:   int = 5,
    ) -> List[Tuple[CPGNode, float]]:
        """
        Main entry point. Returns top_k (node, confidence) pairs.
        confidence: 0.0 (low) → 1.0 (high).
        """
        # Step 1: Extract symbols mentioned in the issue
        symbols = self._extract_symbols(issue)

        # Step 2: Exact CPG lookup for extracted symbols
        exact_matches = self._exact_lookup(symbols)
        if len(exact_matches) >= top_k:
            return [(n, 1.0) for n in exact_matches[:top_k]]

        # Step 3: BM25 over all function signatures + docstrings
        bm25_candidates = self._bm25_search(issue, top_n=15)

        # Merge exact matches + BM25 (exact takes priority)
        seen    = {n.node_id for n in exact_matches}
        merged  = [(n, 1.0) for n in exact_matches]
        for node, score in bm25_candidates:
            if node.node_id not in seen:
                merged.append((node, score))
                seen.add(node.node_id)

        # Step 4: Model re-ranking (if engine available and >top_k candidates)
        if self.engine and len(merged) > top_k:
            merged = self._model_rerank(issue, merged[:15])

        return merged[:top_k]

    def localize_to_files(self, issue: str, top_files: int = 5) -> List[str]:
        """
        Returns the top-k file paths most likely to need modification.
        Used for multi-file issues where full function localization fails.
        """
        candidates = self.localize(issue, top_k=15)
        seen, files = set(), []
        for node, score in candidates:
            if node.file_path not in seen:
                files.append(node.file_path)
                seen.add(node.file_path)
        return files[:top_files]

    # ── Symbol extraction ─────────────────────────────────────────────────────

    def _extract_symbols(self, issue: str) -> dict:
        return {
            "errors":   [m for g in self._ERROR_PATTERN.findall(issue)
                         for m in [g] if m],
            "functions":[m for g in self._FUNC_PATTERN.findall(issue)
                         for m in g if m],
            "classes":  [m for g in self._CLASS_PATTERN.findall(issue)
                         for m in g if m],
            "files":    [m for g in self._FILE_PATTERN.findall(issue)
                         for m in g if m],
            "methods":  self._METHOD_PATTERN.findall(issue),
        }

    # ── Exact CPG lookup ──────────────────────────────────────────────────────

    def _exact_lookup(self, symbols: dict) -> List[CPGNode]:
        matches = []
        all_names = (
            symbols["functions"] +
            symbols["methods"]   +
            [e.replace("Error","").lower() for e in symbols["errors"]]
        )
        for name in all_names:
            for node in self.builder.nodes.values():
                if node.function_name.lower() == name.lower():
                    matches.append(node)
        # File-path exact match (higher confidence)
        for filepath in symbols["files"]:
            for node in self.builder.nodes.values():
                if filepath in node.file_path and node not in matches:
                    matches.append(node)
        return matches

    # ── BM25 index ────────────────────────────────────────────────────────────

    def _build_bm25_index(self):
        """Build BM25 index over all CPG node text (signature + file path)."""
        self._docs = {}
        for nid, node in self.builder.nodes.items():
            text = f"{node.function_name} {node.signature} {node.file_path}"
            if node.summary:
                text += f" {node.summary}"
            self._docs[nid] = text.lower().split()

        # Document frequency
        self._df  = defaultdict(int)
        for tokens in self._docs.values():
            for tok in set(tokens):
                self._df[tok] += 1
        self._N   = len(self._docs)
        self._avgdl = sum(len(t) for t in self._docs.values()) / max(self._N, 1)

    def _bm25_search(self, query: str, top_n: int = 15) -> List[Tuple[CPGNode, float]]:
        """BM25 search. Returns (node, score) sorted by score descending."""
        k1, b  = 1.5, 0.75
        tokens = re.findall(r'\w+', query.lower())
        scores = {}

        for nid, doc_tokens in self._docs.items():
            score = 0.0
            dl    = len(doc_tokens)
            tf    = defaultdict(int)
            for tok in doc_tokens:
                tf[tok] += 1

            for tok in set(tokens):
                if tok not in self._df:
                    continue
                idf  = math.log((self._N - self._df[tok] + 0.5) /
                                (self._df[tok] + 0.5) + 1)
                tftd = tf[tok] * (k1 + 1) / (
                    tf[tok] + k1 * (1 - b + b * dl / self._avgdl))
                score += idf * tftd
            if score > 0:
                scores[nid] = score

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        result = []
        for nid, score in ranked[:top_n]:
            node = self.builder.nodes[nid]
            # Normalize score to 0-1 range
            result.append((node, min(score / 10.0, 1.0)))
        return result

    # ── Model re-ranking ──────────────────────────────────────────────────────

    def _model_rerank(
        self,
        issue:      str,
        candidates: List[Tuple[CPGNode, float]],
    ) -> List[Tuple[CPGNode, float]]:
        """
        For each candidate, ask the 7B model to score its relevance
        to the issue. Returns re-ranked list.
        """
        if not self.engine:
            return candidates

        reranked = []
        for node, bm25_score in candidates:
            prompt = (
                f"GitHub Issue:\n{issue[:600]}\n\n"
                f"Candidate function:\n{node.signature}\n"
                f"File: {node.file_path}\n\n"
                f"On a scale of 0 to 10, how likely is it that modifying "
                f"this function would fix the issue? Reply with ONLY a number."
            )
            try:
                raw = self.engine.generate_raw(prompt, max_tokens=8)
                score  = float(re.search(r'\d+', raw).group()) / 10.0
            except Exception:
                score = bm25_score
            reranked.append((node, score))

        return sorted(reranked, key=lambda x: x[1], reverse=True)
