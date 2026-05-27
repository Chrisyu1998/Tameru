"""User data export — Day 27, DESIGN.md §9.6.

`GET /export` returns the caller's own data as a single JSON object that
the browser saves as `tameru-export-YYYY-MM-DD.json`. The endpoint is the
audit-trail-shaped "give me my data" affordance referenced in the in-app
privacy disclosure on the `/privacy` page and `Settings → Privacy`.

**RLS posture (CLAUDE.md invariant 1):** every read goes through
`supabase_for_user(user.jwt)`. There is no service-role path. A handler
that forgets `WHERE user_id = ?` still cannot leak data because the
`auth.uid() = user_id` policy on each table refuses every other row at
PostgREST.

**v1 scope rationale.** Included: every table that contains user-typed
content or user preferences — the things a person genuinely thinks of as
"my data."

  - transactions, cards, subscriptions — ledger
  - user_memory                          — chat-distilled facts
  - chat_messages                        — full conversation history
  - merchant_category                    — user's category overrides
  - users_meta                           — preferences + home currency

Excluded from v1, deferred to a hypothetical future "full audit export":

  - chat_turn_trace                       — per-turn agent-loop audit
  - ai_call_log, ai_call_log_daily        — AI cost / audit trail
  - email_log                             — Resend send / bounce log

The excluded set is internal observability, not user content. Promoting
any of them into the response is a deliberate scope expansion that
should be discussed before shipping — keep the exclusion list greppable.

**Why no chat tool?** A read-only `export_data()` agent tool wouldn't
trip invariant 8 (which protects writes), but it would bypass the
Settings affordance for no UX gain and cost an `ai_call_log` row + agent
tokens per "export my data" utterance. Settings is the home; chat
answers in prose.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.auth import AuthedUser, get_current_user_with_device
from app.db import supabase_for_user

router = APIRouter(prefix="/export", tags=["export"])

# Schema-version stamp on the dumped object. Bump when the exported shape
# changes in a way a script reading older exports would care about
# (column rename, table drop, semantic re-interpretation of a field).
# Adding a *new* key is a minor bump consumers tolerate; removing one is
# a breaking change worth a major bump and a brief note here.
_EXPORT_SCHEMA_VERSION = 1

# Page size for `_select_all`. Set just under PostgREST's default
# `max-rows` cap of 1000 (`supabase/config.toml` line 18) — a request
# for [0..999] tops out exactly at the cap and lets the "short page =
# done" exit condition fire cleanly when the user has fewer than 1000
# rows in a table. Higher than 1000 would be silently clamped by
# PostgREST, defeating the pagination.
_EXPORT_PAGE_SIZE = 1000


@router.get("")
def export_user_data(
    user: AuthedUser = Depends(get_current_user_with_device),
) -> Response:
    """Return the caller's data as a downloadable JSON file.

    The browser receives `Content-Disposition: attachment` so clicking
    the "Export my data" button triggers a Save-As rather than rendering
    the JSON in-tab. Filename includes the export date so multiple
    downloads on the same day end up as `…-1.json`, `…-2.json` via the
    OS's standard de-dup, not as silent overwrites.

    The response body is a single JSON object whose keys are:

      - `user_id`, `exported_at`, `schema_version` — metadata
      - one array per user-content table (transactions, cards,
        subscriptions, user_memory, chat_messages, merchant_category)
      - `users_meta` — one object (the user's single preference row)
        or `null` for a pre-bootstrap account

    See module docstring for the v1 inclusion / exclusion rationale.

    Performance note: at v1 scale (~10 users, weeks of data each) the
    whole export comfortably fits in one round trip and one JSON
    document. If a single user ever pushes this beyond a few MB, the
    right migration is paginated dumps per table; do not stream from
    here without that pressure.
    """
    client = supabase_for_user(user.jwt)
    payload: dict[str, Any] = {
        "user_id": str(user.user_id),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": _EXPORT_SCHEMA_VERSION,
        "transactions": _select_all(client, "transactions"),
        "cards": _select_all(client, "cards"),
        "subscriptions": _select_all(client, "subscriptions"),
        "user_memory": _select_all(client, "user_memory"),
        "chat_messages": _select_all(client, "chat_messages"),
        "merchant_category": _select_all(client, "merchant_category"),
        "users_meta": _select_one(client, "users_meta", str(user.user_id)),
    }

    body = _json_dumps(payload)
    filename = f"tameru-export-{datetime.now(timezone.utc).date().isoformat()}.json"

    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # Defense in depth: even if a future caller misuses this
            # endpoint by linking to it directly, the no-store hint keeps
            # the dump out of shared caches.
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _select_all(client, table: str) -> list[dict]:
    """Return every row in `table` visible to the caller's JWT.

    Pages through PostgREST's `Range` header until a request returns
    fewer rows than the page size, because PostgREST caps every
    `SELECT` at `max-rows` (1000 by default on Supabase). Without
    pagination a heavy user could ask to export their data, get a
    silently truncated file, and never know — the UI promises every
    transaction/chat message/etc. Codex 2026-05-26.

    RLS scopes each page to the caller's own rows automatically — no
    explicit `WHERE user_id = ...` needed (and one would be redundant
    with the owner policy). Returns an empty list when the user has no
    rows, never None, so downstream JSON serialization stays uniform.
    """
    rows: list[dict] = []
    start = 0
    while True:
        resp = (
            client.table(table)
            .select("*")
            .range(start, start + _EXPORT_PAGE_SIZE - 1)
            .execute()
        )
        page = list(resp.data or [])
        rows.extend(page)
        # A short page is the end-of-results signal — no separate count
        # query needed. An empty page (no rows past the cursor) lands
        # here too and exits cleanly.
        if len(page) < _EXPORT_PAGE_SIZE:
            return rows
        start += _EXPORT_PAGE_SIZE


def _select_one(client, table: str, user_id: str) -> dict | None:
    """Return the single row in a 1:1-per-user table, or None.

    Only `users_meta` has this shape in v1 (PK is `user_id`). Returns
    None for a pre-bootstrap user who hasn't completed the currency
    picker — the export still succeeds, the field is just null.
    """
    resp = (
        client.table(table)
        .select("*")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return rows[0] if rows else None


def _json_dumps(payload: dict) -> str:
    """Serialize the export payload with stable, human-readable JSON.

    Uses indent=2 so a curious user opening the file in a text editor
    can read it. `default=str` covers the handful of types PostgREST
    hands back as Python objects rather than primitives (UUID, datetime,
    Decimal, date) — each has a useful `str()` form.
    """
    import json

    return json.dumps(payload, indent=2, default=str, sort_keys=False)
