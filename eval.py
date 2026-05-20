#!/usr/bin/env python3
"""Tameru eval harness — DESIGN.md §7.10.

Three suites, one runner:

  * categorization — `(merchant, amount) -> expected_category`, scored
    against `categorize()` in app/integrations/gemini.py.
  * chat_extraction — `user_message -> tool_use(args)`, scored against
    the first proposer tool call returned by `run_turn()`. Covers
    `propose_transaction` (50 rows) and `propose_subscription` (10 rows).
    `propose_card` is intentionally not gated — UAT covers it.
  * multi_hop — `user_message -> sequence of tool_use blocks + final
    answer`, scored against the seeded fixtures from
    `scripts._eval_setup`.

Run:

    .venv/bin/python eval.py --eval=all
    .venv/bin/python eval.py --eval=chat_extraction --model=claude-haiku-4-5
    .venv/bin/python eval.py --report   # rebuilds local results.db

Per-run JSON lands in `evals/runs/<run_id>.json`. SQLite at
`evals/results.db` is gitignored and built locally by `--report`.

Exit code: 0 on every-gate-pass (warns on target miss), 1 on any breach.

Threshold split (DESIGN.md §7.10):

  Suite                                   Target   Gate
  --------------------------------------- ------   ----
  categorization                          90%      88%
  chat_extraction propose_transaction
      amount accuracy                     95%      93%
      merchant accuracy                   90%      —
  chat_extraction propose_subscription
      per-row pass                        90%      85%
  multi_hop tool sequence                 90%      85%
  multi_hop final answer                  95%      —

CLAUDE.md invariants honored:
  * No service-role bypass — all reads/writes use the eval user's JWT.
  * `ai_call_log` rows from eval turns land under the eval user (invariant
    14); trimmed weekly by pg_cron (migration 20260520120000).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent
EVALS_DIR = REPO_ROOT / "evals"
RUNS_DIR = EVALS_DIR / "runs"
RESULTS_DB = EVALS_DIR / "results.db"

# Eval runs pin "today" so date-relative corpus rows ("dining in March",
# "started Feb 14", "yesterday") resolve identically no matter when the
# suite runs. It is threaded into run_turn → the system prompt's "Today
# is …" line. The fixtures (scripts/_eval_setup) are dated Jan–Apr 2026;
# this sits just past them so every referenced date is recent-past.
# Without the pin the gate would drift — "March" would resolve to a
# different (empty) window in a later year and fail spuriously.
EVAL_TODAY = _dt.date(2026, 5, 19)

# Thresholds per DESIGN.md §7.10. (target, gate) — gate=None means
# warn-only (no CI block). Each enabled suite contributes one or more
# score keys to the per-run JSON; the exit-code computation walks every
# (score_key, target, gate) triple to decide warn vs block vs pass.
THRESHOLDS: dict[str, tuple[float, float | None]] = {
    "categorization.accuracy":                       (0.90, 0.88),
    "chat_extraction.propose_transaction.amount":    (0.95, 0.93),
    "chat_extraction.propose_transaction.merchant":  (0.90, None),
    "chat_extraction.propose_subscription.row_pass": (0.90, 0.85),
    "multi_hop.tool_sequence":                       (0.90, 0.85),
    "multi_hop.final_answer":                        (0.95, None),
}

ALL_SUITES = ("categorization", "chat_extraction", "multi_hop")


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


def main() -> int:
    """Parse args, run requested suites, write per-run JSON, return exit code."""
    # Load .env first — the suites call app code that reads ANTHROPIC_API_KEY
    # / GEMINI_API_KEY / SUPABASE_* from the environment. Without this a
    # local run with no exported keys fails every row with "API_KEY is not
    # set" and scores a misleading 0.000.
    _load_dotenv()
    args = _parse_args()
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    if args.report:
        _rebuild_results_db()
        return 0

    suites = _suites_from_arg(args.eval)
    if args.model:
        # The chat-extraction and multi-hop suites run the Anthropic
        # agent loop (run_turn → an Anthropic client). --model only
        # swaps the Claude chat model — a claude-* id. A cross-provider
        # A/B against Gemini Flash-Lite (DESIGN.md §11.4) needs a Gemini
        # chat path the loop does not have; routing a Gemini model id
        # into ANTHROPIC_MODEL would just send it to Anthropic and 4xx.
        if not args.model.lower().startswith("claude"):
            print(
                f"[error] --model={args.model!r} is not a Claude model. "
                f"--model swaps the Anthropic chat model only (a claude-* "
                f"id); the agent loop has no Gemini path, so a "
                f"cross-provider Flash-Lite A/B is a post-launch item "
                f"(DESIGN.md §11.4), not something this flag can do today.",
                file=sys.stderr,
            )
            return 2
        os.environ["ANTHROPIC_MODEL"] = args.model

    # Lift the per-user daily token cap for the eval run. The cap
    # (DEFAULT_DAILY_CAP_TOKENS = 200K, app/agent/middleware.py) is a
    # production cost control sized for ~10 human chat turns/day — a
    # full eval pass is ~80 synthetic turns and would trip it mid-suite,
    # spuriously failing every row after the threshold. The cap is read
    # from env on every turn, so overriding it here is enough; no app
    # code changes. The eval user is synthetic — there is no real spend
    # to control.
    os.environ["CHAT_USAGE_CAP_TOKENS_PER_DAY"] = "1000000000"

    # Setup — ensure user, fresh JWT, seeded fixtures. Imported here (not
    # top-level) so `eval.py --report` doesn't require a live Supabase
    # stack just to rebuild the local SQLite.
    from scripts._eval_setup import (
        ensure_eval_user_jwt,
        export_local_supabase_env,
        seed_fixtures,
    )

    # App code (categorize(), the agent tools) reaches Supabase via
    # app.db.supabase_for_user, which reads SUPABASE_URL / SUPABASE_ANON_KEY
    # from the environment — mirror the local stack's values in before
    # any suite runs.
    export_local_supabase_env()
    jwt, user_id = ensure_eval_user_jwt()
    setup = seed_fixtures(jwt, user_id)
    cards_by_name: dict[str, str] = setup["cards"]

    run_id = _new_run_id()
    timestamp = _dt.datetime.now(_dt.timezone.utc).isoformat()
    git_sha = _git_sha()
    model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5")
    prompt_versions = _collect_prompt_versions()

    suite_results: dict[str, dict[str, Any]] = {}
    scores: dict[str, float] = {}

    for suite in suites:
        print(f"[{suite}] running…", file=sys.stderr)
        if suite == "categorization":
            result = _run_categorization(jwt, user_id)
        elif suite == "chat_extraction":
            result = _run_chat_extraction(jwt, user_id, cards_by_name)
        elif suite == "multi_hop":
            result = _run_multi_hop(jwt, user_id, cards_by_name)
        else:
            raise RuntimeError(f"unknown suite {suite!r}")
        suite_results[suite] = result
        for key, score in result["scores"].items():
            scores[f"{suite}.{key}"] = score

    targets_met, gates_met, warnings, breaches = _evaluate_thresholds(scores)

    run_payload = {
        "run_id": run_id,
        "timestamp": timestamp,
        "git_sha": git_sha,
        "model": model,
        "prompt_versions": prompt_versions,
        "suites": list(suites),
        "scores": scores,
        "targets_met": targets_met,
        "gates_met": gates_met,
        "warnings": warnings,
        "breaches": breaches,
        "results": suite_results,
    }
    out_path = RUNS_DIR / f"{run_id}.json"
    out_path.write_text(json.dumps(run_payload, indent=2, default=str))
    print(f"[ok] wrote {out_path.relative_to(REPO_ROOT)}", file=sys.stderr)

    _print_summary(scores, warnings, breaches, suite_results)
    return 1 if breaches else 0


# ---------------------------------------------------------------------------
# Suite runners.
# ---------------------------------------------------------------------------


def _run_categorization(jwt: str, user_id: uuid.UUID) -> dict[str, Any]:
    """Score each `(merchant, amount) -> expected_category` row.

    Calls `categorize()` directly. Amount is parsed for YAML-shape
    fidelity but not passed (categorize_v3 dropped amount from the
    prompt). Rows where `categorize()` raises are scored as misses.
    """
    from app.auth import AuthedUser
    from app.integrations.gemini import categorize, GeminiError

    rows = _load_yaml("categorization.yaml")
    user = AuthedUser(jwt=jwt, user_id=user_id, email="eval@tameru.internal")
    per_row: list[dict[str, Any]] = []
    metas: list[dict[str, Any]] = []
    correct = 0
    for row in rows:
        merchant = row["merchant"]
        expected = row["expected_category"]
        try:
            suggestion = categorize(merchant, user)
            got = suggestion.category
            ok = got == expected
        except GeminiError:
            got = None
            ok = False
        if ok:
            correct += 1
        metas.append(_row_meta(row, ok))
        per_row.append({
            "merchant": merchant,
            "expected": expected,
            "got": got,
            "difficulty": row.get("difficulty", "medium"),
            "tags": row.get("tags") or [],
            "pass": ok,
        })
    n = max(len(rows), 1)
    return {
        "n_rows": len(rows),
        "scores": {"accuracy": correct / n},
        "breakdown": _aggregate_breakdown(metas),
        "rows": per_row,
    }


def _run_chat_extraction(
    jwt: str, user_id: uuid.UUID, cards_by_name: dict[str, str]
) -> dict[str, Any]:
    """Score `user_message -> first proposer tool_use(args)`.

    Runs each row through the full agent loop and inspects the first
    `tool_use` block produced. Returns separate scores for
    `propose_transaction` (amount accuracy, merchant accuracy) and
    `propose_subscription` (per-row pass).
    """
    from app.auth import AuthedUser
    from app.agent.loop import run_turn

    rows = _load_yaml("chat_extraction.yaml")
    user = AuthedUser(jwt=jwt, user_id=user_id, email="eval@tameru.internal")

    # Amount and merchant have separate denominators: a row that only
    # pins `amount` (the multilingual rows deliberately skip merchant —
    # canonicalization is English-centric, §7.7) must not count against
    # merchant accuracy. `*_total` counts only rows that actually pin
    # that field.
    txn_rows = 0
    txn_amount_total = txn_amount_ok = 0
    txn_merchant_total = txn_merchant_ok = 0
    sub_total = sub_pass = 0
    per_row: list[dict[str, Any]] = []
    metas: list[dict[str, Any]] = []

    for row in rows:
        message = row["user_message"]
        expected_tool = row["expected_tool"]
        must_include = dict(row.get("args_must_include", {}))

        try:
            turn = run_turn(
                user,
                conversation_history=[],
                user_message=message,
                today=EVAL_TODAY,
            )
            tool_call = _first_proposer_call(turn.tool_calls, expected_tool)
            run_error: str | None = None
        except Exception as exc:  # noqa: BLE001
            tool_call = None
            run_error = f"{type(exc).__name__}: {exc}"

        if expected_tool == "propose_transaction":
            txn_rows += 1
            amount_ok, merchant_ok, detail = _score_propose_transaction(
                tool_call, must_include, cards_by_name
            )
            if amount_ok is not None:
                txn_amount_total += 1
                txn_amount_ok += int(amount_ok)
            if merchant_ok is not None:
                txn_merchant_total += 1
                txn_merchant_ok += int(merchant_ok)
            # Breakdown "passed" = the gated dimension. For a transaction
            # row that's amount accuracy; merchant-only rows (none today)
            # fall back to merchant.
            row_passed = (amount_ok is True) if amount_ok is not None else (merchant_ok is True)
            metas.append(_row_meta(row, row_passed))
            per_row.append({
                "user_message": message,
                "expected_tool": expected_tool,
                "got_tool": tool_call["name"] if tool_call else None,
                "error": run_error,
                "difficulty": row.get("difficulty", "medium"),
                "tags": row.get("tags") or [],
                "amount_pass": amount_ok,
                "merchant_pass": merchant_ok,
                "detail": detail,
            })
        elif expected_tool == "propose_subscription":
            sub_total += 1
            ok, detail = _score_propose_subscription(
                tool_call, must_include, cards_by_name
            )
            sub_pass += int(ok)
            metas.append(_row_meta(row, ok))
            per_row.append({
                "user_message": message,
                "expected_tool": expected_tool,
                "got_tool": tool_call["name"] if tool_call else None,
                "error": run_error,
                "difficulty": row.get("difficulty", "medium"),
                "tags": row.get("tags") or [],
                "pass": ok,
                "detail": detail,
            })

    return {
        "n_rows": len(rows),
        "scores": {
            "propose_transaction.amount":     _safe_div(txn_amount_ok, txn_amount_total),
            "propose_transaction.merchant":   _safe_div(txn_merchant_ok, txn_merchant_total),
            "propose_subscription.row_pass":  _safe_div(sub_pass, sub_total),
        },
        "counts": {
            "propose_transaction.rows": txn_rows,
            "propose_transaction.amount_scored": txn_amount_total,
            "propose_transaction.merchant_scored": txn_merchant_total,
            "propose_subscription.rows": sub_total,
        },
        "breakdown": _aggregate_breakdown(metas),
        "rows": per_row,
    }


def _run_multi_hop(
    jwt: str, user_id: uuid.UUID, cards_by_name: dict[str, str]
) -> dict[str, Any]:
    """Score `user_message -> tool_use sequence + final-answer value`."""
    from app.auth import AuthedUser
    from app.agent.loop import run_turn

    rows = _load_yaml("multi_hop.yaml")
    user = AuthedUser(jwt=jwt, user_id=user_id, email="eval@tameru.internal")

    seq_ok = 0
    answer_ok = 0
    per_row: list[dict[str, Any]] = []
    metas: list[dict[str, Any]] = []

    for row in rows:
        prompt = row["prompt"]
        expected_seq = row.get("expected_tool_sequence", [])
        sequence_match = row.get("sequence_match", "unordered")
        answer_pattern = row.get("answer_pattern")
        expected_value = row.get("expected_answer_value_usd")
        tolerance = float(row.get("answer_value_tolerance_usd", 1.00))

        try:
            turn = run_turn(
                user,
                conversation_history=[],
                user_message=prompt,
                today=EVAL_TODAY,
            )
        except Exception as exc:  # noqa: BLE001
            metas.append(_row_meta(row, False))
            per_row.append({
                "prompt": prompt,
                "error": f"{type(exc).__name__}: {exc}",
                "difficulty": row.get("difficulty", "medium"),
                "tags": row.get("tags") or [],
                "sequence_pass": False,
                "answer_pass": False,
            })
            continue

        actual_seq = [
            {"name": tc.name, "input": tc.input}
            for tc in turn.tool_calls
            if not tc.name.startswith("propose_") and tc.name != "render_chart"
        ]
        # Also let the matcher see render_chart / propose_* if the YAML
        # row asked for them explicitly.
        if any(
            step["name"] in ("render_chart",)
            or step["name"].startswith("propose_")
            for step in expected_seq
        ):
            actual_seq = [
                {"name": tc.name, "input": tc.input} for tc in turn.tool_calls
            ]

        ok_seq = _match_tool_sequence(actual_seq, expected_seq, sequence_match)
        seq_ok += int(ok_seq)

        ok_answer = _match_final_answer(
            turn.assistant_text, answer_pattern, expected_value, tolerance
        )
        answer_ok += int(ok_answer)

        # Breakdown "passed" = tool-sequence correctness (the gated
        # dimension for multi-hop).
        metas.append(_row_meta(row, ok_seq))
        per_row.append({
            "prompt": prompt,
            "difficulty": row.get("difficulty", "medium"),
            "tags": row.get("tags") or [],
            "expected_tool_sequence": expected_seq,
            "actual_tool_sequence": [
                {"name": s["name"], "args": dict(s["input"])} for s in actual_seq
            ],
            "assistant_text": turn.assistant_text,
            "sequence_pass": ok_seq,
            "answer_pass": ok_answer,
        })

    n = max(len(rows), 1)
    return {
        "n_rows": len(rows),
        "scores": {
            "tool_sequence": seq_ok / n,
            "final_answer":  answer_ok / n,
        },
        "breakdown": _aggregate_breakdown(metas),
        "rows": per_row,
    }


# ---------------------------------------------------------------------------
# Scoring helpers.
# ---------------------------------------------------------------------------


def _row_meta(row: dict[str, Any], passed: bool) -> dict[str, Any]:
    """Extract the breakdown metadata for one corpus row.

    `difficulty` defaults to "medium" when a row omits it; `tags`
    defaults to the empty list. `passed` is the suite's primary pass
    signal for that row (the gated dimension — category correctness,
    transaction-amount accuracy, or multi-hop tool-sequence match).
    """
    return {
        "difficulty": row.get("difficulty", "medium"),
        "tags": list(row.get("tags") or []),
        "passed": bool(passed),
    }


def _aggregate_breakdown(metas: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll per-row metadata into by-difficulty and by-tag pass rates.

    This is diagnostic only — it does NOT feed the CI gate (the gates
    stay on the aggregate suite scores). Its job is to answer "did we
    regress specifically on hard rows / multilingual rows" instead of
    leaving one flat percentage. A row counts toward every tag it
    carries, so by-tag buckets overlap by design.
    """
    by_difficulty: dict[str, dict[str, int]] = {}
    by_tag: dict[str, dict[str, int]] = {}
    for meta in metas:
        difficulty = meta.get("difficulty") or "medium"
        d_bucket = by_difficulty.setdefault(difficulty, {"n": 0, "passed": 0})
        d_bucket["n"] += 1
        d_bucket["passed"] += int(bool(meta.get("passed")))
        for tag in meta.get("tags") or []:
            t_bucket = by_tag.setdefault(tag, {"n": 0, "passed": 0})
            t_bucket["n"] += 1
            t_bucket["passed"] += int(bool(meta.get("passed")))

    def _with_rate(bucket: dict[str, int]) -> dict[str, Any]:
        """Attach a pass `rate` to an {n, passed} bucket."""
        return {
            "n": bucket["n"],
            "passed": bucket["passed"],
            "rate": (bucket["passed"] / bucket["n"]) if bucket["n"] else 0.0,
        }

    return {
        "by_difficulty": {k: _with_rate(v) for k, v in by_difficulty.items()},
        "by_tag": {k: _with_rate(v) for k, v in by_tag.items()},
    }


