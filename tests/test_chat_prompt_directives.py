"""String-presence guards on chat-system-prompt directives.

These tests fail if a refactor of `SYSTEM_PROMPT` drops two load-bearing
behavioral instructions. Both exist because of a real failure:

  1. Without the "always call propose_card" line, the model refuses
     re-adds based on chat history. Cards can be deleted via the cards
     page (which leaves no chat record), so prior add-turns in the
     conversation are not evidence the card is still active.

  2. Without the "do not claim … without verifying" Style directive,
     the model echoes ledger state from memory/history instead of
     calling get_cards / get_transactions / get_subscriptions.

Pure-Python — no Supabase, no Claude. The point is to keep the
behavioral guidance from silently disappearing across prompt edits.
"""

from __future__ import annotations

from app.prompts.chat import SYSTEM_PROMPT


def test_propose_card_directive_present() -> None:
    """The propose_card section instructs the model to always call the tool.

    Guards against a refactor that drops the "do not refuse based on chat
    history or memory" rule. Failure case in production: user deletes a
    card via the cards page, types "Add Amex 1007" in chat, model refuses
    with "already in your wallet" instead of calling propose_card.
    """
    assert "For any add-card intent, always call propose_card" in SYSTEM_PROMPT
    assert "Do not refuse based on chat history or memory" in SYSTEM_PROMPT
    assert "cards page" in SYSTEM_PROMPT


def test_ledger_claim_verification_directive_present() -> None:
    """The Style section bans claims about ledger state without a tool call.

    Generalizes the propose_card fix to transactions and subscriptions —
    the model must not assert presence/absence of any ledger row without
    consulting the live tools. Chat history and cross-session memory both
    go stale relative to the database, which can change outside chat.
    """
    assert (
        "Do not claim a card, transaction, or subscription is already "
        "in the user's wallet without verifying with a tool call"
        in SYSTEM_PROMPT
    )
    assert "Chat history and memory are not authoritative for ledger state" in SYSTEM_PROMPT
