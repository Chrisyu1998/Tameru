"""Unit tests for the eval harness LLM-as-judge dashboard (DESIGN.md §7.10).

Pure-unit: no Supabase stack and no live Anthropic call. The judge is a
NON-GATING quality dashboard over the multi_hop final-answer prose, scoring
only the two qualities the deterministic checks can't read — helpfulness and
tone (numerical correctness is the deterministic final_answer check's job).
These tests pin its score normalization, structured-output extraction,
model/toggle resolution, and — most importantly — its fail-closed behavior: a
judge API or parse error must skip the row (return None) rather than score it
zero, so a judge outage shrinks the sample instead of poisoning the dashboard
or failing the build.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import eval as eval_harness


@dataclass
class _Block:
    """Minimal Anthropic content block (attribute access, like the SDK)."""

    type: str
    name: str | None = None
    input: dict[str, Any] | None = None
    text: str | None = None


@dataclass
class _Resp:
    """Minimal Messages response — only `.content` is read by the harness."""

    content: list[Any]


class _ScriptedJudgeClient:
    """Anthropic client double returning one scripted Messages response.

    Mirrors test_agent_loop's `_ScriptedClient` shape; the judge makes
    exactly one `.messages.create()` call per row. Either returns
    `response` or raises `raise_exc` to exercise the fail-closed path.
    """

    def __init__(self, response: Any = None, raise_exc: Exception | None = None) -> None:
        """Store the scripted response (or the exception to raise)."""
        self._response = response
        self._raise = raise_exc
        self.call_count = 0
        self.last_kwargs: dict[str, Any] = {}
        outer = self

        class _Messages:
            """Captures call kwargs and returns/raises the scripted outcome."""

            def create(self, **kwargs: Any) -> Any:
                """Record the call and return the scripted response or raise."""
                outer.call_count += 1
                outer.last_kwargs = kwargs
                if outer._raise is not None:
                    raise outer._raise
                return outer._response

        self.messages = _Messages()


def test_normalize_maps_endpoints() -> None:
    """1→0.0 and 5→1.0; raw 1–5 ints preserved for the rollup sum."""
    out = eval_harness._normalize_judgment({"helpfulness": 1, "tone": 5})
    assert out is not None
    assert out["helpfulness_score"] == 0.0
    assert out["tone_score"] == 1.0
    assert out["helpfulness"] == 1 and out["tone"] == 5


def test_normalize_maps_midpoint() -> None:
    """3 normalizes to 0.5 — the (x-1)/4 midpoint."""
    out = eval_harness._normalize_judgment({"helpfulness": 3, "tone": 3})
    assert out is not None
    assert out["helpfulness_score"] == 0.5
    assert out["tone_score"] == 0.5


def test_normalize_clamps_out_of_range_scores() -> None:
    """Scores outside 1–5 are clamped, not rejected (model over/undershoot)."""
    out = eval_harness._normalize_judgment({"helpfulness": 9, "tone": 0})
    assert out is not None
    assert out["helpfulness"] == 5
    assert out["tone"] == 1


def test_normalize_returns_none_on_missing_dimension() -> None:
    """A judgment missing either dimension is a skipped row."""
    assert eval_harness._normalize_judgment({"helpfulness": 4}) is None


def test_normalize_returns_none_on_non_numeric() -> None:
    """A non-numeric score is a parse failure → skipped row, not a guess."""
    assert eval_harness._normalize_judgment({"helpfulness": "good", "tone": 4}) is None


def test_extract_tool_input_finds_record_judgment_block() -> None:
    """The first matching tool_use block's input is returned (SDK-shaped)."""
    resp = _judgment_response(4, 4)
    got = eval_harness._extract_tool_input(resp, "record_judgment")
    assert got is not None and got["helpfulness"] == 4


def test_extract_tool_input_ignores_text_blocks() -> None:
    """A response with no matching tool_use block yields None."""
    resp = _Resp(content=[_Block(type="text", text="hi")])
    assert eval_harness._extract_tool_input(resp, "record_judgment") is None