def _first_proposer_call(
    tool_calls: list[Any], expected_tool: str
) -> dict[str, Any] | None:
    """Return the first `tool_use` block matching `expected_tool`, as a dict.

    `get_cards` is a legitimate preamble for proposer turns (Claude
    needs the UUID before filling card_id), so we walk past those rather
    than scoring against them.
    """
    for tc in tool_calls:
        if tc.name == expected_tool:
            return {"name": tc.name, "input": dict(tc.input), "result": dict(tc.result)}
        if tc.name in ("get_cards", "get_transactions", "get_subscriptions"):
            continue
        # First non-skip, non-match tool call — the agent chose the wrong
        # tool. Return it so the eval can record what got produced.
        return {"name": tc.name, "input": dict(tc.input), "result": dict(tc.result)}
    return None


def _score_propose_transaction(
    tool_call: dict[str, Any] | None,
    must_include: dict[str, Any],
    cards_by_name: dict[str, str],
) -> tuple[bool | None, bool | None, dict[str, Any]]:
    """Return (amount_ok, merchant_ok, detail) for one chat-extraction row.

    Each result is `True` / `False`, or `None` when the row does not pin
    that field — the caller skips `None` results so a merchant-less row
    doesn't dent the merchant denominator.

    Amount tolerance: exact match on Decimal-normalized value (no float
    drift). Merchant tolerance: case-insensitive substring containment —
    "Trader Joe's" matches "Trader Joes" and "trader joe's", not "Whole
    Foods".
    """
    tests_amount = "amount" in must_include
    tests_merchant = "merchant" in must_include
    if tool_call is None or tool_call["name"] != "propose_transaction":
        return (
            (False if tests_amount else None),
            (False if tests_merchant else None),
            {"reason": "wrong_tool", "got": tool_call},
        )
    args = tool_call["input"]
    amount_ok: bool | None = None
    merchant_ok: bool | None = None
    detail: dict[str, Any] = {"args": dict(args)}

    if tests_amount:
        expected = Decimal(str(must_include["amount"]))
        got = args.get("amount")
        try:
            amount_ok = got is not None and Decimal(str(got)) == expected
        except Exception:  # noqa: BLE001
            amount_ok = False
        detail["amount_expected"] = str(expected)
        detail["amount_got"] = str(got)

    if tests_merchant:
        expected_m = _normalize_merchant_for_match(str(must_include["merchant"]))
        got_m = _normalize_merchant_for_match(str(args.get("merchant", "")))
        merchant_ok = bool(got_m) and expected_m in got_m
        detail["merchant_expected"] = must_include["merchant"]
        detail["merchant_got"] = args.get("merchant")

    if "card_name_resolves_to" in must_include:
        expected_card_id = cards_by_name.get(must_include["card_name_resolves_to"])
        # The proposal's *resolved* card_id (tool output) is the outcome
        # that matters — the agent passes a short card_ref and the tool
        # resolves it to a UUID. Checking the model's raw input would
        # miss whether resolution actually landed on the right card.
        got_card_id = (tool_call.get("result") or {}).get("card_id")
        if expected_card_id is None or str(got_card_id) != expected_card_id:
            # Card resolution miss doesn't fail amount/merchant scoring
            # (the §7.10 thresholds only meter amount + merchant). It's
            # recorded in detail for the per-run JSON to inspect.
            detail["card_mismatch"] = {
                "expected_id": expected_card_id,
                "got_id": got_card_id,
            }

    return amount_ok, merchant_ok, detail


