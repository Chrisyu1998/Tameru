"""Aggregation + privacy-boundary tests for compose_digest.

We don't touch Supabase here — the digest service takes a client as a
parameter, so a tiny fake works fine. Same posture for Anthropic: the
service accepts an `anthropic_client` keyword argument the test sets to
a mock returning a deterministic message.

The privacy-boundary test is load-bearing: CLAUDE.md privacy posture
forbids merchant names and transaction rows from reaching Anthropic.
The system-prompt + per-call user payload built by `_call_sonnet` must
never include them, even if a future refactor accidentally widens the
shape.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.services.digest import (
    CategoryRollup,
    DigestPayload,
    SonnetCallLog,
    compose_digest,
    render_email,
)


ET = ZoneInfo("America/New_York")
USER_ID = uuid4()


class _FakeSupabase:
    """Just enough surface to satisfy the digest's reads.

    Two tables: users_meta and transactions. The chain
    `.table().select().eq()...execute()` returns a SimpleNamespace with
    a `.data` list of dicts, matching supabase-py's shape.
    """

    def __init__(self, *, transactions: list[dict], home_currency: str = "USD"):
        """Provide fixture data."""
        self._transactions = transactions
        self._home_currency = home_currency

    def table(self, name: str):
        """Provide table."""
        if name == "users_meta":
            return _MetaQuery(self._home_currency)
        if name == "transactions":
            return _TxQuery(self._transactions)
        raise AssertionError(f"unexpected table: {name}")


class _Resp:
    """Mirror supabase-py's response object with a `.data` attribute."""
    def __init__(self, data):
        """Provide resp."""
        self.data = data


class _MetaQuery:
    """Chainable query stub for `users_meta` (always returns one row)."""
    def __init__(self, home_currency: str):
        """Provide meta query."""
        self._home_currency = home_currency

    def select(self, *_args, **_kwargs):
        """Provide select."""
        return self

    def eq(self, *_args, **_kwargs):
        """Provide eq."""
        return self

    def execute(self):
        """Provide execute."""
        return _Resp([{"home_currency": self._home_currency}])


class _TxQuery:
    """Chainable query stub for `transactions` with date-range filter support."""
    def __init__(self, transactions: list[dict]):
        """Provide tx query."""
        self._transactions = transactions
        self._range = None

    def select(self, *_args, **_kwargs):
        """Provide select."""
        return self

    def eq(self, *_args, **_kwargs):
        """Provide eq."""
        return self

    def gte(self, _field, value):
        """Provide gte."""
        if self._range is None:
            self._range = [value, None]
        else:
            self._range[0] = value
        return self

    def lte(self, _field, value):
        """Provide lte."""
        if self._range is None:
            self._range = [None, value]
        else:
            self._range[1] = value
        return self

    def limit(self, *_args, **_kwargs):
        """Provide limit."""
        return self

    def execute(self):
        """Provide execute."""
        if self._range is None:
            return _Resp(list(self._transactions))
        start, end = self._range
        out = [
            t for t in self._transactions
            if (start is None or t["date"] >= start)
            and (end is None or t["date"] <= end)
        ]
        return _Resp(out)


class _FakeAnthropic:
    """Captures the messages sent to verify the privacy boundary."""

    def __init__(self, *, response_text: str):
        """Provide fake anthropic."""
        self.captured_messages: list = []
        self.captured_system: str | None = None
        self._response_text = response_text
        self.messages = self  # bare delegate so .messages.create works

    def create(self, *, model, max_tokens, system, messages):
        """Provide create."""
        self.captured_system = system
        self.captured_messages = messages
        # Build a response shape matching anthropic-sdk-python's API.
        from types import SimpleNamespace
        content_block = SimpleNamespace(type="text", text=self._response_text)
        usage = SimpleNamespace(input_tokens=100, output_tokens=50)
        return SimpleNamespace(content=[content_block], usage=usage)


def test_aggregates_correct():
    """Week total + baseline average + top category math."""
    week_start = _previous_week_start_et()
    transactions = _make_transactions(week_start)
    sb = _FakeSupabase(transactions=transactions, home_currency="USD")
    anth = _FakeAnthropic(
        response_text=json.dumps({"observation": "spending steady", "nudge": None})
    )

    payload, call_log = compose_digest(sb, USER_ID, anthropic_client=anth)

    assert isinstance(payload, DigestPayload)
    assert payload.week_total == Decimal("300.00")
    # 8 prior weeks × $200 = $1600 / 8 = $200 baseline avg
    assert payload.baseline_avg == Decimal("200.00")
    assert payload.top_category is not None
    assert payload.top_category.category == "Dining"
    assert payload.top_category.week_total == Decimal("150.00")
    # Prior weeks: $100/wk dining × 8 / 8 = $100 baseline
    assert payload.top_category.baseline_avg == Decimal("100.00")
    assert payload.home_currency == "USD"
    assert isinstance(call_log, SonnetCallLog)
    assert call_log.success is True


