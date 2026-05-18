"""Day 16 — render_user_memory failure must not 500 the chat turn.

Memory is enrichment, not load-bearing. If user_memory is unreachable
(DB blip, RLS misfire, anything), the chat turn must still complete —
the rendered system prompt just omits the memory block. Parity with
render_user_merchants's "graceful degrade to empty string" posture.
"""

from __future__ import annotations

import pytest


from app.agent import memory as memory_module  # noqa: E402
from app.prompts.chat import render_system_prompt  # noqa: E402




pytestmark = pytest.mark.usefixtures("clean_memory")


def test_render_user_memory_error_does_not_crash_render_system_prompt(
    user_a, monkeypatch,
):
    """If render_user_memory raises, render_system_prompt still returns a
    well-formed two-block array; block[1] contains the merchants/date
    tail but no memory section."""

    def _explode(*_a, **_kw):
        """Stand-in that simulates a DB error during memory read."""
        raise RuntimeError("simulated user_memory read failure")

    monkeypatch.setattr(memory_module, "render_user_memory", _explode)
    # The prompts module imports render_user_memory by name during
    # Day 16 wiring — patch the binding the caller uses, not just the
    # source module. We deliberately patch both surfaces below.
    import app.prompts.chat as chat_module
    if hasattr(chat_module, "render_user_memory"):
        monkeypatch.setattr(chat_module, "render_user_memory", _explode)

    # Must not raise.
    rendered = render_system_prompt(user_jwt=user_a.jwt)

    assert isinstance(rendered, list) and len(rendered) == 2
    assert "What I know about this user" not in rendered[1]["text"], (
        "memory header rendered despite render_user_memory raising — "
        "the failure path should produce no memory block"
    )
    # Block 1 still has its other expected content.
    assert "Today is" in rendered[1]["text"]
