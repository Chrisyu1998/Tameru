"""RLS contract suite — DESIGN.md §13.1.

For every user-owned table: user A inserts, user B cannot read or update.
For every SELECT-only audit table: service role inserts for user A, user B
cannot read.

The read assertion intentionally omits `user_id` from the `.select()` call.
That is the whole point of the exercise: the app must not need a `WHERE
user_id = ?` clause for the query to be safe — RLS does the filtering.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date

import pytest

from app.db import supabase_for_user


# ---------------------------------------------------------------------------
# Per-user card fixtures — `subscriptions` requires `card_id NOT NULL`.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def card_a(user_a) -> str:
    client = supabase_for_user(user_a.jwt)
    resp = (
        client.table("cards")
        .insert(
            {
                "user_id": user_a.id,
                "name": "A card",
                "issuer": "Chase",
                "program": "UR",
            }
        )
        .execute()
    )
    return resp.data[0]["id"]


@pytest.fixture(scope="session")
def card_b(user_b) -> str:
    client = supabase_for_user(user_b.jwt)
    resp = (
        client.table("cards")
        .insert(
            {
                "user_id": user_b.id,
                "name": "B card",
                "issuer": "Amex",
                "program": "MR",
            }
        )
        .execute()
    )
    return resp.data[0]["id"]


# ---------------------------------------------------------------------------
# Row factories — keep every inserted row unique per session so re-running the
# suite in the same session doesn't hit UNIQUE constraints.
# ---------------------------------------------------------------------------


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _row_cards(user_id: str, **_):
    return {
        "user_id": user_id,
        "name": f"Card-{_tag()}",
        "issuer": "Chase",
        "program": "UR",
    }


def _row_transactions(user_id: str, **_):
    return {
        "user_id": user_id,
        "merchant": f"Shop-{_tag()}",
        "amount": 12.34,
        "date": date.today().isoformat(),
        "category": "groceries",
        "source": "manual",
    }


def _row_subscriptions(user_id: str, *, card_id: str, **_):
    return {
        "user_id": user_id,
        "card_id": card_id,
        "name": f"Sub-{_tag()}",
        "amount": 9.99,
        "frequency": "monthly",
        "start_date": date.today().isoformat(),
        "next_billing_date": date.today().isoformat(),
        "category": "entertainment",
    }


def _row_merchant_category(user_id: str, **_):
    return {
        "user_id": user_id,
        "merchant": f"Merch-{_tag()}",
        "category": "coffee",
    }


def _row_user_memory(user_id: str, **_):
    return {
        "user_id": user_id,
        "fact": f"fact-{_tag()}",
        "category": "spending_pattern",
    }


def _row_mcp_tokens(user_id: str, **_):
    token_hash = hashlib.sha256(_tag().encode()).hexdigest()
    return {
        "user_id": user_id,
        "token_hash": token_hash,
        "name": f"token-{_tag()}",
    }


def _row_users_meta(user_id: str, **_):
    # PK is user_id — one row per user. Session fixture ensures we only insert
    # once per user per test run.
    return {"user_id": user_id, "active_device_id": f"dev-{_tag()}"}


# (table_name, row_factory, pk_column, needs_card)
USER_OWNED_TABLES = [
    ("cards", _row_cards, "id", False),
    ("transactions", _row_transactions, "id", False),
    ("subscriptions", _row_subscriptions, "id", True),
    ("merchant_category", _row_merchant_category, "id", False),
    ("user_memory", _row_user_memory, "id", False),
    ("mcp_tokens", _row_mcp_tokens, "id", False),
    ("users_meta", _row_users_meta, "user_id", False),
]


@pytest.fixture(scope="session")
def _seed_users_meta_once(user_a):
    """users_meta has user_id as PK. Upsert so this is idempotent regardless
    of whether the parametrized read test already seeded the row."""
    client = supabase_for_user(user_a.jwt)
    client.table("users_meta").upsert(
        {"user_id": user_a.id}, on_conflict="user_id"
    ).execute()
    return user_a.id


@pytest.mark.parametrize(
    ("table", "factory", "pk", "needs_card"),
    USER_OWNED_TABLES,
    ids=[t[0] for t in USER_OWNED_TABLES],
)
def test_user_b_cannot_read_user_a_rows(
    table, factory, pk, needs_card, user_a, user_b, card_a
):
    client_a = supabase_for_user(user_a.jwt)
    client_b = supabase_for_user(user_b.jwt)

    payload = factory(user_a.id, card_id=card_a)
    ins = client_a.table(table).insert(payload).execute()
    assert ins.data, f"user A failed to insert into {table}: {ins}"

    # Deliberately no `user_id` filter — RLS must return zero rows on its own.
    resp = client_b.table(table).select("*").execute()
    assert resp.data == [], (
        f"RLS leak: user B can read {table} rows owned by user A "
        f"(got {len(resp.data)} rows)"
    )


@pytest.mark.parametrize(
    ("table", "factory", "pk", "needs_card"),
    # users_meta is excluded here — user A's row is inserted by the dedicated
    # fixture; updating by user_id still exercises the same RLS shape below.
    [t for t in USER_OWNED_TABLES if t[0] != "users_meta"],
    ids=[t[0] for t in USER_OWNED_TABLES if t[0] != "users_meta"],
)
def test_user_b_cannot_update_user_a_rows(
    table, factory, pk, needs_card, user_a, user_b, card_a
):
    client_a = supabase_for_user(user_a.jwt)
    client_b = supabase_for_user(user_b.jwt)

    payload = factory(user_a.id, card_id=card_a)
    ins = client_a.table(table).insert(payload).execute()
    row_key = ins.data[0][pk]

    update_fields = {"name": "hacked"} if "name" in payload else {"category": "hacked"}
    resp = client_b.table(table).update(update_fields).eq(pk, row_key).execute()
    assert resp.data == [], (
        f"RLS leak: user B updated a {table} row owned by user A "
        f"(returned {resp.data})"
    )

    # And confirm A's row is untouched.
    a_row = client_a.table(table).select("*").eq(pk, row_key).execute()
    assert a_row.data, f"user A lost visibility of their own {table} row"
    for k, v in update_fields.items():
        assert a_row.data[0][k] != v, (
            f"RLS leak: user B's update to {table}.{k} actually persisted"
        )


def test_user_b_cannot_update_user_a_users_meta(_seed_users_meta_once, user_a, user_b):
    client_a = supabase_for_user(user_a.jwt)
    client_b = supabase_for_user(user_b.jwt)

    resp = (
        client_b.table("users_meta")
        .update({"active_device_id": "hacked"})
        .eq("user_id", user_a.id)
        .execute()
    )
    assert resp.data == [], "RLS leak: user B updated user A's users_meta row"

    a_row = client_a.table("users_meta").select("*").eq("user_id", user_a.id).execute()
    assert a_row.data and a_row.data[0]["active_device_id"] != "hacked"


# ---------------------------------------------------------------------------
# Audit tables — SELECT-only policy. We check that user B cannot read user A's
# rows. INSERT/UPDATE/DELETE are unconditionally rejected by Postgres (no
# policies exist for those verbs), so asserting their rejection is tautological
# and not tested here.
# ---------------------------------------------------------------------------


def test_user_b_cannot_read_user_a_ai_call_log(admin_client, user_a, user_b):
    admin_client.table("ai_call_log").insert(
        {
            "user_id": user_a.id,
            "provider": "anthropic",
            "model": "claude-haiku-4-5",
            "task_type": "chat_turn",
            "success": True,
        }
    ).execute()

    client_b = supabase_for_user(user_b.jwt)
    resp = client_b.table("ai_call_log").select("*").execute()
    assert resp.data == [], "RLS leak: user B read user A's ai_call_log rows"


def test_user_b_cannot_read_user_a_ai_call_log_daily(admin_client, user_a, user_b):
    admin_client.table("ai_call_log_daily").insert(
        {
            "date": date.today().isoformat(),
            "user_id": user_a.id,
            "provider": "anthropic",
            "model": "claude-haiku-4-5",
            "task_type": "chat_turn",
        }
    ).execute()

    client_b = supabase_for_user(user_b.jwt)
    resp = client_b.table("ai_call_log_daily").select("*").execute()
    assert resp.data == [], "RLS leak: user B read user A's ai_call_log_daily rows"