def test_extract_tool_input_handles_dict_blocks() -> None:
    """Dict-shaped blocks (test doubles) are tolerated alongside SDK objects."""
    resp = _Resp(
        content=[{"type": "tool_use", "name": "record_judgment", "input": {"helpfulness": 2}}]
    )
    assert eval_harness._extract_tool_input(resp, "record_judgment") == {"helpfulness": 2}


def test_judge_row_happy_path(monkeypatch) -> None:
    """A valid forced-tool judgment is normalized; forced tool_choice passed."""
    client = _ScriptedJudgeClient(response=_judgment_response(5, 5))
    _patch_client(monkeypatch, client)
    out = eval_harness._judge_multi_hop_row(
        question="how much on dining in March?",
        assistant_answer="About $250 — roughly $40 over your monthly average.",
        retrieved_data=[{"tool": "get_spending_summary", "result": {"dining": 250}}],
    )
    assert client.call_count == 1
    assert client.last_kwargs["tool_choice"] == {"type": "tool", "name": "record_judgment"}
    assert client.last_kwargs["temperature"] == 0
    assert out is not None
    assert out["helpfulness"] == 5 and out["tone_score"] == 1.0


def test_judge_row_returns_none_on_api_error(monkeypatch) -> None:
    """A raised exception from the API is swallowed → skipped row (None)."""
    client = _ScriptedJudgeClient(raise_exc=RuntimeError("boom"))
    _patch_client(monkeypatch, client)
    out = eval_harness._judge_multi_hop_row(
        question="q",
        assistant_answer="a",
        retrieved_data=[],
    )
    assert out is None
    assert client.call_count == 1


def test_judge_row_returns_none_when_no_tool_block(monkeypatch) -> None:
    """A response without the forced tool block is a skipped row (None)."""
    client = _ScriptedJudgeClient(response=_Resp(content=[_Block(type="text", text="no tool")]))
    _patch_client(monkeypatch, client)
    out = eval_harness._judge_multi_hop_row(
        question="q",
        assistant_answer="a",
        retrieved_data=[],
    )
    assert out is None


def test_judge_model_default_is_sonnet(monkeypatch) -> None:
    """With no override the judge defaults to the Sonnet grader."""
    monkeypatch.delenv("ANTHROPIC_JUDGE_MODEL", raising=False)
    assert eval_harness._judge_model() == "claude-sonnet-4-6"


def test_judge_model_env_override(monkeypatch) -> None:
    """ANTHROPIC_JUDGE_MODEL overrides the default grader."""
    monkeypatch.setenv("ANTHROPIC_JUDGE_MODEL", "claude-opus-4-8")
    assert eval_harness._judge_model() == "claude-opus-4-8"


def test_judge_enabled_toggle(monkeypatch) -> None:
    """EVAL_JUDGE=0 disables the pass; the default (unset/1) enables it."""
    monkeypatch.setenv("EVAL_JUDGE", "0")
    assert eval_harness._judge_enabled() is False
    monkeypatch.setenv("EVAL_JUDGE", "1")
    assert eval_harness._judge_enabled() is True
    monkeypatch.delenv("EVAL_JUDGE", raising=False)
    assert eval_harness._judge_enabled() is True


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _judgment_response(helpfulness: int, tone: int) -> _Resp:
    """Build a Messages response carrying one `record_judgment` tool_use block."""
    return _Resp(
        content=[
            _Block(
                type="tool_use",
                name="record_judgment",
                input={
                    "helpfulness": helpfulness,
                    "helpfulness_rationale": "h",
                    "tone": tone,
                    "tone_rationale": "t",
                },
            )
        ]
    )


def _patch_client(monkeypatch, client: _ScriptedJudgeClient) -> None:
    """Point the lazy Anthropic client constructor at the scripted double.

    `_judge_multi_hop_row` imports `_anthropic_client` from app.agent.loop at
    call time, so patching the module attribute is sufficient — no real key
    or singleton reset needed.
    """
    import app.agent.loop as loop_module

    monkeypatch.setattr(loop_module, "_anthropic_client", lambda: client)
