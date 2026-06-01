"""Weekly digest cron entry point (DESIGN.md §6.4, §6.6).

Runs as a Railway scheduled service on `0 * * * *` (hourly, on the hour).
Each run sends only to eligible users for whom it is *currently* Monday in
the [09:00, 12:00) local hours of their own `users_meta.timezone` (DESIGN.md
§6.6 — per-user local delivery, decoupled from currency). The three-hour
window is a retry budget: a failed 09:00 send releases its reservation slot
so the 10:00 fire re-attempts, and the UTC-week unique index makes every
fire after the first success a no-op (so no duplicates). An outage lasting
past noon local means the user misses this week — the documented bounded
false-negative. Users with no zone set fall back to America/New_York, so
pre-Day-29 behavior (Monday morning ET) is preserved. Running hourly +
gating on local time is also DST-correct for free: ET 09:00 is 13:00 or
14:00 UTC depending on the season, and the gate handles both without a
schedule change.

OPERATOR NOTE: the Railway cron schedule for the `digest-cron` service must
be `0 * * * *` (was `0 14 * * 1`). Most hourly runs send to zero users and
exit 0 cleanly; the cost is ~168 sub-second container spin-ups per week,
negligible at v1 scale.

Iterates eligible users, composes + renders + sends the digest, writes
`email_log` (idempotent via the partial unique index — still keyed on the
UTC week, which remains a correct once-per-week guard because each user is
attempted only at their single local-09:00 hour), and logs the Sonnet call
to `ai_call_log` under `task_type='digest'`.

Service-role posture (CLAUDE.md invariant 1, third sanctioned caller):
this module is the only place that imports `supabase_admin` for the
digest path. `app/services/digest.py` takes the client as a parameter
and never imports admin itself, so the leak guard still passes the
service-layer file directly.

CLI:
  python -m app.cron.digest                  # production batch (gated to Mon 09:00 local)
  python -m app.cron.digest --user <uuid>    # one recipient, bypasses the send-window gate
  python -m app.cron.digest --dry-run        # compose + render, no send/log, any time
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from app.db import supabase_admin
from app.integrations.resend import send_digest_email
from app.services.digest import (
    DEFAULT_DIGEST_TZ_NAME,
    SONNET_PROMPT_VERSION,
    SonnetCallLog,
    compose_digest,
    digest_model,
    render_email,
    sonnet_prompt_hash,
)
from app.util.unsubscribe import make_unsubscribe_token

logger = logging.getLogger(__name__)

_DIGEST_KIND = "digest"
# Per-user local send window (DESIGN.md §6.6): Monday (weekday 0), the hours
# [09:00, 12:00) — i.e. 09:00, 10:00, 11:00. The cron fires at minute 0 each
# hour, so the user gets up to three attempts across Monday morning. The
# reserve-then-release pattern makes the first *successful* send claim the
# week's slot (so 10:00/11:00 then no-op via the UTC-week unique index),
# while a *failed* 09:00 send releases its slot so the 10:00 fire retries.
# Capped at noon so a Monday-morning outage retries but the digest never
# arrives as a "good morning" recap in the afternoon/evening; a >3h outage
# means the user misses this week (the documented bounded false-negative).
_SEND_LOCAL_WEEKDAY = 0
_SEND_LOCAL_HOUR_START = 9
_SEND_LOCAL_HOUR_END = 12  # exclusive


@dataclass
class SendReport:
    """Per-run summary the caller can assert against in tests."""
    eligible: int = 0
    sent: int = 0
    skipped_already_sent: int = 0
    skipped_off_schedule: int = 0
    failed: int = 0


def send_weekly_digests(
    *,
    only_user_id: UUID | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> SendReport:
    """Iterate eligible users and send the digest to each.

    RESERVATION PATTERN (the actual duplicate-send guard):

      1. RESERVE a success=true row in email_log BEFORE calling Resend.
         The partial unique index `email_log_dedup_week_uniq` makes this
         insert fail (ON CONFLICT DO NOTHING returns empty) if any
         success=true row already exists for this (user, kind,
         local-Monday-week — see `_local_week_monday`).
      2. If the reservation conflicts, skip — another worker or a
         previous run already claimed this user's slot for this week.
      3. Compose + send.
      4. On send failure: UPDATE the reserved row to success=false,
         which removes it from the partial index and *releases* the
         slot for retry within the same week.
      5. On send success: UPDATE the reserved row with the Resend
         message_id (the webhook's lookup key).

    Why reserve first and not last: if the cron writes the log row only
    AFTER `send_digest_email` returns, a worker crash between accept-by-
    Resend and the log INSERT — or two overlapping cron runs that both
    pass a pre-check — produces a duplicate email. A duplicate weekly
    digest is exactly the kind of thing that generates spam complaints,
    which the webhook then suppresses the user for — bad spiral.

    The tradeoff: a crash AFTER reservation but BEFORE send produces a
    false-positive lock (the user gets no digest this week). At v1 scale
    (~10 users, one cron) that's preferable to duplicate sends. A
    stale-reservation reaper is the path if v1 ever sees this in
    practice.

    `dry_run=True` composes + renders and prints to stdout. It does
    NOT reserve, send, or log — useful for manual UAT without touching
    the email_log or burning a Sonnet call against a real user.

    SEND-WINDOW GATE (DESIGN.md §6.6): the scheduled batch sends to a user
    only when it is currently Monday 09:00 in that user's timezone. The
    gate is bypassed for `dry_run` (prints any time) and for a single-user
    manual run (`only_user_id` set) or explicit `force=True`, so UAT works
    on any weekday/hour.
    """
    admin = supabase_admin()
    report = SendReport()
    now_utc = datetime.now(timezone.utc)
    # A targeted or forced invocation is a manual action — skip the clock
    # gate so the operator isn't constrained to Monday morning.
    apply_gate = not force and only_user_id is None

    rows = _read_eligible(admin, only_user_id=only_user_id)
    for row in rows:
        report.eligible += 1
        user_id = UUID(row["id"])
        email = row["email"]

        if apply_gate and not dry_run and not _is_within_send_window(
            row.get("timezone"), now_utc
        ):
            report.skipped_off_schedule += 1
            continue

        if dry_run:
            try:
                payload, _call_log = compose_digest(admin, user_id)
                unsubscribe_url, _mailto = _unsubscribe_urls(user_id)
                rendered = render_email(
                    payload, unsubscribe_url, app_cta_url=_app_cta_url()
                )
                print(f"--- DRY RUN: {email} ---")
                print(f"Subject: {rendered.subject}")
                print()
                print(rendered.text)
                print(f"--- HTML ({len(rendered.html)} chars) ---")
                print(rendered.html)
            except Exception as exc:
                logger.exception(
                    "dry-run compose/render failed",
                    extra={"user_id": str(user_id), "error_class": type(exc).__name__},
                )
            continue

        # Step 1: reserve the weekly slot BEFORE sending. ON CONFLICT
        # DO NOTHING against the partial unique index returns an empty
        # set when the slot is already taken. The dedup key is the user's
        # LOCAL Monday date (DESIGN.md §6.6) — invariant across the three
        # retry fires and across a mid-week tz change, and (unlike the UTC
        # week) correct for zones east of UTC+9 where Monday 09:00 local
        # falls on Sunday UTC.
        dedup_week = _local_week_monday(row.get("timezone"), now_utc)
        reserved_id = _reserve_slot(admin, user_id=user_id, dedup_week=dedup_week)
        if reserved_id is None:
            report.skipped_already_sent += 1
            logger.info(
                "digest skipped: weekly slot already reserved",
                extra={"user_id": str(user_id)},
            )
            continue

        # PRE-SEND WORK. A failure here means nothing left our system —
        # release the slot so a retry can take it within the same week.
        try:
            payload, call_log = compose_digest(admin, user_id)
            unsubscribe_url, unsubscribe_mailto = _unsubscribe_urls(user_id)
            rendered = render_email(
                payload, unsubscribe_url, app_cta_url=_app_cta_url()
            )
        except Exception as exc:
            report.failed += 1
            logger.exception(
                "digest pre-send failed",
                extra={"user_id": str(user_id), "error_class": type(exc).__name__},
            )
            try:
                _release_reservation(
                    admin, reserved_id=reserved_id, error_code=type(exc).__name__
                )
            except Exception:
                logger.exception(
                    "release_reservation failed after pre-send exception",
                    extra={"user_id": str(user_id)},
                )
            continue

        # SEND. The wrapper's documented contract is never-raises (it
        # converts every SDK exception to ResendSendResult(success=False)).
        # If it raises anyway — wrapper bug, dependency change — we DO
        # NOT release the slot. The message may or may not have reached
        # Resend; the conservative choice is hold the slot (one missed
        # week is fixable; a duplicate triggers spam complaints which
        # the webhook then suppresses permanently). Per the Codex P2
        # rule: no post-send-line failure releases the reservation.
        try:
            send_result = send_digest_email(
                to=email,
                subject=rendered.subject,
                html=rendered.html,
                text=rendered.text,
                list_unsubscribe_url=unsubscribe_url,
                list_unsubscribe_mailto=unsubscribe_mailto,
                idempotency_key=_idempotency_key(user_id=user_id, payload=payload),
            )
        except Exception as exc:
            report.failed += 1
            logger.exception(
                "send_digest_email raised (contract is never-raises); "
                "slot remains held conservatively",
                extra={"user_id": str(user_id), "error_class": type(exc).__name__},
            )
            # ai_call_log is best-effort; do NOT release.
            _safe_log_ai_call(admin, user_id=user_id, call_log=call_log)
            continue

        if not send_result.success:
            # Resend explicitly rejected the message — nothing reached
            # the recipient. Release the slot so a same-week retry can
            # take it. Best-effort ai_call_log write (the Sonnet call
            # did happen and we still want the cost row).
            _safe_log_ai_call(admin, user_id=user_id, call_log=call_log)
            try:
                _release_reservation(
                    admin, reserved_id=reserved_id, error_code=send_result.error_code
                )
            except Exception:
                logger.exception(
                    "release_reservation failed after Resend rejection",
                    extra={"user_id": str(user_id)},
                )
            report.failed += 1
            logger.error(
                "digest send failed",
                extra={
                    "user_id": str(user_id),
                    "error_code": send_result.error_code,
                },
            )
            continue

        # POST-SEND WORK. Resend accepted the message — it may already
        # have hit the recipient's mailbox. From here on, ANY failure
        # MUST keep the slot held (success=true). Releasing it would let
        # the next cron run send a duplicate, which is exactly what the
        # reservation pattern exists to prevent (Codex 2026-05-23 P2:
        # broad except releasing after a successful send defeats the
        # entire idempotency guarantee).
        report.sent += 1

        _safe_log_ai_call(admin, user_id=user_id, call_log=call_log)

        try:
            _finalize_reservation_success(
                admin,
                reserved_id=reserved_id,
                provider_message_id=send_result.message_id,
            )
        except Exception:
            # The slot row stays in place with msg_id=NULL. The email
            # already shipped; we just can't link a future bounce
            # webhook to this row. Log loudly — this is the bounded
            # failure mode the reservation pattern documents.
            logger.exception(
                "finalize_reservation_success failed; email shipped but "
                "provider_message_id not stamped (slot remains held)",
                extra={"user_id": str(user_id)},
            )

    logger.info(
        "digest run complete",
        extra={
            "eligible": report.eligible,
            "sent": report.sent,
            "skipped_already_sent": report.skipped_already_sent,
            "skipped_off_schedule": report.skipped_off_schedule,
            "failed": report.failed,
        },
    )
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — `python -m app.cron.digest [--user X] [--dry-run]`."""
    parser = argparse.ArgumentParser(description="Send the weekly Tameru digest.")
    parser.add_argument(
        "--user",
        type=UUID,
        default=None,
        help="Restrict the run to a single user_id (UUID). Used for manual UAT.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compose and render but skip Resend + email_log + ai_call_log writes.",
    )
    args = parser.parse_args(argv)

    # Configure logging — when running as `python -m`, the FastAPI
    # lifespan that wires JSON logging doesn't fire. Use a simple
    # stdout handler so cron logs land in Railway's log viewer.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    report = send_weekly_digests(only_user_id=args.user, dry_run=args.dry_run)
    logger.info(
        "digest CLI exit",
        extra={
            "eligible": report.eligible,
            "sent": report.sent,
            "skipped_already_sent": report.skipped_already_sent,
            "skipped_off_schedule": report.skipped_off_schedule,
            "failed": report.failed,
        },
    )
    return 0 if report.failed == 0 else 1


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _read_eligible(admin, *, only_user_id: UUID | None) -> list[dict]:
    """Return one row per eligible user: id, email, timezone.

    `timezone` is the user's IANA zone or None (the caller's send-window
    gate falls back to the default zone when None).

    Eligibility:
      - confirmed email,
      - not soft-deleted,
      - users_meta.weekly_digest_enabled = true,
      - at least one `active` transaction in the past 4 weeks.

    Implemented via supabase-py's PostgREST API rather than raw SQL
    because the admin client doesn't expose `client.rpc` for arbitrary
    SELECT statements without a defined function. We fetch a small set
    of candidates (users_meta with the flag set) and then filter in
    Python for activity + email confirmation — at v1's ~10 users this
    is two RPCs and a small loop; promote to a SECURITY DEFINER RPC if
    the user base grows.
    """
    # Step 1: users_meta with the flag enabled (carry timezone through for
    # the caller's per-user send-window gate).
    meta_query = (
        admin.table("users_meta")
        .select("user_id, timezone")
        .eq("weekly_digest_enabled", True)
    )
    if only_user_id is not None:
        meta_query = meta_query.eq("user_id", str(only_user_id))
    meta_resp = meta_query.execute()
    tz_by_id = {r["user_id"]: r.get("timezone") for r in (meta_resp.data or [])}
    candidate_ids = list(tz_by_id.keys())
    if not candidate_ids:
        return []

    # Step 2: pull auth.users rows for those ids (Supabase admin API).
    # The supabase-py admin client surfaces auth.users via list_users(),
    # which is paginated. At v1 scale we list all and filter; promote to
    # a SECURITY DEFINER RPC that joins the two tables when the user
    # base outgrows one page.
    auth_users = _list_auth_users(admin)
    by_id = {u["id"]: u for u in auth_users}

    # Step 3: per-candidate activity check.
    eligible: list[dict] = []
    for uid in candidate_ids:
        user = by_id.get(uid)
        if not user:
            continue
        if not user.get("email"):
            continue
        if not user.get("email_confirmed_at"):
            continue
        if user.get("deleted_at"):
            continue
        if not _has_recent_activity(admin, UUID(uid)):
            continue
        eligible.append(
            {"id": uid, "email": user["email"], "timezone": tz_by_id.get(uid)}
        )
    return eligible


def _is_within_send_window(tz_name: str | None, now_utc: datetime) -> bool:
    """True iff it is currently Monday in the [09:00, 12:00) hours of `tz_name`.

    The three-hour window (09:00 / 10:00 / 11:00 fires) is the retry budget:
    a failed 09:00 send releases its reservation slot and the 10:00 fire
    re-attempts; once a send succeeds, the UTC-week unique index makes the
    remaining fires no-op. `tz_name` None or unresolvable → the default
    digest zone, so users with no zone set keep getting the digest Monday
    morning ET (the historical time). DESIGN.md §6.6.
    """
    try:
        tz = ZoneInfo(tz_name) if tz_name else ZoneInfo(DEFAULT_DIGEST_TZ_NAME)
    except Exception:
        tz = ZoneInfo(DEFAULT_DIGEST_TZ_NAME)
    local = now_utc.astimezone(tz)
    return (
        local.weekday() == _SEND_LOCAL_WEEKDAY
        and _SEND_LOCAL_HOUR_START <= local.hour < _SEND_LOCAL_HOUR_END
    )


def _local_week_monday(tz_name: str | None, now_utc: datetime) -> str:
    """The Monday (ISO date string) of `now_utc`'s week in `tz_name`.

    This is the digest's idempotency key (DESIGN.md §6.6). It is invariant
    across the three Monday-morning retry fires and across a mid-week
    timezone change — and, unlike the UTC week of the send instant, it is
    correct for zones east of UTC+9 where Monday 09:00 local is Sunday UTC
    (e.g. Australia/Sydney: all three fires resolve to the same local
    Monday). `tz_name` None or unresolvable → the default digest zone.
    """
    try:
        tz = ZoneInfo(tz_name) if tz_name else ZoneInfo(DEFAULT_DIGEST_TZ_NAME)
    except Exception:
        tz = ZoneInfo(DEFAULT_DIGEST_TZ_NAME)
    local = now_utc.astimezone(tz)
    return (local - timedelta(days=local.weekday())).date().isoformat()


def _list_auth_users(admin) -> list[dict]:
    """Return all auth.users via the admin API.

    Paginated under the hood; we walk pages until empty. At v1 (~10
    users) this is one page.
    """
    users: list[dict] = []
    page = 1
    while True:
        resp = admin.auth.admin.list_users(page=page, per_page=1000)
        # The SDK returns a list of User objects or a dict depending on
        # version; normalize to plain dicts with the fields we read.
        batch = _normalize_users(resp)
        if not batch:
            break
        users.extend(batch)
        if len(batch) < 1000:
            break
        page += 1
    return users


def _normalize_users(resp) -> list[dict]:
    """Flatten supabase-py's User objects (or dicts) to the fields we need."""
    raw = resp if isinstance(resp, list) else getattr(resp, "users", None) or []
    out: list[dict] = []
    for u in raw:
        if isinstance(u, dict):
            out.append(
                {
                    "id": u.get("id"),
                    "email": u.get("email"),
                    "email_confirmed_at": u.get("email_confirmed_at"),
                    "deleted_at": u.get("deleted_at"),
                }
            )
        else:
            out.append(
                {
                    "id": str(getattr(u, "id", "")),
                    "email": getattr(u, "email", None),
                    "email_confirmed_at": getattr(u, "email_confirmed_at", None),
                    "deleted_at": getattr(u, "deleted_at", None),
                }
            )
    return out


def _has_recent_activity(admin, user_id: UUID) -> bool:
    """True if the user has any active transaction in the past 4 weeks."""
    cutoff = datetime.now(timezone.utc).date()
    cutoff_str = (cutoff - timedelta(weeks=4)).isoformat()
    resp = (
        admin.table("transactions")
        .select("id")
        .eq("user_id", str(user_id))
        .eq("status", "active")
        .gte("date", cutoff_str)
        .limit(1)
        .execute()
    )
    return bool(resp.data)


def _reserve_slot(admin, *, user_id: UUID, dedup_week: str) -> str | None:
    """Reserve the weekly slot for (user_id, digest) BEFORE calling Resend.

    Inserts an email_log row with success=true and no provider_message_id
    yet, via the idempotent RPC. The partial unique index on
    `(user_id, kind, dedup_week) WHERE success AND dedup_week IS NOT NULL`
    makes this INSERT conflict-and-skip when a successful row already
    exists for the week. `dedup_week` is the recipient's LOCAL Monday date
    (ISO string) — see `_local_week_monday`. Returns the new row's id on
    success, or None when the conflict path fired (the caller skips).

    A reserved row that never gets finalized (compose throws, Resend
    times out, worker crashes mid-send) sits as success=true with
    provider_message_id=NULL. That blocks any further send for the week
    — chosen tradeoff vs. duplicate sends. The `_release_reservation`
    path flips success=false in known-failure cases so the slot frees up
    within the same week.
    """
    resp = admin.rpc(
        "email_log_insert_idempotent",
        {
            "p_user_id": str(user_id),
            "p_kind": _DIGEST_KIND,
            "p_success": True,
            "p_provider_message_id": None,
            "p_error_code": None,
            "p_dedup_week": dedup_week,
        },
    ).execute()
    if not resp.data:
        return None
    return resp.data[0]["id"]


def _finalize_reservation_success(
    admin,
    *,
    reserved_id: str,
    provider_message_id: str | None,
) -> None:
    """Stamp the Resend message_id on the reserved row after a successful send.

    The provider_message_id is the webhook's lookup key for bounce or
    complaint events. Without it, a hard bounce can't be tied back to
    the row and the user won't be suppressed on the next run.
    """
    admin.table("email_log").update(
        {"provider_message_id": provider_message_id}
    ).eq("id", reserved_id).execute()


def _release_reservation(
    admin,
    *,
    reserved_id: str,
    error_code: str | None,
) -> None:
    """Release a reserved slot after a failed send so a retry can take it.

    Flips success=true → success=false. The row falls out of the partial
    unique index (predicate is `WHERE success`), so a subsequent cron run
    in the same week can reserve again. error_code is stamped for the
    audit trail in email_log.
    """
    admin.table("email_log").update(
        {"success": False, "error_code": error_code}
    ).eq("id", reserved_id).execute()


def _unsubscribe_urls(user_id: UUID) -> tuple[str, str]:
    """Build the per-user (https_url, mailto) pair for List-Unsubscribe.

    The URL must point at the FastAPI backend, not the Vercel frontend:
    `/unsubscribe` is a FastAPI route, and the SPA's catch-all rewrite
    would otherwise serve `index.html` for the path and Gmail's one-click
    POST would never reach the suppression handler. `BACKEND_PUBLIC_URL`
    is the public hostname of the Railway-hosted backend (e.g.
    `https://tameru-production.up.railway.app`); the request-serving
    process fails fast at boot if it's unset.
    """
    base = os.environ.get("BACKEND_PUBLIC_URL")
    if not base:
        raise RuntimeError("BACKEND_PUBLIC_URL is not set.")
    base = base.rstrip("/")
    token = make_unsubscribe_token(user_id, "digest")
    https = f"{base}/unsubscribe?user={user_id}&kind=digest&token={token}"
    mailto_subject = f"user={user_id}+kind=digest+token={token}"
    mailto = f"mailto:unsubscribe@mail.tameru.xyz?subject={mailto_subject}"
    return https, mailto


def _app_cta_url() -> str:
    """Build the "View this week in Tameru" CTA URL (Day 26b).

    Points at the Vercel PWA host (FRONTEND_ORIGIN), NOT
    BACKEND_PUBLIC_URL — the CTA lands the user on the SPA at `/`.
    `?source=digest` is what the PWA landing handler reads to fire the
    `weekly_digest_opened` PostHog event, then strips.

    `.rstrip("/")` normalizes a trailing slash so we don't emit
    `https://x.xyz//?source=digest`. Lazy read matches the
    `_unsubscribe_urls` pattern; the cron is a separate Railway service
    so `app/main.py`'s `_REQUIRED_ENV_VARS` lifespan check doesn't
    protect it — this is where the cron's fail-loud lives.
    """
    base = os.environ.get("FRONTEND_ORIGIN", "").rstrip("/")
    if not base:
        raise RuntimeError("FRONTEND_ORIGIN is not set for the digest cron.")
    return f"{base}/?source=digest"


def _safe_log_ai_call(admin, *, user_id: UUID, call_log: SonnetCallLog) -> None:
    """Wrap `_log_ai_call_admin` so its failures NEVER propagate.

    Called only AFTER `send_digest_email` has returned. If Resend
    accepted the message, an exception from the audit insert must not
    bubble up — the outer loop would catch it and we'd lose track of
    the post-send semantic boundary. Log loudly; the missing row is a
    bounded cost-accounting gap, not a duplicate-send risk.
    """
    try:
        _log_ai_call_admin(admin, user_id=user_id, call_log=call_log)
    except Exception:
        logger.exception(
            "ai_call_log write failed; digest already shipped or rejected",
            extra={"user_id": str(user_id)},
        )


def _idempotency_key(*, user_id: UUID, payload) -> str:
    """Build a stable per-(user, week) key for Resend's Idempotency-Key header.

    Resend dedupes by this key for ~24h on their side: if the SDK's
    underlying urllib3 retries the POST after a transient network error
    (we don't control its retry policy and it doesn't coordinate with
    our DB lock), Resend returns the cached response instead of creating
    a second send. Closes the in-flight retry vector that our partial
    unique index can't see.

    Format `digest:{user_id}:{week_start_date}` — deterministic from the
    payload the cron is about to send. Same user + same week → same
    key, regardless of how many SDK retries fire inside one send call.
    """
    return f"digest:{user_id}:{payload.week_start.date().isoformat()}"


def _log_ai_call_admin(admin, *, user_id: UUID, call_log: SonnetCallLog) -> None:
    """Write the Sonnet call to ai_call_log under service role (invariant 14).

    This is the system-level write path documented in CLAUDE.md invariant
    14 — `app/integrations/aicalllog.py::log_ai_call` is the JWT-bearing
    request-handler path and cannot be used here because the cron holds
    no user JWT. The payload shape matches exactly so per-user cost math
    works the same regardless of which path emitted the row.
    """
    payload = {
        "user_id": str(user_id),
        "provider": "anthropic",
        "model": digest_model(),
        "task_type": _DIGEST_KIND,
        "prompt_version": SONNET_PROMPT_VERSION,
        "prompt_hash": sonnet_prompt_hash(),
        "input_tokens": call_log.input_tokens,
        "output_tokens": call_log.output_tokens,
        "latency_ms": call_log.latency_ms,
        "success": call_log.success,
        "error_code": call_log.error_code,
    }
    admin.table("ai_call_log").insert(payload).execute()


if __name__ == "__main__":
    raise SystemExit(main())
