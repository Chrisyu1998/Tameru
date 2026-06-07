"""Middleware around the Claude agent loop — Day 9a.

Two concerns:

  * `assert_within_usage_cap(user)` — hard daily token cap per user.
    Checked once at turn entry (lenient mid-turn — once a turn starts
    it finishes, bounding overshoot to one turn). Per DESIGN.md §11.2
    / §7.3. When the cap is hit it also emits a Sentry alert so the
    operator learns about it without polling `ai_call_log` (see
    `_alert_daily_cap_reached`).

  * `ProviderRateLimited` — the structured error the loop raises when
    Anthropic 429s on two consecutive attempts. The retry logic itself
    lives in `app/agent/loop.py` (not here) because each attempt must
    write its own `ai_call_log` row (Day 8 invariant: one row per
    `messages.create` call), which requires audit context the loop
    already has in scope.

Sync by design — Day 8's loop is sync (see its module docstring).

CLAUDE.md invariants:
  * #1: every Supabase read here uses `supabase_for_user(user.jwt)`.
    Never the service role.
  * #14: the cap query is a SELECT on `ai_call_log` via the user JWT;
    the table's RLS SELECT policy scopes it to `auth.uid()`.
  * #15: the cap-hit Sentry alert is a deliberate operational signal
    via `capture_message`, NOT an exception, and NOT an AI-provider
    failure — so it does not contradict "do not route AI provider
    failures to Sentry." It also ships cleanly past `before_send`:
    `app.agent.middleware` is not in the AI-integration drop-list, and
    a `capture_message` carries no exception frames for that rule to
    match on.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os

import sentry_sdk

from app.auth import AuthedUser
from app.db import supabase_for_user

logger = logging.getLogger(__name__)

# Default cap per DESIGN.md §11.2. At ~19K tokens/turn that's roughly 10
# chat turns/day — enough for normal use, bounded enough that one user
# running a chat loop overnight can't 10x the month's bill. Operators
# override via `CHAT_USAGE_CAP_TOKENS_PER_DAY` without a code change.
DEFAULT_DAILY_CAP_TOKENS = 200_000


class UsageCapExceeded(Exception):
    """Raised by `assert_within_usage_cap` when today's tokens >= cap.

    Carries the structured payload the route handler returns verbatim
    as the response body so Day 10's UI can render the cap treatment
    from UX frame 16 without rebuilding the shape.
    """

    code = "DAILY_CAP_EXCEEDED"
    message = "You've used your daily AI quota — resets at midnight UTC."

    def __init__(self, used: int, cap: int) -> None:
        """Support the instance."""
        super().__init__(f"daily cap exceeded: used={used} cap={cap}")
        self.used = used
        self.cap = cap


class ProviderRateLimited(Exception):
    """Anthropic 429'd us twice. The route handler maps this to 503 with
    `AI_PROVIDER_RATE_LIMITED`. Distinct from `UsageCapExceeded` (which
    is the *user's* daily cap, a 429 from us)."""

    code = "AI_PROVIDER_RATE_LIMITED"
    message = "AI provider is temporarily overloaded — try again in a minute."


def assert_within_usage_cap(user: AuthedUser) -> None:
    """Raise `UsageCapExceeded` if this user is already at/over the cap.

    Called once at the start of `run_turn`. By design we do NOT check
    again mid-turn — once a turn begins, finishing it is cheaper than
    aborting and explaining, and the overshoot is bounded at one turn.
    See DESIGN.md §7.3 + the Day 9a prompt for the trade-off rationale.
    """
    cap = _daily_cap_tokens()
    used = _today_chat_tokens_used(user)
    if used >= cap:
        _alert_daily_cap_reached(user, used=used, cap=cap)
        raise UsageCapExceeded(used=used, cap=cap)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _daily_cap_tokens() -> int:
    """Read the cap from env on every call so tests / operators can flip
    it without a process restart. Falls back to the DESIGN.md §11.2
    default; malformed values fall back too rather than crashing the
    request path."""
    raw = os.environ.get("CHAT_USAGE_CAP_TOKENS_PER_DAY")
    if not raw:
        return DEFAULT_DAILY_CAP_TOKENS
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_DAILY_CAP_TOKENS

def _alert_daily_cap_reached(user: AuthedUser, *, used: int, cap: int) -> None:
    """Emit a Sentry alert that this user just hit the daily token cap.

    Why this exists: a cap hit is otherwise invisible — the user sees
    the quota message, but the operator only learns about it by querying
    `ai_call_log`. At ~10 users a blocked user is a real signal worth a
    push notification (DESIGN.md §14.5; a lighter v1 cousin of the
    §17.6 aggregate "cost alert", which is scaling-plan scope).

    Dedup is delegated to Sentry's fingerprint grouping rather than any
    app-side state: every blocked attempt by one user on one UTC day
    shares `fingerprint=[..., user_id, utc_date]`, so Sentry collapses
    them into a single issue (one alert per user per day; the next day's
    date opens a fresh issue). No counter table, no per-attempt spam.

    The token counts (`used`, `cap`) are not financial PII, so they ride
    in `extra`; `user_id` is already on the Sentry user scope via
    `app/auth.py`'s `set_user`. Wrapped in a broad `except` because the
    request is already failing closed — an alerting hiccup must never
    turn a clean 429-style cap response into a 500.
    """
    try:
        utc_date = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
        with sentry_sdk.new_scope() as scope:
            scope.level = "warning"
            scope.fingerprint = ["chat-daily-cap", str(user.user_id), utc_date]
            scope.set_extra("used_tokens", used)
            scope.set_extra("cap_tokens", cap)
            scope.set_extra("utc_date", utc_date)
            sentry_sdk.capture_message("Daily chat token cap reached")
    except Exception:  # noqa: BLE001 — alerting must not break the request path
        logger.warning("failed to emit daily-cap Sentry alert", exc_info=True)


def _today_chat_tokens_used(user: AuthedUser) -> int:
    """Sum today's `chat_turn` input+output tokens for this user.

    Hits raw `ai_call_log` — never the daily rollup (`ai_call_log_daily`
    is the >=90-day archive per §14.1; today's data is never there, so
    querying the rollup for today silently returns 0). The UTC-midnight
    cutoff matches the user-facing "resets at midnight UTC" copy.

    `task_type='chat_turn'` filter is load-bearing — Gemini categorization
    rows (`task_type='categorization'`) are written from inside
    propose-transaction tools and would otherwise count against the chat
    cap they have no reason to.
    """
    client = supabase_for_user(user.jwt)
    # PostgREST has no SUM(input+output) primitive; fetch the two
    # columns and sum in Python. At v1 scale the per-user daily row
    # count is <100, indexed on (user_id, timestamp DESC) by Day 2.
    resp = (
        client.table("ai_call_log")
        .select("input_tokens, output_tokens")
        .eq("user_id", str(user.user_id))
        .eq("task_type", "chat_turn")
        .gte("timestamp", _utc_midnight_iso())
        .execute()
    )
    rows = resp.data or []
    return sum(int(r["input_tokens"]) + int(r["output_tokens"]) for r in rows)

def _utc_midnight_iso() -> str:
    """ISO timestamp for the start of today in UTC.

    Explicit UTC matches the user-facing copy. Don't fall back to
    `CURRENT_DATE` server-side; that uses the database server's
    timezone, which would silently drift if Postgres is ever moved."""
    midnight = _dt.datetime.combine(
        _dt.datetime.now(_dt.timezone.utc).date(),
        _dt.time.min,
        tzinfo=_dt.timezone.utc,
    )
    return midnight.isoformat()
