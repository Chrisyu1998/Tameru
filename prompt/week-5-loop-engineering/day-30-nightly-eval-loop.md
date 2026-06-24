# Day 30 — Nightly eval loop (model-drift detector)

## Goal

A scheduled GitHub Actions workflow runs the full eval suite every night against the env-resolved production models and auto-files a deduped issue (label `eval-regression`) when any gate breaches — with the failing rows in the issue body. This catches the failure class per-PR CI is structurally blind to: **model drift under unchanged code**. Both chat models and Gemini are env-resolved (CLAUDE.md model table), so the provider can change behavior with zero commits; today that drift is invisible until the next eval-relevant PR happens to run the gate.

The corpus, prompts, thresholds, and judge are **byte-for-byte unchanged**. If the dataset changed per-run, a score drop could no longer be attributed to the model — the corpus being constant is what makes the nightly delta meaningful.

## Read first

- `.github/workflows/ci.yml` `eval-gate` job (lines ~74–170) — the step recipe this workflow clones: setup-python → Supabase CLI → `supabase start` → `supabase db reset --no-seed` → `pip install -e ".[dev]"` → `seed_eval_fixtures.py` → `python eval.py --eval=all` → upload `evals/runs/*.json` → `supabase stop`.
- `.github/workflows/prod-health.yml` — the standalone-scheduled-workflow precedent, including the deduped issue-on-failure pattern (`actions/github-script@v7`, comment-on-open-issue instead of daily spam).
- `eval.py` — exit codes (0 = gates met, 1 = gate breach), `EVAL_JUDGE` toggle, per-run JSON shape in `evals/runs/`.
- `memory.md` 2026-05-28 "Scheduled prod-health check is a standalone workflow" — why this is its own file, not `schedule:` bolted onto ci.yml (the post-deploy `needs:`/`if:` chain would skip exactly the jobs a scheduled run wants).

## Deliverables

### 1. `.github/workflows/nightly-eval.yml`

- `on: schedule` (pick an hour clear of both prod-health's 11:00 UTC and the digest cron's hourly fires in log timelines — e.g. `0 7 * * *`) + `workflow_dispatch`.
- `concurrency: { group: nightly-eval, cancel-in-progress: false }`.
- **Single job** (deviation from prod-health's two-job shape, deliberately): the issue-filing step needs to read the per-run JSON from the workspace, so it runs as a final `if: failure()` step in the same job rather than a separate `needs:` job. Job permissions: `contents: read`, `issues: write`.
- Steps: clone the `eval-gate` recipe **without** the `paths-filter` step and without the `if: steps.changed...` guards — nightly always runs everything. Same secrets: `ANTHROPIC_API_KEY_EVAL`, `GEMINI_API_KEY_EVAL`, `EVAL_USER_PASSWORD`; same `GEMINI_MODEL_DEFAULT: gemini-2.5-flash`; same `ANTHROPIC_JUDGE_MODEL: claude-sonnet-4-6`.
- **Judge runs weekly, not nightly**: a small step computes the day — `EVAL_JUDGE=$([ "$(date -u +%u)" = "1" ] && echo 1 || echo 0)` — and exports it into the eval step's env. ~20 Sonnet calls once a week instead of seven times; the deterministic suites (which own all gates) still run every night.
- Upload `evals/runs/*.json` as artifact `nightly-eval-run` with `if: always()`.
- **Issue-on-gate-breach step** (`if: failure()`): `actions/github-script@v7`, label `eval-regression`. Dedup exactly like prod-health: if an open `eval-regression` issue exists, comment on it; else create. Body must be structured for Day 31's `claude-plan` loop to consume:
  - which suite(s) breached, score vs gate,
  - the failing row identifiers + per-row failure detail extracted from the freshest `evals/runs/*.json` (read it from disk in the script step),
  - run URL + artifact name,
  - a trailing line: "To hand this to the plan loop, apply the `claude-plan` label." (**Do not auto-apply the label** — reading the transcripts and deciding it's plan-worthy is the human triage gate.)

### 2. `evals/README.md` — add a "Nightly run" section

When it runs, what it detects (model drift), where results land (artifacts + `eval-regression` issues), and the explicit statement that nightly results are **never committed to the repo** — the artifact + issue stream is the record.

### 3. `DESIGN.md` §7.10 sync (same PR)

One short paragraph: the suite runs nightly on a schedule in addition to the per-PR gate; purpose is model-drift detection; judge weekly; regression issues deduped under `eval-regression`.

## Don't

- Don't bolt `schedule:` onto `ci.yml` — memory.md 2026-05-28 documents why that runs the wrong jobs.
- Don't commit results to `main` from the workflow (no `evals/history.md`, no nightly bot commits). Artifacts + issues are the record; ~365 noise commits/year racing dev branches is the failure mode being avoided.
- Don't change any corpus row, threshold, or prompt in this PR. The constant corpus is the instrument.
- Don't auto-apply `claude-plan` to the regression issue. Human triage stays between detection and planning.
- Don't run the judge nightly. Weekly is the agreed cost posture; `EVAL_JUDGE` already supports it without code change.
- Don't add a new Anthropic/Gemini key — reuse the `*_EVAL` keys; their dashboard quotas are the authoritative budget (the nightly adds ~30 suite runs/month to the same meter — verify the quotas absorb that).

## Done when

- `workflow_dispatch` on a branch with an intentionally broken prompt (local test: temporarily degrade `app/prompts/categorize.py` below the 88% gate) produces a red run AND an `eval-regression` issue whose body names the categorization suite, the score, and ≥1 failing row id.
- A second consecutive failing dispatch **comments** on that issue instead of opening a new one.
- A green dispatch produces no issue and uploads the `nightly-eval-run` artifact.
- On a Monday (or with the date check stubbed), the run JSON contains judge scores; on other days it doesn't, and the deterministic scores are present either way.
- `evals/README.md` and DESIGN.md §7.10 reflect the nightly run in the same PR.
