"""Shared eval-user provisioning + fixture seeding.

Two scripts (`mint_eval_jwt.py`, `seed_eval_fixtures.py`) and the runner
(`eval.py`) all need to: (1) ensure the eval user exists on the local
Supabase stack, (2) get a fresh JWT for that user, and (3) seed the
deterministic cards/transactions/subscriptions the YAML corpora reference
by name. Putting the logic here keeps the three call sites in lockstep —
a change to the fixture set lands in one place and every caller picks it
up on the next run.

The eval user is `eval@tameru.internal` — a real Supabase user, not a
service-role bypass (CLAUDE.md invariant 1). `ai_call_log` rows
accumulated during eval runs are trimmed weekly by a pg_cron job
(supabase/migrations/20260520120000_trim_eval_user_ai_call_log.sql).

Fixture pinning: the multi-hop YAML rows reference exact dollar values
that depend on the fixture transactions summing to specific totals. If
you change the seed totals here, the matching `expected_answer_value_usd`
values in `evals/multi_hop.yaml` must be updated to match — otherwise
multi-hop's final-answer check will spuriously fail.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import sys
from decimal import Decimal
from typing import Any
from uuid import UUID

from supabase import create_client

EVAL_USER_EMAIL = "eval@tameru.internal"
EVAL_USER_PASSWORD = os.environ.get(
    "EVAL_USER_PASSWORD", "eval-fixture-password-2026"
)

# Card fixtures — referenced by name from chat_extraction.yaml's
# `card_name_resolves_to` field and from multi_hop.yaml's expected tool
# args. Keep the names stable; renaming forces a YAML sweep.
_FIXTURE_CARDS: list[dict[str, Any]] = [
    {
        "name": "Amex Gold",
        "issuer": "amex",
        "network": "amex",
        "program": "MR",
        "last_four": "1001",
        "annual_fee": "325.00",
        "color": "#A07E50",
        "multipliers": {"Dining": 4, "Groceries": 4},
        "source_urls": ["https://americanexpress.com/gold"],
    },
    {
        "name": "Chase Sapphire Reserve",
        "issuer": "chase",
        "network": "visa",
        "program": "UR",
        "last_four": "2002",
        "annual_fee": "550.00",
        "color": "#1A2A4F",
        "multipliers": {"Travel": 3, "Dining": 3},
        "source_urls": ["https://chase.com/sapphire-reserve"],
    },
    {
        "name": "Chase Freedom Unlimited",
        "issuer": "chase",
        "network": "visa",
        "program": "UR",
        "last_four": "3003",
        "annual_fee": "0.00",
        "color": "#0F4C81",
        "multipliers": {},
        "source_urls": ["https://chase.com/freedom-unlimited"],
    },
]

# Transaction fixtures keyed by month so the multi-hop deltas land on
# clean numbers. Totals must match the `expected_answer_value_usd` in
# multi_hop.yaml — see module docstring.
#
# Pinned totals (used by multi_hop expected values):
#   Jan 2026 Dining:    $150.00 (3 rows)
#   Feb 2026 Dining:    $200.00 (4 rows)
#   Mar 2026 Dining:    $250.00 (5 rows)   -> Mar - Feb = $50.00
#   Feb 2026 Groceries: $280.00 (4 rows)
#   Mar 2026 Groceries: $300.00 (4 rows)
#   Feb 2026 Coffee:    $35.00  (5 rows on Amex Gold)
#   Mar 2026 Coffee:    $40.00  (5 rows on Amex Gold)
_FIXTURE_TRANSACTIONS: list[dict[str, Any]] = [
    # January 2026 — Dining (3 rows = $150)
    {"date": "2026-01-05", "merchant": "Nobu Malibu",        "amount": "60.00", "category": "Dining"},
    {"date": "2026-01-14", "merchant": "Sushi Ginza",        "amount": "45.00", "category": "Dining"},
    {"date": "2026-01-22", "merchant": "Ramen Nagi",         "amount": "45.00", "category": "Dining"},
    # February 2026 — Dining (4 rows = $200)
    {"date": "2026-02-03", "merchant": "Nobu Malibu",        "amount": "55.00", "category": "Dining"},
    {"date": "2026-02-10", "merchant": "In-N-Out Burger",    "amount": "15.00", "category": "Dining"},
    {"date": "2026-02-17", "merchant": "Sushi Ginza",        "amount": "80.00", "category": "Dining"},
    {"date": "2026-02-24", "merchant": "Ramen Nagi",         "amount": "50.00", "category": "Dining"},
    # March 2026 — Dining (5 rows = $250)
    {"date": "2026-03-04", "merchant": "Nobu Malibu",        "amount": "70.00", "category": "Dining"},
    {"date": "2026-03-09", "merchant": "In-N-Out Burger",    "amount": "20.00", "category": "Dining"},
    {"date": "2026-03-16", "merchant": "Sushi Ginza",        "amount": "85.00", "category": "Dining"},
    {"date": "2026-03-20", "merchant": "Ramen Nagi",         "amount": "45.00", "category": "Dining"},
    {"date": "2026-03-28", "merchant": "Sweetgreen",         "amount": "30.00", "category": "Dining"},
    # February 2026 — Groceries (4 rows = $280)
    {"date": "2026-02-02", "merchant": "Trader Joe's",       "amount": "60.00", "category": "Groceries"},
    {"date": "2026-02-09", "merchant": "Whole Foods",        "amount": "80.00", "category": "Groceries"},
    {"date": "2026-02-16", "merchant": "Trader Joe's",       "amount": "70.00", "category": "Groceries"},
    {"date": "2026-02-23", "merchant": "H Mart",             "amount": "70.00", "category": "Groceries"},
    # March 2026 — Groceries (4 rows = $300)
    {"date": "2026-03-02", "merchant": "Trader Joe's",       "amount": "70.00", "category": "Groceries"},
    {"date": "2026-03-09", "merchant": "Whole Foods",        "amount": "90.00", "category": "Groceries"},
    {"date": "2026-03-16", "merchant": "Trader Joe's",       "amount": "65.00", "category": "Groceries"},
    {"date": "2026-03-23", "merchant": "H Mart",             "amount": "75.00", "category": "Groceries"},
    # February 2026 — Coffee on Amex Gold (5 rows = $35.00)
    {"date": "2026-02-05", "merchant": "Blue Bottle Coffee", "amount": "7.00",  "category": "Coffee Shops", "card_name": "Amex Gold"},
    {"date": "2026-02-12", "merchant": "Blue Bottle Coffee", "amount": "7.00",  "category": "Coffee Shops", "card_name": "Amex Gold"},
    {"date": "2026-02-19", "merchant": "Blue Bottle Coffee", "amount": "7.00",  "category": "Coffee Shops", "card_name": "Amex Gold"},
    {"date": "2026-02-21", "merchant": "Blue Bottle Coffee", "amount": "7.00",  "category": "Coffee Shops", "card_name": "Amex Gold"},
    {"date": "2026-02-26", "merchant": "Blue Bottle Coffee", "amount": "7.00",  "category": "Coffee Shops", "card_name": "Amex Gold"},
    # March 2026 — Coffee on Amex Gold (5 rows = $40.00)
    {"date": "2026-03-03", "merchant": "Blue Bottle Coffee", "amount": "8.00",  "category": "Coffee Shops", "card_name": "Amex Gold"},
    {"date": "2026-03-10", "merchant": "Blue Bottle Coffee", "amount": "8.00",  "category": "Coffee Shops", "card_name": "Amex Gold"},
    {"date": "2026-03-17", "merchant": "Blue Bottle Coffee", "amount": "8.00",  "category": "Coffee Shops", "card_name": "Amex Gold"},
    {"date": "2026-03-24", "merchant": "Blue Bottle Coffee", "amount": "8.00",  "category": "Coffee Shops", "card_name": "Amex Gold"},
    {"date": "2026-03-31", "merchant": "Blue Bottle Coffee", "amount": "8.00",  "category": "Coffee Shops", "card_name": "Amex Gold"},
]

# Subscription fixtures — get_subscriptions reads these. The forward-only
# rule means next_billing_date is set in the future so the autologger
# doesn't fire on them during eval runs.
_FIXTURE_SUBSCRIPTIONS: list[dict[str, Any]] = [
    {
        "name": "Netflix",
        "amount": "15.99",
        "frequency": "monthly",
        "category": "Streaming",
        "card_name": "Amex Gold",
        "start_date": "2025-08-15",
        "next_billing_date": "2026-06-15",
    },
    {
        "name": "Spotify",
        "amount": "9.99",
        "frequency": "monthly",
        "category": "Streaming",
        "card_name": "Amex Gold",
        "start_date": "2025-09-01",
        "next_billing_date": "2026-06-01",
    },
]


def ensure_eval_user_jwt() -> tuple[str, UUID]:
    """Provision eval@tameru.internal on the local Supabase stack and mint a JWT.

    Idempotent: if the user already exists from a prior run, sign in with
    the stored password and return a fresh JWT (sign-in always issues a
    new token; the JWT TTL is whatever Supabase Auth is configured for —
    1h by default on the local stack, plenty for one eval pass).

    Returns:
      (jwt_token, user_id) tuple.

    Raises:
      RuntimeError if `supabase start` isn't running, or if the eval
      user exists with a different password than EVAL_USER_PASSWORD
      (which indicates manual interference).
    """
    status = _load_local_supabase_status()
    admin = create_client(status["API_URL"], status["SERVICE_ROLE_KEY"])

    user_id = _ensure_user_exists(admin)

    anon = create_client(status["API_URL"], status["ANON_KEY"])
    try:
        session = anon.auth.sign_in_with_password(
            {"email": EVAL_USER_EMAIL, "password": EVAL_USER_PASSWORD}
        ).session
    except Exception as exc:
        raise RuntimeError(
            f"Could not sign in as {EVAL_USER_EMAIL}. The user exists but "
            f"the stored password doesn't match EVAL_USER_PASSWORD. Either "
            f"reset the password via Supabase Studio or set "
            f"EVAL_USER_PASSWORD to the stored value. Underlying error: {exc}"
        ) from exc
    return session.access_token, UUID(user_id)


def export_local_supabase_env() -> None:
    """Populate SUPABASE_URL / SUPABASE_ANON_KEY into os.environ.

    The eval runner calls into app code (`categorize()`, the agent
    tools), which reach Supabase through `app.db.supabase_for_user` —
    and that reads `SUPABASE_URL` / `SUPABASE_ANON_KEY` from the
    environment. The scripts themselves discover the local stack via
    `supabase status`, but app code does not, so the runner mirrors the
    discovered values into the environment once at startup. Existing env
    values are left untouched (a CI job pointing at a non-default stack
    can override).
    """
    status = _load_local_supabase_status()
    os.environ.setdefault("SUPABASE_URL", status["API_URL"])
    os.environ.setdefault("SUPABASE_ANON_KEY", status["ANON_KEY"])


def seed_fixtures(jwt: str, user_id: UUID) -> dict[str, Any]:
    """Idempotently upsert the cards, transactions, and subscriptions
    the YAML corpora reference.

    Uses the user JWT so RLS scopes every write — no service-role bypass
    (CLAUDE.md invariant 1).

    Returns a dict with `cards: {name -> card_id}` so the eval runner can
    resolve `card_name_resolves_to` lookups without re-querying Supabase.
    """
    client = create_client(_load_local_supabase_status()["API_URL"], _anon_key())
    # Bind the JWT so RLS scopes by auth.uid().
    client.postgrest.auth(jwt)

    cards_by_name = _seed_cards(client, user_id)
    _ensure_users_meta(client, user_id)
    _seed_transactions(client, user_id, cards_by_name)
    _seed_subscriptions(client, user_id, cards_by_name)
    return {"cards": cards_by_name}


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _load_local_supabase_status() -> dict[str, str]:
    """Read API_URL / ANON_KEY / SERVICE_ROLE_KEY from `supabase status -o json`.

    Same shape as scripts/mint_test_jwt.py — keeps both scripts compatible
    with the same `supabase start` stack.
    """
    raw = subprocess.check_output(
        ["supabase", "status", "-o", "json"], text=True
    )
    brace = raw.find("{")
    if brace < 0:
        raise RuntimeError(
            "supabase status did not return JSON; is the stack running? "
            "Run `supabase start` first."
        )
    return json.loads(raw[brace:])


def _anon_key() -> str:
    """Return the local stack's anon key without re-running supabase status."""
    return _load_local_supabase_status()["ANON_KEY"]


