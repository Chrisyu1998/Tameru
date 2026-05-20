# Tameru eval harness

Automated accuracy tests for the three highest-stakes AI tasks
(DESIGN.md §7.10). Run by `eval.py` at the repo root; gated in CI on
PRs that touch `app/prompts/`, `app/agent/`, `app/integrations/gemini.py`,
or `evals/`.

## What's here

| File | Suite | Rows |
|---|---|---|
| `categorization.yaml` | `(merchant, amount) → expected_category` | 114 |
| `chat_extraction.yaml` | user message → `propose_transaction` / `propose_subscription` args | 61 |
| `multi_hop.yaml` | user message → tool sequence + final answer | 20 |
| `runs/<run_id>.json` | one file per run — the canonical result artifact | — |

`results.db` is a derived local SQLite (gitignored) — rebuild it with
`python eval.py --report` for ad-hoc queries.

## Running

```bash
source .venv/bin/activate

# one-time / per-run setup — provision the eval user + seed fixtures
python scripts/seed_eval_fixtures.py

# run all suites
python eval.py --eval=all

# one suite
python eval.py --eval=chat_extraction

# A/B an alternate Claude chat model (e.g. a newer Claude version)
python eval.py --eval=multi_hop --model=claude-opus-4-7

# rebuild the local SQLite from all run files
python eval.py --report
```

`--model` swaps the **Claude** chat model only — the agent loop is
Anthropic-only, so a non-`claude-*` id is rejected with an error. The
cross-provider Haiku-vs-Flash-Lite A/B (DESIGN.md §11.4) is a post-launch
item: it needs the chat loop to gain a Gemini path first, which v1 does
not have.

`eval.py` requires a running local Supabase stack (`supabase start`) and
the API keys for whichever providers the enabled suites call
(`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`). It auto-loads the repo-root
`.env` at startup, so exported vars are optional locally; CI sets the
keys via the job's `env:` block (exported vars always win over `.env`).
It mints a JWT for the eval user (`eval@tameru.internal`) and seeds
deterministic fixtures before scoring — no service-role bypass
(CLAUDE.md invariant 1).

## Thresholds — targets vs gates

DESIGN.md §7.10 separates the *target* (the accuracy we want) from the
*gate* (the floor that blocks a CI merge). `eval.py` exits `1` only on a
gate breach; a target miss prints a yellow warning and still exits `0`.

| Score key | Target | Gate |
|---|---|---|
| `categorization.accuracy` | 90% | 88% |
| `chat_extraction.propose_transaction.amount` | 95% | 93% |
| `chat_extraction.propose_transaction.merchant` | 90% | — |
| `chat_extraction.propose_subscription.row_pass` | 90% | 85% |
| `multi_hop.tool_sequence` | 90% | 85% |
| `multi_hop.final_answer` | 95% | — |

Do **not** loosen a gate to make a failing PR pass — investigate the
regression. Gates live in `THRESHOLDS` in `eval.py`.

## Difficulty & tags

Every corpus row carries `difficulty` (`easy` | `medium` | `hard`) and
optional `tags`. These are **diagnostic only** — they do not feed the CI
gate. The runner rolls them into a per-tier breakdown in the summary so
a regression can be localized:

```
[multi_hop] breakdown
  difficulty medium   0.917  (11/12)
  difficulty hard     0.667  (4/6)      <- regression is on hard rows
  tag multilingual    0.500  (2/4)      <- specifically multilingual
```

The breakdown's "passed" signal is the suite's gated dimension
(category correctness / transaction-amount accuracy / multi-hop tool
sequence). by-tag buckets overlap — a row counts toward every tag it
carries.

## Adding rows

### Categorization

Monthly, mine `merchant_category` corrections (users telling Tameru it
got a category wrong) for new rows. Each row should disambiguate against
the nearest-neighbor category. Categories must use the `categorize_v5`
taxonomy — `Memberships` (not "Subscriptions"), `Streaming` as its own
bucket. Bump `PROMPT_VERSION` in `app/prompts/categorize.py` if you add a
row that reshapes a category boundary.

### Chat extraction

A row is a user message plus `args_must_include` — the subset of
proposer args that must match. `card_name_resolves_to` is matched
against the seeded fixture cards (Amex Gold, Chase Sapphire Reserve,
Chase Freedom Unlimited — see `scripts/_eval_setup.py`). Keep ≥3 `zh-TW`
and ≥3 `ja-JP` rows for multilingual coverage (DESIGN.md §7.7).
`propose_card` is intentionally not gated — UAT (Day 28) covers it.

### Multi-hop

A row is a prompt, an `expected_tool_sequence`, and optionally an
`answer_pattern` / `expected_answer_value_usd`. The expected values
depend on the fixture totals in `scripts/_eval_setup._FIXTURE_TRANSACTIONS`
— if you change a fixture total, update every multi-hop row that asserts
a value derived from it. `sequence_match: unordered` (default) is a
multiset subset match; use `ordered` only when call order is semantic.
When you add a new agent tool, add a multi-hop row exercising it. Keep
≥2 `zh-TW` and ≥2 `ja-JP` rows.

## Inspecting a run

```bash
python eval.py --report
sqlite3 evals/results.db \
  "SELECT run_id, key, value FROM scores ORDER BY run_id DESC LIMIT 20;"
```

Or read `evals/runs/<run_id>.json` directly — it carries per-row results
for every suite, the `git_sha`, the `model`, and the prompt versions in
effect.
