"""Structural guard for CLAUDE.md invariant 8 — Day 9b.

No agent tool may call a writing method on the Supabase client (`.insert(`,
`.upsert(`, `.update(`, `.delete(`, `.rpc(`) unless it is explicitly
allow-listed. The propose-then-confirm pattern is the load-bearing UX rule:
no row exists until the user taps "looks right." `set_goal` is the lone
direct-write carve-out (DESIGN.md §7.2 — goals are low-risk and reversible).

Adding a tool to ALLOWED_DIRECT_WRITE_TOOLS should require a PR comment
explaining why the row is "low-risk and reversible" enough to skip the
propose flow. The same parametrize-over-registry pattern is used at
`tests/contracts/test_no_service_role_leak.py` (which is grep-based, not
runtime-based — this test is the behavioral complement).

Mocking strategy: replace `supabase_for_user` with a recording stub that
returns chainable proxies. Each method call appended to a recorded list as
a `.method(` string. We don't try to make the stub return useful data —
tools may fail without a real DB, and an exception inside the executor is
fine (we wrap in try/except). What matters is whether any forbidden write
method made it onto the recording.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.agent import tools as tools_module
from app.agent.tools import TOOL_REGISTRY, execute_tool
from app.auth import AuthedUser
from app.integrations.gemini import GeminiProviderError


FORBIDDEN_WRITE_METHODS = (".insert(", ".upsert(", ".update(", ".delete(", ".rpc(")
ALLOWED_DIRECT_WRITE_TOOLS = {"set_goal"}


@pytest.mark.parametrize("tool_name", list(TOOL_REGISTRY.keys()))
def test_tool_does_not_write_unless_allowlisted(tool_name, monkeypatch):
    """Reject any non-allowlisted tool that touches a writing Supabase method.

    Walks every tool in the registry. For each, dispatches with minimal
    input through `execute_tool` so the loop's call shape is exercised.
    The recording stub captures every method invocation; any forbidden
    write from a non-allowlisted tool fails the test with a pointer to
    the allowlist.
    """
    if tool_name in ALLOWED_DIRECT_WRITE_TOOLS:
        pytest.skip("explicitly allowed to write")

    recorded: list[str] = []
    fake_client = _RecordingClient(recorded)
    monkeypatch.setattr(tools_module, "supabase_for_user", lambda jwt: fake_client)
    # Tools that call categorize() would otherwise hit Gemini — neutralize.
    monkeypatch.setattr(tools_module, "categorize", _fake_categorize_raise)

    fake_user = AuthedUser(
        jwt="fake.jwt.token",
        user_id=uuid.uuid4(),
        email="invariant-guard@tameru.local",
    )

    try:
        execute_tool(tool_name, _minimal_args(tool_name), fake_user)
    except Exception:
        # Tools may legitimately fail without a real DB / real Gemini /
        # validation; we only care about what got recorded BEFORE the
        # failure. A successful pass-through to a write method records
        # before raising.
        pass

    offenders = [
        call
        for call in recorded
        for forbidden in FORBIDDEN_WRITE_METHODS
        if forbidden in call
    ]
    assert not offenders, (
        f"Tool {tool_name!r} called a write method but is not in "
        f"ALLOWED_DIRECT_WRITE_TOOLS. Offending calls: {offenders}. "
        f"If this is intentional, add {tool_name!r} to "
        f"ALLOWED_DIRECT_WRITE_TOOLS with a PR comment explaining why "
        f"the row is low-risk and reversible enough to skip the "
        f"propose-then-confirm flow (CLAUDE.md invariant 8)."
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


class _RecordingClient:
    """Chainable Supabase stub that records every method call.

    Real `supabase.Client` instances return query-builder objects from
    `.table(...)`, each of which supports `.select(...).eq(...)` etc.
    This stub mimics that — every attribute access returns a proxy that
    appends `.method(args)` to the recorded list and returns itself.
    `.execute()` returns a sentinel with `data=[]` so the caller's
    `resp.data or []` path doesn't crash.
    """

    def __init__(self, recorded: list[str]):
        """Stash the shared recording list."""
        self._recorded = recorded

    def table(self, name: str) -> "_RecordingProxy":
        """Record the table() entry-point and return a chainable proxy."""
        self._recorded.append(f".table({name!r})")
        return _RecordingProxy(self._recorded)


class _RecordingProxy:
    """Chainable proxy returned by every method on _RecordingClient.

    Any method invocation records `.method(...)` and returns self, so
    `.select(...).eq(...).limit(1).execute()` works without us having to
    enumerate the PostgREST query-builder surface.
    """

    def __init__(self, recorded: list[str]):
        """Stash the shared recording list."""
        self._recorded = recorded

    def __getattr__(self, name: str):
        """Record any method call as `.name(...)` and return self."""
        def _call(*args: Any, **kwargs: Any) -> Any:
            """Append a recording entry and return self/_Resp to chain."""
            self._recorded.append(f".{name}(...)")
            if name == "execute":
                return _Resp(data=[])
            return self
        return _call


class _Resp:
    """Mimic the `APIResponse` shape — only the `data` attribute is read."""

    def __init__(self, data: list[Any]):
        """Hold the canned data field."""
        self.data = data


def _fake_categorize_raise(*_args: Any, **_kwargs: Any) -> Any:
    """Avoid real Gemini calls; drive propose_transaction's fallback path.

    Must raise a GeminiError subclass (not a bare RuntimeError) because
    propose_transaction only catches GeminiError. A non-GeminiError
    would propagate out of the categorize call and abort
    propose_transaction before the post-categorize Supabase calls run —
    leaving the card-lookup and any future post-categorize writes
    unobserved by the guard, which defeats the test's purpose.
    """
    raise GeminiProviderError("invariant-guard: categorize neutralized")


def _minimal_args(tool_name: str) -> dict[str, Any]:
    """Return minimal valid input for each tool so the executor runs.

    A schema-driven generator would be cleaner but propose_transaction
    has required fields with semantic constraints (positive amount, real
    date) that a generic synth can't honor without becoming as complex
    as the validators. Hand-rolled per tool is simpler and reads better
    on test failure.
    """
    if tool_name == "propose_transaction":
        return {
            "merchant": "Test Merchant",
            "amount": 10,
            "date": "2026-05-13",
        }
    if tool_name == "propose_card":
        return {
            "network": "visa",
            "last_four": "1234",
            "program": "Test Card",
        }
    if tool_name == "set_goal":
        return {"amount": 100, "period": "month"}
    return {}
