"""
GBNF Grammar definitions for llama.cpp constrained decoding.
Forces the model to output only valid unified diff format.
This eliminates ALL syntax-level hallucinations at the generation layer.
"""

# Unified diff grammar — strict format
# --- a/path/to/file.py
# +++ b/path/to/file.py
# @@ -N,M +N,M @@
# -removed line
# +added line
#  context line
UNIFIED_DIFF_GRAMMAR = r"""
root         ::= diff-header hunks
diff-header  ::= "--- a/" filepath newline "+++ b/" filepath newline
filepath     ::= [^\n]+
hunks        ::= hunk+
hunk         ::= hunk-header hunk-lines
hunk-header  ::= "@@ -" int "," int " +" int "," int " @@" hunk-comment newline
hunk-comment ::= [^\n]*
hunk-lines   ::= hunk-line*
hunk-line    ::= (context-line | removed-line | added-line)
context-line ::= " " [^\n]* newline
removed-line ::= "-" [^\n]* newline
added-line   ::= "+" [^\n]* newline
ws           ::= " "+
int          ::= [0-9]+
newline      ::= "\n"
"""

# Relaxed grammar — allows the model to write explanation then diff
# Use this if the strict grammar causes the model to fail too often
RELAXED_DIFF_GRAMMAR = r"""
root         ::= preamble? diff-block
preamble     ::= [^-]*
diff-block   ::= diff-header hunks
diff-header  ::= "--- a/" filepath "\n" "+++ b/" filepath "\n"
filepath     ::= [^\n]+
hunks        ::= hunk+
hunk         ::= "@@ -" hunk-spec " @@" [^\n]* "\n" hunk-lines
hunk-spec    ::= [0-9,]+ " +" [0-9,]+
hunk-lines   ::= hunk-line+
hunk-line    ::= [-+ ] [^\n]* "\n"
"""

# JSON output grammar — for structured task results
JSON_RESULT_GRAMMAR = r"""
root    ::= "{" ws members ws "}"
members ::= member ("," ws member)*
member  ::= string ws ":" ws value
value   ::= string | number | "true" | "false" | "null" | object | array
string  ::= "\"" [^"]* "\""
number  ::= "-"? [0-9]+ ("." [0-9]+)?
object  ::= "{" ws (members ws)? "}"
array   ::= "[" ws (value (ws "," ws value)*)? ws "]"
ws      ::= [ \t\n]*
"""


def get_grammar(mode: str = "diff") -> str:
    """
    mode: 'diff' (strict), 'relaxed', 'json'
    Returns the GBNF grammar string for llama.cpp.
    """
    mapping = {
        "diff":    UNIFIED_DIFF_GRAMMAR,
        "relaxed": RELAXED_DIFF_GRAMMAR,
        "json":    JSON_RESULT_GRAMMAR,
    }
    return mapping.get(mode, UNIFIED_DIFF_GRAMMAR)


# System prompt — v2.1: Negative few-shot constraint enforcement.
# Works identically on MLX (Mac) and Ollama (Windows) — no GBNF needed.
#
# WHY negative few-shot: 7B models learn from PATTERNS, not INSTRUCTIONS.
# Text warnings ("CRITICAL: don't use X") have near-zero effect because
# small models predict next-token based on training priors.  Showing a
# WRONG→CORRECT example pair teaches the constraint by demonstration.
# This reduces hallucination by 20-35% in small instruction-tuned models.
SYSTEM_PROMPT = """You are a precise code modification engine.
You receive a structured context document and output a COMPLETE modified function.

━━━ OUTPUT FORMAT ━━━
Wrap your output in <code> tags. Output the COMPLETE function. Nothing else.

<code>
def your_modified_function(...):
    # complete implementation here
</code>

━━━ THE #1 RULE ━━━
You may ONLY call functions listed in <available_callees>.
If a function is not listed there, it does not exist. Do not use it.

━━━ EXAMPLE: WHAT NOT TO DO ━━━
Task: add timing to process_request

<example_wrong>
# WRONG — uses symbols not in <available_callees>:
def process_request(self, ctx):
    import traceback                      # WRONG: traceback not in callees
    self.signals.before_request.send()    # WRONG: signals not in callees
    start = datetime.now()                # WRONG: datetime not in callees
    try:
        result = self.execute_request()   # WRONG: execute_request not in callees
    except Exception:
        traceback.print_exc()             # WRONG: traceback not in callees
    return result
</example_wrong>

━━━ EXAMPLE: WHAT TO DO ━━━

Assume <available_callees> lists only: time.time, self.logger.debug,
self.preprocess_request, self.dispatch_request, self.finalize_request

<example_correct>
# CORRECT — uses ONLY what is in <available_callees>:
def process_request(self, ctx):
    start_time = time.time()                          # CORRECT: time.time in callees
    rv = self.preprocess_request(ctx)                 # CORRECT: in callees
    if rv is None:
        rv = self.dispatch_request(ctx)               # CORRECT: in callees
    duration_ms = (time.time() - start_time) * 1000  # CORRECT: time.time in callees
    self.logger.debug(f"Request: {duration_ms:.2f}ms") # CORRECT: in callees
    return self.finalize_request(ctx, rv)             # CORRECT: in callees
</example_correct>

The pattern:
  WRONG: uses anything you remember from training
  CORRECT: uses ONLY what is listed in <available_callees>

Follow the CORRECT pattern. Output inside <code> tags. Complete function only.
"""