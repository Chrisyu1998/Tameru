"""Admin observability surface — Day 24 (DESIGN.md §14, §14.5).

Read-only summary of recent AI token usage, gated to the env-configured
allowlist `ADMIN_USER_IDS`. v1 ships exactly one admin endpoint and no
admin UI — a Supabase dashboard SQL query is the second-line debugging
path for anything this doesn't cover.

Gating choice: an env-var allowlist (parsed each call) rather than a
`users_meta.is_admin` column. At v1 scale there is one admin (the
author), so the schema bloat does not pay rent. Non-admin callers
receive a 404, not a 403 — minimizing endpoint disclosure to attackers
who probe for admin routes.

Read posture: queries `ai_call_log` directly under the admin's JWT.
Cross-user visibility comes from migration
`20260522130300_ai_call_log_admin_select.sql`, which adds an admin
SELECT policy that passes when `auth.uid()` appears in the
`app.admin_user_ids` postgres custom setting. Two configurations must
agree at deploy time:
  * `ADMIN_USER_IDS` env var on Railway (gates the FastAPI route).
  * `app.admin_user_ids` postgres setting in the Supabase dashboard
    (widens RLS so the cross-user SELECT actually returns rows).
If only the env var is set, the route admits the admin but RLS still
scopes them to their own rows — a sandboxed default. If only the
postgres setting is set, the route 404s and the RLS widening is
unreachable from the admin endpoint anyway.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth import AuthedUser, get_current_user_jwt
from app.db import supabase_for_user

router = APIRouter(prefix="/admin", tags=["admin"])


class AICallSummaryRow(BaseModel):
    """One (provider, model, task_type) aggregate row.

    `count`: number of `ai_call_log` rows in the window.
    `error_count`: subset where `success = false`.
    Token sums are post-aggregation totals for the window.
    """

    provider: str
    model: str
    task_type: str
    count: int = Field(..., ge=0)
    sum_input_tokens: int = Field(..., ge=0)
    sum_output_tokens: int = Field(..., ge=0)
    error_count: int = Field(..., ge=0)


class AICallSummaryResponse(BaseModel):
    """Wire shape for `GET /admin/aicalls/summary`.

    `window_start`/`window_end` echo the resolved time window so a
    client cannot misread `days` as e.g. calendar-day boundaries.
    """

    window_start: datetime
    window_end: datetime
    rows: list[AICallSummaryRow]


def require_admin(user: AuthedUser = Depends(get_current_user_jwt)) -> AuthedUser:
    """FastAPI dep: 404 any caller not in `ADMIN_USER_IDS`.

    Reads the env var on each call (not at import time) so a deploy
    that adds an admin id does not require a re-import to take effect.
    Cheap enough — the expected length is 1 at v1 scale.

    Returns 404 (not 403) to minimize endpoint disclosure — a prober
    cannot distinguish "this route does not exist" from "this route
    exists but I'm not allowed."
    """
    if user.user_id not in _admin_user_ids():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return user


@router.get("/aicalls/summary", response_model=AICallSummaryResponse)
def get_aicalls_summary(
    days: int = Query(7, ge=1, le=90, description="Window size in days (1-90)."),
    user: AuthedUser = Depends(require_admin),
) -> AICallSummaryResponse:
    """Return token usage by (provider, model, task_type) for the window.

    Window is `[now - days, now]`. Always inside the 90-day hot retention
    window for `ai_call_log` (§14.1), so the rollup table is never
    consulted. Aggregation runs in Python because PostgREST GROUP BY
    requires a database view (§8.8 has no admin view) and the row count
    is bounded — ~10 users × ~3 task types × 7 days ≈ a few hundred rows.
    """
    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(days=days)
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("ai_call_log")
        .select("provider, model, task_type, input_tokens, output_tokens, success")
        .gte("timestamp", window_start.isoformat())
        .lte("timestamp", window_end.isoformat())
        .execute()
    )
    rows = _aggregate(resp.data or [])
    return AICallSummaryResponse(
        window_start=window_start,
        window_end=window_end,
        rows=rows,
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _admin_user_ids() -> frozenset[UUID]:
    """Parse the `ADMIN_USER_IDS` env var into a typed frozenset.

    Empty / unset → empty set (the route exists but admits no one).
    Comma-separated UUIDs; malformed entries are silently dropped so a
    typo in one entry does not lock out the rest.
    """
    raw = os.environ.get("ADMIN_USER_IDS", "")
    out: set[UUID] = set()
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            out.add(UUID(piece))
        except ValueError:
            continue
    return frozenset(out)


def _aggregate(rows: list[dict]) -> list[AICallSummaryRow]:
    """Group raw `ai_call_log` rows by (provider, model, task_type).

    Returns `AICallSummaryRow` instances sorted by `count` descending so
    the most-used model surfaces first in a dashboard tail.
    """
    buckets: dict[tuple[str, str, str], dict[str, int]] = {}
    for row in rows:
        key = (row["provider"], row["model"], row["task_type"])
        bucket = buckets.setdefault(
            key,
            {"count": 0, "sum_input_tokens": 0, "sum_output_tokens": 0, "error_count": 0},
        )
        bucket["count"] += 1
        bucket["sum_input_tokens"] += int(row.get("input_tokens") or 0)
        bucket["sum_output_tokens"] += int(row.get("output_tokens") or 0)
        if not row.get("success"):
            bucket["error_count"] += 1
    out = [
        AICallSummaryRow(
            provider=provider,
            model=model,
            task_type=task_type,
            **bucket,
        )
        for (provider, model, task_type), bucket in buckets.items()
    ]
    out.sort(key=lambda r: r.count, reverse=True)
    return out