def _score_propose_subscription(
    tool_call: dict[str, Any] | None,
    must_include: dict[str, Any],
    cards_by_name: dict[str, str],
) -> tuple[bool, dict[str, Any]]:
    """Return (row_pass, detail) for one chat-extraction subscription row.

    All `args_must_include` fields must match for the row to pass —
    `propose_subscription` is gated as a single per-row pass rate per
    DESIGN.md §7.10. The auto-logger amplifies any miss monthly, so the
    threshold treats them holistically rather than splitting by field.
    """
    if tool_call is None or tool_call["name"] != "propose_subscription":
        return False, {"reason": "wrong_tool", "got": tool_call}
    args = tool_call["input"]
    detail: dict[str, Any] = {"args": dict(args), "checks": {}}
    all_ok = True

    for key, expected_raw in must_include.items():
        if key == "card_name_resolves_to":
            expected_card_id = cards_by_name.get(expected_raw)
            # Check the proposal's resolved card_id (tool output), not
            # the model's raw card_ref input — resolution is the outcome
            # the row asserts.
            got_card_id = (tool_call.get("result") or {}).get("card_id")
            ok = (
                expected_card_id is not None
                and str(got_card_id) == expected_card_id
            )
            detail["checks"][key] = {
                "expected_id": expected_card_id,
                "got_id": got_card_id,
                "pass": ok,
            }
            all_ok = all_ok and ok
            continue

        got = args.get(key)
        if key == "amount":
            try:
                ok = got is not None and Decimal(str(got)) == Decimal(str(expected_raw))
            except Exception:  # noqa: BLE001
                ok = False
        elif key == "merchant":
            expected_m = _normalize_merchant_for_match(str(expected_raw))
            got_m = _normalize_merchant_for_match(str(got or ""))
            ok = bool(got_m) and expected_m in got_m
        elif key in ("name",):
            ok = str(got or "").strip().lower() == str(expected_raw).strip().lower()
        else:
            ok = str(got) == str(expected_raw)
        detail["checks"][key] = {"expected": expected_raw, "got": got, "pass": ok}
        all_ok = all_ok and ok

    return all_ok, detail


