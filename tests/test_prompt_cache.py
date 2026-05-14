"""Day 9c — cache-aware system-prompt assembly invariants.

These tests guard the three failure modes that broke the §11.3 cost
projection if violated:

  1. Per-user data leaking into block[0] would bust the prompt cache for
     every user, since the cache key is the prefix hash up to the
     `cache_control` marker.

  2. Dropping the `Today is …` line (a regression I almost shipped) would
     make Claude invent dates from training distribution and
     propose_transaction(date=…) would land in the past.

  3. Folding per-user content into system_prompt_hash would defeat the
     hash's eval-bucketing purpose — every user, every day, would get a
     different prompt_hash for the same chat_v4 prompt.

Tests run against the real local Supabase stack so view + RLS behavior is
exercised, not mocked away.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.agent.tools import tool_schemas
from app.db import supabase_for_user
from app.prompts.chat import (
    SYSTEM_PROMPT,
    render_system_prompt,
    system_prompt_hash,
)
from tests.conftest import TestUser, _delete_user, _make_user


@pytest.fixture
def fresh_user_a(admin_client, supabase_env) -> TestUser:
    """Yield a freshly-created user with no transaction history.

    Same rationale as the matching fixture in
    `tests/test_render_user_merchants.py` — session-scoped user_a's
    merchant history is polluted with hundreds of single-visit rows
    from the wider suite, so a test that needs a clean merchant
    universe (e.g. asserting "unique merchant X appears in this user's
    block") must spin up a fresh user.
    """
    user = _make_user(
        admin_client,
        supabase_env["url"],
        supabase_env["anon_key"],
        f"cache-a-{uuid.uuid4().hex[:6]}",
    )
    yield user
    _delete_user(admin_client, user.id)


@pytest.fixture
def fresh_user_b(admin_client, supabase_env) -> TestUser:
    """Yield a second freshly-created user for the cross-user block diff test."""
    user = _make_user(
        admin_client,
        supabase_env["url"],
        supabase_env["anon_key"],
        f"cache-b-{uuid.uuid4().hex[:6]}",
    )
    yield user
    _delete_user(admin_client, user.id)


# ---------------------------------------------------------------------------
# Block-array structure: cached preamble first, dynamic tail second.
# ---------------------------------------------------------------------------


def test_render_returns_two_blocks_with_cache_control(user_a):
    """render_system_prompt must return a list of exactly two content
    blocks. Block 0 carries `cache_control: ephemeral`; block 1 does
    not. The Anthropic SDK uses the marker on block 0 to define the
    cached-prefix boundary, so an extra block or a swapped order means
    every user gets a cold cache.
    """
    blocks = render_system_prompt(user_jwt=user_a.jwt)

    assert isinstance(blocks, list)
    assert len(blocks) == 2
    assert blocks[0]["type"] == "text"
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[1]["type"] == "text"
    # Block 1 has no cache_control — this is what keeps per-user
    # variation out of the cached prefix.
    assert "cache_control" not in blocks[1]


def test_cached_block_matches_static_preamble(user_a):
    """Block 0's text must equal SYSTEM_PROMPT exactly — no date, no
    merchants, no per-user content. If anything user-specific slips in
    here, the cache breakpoint becomes a no-op for the multi-user case
    because each user's prefix hashes differently.
    """
    blocks = render_system_prompt(user_jwt=user_a.jwt)
    assert blocks[0]["text"] == SYSTEM_PROMPT


def test_cached_block_identical_across_users(user_a, user_b):
    """Two different users must share the exact same cached prefix. This
    is the defining property of the cache breakpoint: all users hit the
    same prefix hash so Anthropic can reuse the cached attention state.
    """
    blocks_a = render_system_prompt(user_jwt=user_a.jwt)
    blocks_b = render_system_prompt(user_jwt=user_b.jwt)
    assert blocks_a[0] == blocks_b[0]


def test_dynamic_block_differs_when_merchants_differ(fresh_user_a, fresh_user_b):
    """When users have different transaction histories, their block 1
    must differ. This is the counter-check to
    test_cached_block_identical_across_users — proves the user-specific
    content actually lands somewhere, just not in the cached prefix.

    Uses fresh function-scoped users so the seeded merchants are
    guaranteed to make the view's LIMIT 30 — the session-scoped
    user_a/user_b accumulate too many single-visit rows from the wider
    suite to keep this test deterministic."""
    unique_a = f"OnlyA-{uuid.uuid4().hex[:8]}"
    unique_b = f"OnlyB-{uuid.uuid4().hex[:8]}"
    _seed_transaction(fresh_user_a, card_id=None, merchant=unique_a, amount="10.00")
    _seed_transaction(fresh_user_b, card_id=None, merchant=unique_b, amount="10.00")

    blocks_a = render_system_prompt(user_jwt=fresh_user_a.jwt)
    blocks_b = render_system_prompt(user_jwt=fresh_user_b.jwt)

    assert unique_a in blocks_a[1]["text"]
    assert unique_a not in blocks_b[1]["text"]
    assert unique_b in blocks_b[1]["text"]
    assert unique_b not in blocks_a[1]["text"]


# ---------------------------------------------------------------------------
# Date line: present in the dynamic tail, never in the cached prefix.
# ---------------------------------------------------------------------------


def test_today_line_lives_in_dynamic_tail(user_a):
    """The `Today is YYYY-MM-DD.` line is what lets Claude resolve
    relative dates and the date arg on propose_transaction. It must be
    present in block[1] and absent from block[0] — if it lands in the
    cached prefix, the cache invalidates every midnight UTC and users
    pay full price on the first turn of every day."""
    fixed = date(2026, 5, 14)
    blocks = render_system_prompt(user_jwt=user_a.jwt, today=fixed)

    assert "Today is 2026-05-14." in blocks[1]["text"]
    assert "Today is" not in blocks[0]["text"]


# ---------------------------------------------------------------------------
# Hash stability: prompt_hash buckets by prompt version + tool schemas
# only, ignoring per-user variation in the dynamic tail.
# ---------------------------------------------------------------------------


def test_hash_identical_across_users(user_a, user_b):
    """Two users on the same chat_v4 prompt must produce the same
    prompt_hash. This is what keeps `ai_call_log.prompt_hash` useful
    for eval bucketing and cost-curve queries — if every user got a
    different hash, the bucketing collapses to one-row-per-bucket.
    """
    schemas = tool_schemas()
    blocks_a = render_system_prompt(user_jwt=user_a.jwt)
    blocks_b = render_system_prompt(user_jwt=user_b.jwt)
    assert system_prompt_hash(blocks_a, schemas) == system_prompt_hash(blocks_b, schemas)


def test_hash_changes_when_tool_schemas_change(user_a):
    """Swapping the tool schemas must produce a different hash. The
    model's behavior depends on its tool surface, so eval comparison
    has to bucket by both the prompt and the tool set; if the hash
    ignored tools, regressions caused by an added or modified tool
    would average across heterogeneous prompts."""
    schemas = tool_schemas()
    blocks = render_system_prompt(user_jwt=user_a.jwt)

    hash_real = system_prompt_hash(blocks, schemas)
    hash_swapped = system_prompt_hash(blocks, schemas + [{"name": "phantom_tool"}])

    assert hash_real != hash_swapped


def test_hash_stable_across_dynamic_tail_changes(fresh_user_a):
    """The dynamic tail (date, merchants) must not affect the hash. We
    capture the hash, then add a new transaction that would change the
    merchants block, then capture again — both hashes must match.
    Without this property, the hash would drift as the user's history
    evolves, and `prompt_hash` would no longer identify "this prompt
    version" for a Day 9-style A/B comparison.

    Uses a fresh function-scoped user so the seeded Drift-* merchant is
    guaranteed to land in the view's LIMIT 30. With the session-scoped
    user_a, the full-suite run accumulates 30+ more-recent merchants
    and the new single-visit row gets clipped out, breaking the sanity
    check (the hash invariant itself would still hold)."""
    schemas = tool_schemas()
    blocks_before = render_system_prompt(user_jwt=fresh_user_a.jwt)
    hash_before = system_prompt_hash(blocks_before, schemas)

    _seed_transaction(
        fresh_user_a, card_id=None,
        merchant=f"Drift-{uuid.uuid4().hex[:8]}", amount="10.00",
    )

    blocks_after = render_system_prompt(user_jwt=fresh_user_a.jwt)
    hash_after = system_prompt_hash(blocks_after, schemas)

    # Sanity: the dynamic tail did change.
    assert blocks_before[1]["text"] != blocks_after[1]["text"]
    # But the hash did not.
    assert hash_before == hash_after


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _seed_transaction(
    user,
    *,
    card_id: str | None,
    merchant: str,
    amount: str,
    category: str = "Dining",
) -> str:
    """Insert one transaction via the user's RLS-scoped client; return id."""
    client = supabase_for_user(user.jwt)
    row: dict[str, object] = {
        "user_id": user.id,
        "merchant": merchant,
        "amount": amount,
        "date": date.today().isoformat(),
        "category": category,
        "source": "manual",
        "client_request_id": str(uuid.uuid4()),
    }
    if card_id is not None:
        row["card_id"] = card_id
    return client.table("transactions").insert(row).execute().data[0]["id"]