def _ensure_user_exists(admin: Any) -> str:
    """Create the eval user if missing; return its UUID either way."""
    try:
        created = admin.auth.admin.create_user(
            {
                "email": EVAL_USER_EMAIL,
                "password": EVAL_USER_PASSWORD,
                "email_confirm": True,
            }
        )
        return created.user.id
    except Exception:
        # Almost certainly "user already exists" — look it up by listing.
        # supabase-py exposes list_users() but its filter parameter is
        # inconsistent across versions; iterate defensively.
        page = 1
        while True:
            resp = admin.auth.admin.list_users(page=page, per_page=200)
            users = getattr(resp, "users", None) or resp
            for user in users:
                if getattr(user, "email", None) == EVAL_USER_EMAIL:
                    return user.id
            if not users or len(users) < 200:
                break
            page += 1
        raise RuntimeError(
            f"could not find or create {EVAL_USER_EMAIL!r} on the local stack"
        )


def _ensure_users_meta(client: Any, user_id: UUID) -> None:
    """Insert a users_meta row if the eval user doesn't have one yet.

    `users_meta.home_currency` is required at signup in the production
    flow; the seed shortcuts that by writing the row directly. Subsequent
    runs are no-ops via ON CONFLICT.
    """
    client.table("users_meta").upsert(
        {
            "user_id": str(user_id),
            "home_currency": "USD",
            "active_device_id": "eval-fixture-device",
        },
        on_conflict="user_id",
    ).execute()


