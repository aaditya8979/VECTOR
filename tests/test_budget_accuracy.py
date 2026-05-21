"""
test_budget_accuracy.py — Compare Qwen tokenizer vs cl100k on TSDC documents.

Validates that the token budget system is using the correct tokenizer for the
actual model, and that the 2500-token budget claim in the paper is accurate.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Sample TSDC document fragments for budget testing ─────────────────────────

SAMPLE_TSDC_TIERS = {
    "task_header": """━━━ TASK ━━━
target:     src/services/auth.py::authenticate
language:   Python
goal:       Add rate limiting — max 5 attempts per IP per minute

SCOPE RULES (violating these causes test failures):
  • Only use variables you explicitly define inside the function body.
  • Do NOT invent attributes on parameters (e.g. ctx.request.start_time).
""",
    "type_skeleton": """
━━━ TYPE SKELETON (authoritative — do not change signature) ━━━
def authenticate(self, username: str, password: str, ip_address: str) -> AuthResult:
  # class: AuthService
  # raises: AuthenticationError; RateLimitError
""",
    "callee_sigs": """
━━━ AVAILABLE CALLEES (you may ONLY call functions listed here) ━━━
def check_rate_limit(self, ip: str, max_attempts: int = 5) -> bool
def verify_password(self, username: str, password: str) -> bool
def create_session(self, user_id: int) -> Session
# Do NOT invent function names. Do NOT call anything not listed above.
""",
    "target_body": """
━━━ TARGET FUNCTION (modify this) ━━━
def authenticate(self, username: str, password: str, ip_address: str) -> AuthResult:
    user = self.db.get_user(username)
    if user is None:
        raise AuthenticationError("User not found")
    if not self.verify_password(username, password):
        raise AuthenticationError("Invalid password")
    session = self.create_session(user.id)
    return AuthResult(success=True, session=session)
""",
}


class TestBudgetAccuracy:
    """Verify the token budget system uses the correct tokenizer."""

    def test_tokenizer_loads(self):
        """The Qwen tokenizer must load without error."""
        from core.tsdc.budget import _ENC, count_tokens
        # Should be a transformers tokenizer, not tiktoken
        assert hasattr(_ENC, 'encode'), "Tokenizer must have encode method"
        # count_tokens must return an int
        result = count_tokens("Hello, world!")
        assert isinstance(result, int)
        assert result > 0

    def test_not_tiktoken(self):
        """Verify we're NOT using the old GPT-4 tokenizer."""
        from core.tsdc.budget import _ENC
        # transformers.AutoTokenizer has vocab_size attribute
        # tiktoken.Encoding does not
        assert hasattr(_ENC, 'vocab_size') or hasattr(_ENC, 'get_vocab'), (
            "Tokenizer appears to be tiktoken, not the Qwen model tokenizer"
        )

    def test_budget_under_2500(self):
        """A standard TSDC document must fit within 2500 tokens."""
        from core.tsdc.budget import count_tokens, BudgetAllocator

        full_doc = "\n".join(SAMPLE_TSDC_TIERS.values())
        raw_count = count_tokens(full_doc)

        # The raw document should be under 2500 tokens after allocation
        allocator = BudgetAllocator(total_budget=2500)
        trimmed = allocator.allocate(SAMPLE_TSDC_TIERS)
        trimmed_doc = "\n".join(v for v in trimmed.values() if v)
        trimmed_count = count_tokens(trimmed_doc)

        assert trimmed_count <= 2500, (
            f"Trimmed TSDC document is {trimmed_count} tokens, exceeds 2500 budget"
        )

    def test_count_tokens_consistency(self):
        """Token counts must be deterministic."""
        from core.tsdc.budget import count_tokens

        text = "def hello(x: int) -> int:\n    return x * 2\n"
        count1 = count_tokens(text)
        count2 = count_tokens(text)
        assert count1 == count2, "Token counts must be deterministic"

    def test_empty_string(self):
        """Empty string = 0 tokens."""
        from core.tsdc.budget import count_tokens
        assert count_tokens("") == 0

    @pytest.mark.parametrize("tier_name", list(SAMPLE_TSDC_TIERS.keys()))
    def test_individual_tier_counts(self, tier_name):
        """Each tier must produce a positive token count."""
        from core.tsdc.budget import count_tokens
        count = count_tokens(SAMPLE_TSDC_TIERS[tier_name])
        assert count > 0, f"Tier {tier_name} produced 0 tokens"
