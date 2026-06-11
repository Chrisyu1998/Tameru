"""Day 24 — `aggregate_aicalllog()` correctness + idempotency.

Hits the real local Supabase migration (`20260522130100_aicalllog_aggregator_function.sql`).
Five behaviors pinned, matching DESIGN.md §8.9 and §14.1:

  * 95-day non-null user_id rows produce correct daily rollups
    (sum_input_tokens, sum_output_tokens, count, avg_latency_ms,
    error_count).
  * 89-day rows are left in `ai_call_log` and never reach
    `ai_call_log_daily` — the 90-day window is the boundary.
  * `user_id IS NULL` rows are intentionally skipped (§8.9 line 1034 —
    the composite PK forbids NULL user_id; system-level calls do not
    roll up to per-user aggregates).
  * Aggregated source rows are deleted; the daily table grows.
  * A second invocation produces no error and no duplicates — the
    function is idempotent against the rare cron double-fire.

The RPC is invoked via `admin_client` (service_role through PostgREST).
The migration's `REVOKE EXECUTE ... FROM authenticated; GRANT EXECUTE
... TO service_role;` posture is what makes this the right path —
calling from a regular user JWT would raise `permission denied`.
"""

from __future__ import annotations

import datetime as dt
from uuid import UUID

import pytest

pytestmark = pytest.mark.usefixtures("clean_ai_call_log")


def test_95_day_rows_roll_up_with_correct_aggregates(user_a, admin_client):
    """Two 95-day rows for one (provider, model, task_type) collapse
    into one daily row. Token sums, count, error_count, and
    avg_latency_ms all reflect the source set exactly."""
    base_day = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=95)
    base_ts = dt.datetime.combine(base_day, dt.time(12, 0), tzinfo=dt.timezone.utc)
    _seed_rows(
        admin_client,
        user_a.id,
        [
            _row(base_ts, "anthropic", "claude-haiku-4-5", "chat_turn",
                 input_tokens=100, output_tokens=50, latency_ms=200, success=True),
            _row(base_ts + dt.timedelta(hours=2), "anthropic", "claude-haiku-4-5", "chat_turn",
                 input_tokens=300, output_tokens=70, latency_ms=400, success=False),
        ],
    )

    admin_client.rpc("aggregate_aicalllog", {}).execute()

    rollup = _daily_rows_for(admin_client, user_a.id)
    assert len(rollup) == 1, f"expected one daily row, got {len(rollup)}"
    row = rollup[0]
    assert row["date"] == base_day.isoformat()
    assert row["provider"] == "anthropic"
    assert row["model"] == "claude-haiku-4-5"
    assert row["task_type"] == "chat_turn"
    assert row["sum_input_tokens"] == 400
    assert row["sum_output_tokens"] == 120
    assert row["count"] == 2
    assert row["avg_latency_ms"] == 300  # (200 + 400) / 2
    assert row["error_count"] == 1  # one row with success=false


def test_89_day_rows_are_not_aggregated(user_a, admin_client):
    """The 90-day boundary is strict: 89 days old stays in the hot
    table and never appears in `ai_call_log_daily`. Pinning this
    prevents a future migration accident widening the predicate."""
    fresh_ts = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=89)
    _seed_rows(
        admin_client,
        user_a.id,
        [
            _row(fresh_ts, "google", "gemini-2.5-flash", "categorization",
                 input_tokens=50, output_tokens=10, latency_ms=120, success=True),
        ],
    )

    admin_client.rpc("aggregate_aicalllog", {}).execute()

    rollup = _daily_rows_for(admin_client, user_a.id)
    assert rollup == [], "89-day-old row must not roll up"
    survivors = _hot_rows_for(admin_client, user_a.id)
    assert len(survivors) == 1, "89-day-old row must remain in ai_call_log"


def test_null_user_id_rows_are_skipped(admin_client):
    """System-level rows (`user_id IS NULL`) must not appear in the
    daily table — its PK forbids NULL user_id (DESIGN.md §8.9 line
    1034). The source rows stay in `ai_call_log` past 90 days for
    on-demand query."""
    old_ts = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=95)
    _seed_rows(
        admin_client,
        None,
        [
            _row(old_ts, "anthropic", "claude-sonnet-4-6", "digest",
                 input_tokens=1000, output_tokens=200, latency_ms=800, success=True),
        ],
    )

    admin_client.rpc("aggregate_aicalllog", {}).execute()

    # The daily table has no rows attributable to NULL — query the
    # entire table and confirm none came from this seed.
    daily = admin_client.table("ai_call_log_daily").select("*").execute().data
    assert all(row["task_type"] != "digest" for row in daily or []), (
        "NULL-user_id row must not be rolled up"
    )
    # And the source row is intentionally NOT deleted.
    hot = (
        admin_client.table("ai_call_log")
        .select("*")
        .is_("user_id", "null")
        .eq("task_type", "digest")
        .execute()
        .data
    )
    assert len(hot) == 1, "NULL-user_id source row must survive aggregation"


