"""Versioned judge prompt for the multi-hop final-answer quality dashboard.

The Tameru eval harness (DESIGN.md §7.10) scores everything deterministically
— exact amounts, tool-sequence multisets, regex + value extraction on prose.
That is the right tool for typed-tool trajectories and it owns the CI *gate*.
But it cannot read *helpfulness* or *tone*, and its numerical check only asks
"does the right number appear somewhere in the text" — not "did the answer
actually resolve the question, in Tameru's voice."

This module backs a NON-GATING dashboard layer that fills exactly that gap:
a stronger model (Sonnet by default — deliberately not the Haiku student it
is grading) scores the multi-hop suite's final-answer prose on two 1–5
dimensions — helpfulness and tone — i.e. only the qualities the deterministic
checks CAN'T read. Numerical correctness is deliberately NOT a judge dimension:
the deterministic `multi_hop.final_answer` check already meters whether the
right value appears, so a judge score there would overlap an assertion and
muddy the crisp split (deterministic owns everything assertable; the judge
owns only the unassertable). The scores warn but never breach — judge drift
must never flip CI (memory.md 2026-05-20, the deterministic-eval decision,
whose "Alternatives considered" named this hybrid as the sanctioned future
enhancement).

JUDGE_PROMPT_VERSION is snapshotted into each eval run's `prompt_versions`
so a dashboard shift can be triaged against a known rubric revision.

Version log:
  * judge_v1 — two dimensions (helpfulness, tone), 1–5 each, forced
    `record_judgment` tool, tone anchored on the chat SYSTEM_PROMPT "Style"
    section + §6.2/§6.3 voice rules (warm, delta-framed, no guilt framing,
    no bare absolutes). numerical_accuracy was considered and dropped —
    overlaps the deterministic final-answer check.
"""

from __future__ import annotations

import json
from typing import Any

JUDGE_PROMPT_VERSION = "judge_v1"

# Forced-tool schema. tool_choice pins this tool so the judge cannot answer in
# prose — every call returns the same validated shape. Scores are integers
# 1–5; the harness normalizes to 0–1 via (x-1)/4. Rationales are one line each
# and exist for the per-run JSON, not for scoring.
JUDGE_TOOL: dict[str, Any] = {
    "name": "record_judgment",
    "description": (
        "Record the 1–5 quality scores for the assistant's final answer. "
        "Call this exactly once."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "helpfulness": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "description": "Did the answer actually resolve the user's question, clearly and directly?",
            },
            "helpfulness_rationale": {"type": "string"},
            "tone": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "description": "Does the answer match Tameru's voice (warm, brief, delta-framed, no guilt framing)?",
            },
            "tone_rationale": {"type": "string"},
        },
        "required": [
            "helpfulness",
            "helpfulness_rationale",
            "tone",
            "tone_rationale",
        ],
    },
}

JUDGE_SYSTEM_PROMPT = """\
You are a strict quality grader for Tameru, a personal spending-intelligence \
assistant. You are scoring the assistant's FINAL prose answer to a user's \
question about their own spending. You are NOT the assistant — do not answer \
the question yourself; only grade the answer you are given.

Score two dimensions, each an integer 1–5, by calling the `record_judgment` \
tool exactly once. Be calibrated: 3 is an acceptable answer, 5 is reserved for \
genuinely excellent, 1–2 means a real defect. Do not inflate. (Numerical \
correctness is graded separately by a deterministic check — do NOT score it \
here; the data below is context for judging helpfulness, not a math test.)

## helpfulness
Did the answer actually resolve the question, clearly and directly? The data \
the assistant retrieved is provided below so you can tell whether the answer \
used it or ignored/contradicted it.
  5 — answers exactly what was asked, grounded in the retrieved data, no \
hedging, no irrelevant padding.
  3 — answers the question but is verbose, slightly off-target, or buries it.
  1 — does not answer, asks for something already provided, ignores the \
retrieved data, or goes off-topic.
A correct clarifying question (when the user genuinely omitted a needed window \
or filter) is helpful — score it well. Refusing to answer something answerable \
is not.

## tone
Does the answer match Tameru's voice?
  - Brief, plain prose. One or two sentences is usually right. No markdown \
headers; no bullet lists unless the user asked for a breakdown.
  - Warm and non-judgmental. Spending facts are framed as observations or \
deltas ("about $40 more than your dining average"), NEVER as guilt or scolding \
("you spend way too much on takeout") and never as a bare absolute judgment.
  - Honest about proposals: the assistant must not claim a transaction, card, \
or subscription was added/saved — those are only proposed for the user to \
confirm.
  5 — on-voice, warm, appropriately brief.
  3 — correct but slightly stiff, too long, or over-formatted.
  1 — judgmental/guilt-framing, falsely claims something was saved, or is a \
wall of markdown.
"""


def build_judge_user_content(
    *,
    question: str,
    assistant_answer: str,
    retrieved_data: list[dict[str, Any]] | None,
) -> str:
    """Render the judge's user-turn content for one multi-hop row.

    Bundles everything the (helpfulness + tone) rubric references into one
    labeled block: the user's original question, the assistant's final answer
    (the thing being graded), and the tool-call results the assistant actually
    retrieved — so helpfulness can be judged on whether the answer used the
    data rather than ignored or contradicted it. No ground-truth value is
    included: numerical correctness is the deterministic check's job, not the
    judge's.

    Request shape (kwargs):
      * question: the user's multi-hop prompt.
      * assistant_answer: `turn.assistant_text` — the prose under judgment.
      * retrieved_data: a list of `{tool, result}` dicts for the turn's tool
        calls, or None when the turn made no tool calls.

    Response shape: a single string suitable for a `messages=[{role:"user",
    content: <this>}]` turn. The structured scores come back via the forced
    `record_judgment` tool, not from parsing this content.
    """
    data_block = (
        json.dumps(retrieved_data, indent=2, default=str)
        if retrieved_data
        else "(the assistant made no data tool calls this turn)"
    )
    return (
        "## User question\n"
        f"{question}\n\n"
        "## Assistant's final answer (grade THIS)\n"
        f"{assistant_answer or '(empty answer)'}\n\n"
        "## Data the assistant retrieved this turn\n"
        f"{data_block}\n"
    )
