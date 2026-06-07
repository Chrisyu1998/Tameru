"""Admin observability surface — Day 24 (DESIGN.md §14, §14.5).

Read-only summary of recent AI token usage, gated to membership in the
`admins` table (migration `20260522130300_ai_call_log_admin_select.sql`).
v1 ships exactly one admin endpoint and no admin UI — a Supabase
dashboard SQL query is the second-line debugging path for anything
this doesn't cover.

Gating choice: a small `admins` table is the single source of truth.
An earlier draft used an `ADMIN_USER_IDS` env var + a `app.admin_user_ids`
postgres setting, both of which had to be kept in sync; the postgres
setting also required `ALTER DATABASE`, which Supabase Free tier
denies. A table works on every tier, survives restarts, and is the
same source of truth for both the route's admittance check (here)
*and* the cross-user SELECT policy on `ai_call_log`. There is no env
var to forget to set.

Non-admin callers receive a 404, not a 403 — minimizing endpoint
disclosure to attackers who probe for admin routes.

Read posture: queries `ai_call_log` directly under the admin's JWT.
Cross-user visibility comes from the migration above, which adds an
admin SELECT policy that returns true when the caller's `auth.uid()`
appears in the `admins` table.

To add yourself as admin (run once in Supabase SQL Editor):
  INSERT INTO admins (user_id) VALUES ('<your-uuid>');
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth import AuthedUser, get_current_user_jwt
from app.db import supabase_for_user

router = APIRouter(prefix="/admin", tags=["admin"])


class AICallSummaryRow(BaseModel):
    """One aggregate row.

    Grouped by (provider, model, task_type) by default; when the caller
    passes `group_by_user=true`, `user_id` is also part of the key and
    is populated here. In the un-grouped view it is `None` and the route
    omits it from the response (`response_model_exclude_none`), so that
    response stays byte-identical to its pre-`user_id` shape. `user_id`
    answers the operational "who is heavy?" question that the un-grouped
    totals cannot.

    `count`: number of `ai_call_log` rows in the window.
    `error_count`: subset where `success = false`.
    Token sums are post-aggregation totals for the window.
    """

    user_id: str | None = None
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
    """FastAPI dep: 404 any caller not in the `admins` table.

    Queries `admins` under the caller's JWT. The table's RLS lets
    every user see only their own admins row (or none), so this is a
    presence check disguised as a SELECT: empty result → 404, one row
    → admin. No cross-user disclosure even if a non-admin probes.

    Returns 404 (not 403) to minimize endpoint disclosure — a prober
    cannot distinguish "this route does not exist" from "this route
    exists but I'm not allowed."
    """
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("admins")
        .select("user_id")
        .eq("user_id", str(user.user_id))
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return user


# PostgREST caps every SELECT at `max-rows` (1000 on Supabase by default),
# silently — no error when truncated. The per-user "who is heavy?" view is
# only correct if it sees every row, so the fetch pages explicitly until a
# short page signals the end. Page size matches the server cap.
_PAGE_SIZE = 1000


@router.get(
    "/aicalls/summary",
    response_model=AICallSummaryResponse,
    # Keep the un-grouped response byte-identical to its pre-`user_id` shape:
    # with `exclude_none`, the new optional `user_id` key is omitted entirely
    # when null (the un-grouped path) and present only in the grouped view
    # (where it is always a non-null string). No other field is ever None.
    response_model_exclude_none=True,
)
def get_aicalls_summary(
    days: int = Query(7, ge=1, le=90, description="Window size in days (1-90)."),
    group_by_user: bool = Query(
        False,
        description="Also break the totals down per user_id (answers 'who is heavy?').",
    ),
    user: AuthedUser = Depends(require_admin),
) -> AICallSummaryResponse:
    """Return token usage by (provider, model, task_type) for the window.

    Window is `[now - days, now]`. Always inside the 90-day hot retention
    window for `ai_call_log` (§14.1), so the rollup table is never
    consulted. Aggregation runs in Python because PostgREST GROUP BY
    requires a database view (§8.8 has no admin view).

    With `group_by_user=true` the key gains `user_id` and the rows sort by
    total tokens descending (so the heaviest user surfaces first); without
    it the shape is unchanged and rows sort by `count` descending. The
    cross-user rows come from the admin SELECT policy on `ai_call_log`
    (migration `20260522130300`).
    """
    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(days=days)
    client = supabase_for_user(user.jwt)
    raw = _fetch_window_rows(client, window_start, window_end, by_user=group_by_user)
    rows = _aggregate(raw, by_user=group_by_user)
    return AICallSummaryResponse(
        window_start=window_start,
        window_end=window_end,
        rows=rows,
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _fetch_window_rows(
    client, window_start: datetime, window_end: datetime, *, by_user: bool
) -> list[dict]:
    """Page through `ai_call_log` for the window, returning every row.

    `user_id` is selected only when needed for grouping — keeping the
    un-grouped path's payload identical to before. Paginates via `.range()`
    because a single SELECT is silently capped at `_PAGE_SIZE`; without
    paging a busy week would under-count and the per-user totals would lie.
    """
    columns = "provider, model, task_type, input_tokens, output_tokens, success"
    if by_user:
        columns = "user_id, " + columns
    out: list[dict] = []
    offset = 0
    while True:
        resp = (
            client.table("ai_call_log")
            .select(columns)
            .gte("timestamp", window_start.isoformat())
            .lte("timestamp", window_end.isoformat())
            .range(offset, offset + _PAGE_SIZE - 1)
            .execute()
        )
        page = resp.data or []
        out.extend(page)
        if len(page) < _PAGE_SIZE:
            return out
        offset += _PAGE_SIZE


def _aggregate(rows: list[dict], *, by_user: bool) -> list[AICallSummaryRow]:
    """Group raw `ai_call_log` rows into `AICallSummaryRow` instances.

    Key is (provider, model, task_type), plus `user_id` when `by_user`.
    Sorts by total tokens descending in the per-user view (the heaviest
    user is the point of the breakdown) and by `count` descending
    otherwise (preserving the pre-existing un-grouped contract).
    """
    buckets: dict[tuple, dict] = {}
    for row in rows:
        uid = row.get("user_id") if by_user else None
        key = (uid, row["provider"], row["model"], row["task_type"])
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
            user_id=str(uid) if uid is not None else None,
            provider=provider,
            model=model,
            task_type=task_type,
            **bucket,
        )
        for (uid, provider, model, task_type), bucket in buckets.items()
    ]
    if by_user:
        out.sort(key=lambda r: r.sum_input_tokens + r.sum_output_tokens, reverse=True)
    else:
        out.sort(key=lambda r: r.count, reverse=True)
    return out
