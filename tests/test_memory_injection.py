"""Day 16 — render_system_prompt injects user_memory into block[1] only.

Verifies the §11.3 cache-breakpoint invariant: per-user memory belongs
in the dynamic tail (block[1]), never in the cached preamble (block[0]).
A regression that drops memory into block[0] would silently invalidate
the prefix cache for every user and break the cost projection — this
test is the load-bearing guard against that.

Also covers the delete contract: hard-deleting a row removes it from
the next render.
"""

from __future__ import annotations

import uuid

import pytest


from app.db import supabase_for_user  # noqa: E402
from app.prompts.chat import SYSTEM_PROMPT, render_system_prompt  # noqa: E402




pytestmark = pytest.mark.usefixtures("clean_memory")


def test_seeded_facts_appear_in_block1_not_block0(user_a):
    """Three seeded facts → rendered system prompt's block[1] contains
    all three fact texts under a 'What I know about this user:' header;
    block[0] is unchanged from SYSTEM_PROMPT."""
    client = supabase_for_user(user_a.jwt)
    facts = [
        ("User is working toward CSR $4K SUB by Q2 2026", "goal", 0.9),
        ("User puts Costco purchases on CSR", "card_preference", 0.7),
        ("User prefers grocery rewards over dining rewards", "preference", 0.5),
    ]
    inserted_ids = []
    for fact, category, score in facts:
        row = (
            client.table("user_memory")
            .insert(
                {
                    "user_id": user_a.id,
                    "fact": fact,
                    "category": category,
                    "relevance_score": score,
                }
            )
            .execute()
            .data[0]
        )
        inserted_ids.append(row["id"])

    rendered = render_system_prompt(user_jwt=user_a.jwt)

    assert isinstance(rendered, list) and len(rendered) == 2
    block0_text = rendered[0]["text"]
    block1_text = rendered[1]["text"]

    # Block 0 is the cached preamble — bytes equal to SYSTEM_PROMPT.
    # No per-user content here, ever.
    assert block0_text == SYSTEM_PROMPT
    assert rendered[0].get("cache_control") == {"type": "ephemeral"}
    for fact, _, _ in facts:
        assert fact not in block0_text, (
            f"per-user fact leaked into cached preamble — invalidates §11.3 cache: {fact!r}"
        )

    # Block 1 carries the header + every fact.
    assert "What I know about this user" in block1_text
    for fact, _, _ in facts:
        assert fact in block1_text

    # Now delete the goal row and re-render — that fact must be gone.
    goal_id = inserted_ids[0]
    client.table("user_memory").delete().eq("id", goal_id).execute()

    rendered_after = render_system_prompt(user_jwt=user_a.jwt)
    block1_after = rendered_after[1]["text"]
    assert facts[0][0] not in block1_after, (
        "deleted fact still appears in rendered prompt — hard-delete contract broken"
    )
    # The other two facts remain.
    assert facts[1][0] in block1_after
    assert facts[2][0] in block1_after


def test_render_prompt_with_no_facts_omits_memory_header(user_a):
    """A user with zero user_memory rows gets a normal block[1] (date +
    merchants only) — no empty 'What I know about this user:' header
    burning tokens for nothing."""
    rendered = render_system_prompt(user_jwt=user_a.jwt)
    block1_text = rendered[1]["text"]
    assert "What I know about this user" not in block1_text
