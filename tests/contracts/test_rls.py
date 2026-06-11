"""RLS contract suite — DESIGN.md §13.1.

For every user-owned table: user A inserts, user B cannot read or update.

For the `ai_call_log` audit table (SELECT + narrow INSERT, no UPDATE/DELETE —
CLAUDE.md invariant 14):
    - user B cannot read user A's rows;
    - user A can insert a row with `user_id = auth.uid()` (the Day 4
      in-handler logger path);
    - user A cannot insert a row with `user_id = <user_b.id>` (the narrow
      INSERT policy's `WITH CHECK` rejects it — the only protection against
      a compromised JWT forging rows on another account);
    - user A cannot UPDATE or DELETE their own audit rows (audit history is
      not scrubbable).

For `ai_call_log_daily` (SELECT only): user B cannot read user A's rows.

The read assertion intentionally omits `user_id` from the `.select()` call.
That is the whole point of the exercise: the app must not need a `WHERE
user_id = ?` clause for the query to be safe — RLS does the filtering.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.db import supabase_for_user


# ---------------------------------------------------------------------------
# Row factories — keep every inserted row unique per session so re-running the
# suite in the same session doesn't hit UNIQUE constraints. `card_a` and
# `card_b` session fixtures live in conftest.py so the Day 5 suite can use
# them too.
# ---------------------------------------------------------------------------


# (table_name, row_factory, pk_column, needs_card)
USER_OWNED_TABLES = [
    ("cards", "_row_cards", "id", False),
    ("transactions", "_row_transactions", "id", False),
    ("subscriptions", "_row_subscriptions", "id", True),
    ("merchant_category", "_row_merchant_category", "id", False),
    ("user_memory", "_row_user_memory", "id", False),
    ("users_meta", "_row_users_meta", "user_id", False),
    ("chat_messages", "_row_chat_messages", "id", False),
    ("chat_turn_trace", "_row_chat_turn_trace", "id", False),
    ("goals", "_row_goals", "id", False),
]


@pytest.mark.parametrize(
    ("table", "factory", "pk", "needs_card"),
    USER_OWNED_TABLES,
    ids=[t[0] for t in USER_OWNED_TABLES],
)
def test_user_b_cannot_read_user_a_rows(
    table, factory, pk, needs_card, user_a, user_b, card_a
):
    """Verify that user b cannot read user a rows."""
    factory = globals()[factory]
    client_a = supabase_for_user(user_a.jwt)
    client_b = supabase_for_user(user_b.jwt)

    payload = factory(user_a.id, card_id=card_a)
    if table == "users_meta":
        # Preserve user_a's real device id. The row already exists from
        # conftest bootstrap; the factory hands back a random `dev-<tag>`
        # active_device_id, and upserting that would overwrite the bootstrap
        # value for the whole session (user_a is session-scoped). That breaks
        # the single-active-device invariant (active_device_id == device_id),
        # so every later device-gated request 401s with DEVICE_DISPLACED —
        # an order-dependent cross-file flake. Writing the real device id back
        # keeps the upsert idempotent w.r.t. the device gate.
        payload = {**payload, "active_device_id": user_a.device_id}
        ins = client_a.table(table).upsert(payload, on_conflict=pk).execute()
    else:
        ins = client_a.table(table).insert(payload).execute()
    assert ins.data, f"user A failed to insert into {table}: {ins}"
    row_key = ins.data[0][pk]

    # Deliberately no `user_id` filter — RLS may return user B's own rows,
    # but it must not return the row user A just wrote.
    resp = client_b.table(table).select("*").execute()
    assert all(row.get(pk) != row_key for row in resp.data), (
        f"RLS leak: user B can read user A's {table} row "
        f"(pk={row_key}, returned {resp.data})"
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
    """Verify that user b cannot update user a rows."""
    factory = globals()[factory]
    client_a = supabase_for_user(user_a.jwt)
    client_b = supabase_for_user(user_b.jwt)

    payload = factory(user_a.id, card_id=card_a)
    ins = client_a.table(table).insert(payload).execute()
    row_key = ins.data[0][pk]

    # Pick a writable field that exists on this table. `name` and
    # `category` cover most user-owned tables; `chat_messages` has
    # neither (its only mutable text-ish field is `role`, CHECK-
    # constrained to {'user','assistant'} — flip to the other value);
    # `chat_turn_trace` has only `messages` (JSONB).
    if "name" in payload:
        update_fields = {"name": "hacked"}
    elif "category" in payload:
        update_fields = {"category": "hacked"}
    elif "role" in payload:
        update_fields = {"role": "assistant" if payload["role"] == "user" else "user"}
    elif "messages" in payload:
        update_fields = {"messages": [{"role": "user", "content": "hacked"}]}
    else:
        raise AssertionError(
            f"no known update field for table {table!r} — extend the "
            "branch above when adding a new user-owned table"
        )
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
    """Verify that user b cannot update user a users meta."""
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
# Audit tables — `ai_call_log` has SELECT + narrow INSERT policies and NO
# UPDATE/DELETE policies; `ai_call_log_daily` has SELECT only. Tests below
# cover the full contract (CLAUDE.md invariant 14):
#   - SELECT scoping across users;
#   - narrow INSERT accepts own-row writes (the Day 4 logger path);
#   - narrow INSERT rejects foreign-user writes (the forgery protection);
#   - UPDATE and DELETE are rejected on own rows (history is not scrubbable).
# ---------------------------------------------------------------------------


def test_user_b_cannot_read_user_a_ai_call_log(admin_client, user_a, user_b):
    """Verify that user b cannot read user a ai call log."""
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
    """Verify that user b cannot read user a ai call log daily."""
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


def test_user_a_can_insert_own_ai_call_log_row(user_a):
    """Narrow INSERT policy accepts own-row writes — the Day 4 logger path."""
    client_a = supabase_for_user(user_a.jwt)
    row = _ai_call_log_row(user_a.id)
    resp = client_a.table("ai_call_log").insert(row).execute()
    assert resp.data, "narrow INSERT policy rejected user A's own-row write"
    assert resp.data[0]["user_id"] == user_a.id


def test_user_a_cannot_insert_foreign_ai_call_log_row(user_a, user_b):
    """Narrow INSERT's WITH CHECK rejects a forged user_id — the protection
    against a compromised JWT writing rows on another account."""
    client_a = supabase_for_user(user_a.jwt)
    forged = _ai_call_log_row(user_b.id)  # user A's client, user B's user_id
    with pytest.raises(Exception):
        client_a.table("ai_call_log").insert(forged).execute()

    # Double-check: no row with this sentinel landed.
    check = (
        supabase_for_user(user_b.jwt)
        .table("ai_call_log")
        .select("id")
        .eq("prompt_version", forged["prompt_version"])
        .execute()
    )
    assert check.data == [], "RLS leak: a forged foreign-user row was persisted"


def test_user_a_cannot_update_own_ai_call_log_row(user_a):
    """No UPDATE policy on ai_call_log — users cannot mutate audit history."""
    client_a = supabase_for_user(user_a.jwt)
    row = _ai_call_log_row(user_a.id)
    ins = client_a.table("ai_call_log").insert(row).execute()
    row_id = ins.data[0]["id"]

    resp = (
        client_a.table("ai_call_log")
        .update({"success": False})
        .eq("id", row_id)
        .execute()
    )
    assert resp.data == [], (
        "ai_call_log UPDATE should affect zero rows (no UPDATE policy exists)"
    )

    # And the row is unchanged.
    fetched = (
        client_a.table("ai_call_log")
        .select("success")
        .eq("id", row_id)
        .single()
        .execute()
    )
    assert fetched.data["success"] is True, (
        "ai_call_log row was mutated despite no UPDATE policy"
    )


def test_user_a_cannot_delete_own_ai_call_log_row(user_a):
    """No DELETE policy on ai_call_log — users cannot scrub audit history."""
    client_a = supabase_for_user(user_a.jwt)
    row = _ai_call_log_row(user_a.id)
    ins = client_a.table("ai_call_log").insert(row).execute()
    row_id = ins.data[0]["id"]

    resp = client_a.table("ai_call_log").delete().eq("id", row_id).execute()
    assert resp.data == [], (
        "ai_call_log DELETE should affect zero rows (no DELETE policy exists)"
    )

    # And the row is still there.
    fetched = (
        client_a.table("ai_call_log")
        .select("id")
        .eq("id", row_id)
        .single()
        .execute()
    )
    assert fetched.data["id"] == row_id, (
        "ai_call_log row was deleted despite no DELETE policy"
    )


def test_user_a_cannot_delete_own_users_meta_row(user_a):
    """No DELETE policy on users_meta — the home_currency bypass is closed.

    The original FOR ALL owner policy let a user DELETE their users_meta
    row and re-bootstrap with a different home_currency, mutating the
    immutable column in two statements around the BEFORE UPDATE trigger
    (audit P3-9; migration 20260610130000 split the policy and granted no
    DELETE). The row may die only via the auth.users cascade — real
    account deletion through the service-role admin API.
    """
    client_a = supabase_for_user(user_a.jwt)

    resp = (
        client_a.table("users_meta")
        .delete()
        .eq("user_id", user_a.id)
        .execute()
    )
    assert resp.data == [], (
        "users_meta DELETE should affect zero rows (no DELETE policy exists)"
    )

    # And the row is still there, currency intact.
    fetched = (
        client_a.table("users_meta")
        .select("user_id, home_currency")
        .eq("user_id", user_a.id)
        .single()
        .execute()
    )
    assert fetched.data["user_id"] == user_a.id, (
        "users_meta row was deleted despite no DELETE policy"
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _tag() -> str:
    """Support tag."""
    return uuid.uuid4().hex[:8]

def _row_cards(user_id: str, **_):
    """Support row cards."""
    tag = _tag()
    # The `network` + `last_four` Day-14 columns are NOT NULL on `cards`;
    # `issuer` is a closed CHECK enum since the Day 14 follow-up migration
    # (20260516140000_cards_uniqueness_by_issuer.sql) — use canonical
    # lowercase. `_tag()` is hex so we keep only digits and pad to 4 to
    # satisfy the 4-digit shape. Per-row unique so the partial unique
    # identity index (DESIGN.md §8.1) doesn't collide across this test's
    # repeats.
    digits = "".join(c for c in tag if c.isdigit())
    last_four = (digits + "0000")[:4]
    return {
        "user_id": user_id,
        "name": f"Card-{tag}",
        "issuer": "chase",
        "program": "UR",
        "network": "visa",
        "last_four": last_four,
    }

def _row_transactions(user_id: str, **_):
    """Support row transactions."""
    return {
        "user_id": user_id,
        "merchant": f"Shop-{_tag()}",
        "amount": 12.34,
        "date": date.today().isoformat(),
        "category": "groceries",
        "source": "manual",
    }

def _row_subscriptions(user_id: str, *, card_id: str, **_):
    """Support row subscriptions."""
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
    """Support row merchant category."""
    return {
        "user_id": user_id,
        "merchant": f"Merch-{_tag()}",
        "category": "coffee",
    }

def _row_user_memory(user_id: str, **_):
    """Support row user memory."""
    return {
        "user_id": user_id,
        "fact": f"fact-{_tag()}",
        "category": "spending_pattern",
    }

def _row_users_meta(user_id: str, **_):
    # PK is user_id — one row per user. Session fixture ensures we only insert
    # once per user per test run.
    """Support row users meta."""
    return {"user_id": user_id, "active_device_id": f"dev-{_tag()}"}

def _row_chat_messages(user_id: str, **_):
    # One row per turn; conversation_id is a plain UUID grouper. The
    # update-isolation test mutates `role` (the only updatable text-ish
    # field besides content_blocks); flipping role through the CHECK
    # constraint requires a valid value.
    """Support row chat messages."""
    return {
        "user_id": user_id,
        "conversation_id": str(uuid.uuid4()),
        "role": "user",
        "content_blocks": [{"type": "text", "text": f"msg-{_tag()}"}],
    }

def _row_goals(user_id: str, **_):
    # One row per (user, category, period). Use a unique category per
    # insert so re-running this suite in the same session doesn't trip
    # the goals_user_cat_period_uniq constraint (the contract under test
    # is RLS isolation, not idempotent upsert — that lives in
    # tests/test_tools.py). category values here are unique tag strings,
    # which is fine because the DB has no CHECK on `category`; the
    # closed-enum validation lives at the Pydantic model layer.
    """Support row goals."""
    return {
        "user_id": user_id,
        "category": f"Tag-{_tag()}",
        "amount": 100,
        "period": "month",
    }

def _row_chat_turn_trace(user_id: str, **_):
    # One row per turn; `messages` is the full Anthropic message-list
    # slice. The update-isolation test mutates `messages` (JSONB).
    """Support row chat turn trace."""
    return {
        "user_id": user_id,
        "conversation_id": str(uuid.uuid4()),
        "messages": [
            {"role": "user", "content": f"hi-{_tag()}"},
            {"role": "assistant", "content": [{"type": "text", "text": "ack"}]},
        ],
    }

@pytest.fixture(scope="session")
def _seed_users_meta_once(user_a):
    """users_meta has user_id as PK. Upsert so this is idempotent regardless
    of whether the parametrized read test already seeded the row."""
    client = supabase_for_user(user_a.jwt)
    client.table("users_meta").upsert(
        {"user_id": user_a.id}, on_conflict="user_id"
    ).execute()
    return user_a.id

def _ai_call_log_row(user_id: str) -> dict:
    # prompt_version tagged per row so we can find the exact row back later
    # without worrying about other test rows for the same user.
    """Support ai call log row."""
    return {
        "user_id": user_id,
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "task_type": "chat_turn",
        "prompt_version": f"rls-probe-{_tag()}",
        "success": True,
    }
