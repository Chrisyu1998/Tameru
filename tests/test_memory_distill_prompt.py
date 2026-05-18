"""String-presence guards on the memory-distillation system prompt.

These tests fail if a refactor of `DISTILL_SYSTEM_PROMPT` drops the
exclusion rule that keeps ledger state out of `user_memory`. Without
the exclusion, the model can persist facts like "User has Amex
Platinum 1007" that go stale the moment the user deletes the card —
and that stale fact then bleeds into future chat sessions, causing
the model to refuse re-adds.

Also pins `PROMPT_VERSION` so a future prompt edit forces a version
bump (the `ai_call_log.prompt_version` bucket needs to flip for cost /
eval analysis when extraction behavior changes).

Pure-Python — no Supabase, no Claude.
"""

from __future__ import annotations

from app.agent.memory import DISTILL_SYSTEM_PROMPT, PROMPT_VERSION


def test_prompt_version_pinned_to_v2() -> None:
    """`PROMPT_VERSION` was bumped to v2 when the ledger-exclusion rule landed.

    The comment at the top of memory.py asks for a bump whenever
    `DISTILL_SYSTEM_PROMPT` changes in a way that affects extraction.
    Adding the "do not extract live-ledger state" paragraph qualifies.
    """
    assert PROMPT_VERSION == "memory_distill_v2"


def test_ledger_state_exclusion_present() -> None:
    """The distill prompt forbids extracting which cards the user owns.

    The model is allowed to extract card-spending HABITS ("User puts
    Costco runs on CSR") but not card OWNERSHIP / inventory. Mirror
    rule for subscriptions and transactions, which the live tools
    answer authoritatively.
    """
    assert "Do NOT extract live-ledger state" in DISTILL_SYSTEM_PROMPT
    assert "Which cards the user currently owns" in DISTILL_SYSTEM_PROMPT
    assert "Card and subscription HABITS are fine" in DISTILL_SYSTEM_PROMPT