def _match_tool_sequence(
    actual: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    mode: str,
) -> bool:
    """Check actual tool calls satisfy the expected sequence.

    `mode="unordered"`: multiset match — every expected step must be
    matched by some distinct actual step (subset match on `args_must_include`).
    `mode="ordered"`: strict prefix — actual[i] must match expected[i] for
    each i in range(len(expected)).

    A "match" between an expected step and an actual step:
      * name equality, plus
      * every key in expected.args_must_include must be present in
        actual.input AND equal (string comparison; dates and enum values
        come through as strings).
    """
    if not expected:
        return True
    if mode == "ordered":
        if len(actual) < len(expected):
            return False
        for exp, act in zip(expected, actual):
            if not _step_matches(act, exp):
                return False
        return True
    # unordered — multiset assignment
    remaining = list(actual)
    for exp in expected:
        idx = next(
            (i for i, act in enumerate(remaining) if _step_matches(act, exp)),
            None,
        )
        if idx is None:
            return False
        remaining.pop(idx)
    return True


def _step_matches(actual_step: dict[str, Any], expected_step: dict[str, Any]) -> bool:
    """Return True if `actual_step` satisfies `expected_step`'s shape.

    Argument comparison is case-insensitive and whitespace-trimmed —
    the model may emit `merchant_contains: "blue bottle"` for an expected
    "Blue Bottle", and category/date values (digit strings, capitalized
    enums) are unaffected by lowercasing.
    """
    if actual_step["name"] != expected_step["name"]:
        return False
    must_include = expected_step.get("args_must_include", {}) or {}
    args = actual_step.get("input", {}) or {}
    for key, expected_value in must_include.items():
        if key not in args:
            return False
        if str(args[key]).strip().lower() != str(expected_value).strip().lower():
            return False
    return True