def _seed_cards(client: Any, user_id: UUID) -> dict[str, str]:
    """Upsert fixture cards; return name → card_id map.

    Cards have a natural-key unique index on (user_id, issuer, last_four)
    WHERE status='active', so re-running the seed after a card already
    exists hits the partial unique and we read its id instead.
    """
    out: dict[str, str] = {}
    for fixture in _FIXTURE_CARDS:
        resp = (
            client.table("cards")
            .select("id, name")
            .eq("issuer", fixture["issuer"])
            .eq("last_four", fixture["last_four"])
            .eq("status", "active")
            .execute()
        )
        rows = resp.data or []
        if rows:
            out[fixture["name"]] = rows[0]["id"]
            continue
        insert_payload = {
            "user_id": str(user_id),
            "name": fixture["name"],
            "issuer": fixture["issuer"],
            "network": fixture["network"],
            "program": fixture["program"],
            "last_four": fixture["last_four"],
            "annual_fee": fixture["annual_fee"],
            "color": fixture["color"],
            "multipliers": fixture["multipliers"],
            "source_urls": fixture["source_urls"],
        }
        created = client.table("cards").insert(insert_payload).execute()
        out[fixture["name"]] = created.data[0]["id"]
    return out


def _seed_transactions(
    client: Any, user_id: UUID, cards_by_name: dict[str, str]
) -> None:
    """Insert fixture transactions if not already present.

    Uses a per-row existence check on (user_id, date, merchant, amount)
    so a re-run on top of an existing seed is a no-op — `transactions`
    has no natural-key constraint that would 409 us. Adequate at fixture
    scale (~30 rows).
    """
    for fixture in _FIXTURE_TRANSACTIONS:
        existing = (
            client.table("transactions")
            .select("id")
            .eq("date", fixture["date"])
            .eq("merchant", fixture["merchant"])
            .eq("amount", fixture["amount"])
            .eq("status", "active")
            .execute()
        )
        if existing.data:
            continue
        # `source` is CHECK-constrained to
        # ('manual', 'nlp', 'receipt_photo', 'auto_logged', 'csv_import')
        # — 'manual' is the honest label for a directly-seeded row.
        payload: dict[str, Any] = {
            "user_id": str(user_id),
            "date": fixture["date"],
            "merchant": fixture["merchant"],
            "amount": fixture["amount"],
            "category": fixture["category"],
            "source": "manual",
        }
        card_name = fixture.get("card_name")
        if card_name and card_name in cards_by_name:
            payload["card_id"] = cards_by_name[card_name]
        client.table("transactions").insert(payload).execute()


def _seed_subscriptions(
    client: Any, user_id: UUID, cards_by_name: dict[str, str]
) -> None:
    """Insert fixture subscriptions keyed on (user_id, name) idempotently.

    `subscriptions` has no name uniqueness constraint either (multiple
    Netflix subs is technically legal), so the seed pre-checks by name
    and skips if found.
    """
    for fixture in _FIXTURE_SUBSCRIPTIONS:
        existing = (
            client.table("subscriptions")
            .select("id")
            .eq("name", fixture["name"])
            .eq("status", "active")
            .execute()
        )
        if existing.data:
            continue
        payload: dict[str, Any] = {
            "user_id": str(user_id),
            "name": fixture["name"],
            "amount": fixture["amount"],
            "frequency": fixture["frequency"],
            "category": fixture["category"],
            "start_date": fixture["start_date"],
            "next_billing_date": fixture["next_billing_date"],
        }
        card_name = fixture.get("card_name")
        if card_name and card_name in cards_by_name:
            payload["card_id"] = cards_by_name[card_name]
        client.table("subscriptions").insert(payload).execute()
