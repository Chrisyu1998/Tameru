# Day 22 — Eval harness (categorization + NL parse) with regression gate

## Goal

`python eval.py` runs both evals against the production prompts, scores accuracy, persists results to `evals/results.db`, and CI fails any PR that drops below the regression thresholds.

## Read first

- `DESIGN.md` §7.10 (eval harness — full spec).

## Deliverables

- `evals/categorization.yaml` — full 100 hand-curated `(merchant, amount) → expected_category` rows. Source from your real transactions, anonymized if needed.
- `evals/nl_parse.yaml` — full 50 hand-curated NL strings → expected `(merchant, amount, date, card)`.
- `evals/multi_hop.yaml` — **20 hand-curated chat prompts requiring 2+ tool calls.** Each row:
  ```yaml
  - prompt: "How much more did I spend on dining in March vs February?"
    expected_tool_sequence:
      - name: calculate_total
        args_must_include: { category: "Dining", date_from: "2026-03-01" }
      - name: calculate_total
        args_must_include: { category: "Dining", date_from: "2026-02-01" }
    answer_pattern: "(more|less|increase|decrease).*\\$\\d+"
    answer_value_tolerance_usd: 1.00
  ```
  Cover: cross-month deltas, category × card filters, generative chart asks, subscription queries, memory-touching prompts.
- `eval.py` (repo root):
  - `--eval=categorization|nl_parse|multi_hop|all` flag picks which suite(s).
  - `--model=<id>` flag overrides the default chat model — used to A/B Haiku vs Flash-Lite without changing app code.
  - For categorization: runs each row through `categorize()`.
  - For NL parse: runs each row through `parse_nl_entry()`.
  - For multi-hop: runs each row through the full `run_turn()` agent loop, captures the `tool_use` block sequence, scores tool sequence (per `expected_tool_sequence`) and final answer (per `answer_pattern` + `answer_value_tolerance_usd`).
  - Writes a row to `evals/results.db` per run with: timestamp, prompt_version, model, suite, accuracy, full per-row results.
  - Prints a human-readable summary table.
  - Exit code 0 if all enabled suites meet thresholds (categorization ≥ 88%, NL amount ≥ 93%, multi-hop tool sequence ≥ 85%); 1 otherwise.
- CI:
  - Add to `.github/workflows/ci.yml`: on PRs that change `app/prompts/` or `app/agent/`, run `python eval.py --mode=real`. Use a separate Anthropic + Gemini key with a tight quota for CI.
  - Block merge if exit code is non-zero.
- `evals/README.md` documenting how to add new rows (sourced monthly from `merchant_category` corrections per the design).

## Don't

- Don't run evals on every commit. PR-only, and only when prompt or agent code changes.
- Don't store the SQLite as binary blob in git unhelpfully. Commit it; it's small and append-only.
- Don't loosen thresholds to make a failing PR pass. Investigate the regression.

## Done when

- `python eval.py --mode=real` runs end-to-end and prints a summary table.
- A row appears in `evals/results.db` per run.
- A PR that intentionally degrades the categorization prompt fails CI.
