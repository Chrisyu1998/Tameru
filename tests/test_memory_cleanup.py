"""Day 17 — prune_user_memory time-decay + capacity-trim sweep.

Four cases, each running against the real local Supabase migration:

  * `test_time_decay_deletes_stale_and_keeps_fresh` — rows with
    `reinforced_at < now() - 90d` are deleted; younger rows stay.
  * `test_capacity_trim_keeps_top_60_by_recency_x_relevance` — a 65-row
    seed with deterministic scoring is trimmed to exactly the expected
    60 rows.
  * `test_tie_breaker_prefers_more_recently_reinforced` — when two rows
    are tied on `relevance / (1 + days/30)` and one was reinforced more
    recently, the older-reinforced one is deleted first.
  * `test_render_user_memory_caps_at_60_on_overflow` — a 62-row state
    (distillation overflow before the next sweep) renders exactly 60
    lines in the block injected into the chat system prompt.

Not covered here: the per-user `pg_try_advisory_xact_lock` isolation
between cron and a concurrent distillation. Verified by code review of
the SQL function — the lock is acquired with a single
`pg_try_advisory_xact_lock` call and the failure mode is `CONTINUE`
(skip this user, pick them up tomorrow). Building a cross-process test
would need a second open Postgres connection (psycopg) outside the
project's dependency set; the value-to-effort ratio is poor for a
well-understood lock primitive.

The prune RPC is invoked via the `admin_client` fixture (service_role
through PostgREST). `GRANT EXECUTE ... TO service_role` in the
migration is what lets this path call the function — `authenticated`
has no execute privilege, by design.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.agent import memory as memory_module
from app.db import supabase_for_user

pytestmark = pytest.mark.usefixtures("clean_memory")


def test_time_decay_at_90_day_boundary(user_a, admin_client):
    """Step 1 of the sweep: the predicate is `reinforced_at < now() - 90d`.

    Tests the boundary literally as written in Day 17's done-when: a
    91-day-stale row is gone after the sweep; an 89-day-stale row
    remains. Both rows carry `relevance_score=1.0` so the capacity
    trim cannot be what (incorrectly) deletes the 91-day row — only
    step 1's `reinforced_at` predicate should match.
    """
    now = dt.datetime.now(dt.timezone.utc)
    _seed_facts(
        user_a,
        [
            ("91-day stale", "preference", 1.0, now - dt.timedelta(days=91)),
            ("89-day fresh", "preference", 1.0, now - dt.timedelta(days=89)),
        ],
    )

    admin_client.rpc("prune_user_memory", {}).execute()

    survivors = {row["fact"] for row in _read_facts(user_a)}
    assert "89-day fresh" in survivors
    assert "91-day stale" not in survivors


def test_capacity_trim_with_100_facts_leaves_exactly_60(user_a, admin_client):
    """Step 2: with 100 rows present, exactly 60 survive (Day 17 done-when).

    Seed shape:
      * 60 "keepers": relevance_score=0.9, reinforced_at=now → score ≈ 0.9.
      * 40 "drops":   relevance_score=0.1, reinforced_at=now-60d → score
                      = 0.1 / (1 + 60/30) = 0.0333.

    The score gap is wide enough that no tie-breaker logic is exercised
    — this test isolates "step 2 picks the lowest-scoring rows" from
    "step 2 breaks ties correctly," which has its own test below. All
    40 drops have `reinforced_at = now - 60d`, well inside the 90-day
    time-decay threshold, so step 1 does not delete them; the trim to
    60 must be what step 2 does.
    """
    now = dt.datetime.now(dt.timezone.utc)
    seeds = [
        (f"keeper {i}", "preference", 0.9, now)
        for i in range(60)
    ] + [
        (
            f"drop {i}",
            "preference",
            0.1,
            now - dt.timedelta(days=60),
        )
        for i in range(40)
    ]
    _seed_facts(user_a, seeds)

    admin_client.rpc("prune_user_memory", {}).execute()

    survivors = _read_facts(user_a)
    assert len(survivors) == 60
    assert all(row["fact"].startswith("keeper ") for row in survivors)


def test_tie_breaker_prefers_more_recently_reinforced(user_a, admin_client):
    """Same recency × relevance score, different `reinforced_at` → older
    one is deleted.

    Score = relevance × 30 / (30 + days). Picking:
      * fact A: relevance=0.4, days=0  → score = 0.4 × 30/30 = 0.4
      * fact B: relevance=0.6, days=15 → score = 0.6 × 30/45 = 0.4

    Tied on score; B is older. The migration's `ORDER BY ... DESC,
    reinforced_at DESC` ranks A above B, so B falls past the rank-60
    cutoff and gets deleted. Seeded around 60 high-score keepers so the
    tied pair sits at the boundary.
    """
    now = dt.datetime.now(dt.timezone.utc)
    seeds: list[tuple[str, str, float, dt.datetime]] = [
        (f"keeper {i}", "preference", 0.9, now)
        for i in range(59)
    ]
    # The tied pair lives in the bottom 2 of the user's 61-row state.
    seeds.append(("tied newer", "preference", 0.4, now))
    seeds.append(("tied older", "preference", 0.6, now - dt.timedelta(days=15)))
    _seed_facts(user_a, seeds)

    admin_client.rpc("prune_user_memory", {}).execute()

    survivors = {row["fact"] for row in _read_facts(user_a)}
    assert "tied newer" in survivors, (
        "Tiebreaker should prefer the more recently reinforced row"
    )
    assert "tied older" not in survivors
    assert len(survivors) == 60


def test_render_user_memory_caps_at_60_on_overflow(user_a):
    """Distillation may push the user over 60 before the next sweep.

    With 62 rows seeded and NO prune call, `render_user_memory` must
    still return exactly 60 `- [category] ...` lines (plus the header
    and a trailing blank). This is the contract that makes the soft-cap
    safe: over-cap rows are invisible to the agent until cron trims.
    """
    now = dt.datetime.now(dt.timezone.utc)
    _seed_facts(
        user_a,
        [
            (f"overflow fact {i}", "preference", 0.9, now)
            for i in range(62)
        ],
    )

    rendered = memory_module.render_user_memory(user_a.jwt)

    # Header + 60 facts + trailing blank ⇒ 62 lines total. Count just
    # the bullet lines to avoid coupling to header/footer formatting.
    fact_lines = [
        line for line in rendered.splitlines() if line.startswith("- [")
    ]
    assert len(fact_lines) == 60


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _seed_facts(
    user,
    rows: list[tuple[str, str, float, dt.datetime]],
) -> None:
    """Insert user_memory rows with explicit `reinforced_at` timestamps.

    Tuple shape: (fact, category, relevance_score, reinforced_at). The
    default DB `reinforced_at = now()` would race the time-decay
    predicate, so every seed sets an explicit value.
    """
    client = supabase_for_user(user.jwt)
    payload = [
        {
            "user_id": user.id,
            "fact": fact,
            "category": category,
            "relevance_score": relevance,
            "reinforced_at": reinforced_at.isoformat(),
        }
        for fact, category, relevance, reinforced_at in rows
    ]
    client.table("user_memory").insert(payload).execute()


def _read_facts(user) -> list[dict]:
    """Return all of `user`'s user_memory rows under their own JWT."""
    client = supabase_for_user(user.jwt)
    return (
        client.table("user_memory")
        .select("id, fact, category, relevance_score, reinforced_at")
        .eq("user_id", user.id)
        .execute()
        .data
        or []
    )