def _match_final_answer(
    text: str,
    pattern: str | None,
    expected_value: Any | None,
    tolerance: float,
) -> bool:
    """Check the prose answer matches the YAML row's expectations.

    Both pattern and value checks are optional. When both are set, both
    must pass. When neither is set (e.g. a sequence-only row), this
    returns True so the answer score doesn't drag down on rows where
    only the tool sequence is what we're meter-ing.

    Value check: a delta answer typically names several dollar figures
    ("you spent $50 more in March — $250 vs $200"), and which one comes
    first depends on phrasing. So the check passes if ANY dollar value
    in the prose lands within `tolerance` of `expected_value`. A row
    where the model never states the right number — even buried in a
    breakdown — still fails, which is the regression we want to catch.
    """
    if pattern is None and expected_value is None:
        return True
    text_l = text or ""
    if pattern is not None and not re.search(pattern, text_l, re.IGNORECASE):
        return False
    if expected_value is not None:
        values = _extract_usd_values(text_l)
        if not values:
            return False
        target = float(expected_value)
        if not any(abs(v - target) <= float(tolerance) for v in values):
            return False
    return True


def _extract_usd_values(text: str) -> list[float]:
    """Pull every dollar value out of free-form prose.

    Tolerant to "$1,234.56", "$50", "$50.00". The model answers in plain
    prose; for delta questions it usually states both operands and the
    result, so the caller checks whether any extracted value matches.
    """
    out: list[float] = []
    for raw in re.findall(r"\$\s*([\d,]+(?:\.\d+)?)", text):
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return out


