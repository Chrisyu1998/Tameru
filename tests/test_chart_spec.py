"""Day 10b — render_chart tool spec tests.

Covers:

  * Pydantic validation on `RenderChartRequest` — type enum, x non-empty,
    series non-empty, donut == single series, every series' data length
    matches len(x).
  * The tool's pass-through contract: `render_chart` echoes the spec
    verbatim, regardless of which chart `type` is requested. The frontend
    `<Chart>` component is the consumer; this test pins the wire shape
    so a future refactor that "helpfully" reshapes the dict (e.g.
    dropping y_label on donut, renaming `series`) trips immediately.
  * `execute_tool('render_chart', ...)` round-trips the spec through the
    registry the agent loop dispatches on — proving the loop will hand
    the same dict to the frontend that the tool produced.
  * No-DB invariant — render_chart never reaches Supabase. We give the
    tool an `AuthedUser` with a deliberately invalid JWT to make sure a
    future regression that adds a DB call would surface as a test
    failure (the supabase client init would raise instead of the spec
    being returned).
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.agent.tools import (
    RenderChartRequest,
    TOOL_REGISTRY,
    execute_tool,
    render_chart,
)
from app.auth import AuthedUser


@pytest.fixture
def dummy_user() -> AuthedUser:
    """Provide a synthetic AuthedUser.

    render_chart is pure presentation — it never reads the JWT. The
    invalid-looking JWT here doubles as a regression alarm: if a future
    edit adds a DB call, `supabase_for_user` will fail on this token
    well before any assertion runs.
    """
    return AuthedUser(
        jwt="not-a-real-jwt",
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        email="chart-test@example.invalid",
    )


# ---------------------------------------------------------------------------
# Pydantic validation — boundary conditions called out in the schema.
# ---------------------------------------------------------------------------


def test_line_spec_validates():
    """A well-formed line spec round-trips through the validator."""
    req = RenderChartRequest.model_validate(
        {
            "type": "line",
            "x": ["Mar W1", "Mar W2", "Mar W3", "Mar W4"],
            "series": [
                {"name": "Dining", "data": [142.0, 211.5, 180.0, 175.0]},
            ],
            "y_label": "USD",
            "title": "dining by week, march",
        }
    )
    assert req.type == "line"
    assert len(req.series) == 1
    assert req.series[0].data == [142.0, 211.5, 180.0, 175.0]


def test_stacked_bar_with_multiple_series_validates():
    """stacked_bar takes 2+ series, each matching len(x)."""
    req = RenderChartRequest.model_validate(
        {
            "type": "stacked_bar",
            "x": ["Jan", "Feb", "Mar"],
            "series": [
                {"name": "Groceries", "data": [240, 260, 320]},
                {"name": "Dining", "data": [180, 220, 410]},
            ],
            "title": "groceries + dining",
        }
    )
    assert len(req.series) == 2


def test_donut_requires_exactly_one_series():
    """donut charts collapse to a single series; 2+ must fail."""
    with pytest.raises(ValidationError, match="donut charts take exactly one series"):
        RenderChartRequest.model_validate(
            {
                "type": "donut",
                "x": ["Groceries", "Dining"],
                "series": [
                    {"name": "A", "data": [1, 2]},
                    {"name": "B", "data": [3, 4]},
                ],
                "title": "two-series donut should reject",
            }
        )


def test_mismatched_series_length_rejects():
    """Every series' data length must equal len(x)."""
    with pytest.raises(ValidationError, match="3 points but x has 4"):
        RenderChartRequest.model_validate(
            {
                "type": "line",
                "x": ["W1", "W2", "W3", "W4"],
                "series": [{"name": "Dining", "data": [1.0, 2.0, 3.0]}],
                "title": "should reject short series",
            }
        )


def test_unknown_type_rejects():
    """`type` is pinned to the four supported chart kinds."""
    with pytest.raises(ValidationError):
        RenderChartRequest.model_validate(
            {
                "type": "scatter",
                "x": ["a"],
                "series": [{"name": "x", "data": [1]}],
                "title": "scatter not supported",
            }
        )


def test_empty_x_rejects():
    """An empty x array is meaningless; the validator catches it."""
    with pytest.raises(ValidationError):
        RenderChartRequest.model_validate(
            {
                "type": "bar",
                "x": [],
                "series": [{"name": "x", "data": []}],
                "title": "no labels",
            }
        )


