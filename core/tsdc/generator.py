"""
TSDC Generator — Task-Scoped Deterministic Context.
Produces the 2K–3K token context document for one code modification task.
Entirely symbolic. Zero LLM calls. Runs in <200ms on M4.

Supports Python, TypeScript, JavaScript, Go, Rust, C++, and C via
the language registry. Language-specific scope rules and output instructions
are generated based on the target file's extension.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from config import (
    TSDC_BUDGET, MAX_CALLEES, MAX_CALLERS, MAX_RULES, DIGEST_DAYS
)
from core.cpg.builder import CPGBuilder
from core.cpg.models import CPGNode
from core.cpg.language_registry import get_language_config
from core.memory.state_db import StateDB
from core.memory.knowledge import KnowledgeStore
from core.tsdc.extractors import (
    ContractExtractor, CallerPatternExtractor, BodySkeletonExtractor
)
from core.tsdc.budget import BudgetAllocator, count_tokens


# ── Language-specific scope rules ────────────────────────────────────────────

_SCOPE_RULES = {
    ".py": [
        "  • Only use variables you explicitly define inside the function body.",
        "  • Do NOT invent attributes on parameters (e.g. ctx.request.start_time).",
        "  • If you need a new variable, define it yourself (e.g. start_time = time.time()).",
        "  • 'import time' is available at module level — use time.time() directly.",
        "  • Use self.logger.debug(...) for logging, not print() or logging.info().",
    ],
    ".ts": [
        "  • Only use variables you explicitly declare inside the function body.",
        "  • Do NOT invent properties on parameters that don't exist in the type.",
        "  • Use console.log() for debugging, not print().",
        "  • Use Date.now() for timestamps, performance.now() for high-resolution timing.",
        "  • Respect TypeScript strict mode: no implicit any, handle null/undefined.",
    ],
    ".tsx": [
        "  • Only use variables you explicitly declare inside the function body.",
        "  • Do NOT invent React props that don't exist in the component's type.",
        "  • Use useState/useEffect hooks correctly — no hooks inside conditionals.",
        "  • Respect TypeScript strict mode: no implicit any.",
    ],
    ".js": [
        "  • Only use variables you explicitly declare inside the function body.",
        "  • Do NOT invent properties on objects that don't exist.",
        "  • Use console.log() for debugging.",
        "  • Use Date.now() for timestamps.",
    ],
    ".go": [
        "  • Only use variables you explicitly declare inside the function body.",
        "  • Do NOT invent methods on types that don't exist in the struct definition.",
        "  • Always handle errors — use if err != nil { return err } patterns.",
        "  • Use fmt.Println() for debugging, log.Printf() for production logging.",
        "  • Use time.Now() for timestamps, time.Since() for duration.",
    ],
    ".rs": [
        "  • Only use variables you explicitly bind inside the function body.",
        "  • Do NOT invent methods on types — check the impl block in AVAILABLE CALLEES.",
        "  • Handle errors with ? operator or match — do NOT use unwrap() in production.",
        "  • Use std::time::Instant::now() for timing.",
        "  • Respect ownership: do NOT move values you still need.",
    ],
    ".cpp": [
        "  • Only use variables you explicitly declare inside the function body.",
        "  • Do NOT invent member functions on classes that don't exist.",
        "  • Use auto for type inference where appropriate.",
        "  • Use std::chrono for timing, not time().",
        "  • Avoid raw new/delete — use smart pointers (std::unique_ptr, std::shared_ptr).",
    ],
    ".c": [
        "  • Only use variables you explicitly declare inside the function body.",
        "  • Do NOT invent struct members that don't exist in the type definition.",
        "  • Always check return values of malloc/calloc for NULL.",
        "  • Free allocated memory before returning from all code paths.",
    ],
}

# ── Language-specific output instructions ────────────────────────────────────

_OUTPUT_KEYWORDS = {
    ".py":  ("def", "def function_name(...):\n    # your modified code here"),
    ".ts":  ("function", "function functionName(...): ReturnType {\n    // your modified code here\n}"),
    ".tsx": ("function", "function ComponentName(...): JSX.Element {\n    // your modified code here\n}"),
    ".js":  ("function", "function functionName(...) {\n    // your modified code here\n}"),
    ".go":  ("func", "func functionName(...) returnType {\n    // your modified code here\n}"),
    ".rs":  ("fn", "fn function_name(...) -> ReturnType {\n    // your modified code here\n}"),
    ".cpp": ("type", "ReturnType function_name(...) {\n    // your modified code here\n}"),
    ".c":   ("type", "return_type function_name(...) {\n    // your modified code here\n}"),
}

# Map alternate extensions to their canonical form for scope/output lookups
_ALT_EXT_MAP = {
    ".cc": ".cpp", ".cxx": ".cpp", ".hpp": ".cpp",
    ".jsx": ".js",  ".mjs": ".js",
    ".h":   ".c",
}

def _canonical_ext(ext: str) -> str:
    """Map alternate extensions to their canonical form."""
    return _ALT_EXT_MAP.get(ext, ext)


class TSDCGenerator:
    def __init__(
        self,
        builder:    CPGBuilder,
        state_db:   StateDB,
        knowledge:  KnowledgeStore,
        budget:     int = TSDC_BUDGET,
    ):
        self.builder   = builder
        self.state_db  = state_db
        self.knowledge = knowledge
        self.allocator = BudgetAllocator(budget)
        self.contract_ex = ContractExtractor()
        self.caller_ex   = CallerPatternExtractor()
        self.body_ex     = BodySkeletonExtractor()

    def generate(
        self,
        file_path:    str,       # relative path from project root
        func_name:    str,
        task_goal:    str,
        test_guard:   str = "",  # pytest node IDs that must pass, space-separated
        error_feedback: Optional[str] = None,   # from previous failed attempt
        attempt:      int = 1,   # current attempt number (1-5)
        prev_error_type: Optional[str] = None,  # layer_failed from prev attempt
    ) -> str:
        """
        Build the full TSDC document for one modification task.
        Returns a structured string ready to be prepended to the model prompt.
        """
        node = self.builder.find_node_by_function(file_path, func_name)
        if node is None:
            raise ValueError(f"Function '{func_name}' not found in CPG for {file_path}")

        # Check staleness — resolve before generating context
        stale = self.builder.get_stale_neighbors(node.node_id)
        if node.is_stale:
            stale.append(node.node_id)

        callees = self.builder.get_direct_callees(node.node_id)[:MAX_CALLEES]
        callers = self.builder.get_direct_callers(node.node_id)[:MAX_CALLERS]

        # Read target function source
        abs_path = self.builder.project_root / file_path
        source   = abs_path.read_text(errors="replace")

        # Detect language
        ext = Path(file_path).suffix.lower()

        # Extract all tiers
        tiers = {
            "task_header":     self._tier1_task(file_path, func_name, task_goal, test_guard,
                                                 stale, error_feedback, ext,
                                                 attempt_number=attempt,
                                                 prev_error_type=prev_error_type),
            "type_skeleton":   self._tier2_skeleton(node),
            "callee_sigs":     self._tier3_callees(callees),
            "contract":        self._tier4_contract(source, func_name, file_path),
            "caller_patterns": self._tier5_callers(callers, func_name),
            "diff_digest":     self._tier6_digest(node, callees, callers),
            "codebase_rules":  self._tier7_rules(func_name, file_path),
            "target_body":     self._tier8_body(source, func_name, node, file_path),
        }

        # ── Tier relevance pruning ────────────────────────────────────────────
        # Lightweight alternative to agent-driven retrieval: remove tiers that
        # are provably irrelevant to the current task.  Zero LLM calls.
        tiers = self._prune_irrelevant_tiers(tiers, task_goal, node, callers)

        # Enforce budget (with retry-aware rebalancing)
        is_retry = attempt > 1
        tiers = self.allocator.allocate(tiers, is_retry=is_retry)

        # Assemble final document
        return self._assemble(tiers, ext)

    # ── Tier builders ─────────────────────────────────────────────────────────

    def _tier1_task(
        self,
        file_path: str,
        func_name: str,
        goal: str,
        test_guard: str,
        stale: List[str],
        error_feedback: Optional[str],
        ext: str,
        attempt_number: int = 1,
        prev_error_type: Optional[str] = None,
    ) -> str:
        config = get_language_config(file_path)
        lang_name = config.display_name if config else "Unknown"

        lines = []

        # ── FRESH START SIGNAL (attempts 3+, same error persists) ────────────
        # When the model has failed 2+ times with the same error type,
        # it's anchored to a flawed approach.  This breaks that anchoring.
        if attempt_number >= 3 and prev_error_type:
            lines += [
                "<fresh_start>",
                f"Your previous {attempt_number - 1} attempts ALL FAILED with the "
                f"same error type: {prev_error_type}.",
                "MANDATORY: Abandon your previous approach entirely.",
                "Do NOT reference or repeat any code from your previous attempts.",
                "Generate a completely different implementation from scratch.",
                "The ONLY valid functions are listed in <available_callees> below.",
                "Nothing else exists in this codebase.",
                "</fresh_start>",
                "",
            ]

        lines += [
            "━━━ TASK ━━━",
            f"target:     {file_path}::{func_name}",
            f"language:   {lang_name}",
            f"goal:       {goal}",
        ]
        if test_guard:
            lines.append(f"test_guard: {test_guard}")
        if stale:
            lines.append(f"WARNING — stale dependencies: {', '.join(stale[:4])}")
            lines.append("  Treat their signatures below as authoritative, not their bodies.")

        # Language-specific scope rules
        lines.append("")
        lines.append("SCOPE RULES (violating these causes test failures):")
        scope_rules = _SCOPE_RULES.get(_canonical_ext(ext), _SCOPE_RULES[".py"])
        lines.extend(scope_rules)

        # Hard callee constraint — explicitly inside <task> so the model can't ignore it
        lines.append("")
        lines.append("CRITICAL: Using any function, method, or symbol NOT listed in")
        lines.append("<available_callees> is an AUTOMATIC FAILURE. If you need a function")
        lines.append("that is not listed, use standard library equivalents instead.")

        if error_feedback:
            lines.append("")
            lines.append("━━━ PREVIOUS ATTEMPT FAILED — READ THIS BEFORE GENERATING ━━━")
            lines.append(error_feedback[:800])
        return "\n".join(lines)

    def _tier2_skeleton(self, node: CPGNode) -> str:
        lines = ["", "━━━ TYPE SKELETON (authoritative — do not change signature) ━━━"]
        for dec in node.decorators:
            lines.append(dec)
        lines.append(node.signature + ":")
        if node.class_name:
            lines.append(f"  # class: {node.class_name}")
        if node.raises:
            lines.append(f"  # raises: {'; '.join(node.raises)}")
        return "\n".join(lines)

    def _tier3_callees(self, callees: List[CPGNode]) -> str:
        if not callees:
            return ""
        lines = ["", "━━━ AVAILABLE CALLEES (you may ONLY call functions listed here) ━━━"]
        for c in callees:
            line = c.signature
            if c.summary:
                line += f"  # {c.summary[:80]}"
            lines.append(line)
        lines.append("# Do NOT invent function names. Do NOT call anything not listed above.")
        return "\n".join(lines)

    def _tier4_contract(self, source: str, func_name: str, file_path: str = "") -> str:
        c = self.contract_ex.extract(source, func_name, file_path=file_path)
        lines = ["", "━━━ CONTRACT (must be satisfied in your output) ━━━"]
        if c["preconditions"]:
            lines.append("pre:")
            for p in c["preconditions"][:4]:
                lines.append(f"  • {p}")
        if c["postconditions"]:
            lines.append("post:")
            for p in c["postconditions"][:3]:
                lines.append(f"  • {p}")
        if c["side_effects"]:
            lines.append("side-effects:")
            for s in c["side_effects"][:3]:
                lines.append(f"  • {s}")
        if c["raises"]:
            lines.append("raises:")
            for r in c["raises"][:3]:
                lines.append(f"  • {r}")
        if c["invariants"]:
            lines.append("invariants:")
            for i in c["invariants"][:2]:
                lines.append(f"  • {i}")
        if len(lines) == 2:  # only header added
            lines.append("  (no explicit contract extracted)")
        return "\n".join(lines)

    def _tier5_callers(self, callers: List[CPGNode], func_name: str) -> str:
        if not callers:
            return ""
        patterns = self.caller_ex.extract(
            str(self.builder.project_root), func_name, callers, MAX_CALLERS
        )
        if not patterns:
            return ""
        lines = ["", "━━━ CALLER PATTERNS (how this function is actually invoked) ━━━"]
        for p in patterns:
            lines.append(f"  {p}")
        return "\n".join(lines)

    def _tier6_digest(
        self, node: CPGNode, callees: List[CPGNode], callers: List[CPGNode]
    ) -> str:
        all_ids = [node.node_id] + [c.node_id for c in callees + callers]
        recent  = self.state_db.get_recent_changes(all_ids, days=DIGEST_DAYS)
        if not recent:
            return ""
        lines = ["", "━━━ RECENT CHANGES IN CONNECTED FUNCTIONS ━━━"]
        for entry in recent[:5]:
            lines.append(f"  [{entry['days_ago']}d] {entry['node_id']} — {entry['description']}")
        return "\n".join(lines)

    def _tier7_rules(self, func_name: str, file_path: str) -> str:
        rules = self.knowledge.get_rules_for(func_name, file_path, max_rules=MAX_RULES)
        if not rules:
            return ""
        lines = ["", "━━━ CODEBASE RULES (extracted from verified past edits) ━━━"]
        for r in rules:
            lines.append(f"  • {r}")
        return "\n".join(lines)

    def _tier8_body(self, source: str, func_name: str, node: CPGNode,
                    file_path: str = "") -> str:
        lines = ["", "━━━ TARGET FUNCTION (modify this) ━━━"]
        if node.loc > 80:
            body = self.body_ex.extract(source, func_name, max_lines=80, file_path=file_path)
        else:
            body = self._extract_full_body(source, func_name, file_path)
        lines.append(body)
        return "\n".join(lines)

    def _extract_full_body(self, source: str, func_name: str, file_path: str = "") -> str:
        ext = Path(file_path).suffix.lower() if file_path else ".py"

        if ext == ".py":
            return self._extract_full_body_python(source, func_name)
        else:
            return self._extract_full_body_generic(source, func_name, file_path)

    def _extract_full_body_python(self, source: str, func_name: str) -> str:
        """Python fast path — uses ast module."""
        import ast
        try:
            tree      = ast.parse(source)
            src_lines = source.splitlines()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name == func_name:
                        start = node.lineno - 1
                        end   = node.end_lineno
                        return "\n".join(src_lines[start:end])
        except Exception:
            pass
        return source

    def _extract_full_body_generic(self, source: str, func_name: str, file_path: str) -> str:
        """Generic path — uses tree-sitter."""
        config = get_language_config(file_path)
        if not config:
            return source

        try:
            from tree_sitter import Parser
            from core.cpg.language_registry import get_ts_language

            lang    = get_ts_language(config)
            parser  = Parser(lang)
            src_bytes = source.encode("utf-8")
            tree    = parser.parse(src_bytes)
            lines   = source.splitlines()

            all_func_types = set(config.func_node_types + config.method_node_types)
            for node in self._walk_types(tree.root_node, all_func_types):
                name_node = node.child_by_field_name(config.name_field)
                if not name_node:
                    continue
                name = src_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace").strip()
                if config.name_field == "declarator":
                    name = name.split("::")[-1].split("(")[0].strip()
                if name == func_name:
                    start = node.start_point[0]
                    end   = node.end_point[0] + 1
                    return "\n".join(lines[start:end])
        except (ImportError, Exception):
            pass
        return source

    @staticmethod
    def _walk_types(node, types: set):
        if node.type in types:
            yield node
        for child in node.children:
            yield from TSDCGenerator._walk_types(child, types)

    # ── Tier relevance pruning ────────────────────────────────────────────────

    _SIMPLE_GOAL_KEYWORDS = {
        "add logging", "log ", "raise ", "add a guard", "add a check",
        "add a warning", "log the", "log a ", "raise ValueError",
        "raise TypeError", "raise RuntimeError",
    }

    def _prune_irrelevant_tiers(
        self,
        tiers: Dict[str, str],
        task_goal: str,
        node,
        callers: list,
    ) -> Dict[str, str]:
        """Remove tiers that are provably irrelevant to the current task.
        This is the lightweight alternative to agent-driven CPG retrieval.
        
        Gap 5 fix: only prune codebase_rules if the tier is small enough
        that removing it saves ≥200 tokens. This prevents dropping critical
        rules like 'never use print(), use self.logger' for logging tasks.
        """
        from core.tsdc.budget import count_tokens
        goal_lower = task_goal.lower()

        # No callers → caller_patterns is empty noise
        if not callers:
            tiers["caller_patterns"] = ""

        # Simple tasks: only prune if the token savings justify it
        if any(kw in goal_lower for kw in self._SIMPLE_GOAL_KEYWORDS):
            # Always safe to drop diff_digest for simple tasks
            tiers["diff_digest"] = ""
            # Only drop codebase_rules if it's a substantial block (≥200 tokens)
            # Small rule sets are cheap and may contain critical constraints
            rules_text = tiers.get("codebase_rules", "")
            if rules_text and count_tokens(rules_text) >= 200:
                tiers["codebase_rules"] = ""
            # else: keep it — it's small and may prevent hallucinations

        return tiers

    # ── Assembly ──────────────────────────────────────────────────────────────

    # XML tag mapping — provides attention-head anchors for the model
    _TIER_XML_TAGS = {
        "task_header":     "task",
        "type_skeleton":   "signature",
        "callee_sigs":     "available_callees",
        "contract":        "contract",
        "caller_patterns": "caller_patterns",
        "diff_digest":     "recent_changes",
        "codebase_rules":  "rules",
        "target_body":     "current_code",
    }

    def _assemble(self, tiers: Dict[str, str], ext: str = ".py") -> str:
        order = [
            "task_header", "type_skeleton", "callee_sigs", "contract",
            "caller_patterns", "diff_digest", "codebase_rules", "target_body",
        ]
        parts = []
        for key in order:
            val = tiers.get(key, "")
            if val and val.strip():
                tag = self._TIER_XML_TAGS.get(key, key)
                parts.append(f"<{tag}>\n{val}\n</{tag}>")
        doc = "\n".join(parts)

        # Language-specific output instructions
        keyword, example = _OUTPUT_KEYWORDS.get(_canonical_ext(ext), _OUTPUT_KEYWORDS[".py"])

        doc += "\n\n<output_instructions>\n"
        doc += "Output the COMPLETE MODIFIED function body inside <code> tags.\n"
        doc += "Format:\n"
        doc += f"<code>\n{example}\n</code>\n"
        doc += "\n"
        doc += "RULES — violating any of these causes IMMEDIATE REJECTION:\n"
        doc += f"• Start with the `{keyword} ` line, include all code, end after the last line.\n"
        doc += "• Wrap output in <code>...</code> tags. Nothing outside the tags.\n"
        doc += "• No prose, no explanation, no comments outside <code> tags.\n"
        doc += "• Use ONLY functions from <available_callees>. Inventing names = rejection.\n"
        doc += "• Do NOT change the function signature unless <task> explicitly requires it.\n"
        doc += "• Preserve the exact indentation of the original function.\n"
        doc += "</output_instructions>"
        return doc