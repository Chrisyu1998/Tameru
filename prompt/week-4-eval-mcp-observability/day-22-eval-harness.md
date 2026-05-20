# Day 22 — Eval harness (categorization + chat transaction extraction + multi-hop) with regression gate

## Goal

`python eval.py --eval=all` runs all three evals against the production prompts, scores accuracy, persists per-run results to `evals/runs/<run_id>.json`, and CI fails any PR that drops below the §7.10 regression gates.

Two seam shifts vs. the original DESIGN.md §7.10 wording — both must also land in DESIGN.md in this PR (CLAUDE.md "Keeping DESIGN.md in sync"):

- **`nl_parse.yaml` → `chat_extraction.yaml`.** In the chat-unified UX (invariant 8) there is no standalone `parse_nl_entry` Gemini function. Chat-based transaction extraction is now Claude's `tool_use` arg-filling for `propose_transaction`. The eval runs the full agent loop against the user message and asserts the resulting tool call — same quality bar, different seam.
- **Per-run JSON, not committed SQLite.** Concurrent PRs each writing into `evals/results.db` produce binary-merge conflicts. Per-run JSON sidesteps that; the SQLite is rebuildable locally via `--report` and gitignored.

## Read first

- `DESIGN.md` §7.10 (eval harness — full spec), §7.7 line 689 (multilingual requirement on `multi_hop.yaml`), §7.2 (typed tools).
- `app/agent/loop.py` (`run_turn` is the entry point — eval feeds messages through it).
- `app/integrations/gemini.py` (`categorize()` — categorization eval entry point).
- `app/prompts/categorize.py` (`PROMPT_VERSION = "categorize_v5"` — taxonomy uses `Memberships`, not `Subscriptions`).
- `evals/categorization.yaml` (14-row skeleton to expand).

## Setup

The eval is a real client of the agent loop, so before any row runs we need:

1. **Eval user JWT minting.** Add `scripts/mint_eval_jwt.py`: signs a JWT for the seeded eval user (`eval@tameru.internal`) using `SUPABASE_JWT_SECRET`, TTL ≥ 1h, prints the token. Loaded by `eval.py` at startup; never echoed to artifacts. Invariant 1 — no service-role bypass.
2. **Fixture seed.** Add `scripts/seed_eval_fixtures.py`: idempotent upsert for the eval user of the cards, transactions, and subscriptions the corpora reference. At minimum: the cards named by `chat_extraction.yaml`'s `card_name_resolves_to` rows, enough Jan/Feb/Mar 2026 transactions for `multi_hop.yaml`'s cross-month deltas to return the value pinned in each row's `expected_answer_value_usd`, and a starter subscription set for any multi-hop row that touches `get_subscriptions`. Rerunnable; safe to call before every `eval.py` invocation. Note: `propose_subscription` rows do **not** need pre-seeded subscriptions — the eval runs the agent through proposal, not commit, so the proposer is exercised against an empty subscription state.
3. **Audit-log posture.** Eval `run_turn` calls write `ai_call_log` rows under the eval JWT (invariant 14 — same as production, no special-case suppression). Add a `pg_cron` job trimming `ai_call_log` rows for `eval@tameru.internal` older than 7 days so the audit table doesn't accumulate.

## Deliverables

- `evals/categorization.yaml` — 100 hand-curated `(merchant, amount) → expected_category` rows. Categories must use the `categorize_v5` taxonomy (`Memberships`, not `Subscriptions`; `Streaming` is its own bucket). Source from your real transactions, anonymize as needed. Expand the 14-row skeleton.