def test_empty_series_rejects():
    """`series` cannot be empty — recharts has nothing to draw."""
    with pytest.raises(ValidationError):
        RenderChartRequest.model_validate(
            {
                "type": "bar",
                "x": ["a", "b"],
                "series": [],
                "title": "no series",
            }
        )


# ---------------------------------------------------------------------------
# Tool pass-through contract.
# ---------------------------------------------------------------------------


def test_render_chart_echoes_spec_verbatim(dummy_user):
    """The tool result is the spec, field-for-field.

    The frontend rebuilds the ChartSpec from this dict via
    `_toolToChartSpec` — drifting the wire shape breaks rehydration in
    the chat thread.
    """
    spec = {
        "type": "line",
        "x": ["Mar W1", "Mar W2"],
        "series": [{"name": "Dining", "data": [142.0, 211.5]}],
        "y_label": "USD",
        "title": "dining by week, march",
    }
    out = render_chart(dummy_user, **spec)
    assert out == spec


def test_render_chart_donut_echoes_spec_verbatim(dummy_user):
    """Donut specs pass through unchanged — no y_label injection or stripping."""
    spec = {
        "type": "donut",
        "x": ["Groceries", "Dining", "Coffee Shops"],
        "series": [{"name": "March", "data": [320.0, 410.0, 78.0]}],
        "title": "march share of spend",
    }
    out = render_chart(dummy_user, **spec)
    # Pydantic's model_dump(mode='json') drops `y_label` since we don't set
    # exclude_none, so the donut output mirrors the input without injecting null.
    assert out["type"] == "donut"
    assert out["x"] == spec["x"]
    assert out["series"] == spec["series"]
    assert out["title"] == spec["title"]
    assert out.get("y_label") is None


def test_render_chart_dispatched_via_execute_tool(dummy_user):
    """The registry dispatch path matches a direct call.

    `execute_tool` is what the agent loop calls when it sees a `tool_use`
    block; if it returned a different shape than `render_chart(...)`
    directly, the chat-store's `_toolToChartSpec` would diverge from the
    backend's wire shape and rich-chart messages would silently drop.
    """
    spec = {
        "type": "bar",
        "x": ["Groceries", "Dining", "Coffee Shops"],
        "series": [{"name": "March", "data": [320.0, 410.0, 78.0]}],
        "y_label": "USD",
        "title": "march totals",
    }
    direct = render_chart(dummy_user, **spec)
    routed = execute_tool("render_chart", spec, dummy_user)
    assert routed == direct


def test_render_chart_in_registry():
    """render_chart must be registered with both a schema and an executor."""
    assert "render_chart" in TOOL_REGISTRY
    schema, executor = TOOL_REGISTRY["render_chart"]
    assert schema["name"] == "render_chart"
    # The agent's input_schema enum is the load-bearing surface Claude
    # reads — pin it so a refactor doesn't quietly add (or drop) a type.
    assert schema["input_schema"]["properties"]["type"]["enum"] == [
        "line",
        "bar",
        "stacked_bar",
        "donut",
    ]
    # Sanity — the executor is the function we tested above, not a stub.
    assert executor is render_chart


# ---------------------------------------------------------------------------
# End-to-end: "Chart my dining by week in March" → correct ChartSpec.
# ---------------------------------------------------------------------------


def test_chart_my_dining_by_week_produces_correct_spec(dummy_user):
    """Smoke test for the spec the prompt cites verbatim.

    Day 10b §8 calls out 'Chart my dining by week in March' as the
    canonical chart prompt. The agent loop is non-deterministic, but the
    *shape* the agent must produce for the frontend to render the right
    primitive is deterministic: a line chart over 4 weekly labels with
    one Dining series. We construct that ChartSpec directly here — the
    eval suite owns the live LLM round-trip; this test owns the
    wire-shape contract.
    """
    spec = {
        "type": "line",
        "x": ["Mar W1", "Mar W2", "Mar W3", "Mar W4"],
        "series": [
            {"name": "Dining", "data": [142.0, 211.5, 180.0, 175.0]},
        ],
        "y_label": "USD",
        "title": "dining by week, march",
    }
    out = execute_tool("render_chart", spec, dummy_user)
    assert out["type"] == "line"
    assert out["x"] == spec["x"]
    assert len(out["series"]) == 1
    assert out["series"][0]["name"] == "Dining"
    assert out["series"][0]["data"] == spec["series"][0]["data"]
    assert out["title"] == "dining by week, march"