def test_privacy_boundary_no_merchants_to_sonnet():
    """The user payload sent to Sonnet must carry no merchant names or tx rows.

    Load-bearing regression guard for CLAUDE.md privacy posture: a
    future refactor of `_call_sonnet`'s payload shape must continue to
    send aggregates only.
    """
    week_start = _previous_week_start_et()
    transactions = _make_transactions(week_start)
    # Inject a recognizable merchant name into the transaction shape;
    # if it leaks into the Sonnet payload, this test catches it.
    for t in transactions:
        t["merchant"] = "BLUE BOTTLE COFFEE SECRET MARKER"
    sb = _FakeSupabase(transactions=transactions)
    anth = _FakeAnthropic(
        response_text=json.dumps({"observation": "ok", "nudge": None})
    )

    compose_digest(sb, USER_ID, anthropic_client=anth)

    # The user-role message is the only place transaction data can leak.
    user_msgs = [m for m in anth.captured_messages if m["role"] == "user"]
    assert user_msgs, "expected at least one user message to Sonnet"
    blob = json.dumps(user_msgs)
    assert "BLUE BOTTLE COFFEE SECRET MARKER" not in blob
    # The literal merchant *value* never reaches Sonnet. (The word
    # "merchant" appears in the prompt copy itself — "no merchant
    # names, no transaction list" — which is the prompt acknowledging
    # the boundary, not a leak.)
    assert "BLUE BOTTLE" not in blob.upper().replace("MERCHANT", "")
    # Aggregates must be present (positive control).
    assert "week_total" in blob
    assert "baseline_avg" in blob


def test_sonnet_failure_falls_back():
    """Sonnet 5xx → fallback narrative; success=false on the call log."""

    class _BoomAnthropic:
        """Anthropic stub that always 5xx's — verifies the fallback path."""
        def __init__(self):
            """Initialize the bare-minimum messages delegate."""
            self.messages = self
        def create(self, **_kwargs):
            """Always raise to simulate an Anthropic 5xx."""
            raise RuntimeError("upstream 500")

    week_start = _previous_week_start_et()
    sb = _FakeSupabase(transactions=_make_transactions(week_start))
    payload, call_log = compose_digest(sb, USER_ID, anthropic_client=_BoomAnthropic())

    assert call_log.success is False
    assert call_log.error_code == "RuntimeError"
    # The email still ships with a deterministic observation rather
    # than a silent abort.
    assert payload.observation


def test_render_email_shape():
    """Rendered email is ≤5 content blocks; both HTML and plaintext present."""
    payload = DigestPayload(
        user_id=USER_ID,
        week_start=datetime(2026, 5, 11, tzinfo=ET),
        week_end=datetime(2026, 5, 17, 23, 59, 59, tzinfo=ET),
        week_total=Decimal("300.00"),
        baseline_avg=Decimal("200.00"),
        top_category=CategoryRollup(
            category="Dining",
            week_total=Decimal("150.00"),
            baseline_avg=Decimal("100.00"),
        ),
        home_currency="USD",
        observation="Dining was higher than usual this week.",
        nudge=None,
    )
    rendered = render_email(payload, unsubscribe_url="https://x/unsub?u=1&token=t")
    assert rendered.subject.startswith("Tameru")
    # Plaintext: total + top + observation + unsubscribe = 4 blocks
    # (no nudge in this fixture).
    text_blocks = [b for b in rendered.text.strip().split("\n\n") if b.strip()]
    assert len(text_blocks) == 4
    assert "Unsubscribe: https://x/unsub" in rendered.text
    # HTML carries both inline-styled paragraphs and the unsubscribe link.
    assert 'style="' in rendered.html
    assert "Unsubscribe" in rendered.html
    # Inline style only — no <style> block, no class names.
    assert "<style" not in rendered.html
    assert ' class="' not in rendered.html


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _previous_week_start_et() -> datetime:
    """Compute the same Monday-ET that compose_digest will use."""
    now_et = datetime.now(ET)
    today_et = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
    monday_this_week = today_et - timedelta(days=today_et.weekday())
    return monday_this_week - timedelta(days=7)


def _make_transactions(week_start: datetime) -> list[dict]:
    """Seed two weeks of data: $300 this week (dining-heavy), $200/week prior."""
    txs = []
    # Last week: 3x $50 dining, 1x $100 transport, 1x $50 groceries.
    for i, (amt, cat) in enumerate([
        ("50.00", "Dining"),
        ("50.00", "Dining"),
        ("50.00", "Dining"),
        ("100.00", "Transport"),
        ("50.00", "Groceries"),
    ]):
        txs.append({
            "date": (week_start + timedelta(days=i)).date().isoformat(),
            "amount": amt,
            "category": cat,
        })
    # Eight prior weeks: $200/wk evenly split. Average rolls up below.
    for week_back in range(1, 9):
        prior_start = week_start - timedelta(weeks=week_back)
        txs.append({
            "date": prior_start.date().isoformat(),
            "amount": "100.00",
            "category": "Dining",
        })
        txs.append({
            "date": (prior_start + timedelta(days=2)).date().isoformat(),
            "amount": "100.00",
            "category": "Transport",
        })
    return txs