def _normalize_merchant_for_match(value: str) -> str:
    """Lowercase + collapse whitespace + strip apostrophes for fuzzy compare.

    Keeps "Trader Joe's" / "trader joes" / "TRADER JOE'S" equivalent
    without depending on the production merchant normalizer (which would
    also strip parens, suffix counts, etc. — too aggressive for this
    use case).
    """
    s = value.strip().lower().replace("'", "").replace("’", "")
    return " ".join(s.split())


def _safe_div(num: int, denom: int) -> float:
    """Return 0.0 when denom is 0 (a suite contributing zero rows gets a
    neutral score and falls through threshold checks against suites that
    do contribute)."""
    return (num / denom) if denom else 0.0


# ---------------------------------------------------------------------------
# Threshold + summary.
# ---------------------------------------------------------------------------


def _evaluate_thresholds(
    scores: dict[str, float],
) -> tuple[bool, bool, list[str], list[str]]:
    """Compare each score to its (target, gate) pair.

    Returns (all_targets_met, all_gates_met, warnings, breaches).
    Score keys not in THRESHOLDS are reported in the summary but don't
    affect exit code — that lets new score lines land without a gate
    change.
    """
    warnings: list[str] = []
    breaches: list[str] = []
    targets_met = True
    gates_met = True
    for key, (target, gate) in THRESHOLDS.items():
        value = scores.get(key)
        if value is None:
            # Suite for this score wasn't run — neither warn nor breach.
            continue
        if gate is not None and value < gate:
            breaches.append(f"{key}: {value:.3f} < gate {gate:.3f}")
            gates_met = False
            targets_met = False
            continue
        if value < target:
            warnings.append(f"{key}: {value:.3f} < target {target:.3f}")
            targets_met = False
    return targets_met, gates_met, warnings, breaches