def test_aggregated_source_rows_are_deleted(user_a, admin_client):
    """After aggregation, the 95-day source rows are gone from
    `ai_call_log`. The hot table never grows unbounded."""
    base_ts = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=95)
    _seed_rows(
        admin_client,
        user_a.id,
        [
            _row(base_ts, "anthropic", "claude-haiku-4-5", "chat_turn",
                 input_tokens=100, output_tokens=50, latency_ms=200, success=True),
        ],
    )

    admin_client.rpc("aggregate_aicalllog", {}).execute()

    hot = _hot_rows_for(admin_client, user_a.id)
    assert hot == [], "95-day source row must be deleted after aggregation"


def test_aggregator_is_idempotent_on_rerun(user_a, admin_client):
    """A second `aggregate_aicalllog()` call after the first must not
    raise and must not duplicate rollup rows. The first call deletes
    the source, so the second's SELECT finds nothing — but the
    `ON CONFLICT DO NOTHING` is what makes the rarer double-fire
    window safe too."""
    base_ts = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=95)
    _seed_rows(
        admin_client,
        user_a.id,
        [
            _row(base_ts, "google", "gemini-2.5-flash", "categorization",
                 input_tokens=50, output_tokens=10, latency_ms=120, success=True),
        ],
    )

    admin_client.rpc("aggregate_aicalllog", {}).execute()
    admin_client.rpc("aggregate_aicalllog", {}).execute()  # second pass — must be a no-op

    rollup = _daily_rows_for(admin_client, user_a.id)
    assert len(rollup) == 1, "second invocation must not duplicate the rollup row"


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_ai_call_log(admin_client):
    """Wipe `ai_call_log` and `ai_call_log_daily` before AND after each test.

    A wholesale truncate is fine here — every other suite that touches
    these tables also runs against the local stack and is rerun fresh.
    The aggregator tests must start from a known-empty rollup table to
    pin row-count assertions precisely.

    The after-wipe matters: the before-only version left the LAST test's
    ai_call_log_daily rows behind for the rest of the session, so later
    suites were safe only by collection order (audit P3-39).
    """

    def _wipe() -> None:
        """Truncate both tables (admin client, RLS-free)."""
        admin_client.table("ai_call_log_daily").delete().neq(
            "date", "1900-01-01"
        ).execute()
        admin_client.table("ai_call_log").delete().neq(
            "id", "00000000-0000-0000-0000-000000000000"
        ).execute()

    _wipe()
    yield
    _wipe()


def _row(
    ts: dt.datetime,
    provider: str,
    model: str,
    task_type: str,
    *,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    success: bool,
) -> dict:
    """Build one ai_call_log insert payload.

    Caller controls `timestamp` so the test can pin rows to a known
    distance from `now()` — the DEFAULT `now()` would race the
    aggregator's `< now() - 90 days` predicate.
    """
    return {
        "timestamp": ts.isoformat(),
        "provider": provider,
        "model": model,
        "task_type": task_type,
        "prompt_version": "test_v1",
        "prompt_hash": "deadbeef" * 8,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": latency_ms,
        "success": success,
        "error_code": None if success else "schema_violation",
    }


def _seed_rows(admin_client, user_id: str | None, rows: list[dict]) -> None:
    """Insert ai_call_log rows under the service role.

    The aggregator tests need to write arbitrary timestamps and (for
    the NULL-user test) `user_id = NULL`, both of which the user-JWT
    INSERT policy would forbid. Service role is the right path here —
    these tests run against the local stack only (the
    `_supabase_stack_ready` fixture asserts SUPABASE_URL is localhost).
    """
    payload = [{**row, "user_id": user_id} for row in rows]
    admin_client.table("ai_call_log").insert(payload).execute()


def _hot_rows_for(admin_client, user_id: str) -> list[dict]:
    """All ai_call_log rows owned by `user_id`."""
    return (
        admin_client.table("ai_call_log")
        .select("*")
        .eq("user_id", user_id)
        .execute()
        .data
        or []
    )


def _daily_rows_for(admin_client, user_id: str) -> list[dict]:
    """All ai_call_log_daily rows owned by `user_id`."""
    return (
        admin_client.table("ai_call_log_daily")
        .select("*")
        .eq("user_id", user_id)
        .execute()
        .data
        or []
    )
