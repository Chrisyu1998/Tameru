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


def test_prompt_version_pinned_to_v3() -> None:
    """`PROMPT_VERSION` was bumped to v3 with the less-conservative rewrite.

    The comment at the top of memory.py asks for a bump whenever
    `DISTILL_SYSTEM_PROMPT` changes in a way that affects extraction. v3
    (2026-07-03) made the prompt generous and added few-shot examples so
    it stops returning `[]` on ledger-heavy chat (E1/E2) — that qualifies.
    """
    assert PROMPT_VERSION == "memory_distill_v3"


def test_ledger_state_exclusion_present() -> None:
    """The distill prompt still forbids extracting which cards the user owns.

    The v3 rewrite made extraction more generous but must keep the
    inventory/one-off exclusion: the model may extract card-spending
    HABITS ("User puts Costco runs on CSR") but not card OWNERSHIP, and
    not specific subscriptions/transactions the live tools answer
    authoritatively. Without it, a stale "User has Amex Platinum 1007"
    bleeds into future sessions once the card is deleted.
    """
    assert "Do NOT extract live inventory" in DISTILL_SYSTEM_PROMPT
    assert "Which cards the user currently owns" in DISTILL_SYSTEM_PROMPT
    assert "card HABIT is fine" in DISTILL_SYSTEM_PROMPT


def test_prompt_is_generous_with_few_shot_examples() -> None:
    """v3 fixes the "returns []" problem with generous framing + examples.

    The single highest-ROI recall fix (per the Mem0 fact-extraction
    guidance) is few-shot examples showing both a populated output and a
    correct empty one, plus explicit "be generous" framing so the model
    stops classifying real signal as not-worth-remembering.
    """
    assert "Be generous" in DISTILL_SYSTEM_PROMPT
    # Both a populated example and an empty-output example must be present
    # so the model learns the boundary rather than defaulting to [].
    assert "Output:" in DISTILL_SYSTEM_PROMPT
    assert "card_preference" in DISTILL_SYSTEM_PROMPT
    assert "[]" in DISTILL_SYSTEM_PROMPT
    # The reframe that unlocks signal from a spending app: patterns/intent
    # out of ledger-heavy chat, not the raw transaction rows.
    assert "PATTERNS and INTENT" in DISTILL_SYSTEM_PROMPT