- `evals/chat_extraction.yaml` — 60 hand-curated NL strings → expected proposer args. Split: **50 `propose_transaction` rows + 10 `propose_subscription` rows**. **Includes ≥3 `zh-TW` and ≥3 `ja-JP` rows** for multilingual parity with the multi-hop suite (distribute across both proposers). Row shapes:
  ```yaml
  - user_message: "spent $47 at Trader Joe's on my Amex Gold just now"
    expected_tool: propose_transaction
    args_must_include:
      merchant: "Trader Joe's"
      amount: 47.00
      card_name_resolves_to: "Amex Gold"   # eval resolves to fixture card_id

  - user_message: "add Netflix, $22.99 a month, started Feb 14 on my Amex Gold"
    expected_tool: propose_subscription
    args_must_include:
      name: "Netflix"
      amount: 22.99
      frequency: "monthly"
      start_date: "2026-02-14"
      card_name_resolves_to: "Amex Gold"
  ```
  `propose_subscription` is included because the pg_cron auto-logger amplifies any extraction error monthly — a wrong `frequency` or `start_date` silently corrupts the ledger for the subscription's lifetime, which is a strictly worse blast radius than a one-off transaction error. Cardless subs (rent, utilities — invariant added 2026-05-19) must be represented: ≥2 of the 10 rows omit `card_name_resolves_to` and assert the proposer accepts the cardless path.

  `propose_card` is intentionally **not** gated in v1 evals — it fires a handful of times per user lifetime, the parse card UI catches most extraction errors (the user reads "Visa · ending 4321" carefully because card add is a deliberate flow, not a tap-and-go), and the failure mode is recoverable (re-add). UAT at Day 28 is sufficient pre-launch coverage; post-launch corpus expansion can add it.

- `evals/multi_hop.yaml` — 20 hand-curated chat prompts requiring 2+ tool calls. **Includes ≥2 `zh-TW` and ≥2 `ja-JP` rows** per DESIGN.md §7.7 line 689. Row shape:
  ```yaml
  - prompt: "How much more did I spend on dining in March vs February?"
    expected_tool_sequence:
      - name: calculate_total
        args_must_include: { category: "Dining", date_from: "2026-03-01" }
      - name: calculate_total
        args_must_include: { category: "Dining", date_from: "2026-02-01" }
    sequence_match: unordered            # default; use 'ordered' when order is semantic
    answer_pattern: "(more|less|increase|decrease).*\\$\\d+"
    expected_answer_value_usd: 47.50     # delta computed from seeded fixtures
    answer_value_tolerance_usd: 1.00
  ```
  Coverage axes: cross-month deltas, category × card filters, generative chart asks, subscription queries, memory-touching prompts, chat-based disambiguation, the two multilingual sets.

- `eval.py` (repo root):
  - `--eval=categorization|chat_extraction|multi_hop|all` (default `all`).
  - `--model=<id>` overrides the Claude chat model (a `claude-*` id) for the chat suites — A/B two Claude versions. Claude-only: the agent loop is Anthropic-only, so a non-`claude-*` id is rejected. A cross-provider Flash-Lite A/B needs a Gemini chat path (post-launch, DESIGN.md §11.4).
  - `--report` rebuilds local `evals/results.db` from `evals/runs/*.json` for ad-hoc querying (DB is gitignored, derived).
  - **Categorization runner:** calls `categorize()` in `app/integrations/gemini.py` per row.
  - **Chat-extraction runner:** calls `run_turn()` with the eval JWT and the row's `user_message`; scores the first `tool_use` block against `expected_tool` + `args_must_include` (subset match on the tool name and each pinned arg). `card_name_resolves_to` resolves the seeded card by name and compares to the tool call's `card_id`. Same matcher for `propose_transaction` and `propose_subscription` rows; the runner does not branch by proposer.
  - **Multi-hop runner:** calls `run_turn()`, captures the full `tool_use` block sequence, matches per `sequence_match` (`unordered` = multiset match on tool name + `args_must_include` subset; `ordered` = strict sequence). Then evaluates the final prose answer against `answer_pattern` and extracts a dollar value to compare against `expected_answer_value_usd` within `answer_value_tolerance_usd`.
  - Writes `evals/runs/<run_id>.json` per run with `run_id` (ULID), `timestamp`, `git_sha`, `prompt_versions` (map of `categorize_v5`, agent-prompt SHA, etc.), `model`, `suite`, `accuracy`, `targets_met`, `gates_met`, per-row results. Per-PR file = no merge conflicts.
  - Prints a human-readable summary table with target vs. gate status per suite.
  - Exit codes:
    - `0` — every enabled suite meets its **gate**.
    - `0` with a yellow "below target" warning — gate passes but target missed.
    - `1` — any enabled suite breaches its gate.

