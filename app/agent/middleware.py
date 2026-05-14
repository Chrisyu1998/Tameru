"""Middleware around the Claude agent loop — Day 9a.

Two concerns:

  * `assert_within_usage_cap(user)` — hard daily token cap per user.
    Checked once at turn entry (lenient mid-turn — once a turn starts
    it finishes, bounding overshoot to one turn). Per DESIGN.md §11.2
    / §7.3.

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
"""

from __future__ import annotations

import os

from app.auth import AuthedUser
from app.db import supabase_for_user

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
    import datetime as _dt

    midnight = _dt.datetime.combine(
        _dt.datetime.now(_dt.timezone.utc).date(),
        _dt.time.min,
        tzinfo=_dt.timezone.utc,
    )
    return midnight.isoformat()
