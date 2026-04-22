# Day 22 — Eval harness (categorization + chat transaction extraction + multi-hop) with regression gate

## Goal

`python eval.py` runs all three evals against the production prompts, scores accuracy, persists results to `evals/results.db`, and CI fails any PR that drops below the regression thresholds.

Note the shift from the earlier plan: in the chat-unified UX (CLAUDE.md invariant 8), there is no standalone `parse_nl_entry` Gemini function to target. Chat-based transaction extraction is now part of Claude's `tool_use` arg-filling for `propose_transaction`. The "NL parse" eval therefore runs through the full agent loop against chat messages and asserts the resulting tool call args — same quality bar, different seam.

## Read first

- `DESIGN.md` §7.10 (eval harness — full spec), §7.7 (chat-based transaction extraction), §7.2 (tool sequences).

## Deliverables

- `evals/categorization.yaml` — full 100 hand-curated `(merchant, amount) → expected_category` rows. Source from your real transactions, anonymized if needed.
- `evals/chat_extraction.yaml` — full 50 hand-curated NL strings → expected `propose_transaction` args `(merchant, amount, date, card)`. Each row is a user chat message; the eval runs the full agent turn and asserts the tool call the agent makes. Example row:
  ```yaml
  - user_message: "spent $47 at Trader Joe's on my Amex Gold just now"
    expected_tool: propose_transaction
    args_must_include:
      merchant: "Trader Joe's"
      amount: 47.00
      card_name_resolves_to: "Amex Gold"
  ```
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
  Cover: cross-month deltas, category × card filters, generative chart asks, subscription queries, memory-touching prompts, chat-based disambiguation ("change that $10 coffee from last week" → expects `get_transactions` with `merchant_contains` + amount range).
- `eval.py` (repo root):
  - `--eval=categorization|chat_extraction|multi_hop|all` flag picks which suite(s).
  - `--model=<id>` flag overrides the default chat model — used to A/B Haiku vs Flash-Lite without changing app code.
  - For categorization: runs each row through `categorize()` (Day 4).
  - For chat extraction: runs each row's `user_message` through the full `run_turn()` agent loop; scores the resulting `tool_use` block against `expected_tool` + `args_must_include`.
  - For multi-hop: runs each row through `run_turn()`, captures the full `tool_use` block sequence, scores tool sequence (per `expected_tool_sequence`) and final answer (per `answer_pattern` + `answer_value_tolerance_usd`).
  - Writes a row to `evals/results.db` per run with: timestamp, prompt_version, model, suite, accuracy, full per-row results.
  - Prints a human-readable summary table.
  - Exit code 0 if all enabled suites meet thresholds (categorization ≥ 88%, chat extraction amount+merchant ≥ 93%, multi-hop tool sequence ≥ 85%); 1 otherwise.
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
