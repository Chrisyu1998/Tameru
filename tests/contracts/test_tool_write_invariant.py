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
returns chainable proxies — in the tools module AND in every module the
tools delegate to (services/transactions, services/goals,
integrations/gemini, integrations/aicalllog).
The audit (P3-14) found the original tools-module-only patch was blind to
writes routed through delegates: a refactor moving a ledger write into
app/services/ stayed green. Each recorded entry carries its table name so
the one sanctioned delegate write — the `ai_call_log` INSERT under the
user's JWT (CLAUDE.md invariant 14) — can be carved out without blessing
any other write. We don't try to make the stub return useful data — tools
may fail without a real DB, and an exception inside the executor is fine
(we wrap in try/except). What matters is whether any forbidden write
method made it onto the recording.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.agent import tools as tools_module
from app.agent.tools import TOOL_REGISTRY, execute_tool
from app.auth import AuthedUser
from app.integrations import aicalllog as aicalllog_module
from app.integrations import gemini as gemini_module
from app.integrations.gemini import GeminiProviderError
from app.services import goals as goals_module
from app.services import transactions as transactions_module


FORBIDDEN_WRITE_METHODS = (".insert(", ".upsert(", ".update(", ".delete(", ".rpc(")
ALLOWED_DIRECT_WRITE_TOOLS = {"set_goal"}

# Every module on a tool's call path that builds its own RLS-scoped client.
# A new delegate module with its own `supabase_for_user` import must be
# added here or its writes are invisible to this guard (audit P3-14).
_CLIENT_BUILDING_MODULES = (
    tools_module,
    transactions_module,
    goals_module,
    gemini_module,
    # card_lookup has no client of its own — it logs through aicalllog.
    aicalllog_module,
)

# The one sanctioned delegate write: log_ai_call's INSERT into ai_call_log
# under the user's JWT (CLAUDE.md invariant 14). (table, method) pairs.
_SANCTIONED_DELEGATE_WRITES = {("ai_call_log", "insert")}


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
    for module in _CLIENT_BUILDING_MODULES:
        monkeypatch.setattr(module, "supabase_for_user", lambda jwt: fake_client)
    # propose_transaction reaches categorize() through
    # services/transactions.build_transaction_proposal now (P3-14 delegate
    # shape) — patch the name in that module's namespace, not on tools, so the
    # fallback path runs without hitting Gemini.
    monkeypatch.setattr(transactions_module, "categorize", _fake_categorize_raise)

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
        if forbidden in call and not _is_sanctioned(call)
    ]
    assert not offenders, (
        f"Tool {tool_name!r} called a write method but is not in "
        f"ALLOWED_DIRECT_WRITE_TOOLS. Offending calls: {offenders}. "
        f"If this is intentional, add {tool_name!r} to "
        f"ALLOWED_DIRECT_WRITE_TOOLS with a PR comment explaining why "
        f"the row is low-risk and reversible enough to skip the "
        f"propose-then-confirm flow (CLAUDE.md invariant 8)."
    )


def test_direct_write_allowlist_is_exactly_set_goal():
    """Pin ALLOWED_DIRECT_WRITE_TOOLS == {"set_goal"} — widening fails CI.

    The per-tool guard above merely *skips* allowlisted names, so before
    this pin, adding a tool to the allowlist silently exempted it —
    CLAUDE.md's "fails the build if anyone widens ... without a rationale
    comment" overstated the enforcement (audit P3-16). Now widening
    requires editing this assertion too, which is the mechanical moment
    to supply the rationale CLAUDE.md invariant 8 demands (explicit user
    approval; the row must be low-risk, reversible, and off the
    transaction ledger, like goals).
    """
    assert ALLOWED_DIRECT_WRITE_TOOLS == {"set_goal"}, (
        "ALLOWED_DIRECT_WRITE_TOOLS was widened beyond set_goal. Adding a "
        "direct-write agent tool requires explicit user approval per "
        "CLAUDE.md invariant 8 — update this test in the same change with "
        "the rationale."
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


class _RecordingClient:
    """Chainable Supabase stub that records every method call.

    Real `supabase.Client` instances return query-builder objects from
    `.table(...)`, each of which supports `.select(...).eq(...)` etc.
    This stub mimics that — every attribute access returns a proxy that
    appends `[table].method(...)` to the recorded list and returns
    itself. `.execute()` returns a sentinel with `data=[]` so the
    caller's `resp.data or []` path doesn't crash. Client-level `.rpc(`
    is recorded explicitly — it used to AttributeError on this stub and
    vanish into the test's try/except, leaving rpc-routed writes
    unobserved.
    """

    def __init__(self, recorded: list[str]):
        """Stash the shared recording list."""
        self._recorded = recorded

    def table(self, name: str) -> "_RecordingProxy":
        """Record the table() entry-point and return a chainable proxy."""
        self._recorded.append(f".table({name!r})")
        return _RecordingProxy(self._recorded, table=name)

    def rpc(self, name: str, *args: Any, **kwargs: Any) -> "_RecordingProxy":
        """Record a client-level RPC call and return a chainable proxy."""
        self._recorded.append(f"[rpc:{name}].rpc(...)")
        return _RecordingProxy(self._recorded, table=f"rpc:{name}")


class _RecordingProxy:
    """Chainable proxy returned by every method on _RecordingClient.

    Any method invocation records `[table].method(...)` and returns
    self, so `.select(...).eq(...).limit(1).execute()` works without us
    having to enumerate the PostgREST query-builder surface. The table
    tag lets the offender filter carve out the sanctioned `ai_call_log`
    INSERT without blessing the same method on any other table.
    """

    def __init__(self, recorded: list[str], table: str = "?"):
        """Stash the shared recording list and the table context."""
        self._recorded = recorded
        self._table = table

    def __getattr__(self, name: str):
        """Record any method call as `[table].name(...)` and return self."""
        def _call(*args: Any, **kwargs: Any) -> Any:
            """Append a recording entry and return self/_Resp to chain."""
            self._recorded.append(f"[{self._table}].{name}(...)")
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
    if tool_name == "propose_subscription":
        # Without valid args, Pydantic raises before the tool body runs
        # and the body's Supabase calls are never exercised by this
        # guard (audit P3-15).
        return {
            "name": "Test Sub",
            "amount": 9.99,
            "frequency": "monthly",
            "start_date": "2026-05-01",
        }
    if tool_name == "set_goal":
        return {"amount": 100, "period": "month"}
    return {}


def _is_sanctioned(call: str) -> bool:
    """True iff this recorded write is the invariant-14 ai_call_log INSERT.

    The carve-out is (table, method)-exact: `[ai_call_log].insert(...)`
    only. An UPDATE/DELETE on ai_call_log, or an insert on any other
    table routed through a delegate module, still fails the guard.
    """
    return any(
        call.startswith(f"[{table}].{method}(") for table, method in _SANCTIONED_DELEGATE_WRITES
    )