- `evals/runs/.gitkeep` — committed. Add `evals/results.db` to `.gitignore`.

- `.github/workflows/ci.yml` — add an `eval-gate` job:
  - **Path filter:** triggers only on PRs that touch `app/prompts/**`, `app/agent/**`, `app/integrations/gemini.py`, or `evals/**`.
  - **Secrets:** `ANTHROPIC_API_KEY_EVAL` and `GEMINI_API_KEY_EVAL` — separate from prod, with a tight monthly quota set in the respective dashboards (no in-prompt budget number; the dashboard is authoritative).
  - **Steps:** check out → start local Supabase (reuse `backend-test`'s pattern) → apply migrations → run `scripts/seed_eval_fixtures.py` → mint JWT via `scripts/mint_eval_jwt.py` → run `python eval.py --eval=all` → upload `evals/runs/<run_id>.json` as a workflow artifact.
  - **Gate:** blocks merge if exit code is non-zero.

- `evals/README.md` — documents corpus add cadence (monthly mine of `merchant_category` corrections per §7.10), multilingual row policy, how to inspect a `runs/*.json` locally via `python eval.py --report`, and how to add a new tool to the multi-hop suite.

- `DESIGN.md` §7.10 sync (same PR):
  - Line 741: rename `evals/nl_parse.yaml` → `evals/chat_extraction.yaml`.
  - Line 729: storage line changes to "per-run JSON in `evals/runs/<run_id>.json`; `evals/results.db` is a derived local SQLite rebuildable via `eval.py --report`, gitignored".
  - Add a sentence noting the eval user is real (`eval@tameru.internal`) and its `ai_call_log` rows are trimmed weekly per invariant 14.

## Thresholds (targets vs gates, per DESIGN.md §7.10)

| Suite | Target (warn-if-below) | Gate (block-if-below) |
|---|---|---|
| Categorization | 90% | 88% |
| Chat extraction — `propose_transaction` amount | 95% | 93% |
| Chat extraction — `propose_transaction` merchant | 90% | — (target only) |
| Chat extraction — `propose_subscription` per-row | 90% | 85% |
| Multi-hop tool sequence | 90% | 85% |
| Multi-hop final answer | 95% | — (target only) |

The `propose_subscription` score is a single per-row pass rate (all `args_must_include` fields match → row passes; any miss → row fails). At 10 rows, the 85% gate tolerates one failure; two failures break the build.

## Don't

- Don't run evals on every commit. PR-only, gated by the path filter.
- Don't commit `evals/results.db`. SQLite binary merges are a tax we don't need. Per-run JSON is the source of truth.
- Don't loosen thresholds to make a failing PR pass. Investigate the regression.
- Don't use the service role to bypass RLS in evals. Mint a real eval-user JWT (invariant 1).
- Don't log the eval JWT, `ANTHROPIC_API_KEY_EVAL`, or `GEMINI_API_KEY_EVAL` to stdout, artifacts, or the per-run JSON.

## Done when

- `python eval.py --eval=all` runs end-to-end locally against the dev Supabase stack and prints the summary table.
- A new `evals/runs/<run_id>.json` lands per run, with `targets_met` and `gates_met` populated.
- A PR that intentionally degrades the categorization prompt below 88% fails CI on the `eval-gate` job.
- A PR that drops categorization to 89% (between gate and target) passes CI but surfaces a "below target" warning.
- `DESIGN.md` §7.10 reflects the rename + storage change in the same PR.