def _print_summary(
    scores: dict[str, float],
    warnings: list[str],
    breaches: list[str],
    suite_results: dict[str, dict[str, Any]],
) -> None:
    """Pretty-print scores, the difficulty/tag breakdown, and status.

    All output goes to stderr so stdout stays clean. The breakdown is
    diagnostic — it surfaces *where* a regression landed (hard rows?
    multilingual rows?) without affecting the exit code.
    """
    print("", file=sys.stderr)
    print("=== eval summary ===", file=sys.stderr)
    for key in sorted(scores):
        target, gate = THRESHOLDS.get(key, (None, None))
        line = f"  {key:50s}  {scores[key]:.3f}"
        if target is not None:
            line += f"   target={target:.2f}"
        if gate is not None:
            line += f"  gate={gate:.2f}"
        print(line, file=sys.stderr)

    # Per-suite difficulty + tag breakdown.
    for suite in ("categorization", "chat_extraction", "multi_hop"):
        result = suite_results.get(suite)
        if not result or "breakdown" not in result:
            continue
        breakdown = result["breakdown"]
        by_diff = breakdown.get("by_difficulty", {})
        by_tag = breakdown.get("by_tag", {})
        if not by_diff and not by_tag:
            continue
        print(f"\n  [{suite}] breakdown", file=sys.stderr)
        for difficulty in ("easy", "medium", "hard"):
            b = by_diff.get(difficulty)
            if b:
                print(
                    f"    difficulty {difficulty:7s}  "
                    f"{b['rate']:.3f}  ({b['passed']}/{b['n']})",
                    file=sys.stderr,
                )
        for tag in sorted(by_tag):
            b = by_tag[tag]
            print(
                f"    tag {tag:20s}  {b['rate']:.3f}  ({b['passed']}/{b['n']})",
                file=sys.stderr,
            )

    if warnings:
        print("\n[warn] below target:", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)
    if breaches:
        print("\n[FAIL] gate breached:", file=sys.stderr)
        for b in breaches:
            print(f"  - {b}", file=sys.stderr)
        return
    if not warnings:
        print("\n[ok] all targets met", file=sys.stderr)


# ---------------------------------------------------------------------------
# IO helpers.
# ---------------------------------------------------------------------------


def _load_dotenv() -> None:
    """Read KEY=VALUE lines from the repo-root .env into os.environ.

    No python-dotenv dependency — the eval only needs plain KEY=VALUE
    lines with optional surrounding quotes. Already-exported env vars
    win, so CI (which sets keys via the job's `env:`) overrides the
    file. Mirrors `scripts/smoke_prod.py::_load_dotenv`.
    """
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in os.environ:
            continue
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        os.environ[key] = value


def _load_yaml(name: str) -> list[dict[str, Any]]:
    """Load a YAML corpus file; raise if it's missing or empty."""
    path = EVALS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"missing eval corpus: {path}")
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"{path}: expected a non-empty list of rows")
    return data


def _new_run_id() -> str:
    """Sortable run id: UTC timestamp + short random suffix."""
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-{uuid.uuid4().hex[:8]}"


def _git_sha() -> str:
    """Return short HEAD SHA or 'unknown' (CI checkout depth may shadow this)."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True
        )
        return out.strip()
    except Exception:
        return "unknown"


def _collect_prompt_versions() -> dict[str, str]:
    """Snapshot the prompt-version constants the suites depend on.

    Recorded per run so a degraded score can be triaged against a known
    prompt revision rather than re-deriving from git history.
    """
    versions: dict[str, str] = {}
    try:
        from app.prompts.categorize import PROMPT_VERSION as cat_v
        versions["categorize"] = cat_v
    except Exception:
        versions["categorize"] = "unavailable"
    try:
        from app.prompts.chat import PROMPT_VERSION as chat_v
        versions["chat"] = chat_v
    except Exception:
        versions["chat"] = "unavailable"
    return versions


def _rebuild_results_db() -> None:
    """Rebuild local SQLite from all `evals/runs/*.json` files.

    SQLite is gitignored — this is a local convenience for ad-hoc
    queries ("show me all categorization runs on chat_v8 sorted by
    accuracy"). The per-run JSON files are the canonical artifact.
    """
    if RESULTS_DB.exists():
        RESULTS_DB.unlink()
    conn = sqlite3.connect(RESULTS_DB)
    conn.execute(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            timestamp TEXT,
            git_sha TEXT,
            model TEXT,
            categorize_prompt_version TEXT,
            chat_prompt_version TEXT,
            payload TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE scores (
            run_id TEXT,
            key TEXT,
            value REAL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        )
        """
    )
    count = 0
    for path in sorted(RUNS_DIR.glob("*.json")):
        payload = json.loads(path.read_text())
        conn.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                payload.get("run_id"),
                payload.get("timestamp"),
                payload.get("git_sha"),
                payload.get("model"),
                payload.get("prompt_versions", {}).get("categorize"),
                payload.get("prompt_versions", {}).get("chat"),
                json.dumps(payload),
            ),
        )
        for key, value in (payload.get("scores") or {}).items():
            conn.execute(
                "INSERT INTO scores VALUES (?, ?, ?)",
                (payload.get("run_id"), key, value),
            )
        count += 1
    conn.commit()
    conn.close()
    print(f"[ok] rebuilt {RESULTS_DB} from {count} run files", file=sys.stderr)


def _parse_args() -> argparse.Namespace:
    """Parse CLI args."""
    p = argparse.ArgumentParser(description="Tameru eval harness (DESIGN.md §7.10)")
    p.add_argument(
        "--eval",
        default="all",
        choices=("categorization", "chat_extraction", "multi_hop", "all"),
        help="Which suite(s) to run. Default: all.",
    )
    p.add_argument(
        "--model",
        default=None,
        help=(
            "Override the Claude chat model (a claude-* id) for the "
            "chat-extraction and multi-hop suites — e.g. A/B two Claude "
            "versions. Not a cross-provider switch: the agent loop is "
            "Anthropic-only; a Gemini Flash-Lite A/B is post-launch "
            "(DESIGN.md §11.4)."
        ),
    )
    p.add_argument(
        "--report",
        action="store_true",
        help=(
            "Rebuild evals/results.db from all evals/runs/*.json files "
            "(local convenience; the DB is gitignored)."
        ),
    )
    return p.parse_args()


def _suites_from_arg(arg: str) -> tuple[str, ...]:
    """Map --eval value to the suite tuple to run."""
    if arg == "all":
        return ALL_SUITES
    return (arg,)


if __name__ == "__main__":
    raise SystemExit(main())
