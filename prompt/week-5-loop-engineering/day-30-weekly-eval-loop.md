# Day 30 — Weekly eval loop (model-drift detector)

## Goal

A scheduled GitHub Actions workflow runs the full eval suite **once a week** against the env-resolved production models and auto-files a deduped issue (label `eval-regression`) when any gate breaches — with the failing rows in the issue body. This catches the failure class per-PR CI is structurally blind to: **model drift under unchanged code**. Both chat models and Gemini are env-resolved (CLAUDE.md model table), so the provider can change behavior with zero commits; today that drift is invisible until the next eval-relevant PR happens to run the gate.

**Weekly, not nightly.** An env-resolved model only changes behavior when the provider silently updates the model behind a stable ID — rare, and never a same-day emergency at v1's ~10-user scale (rotating the env var yourself is a deploy, already caught by the per-PR gate). Weekly detection latency is fine for this failure class, costs ~7× less than nightly (~4 paid suite runs/month vs ~30 — roughly a fraction of v1's whole ~$30/mo AI budget instead of doubling it), and lets the judge run on every scheduled run instead of needing a day-of-week gate.

The corpus, prompts, thresholds, and judge are **byte-for-byte unchanged**. If the dataset changed per-run, a score drop could no longer be attributed to the model — the corpus being constant is what makes the weekly delta meaningful.

## Read first

- `.github/workflows/ci.yml` `eval-gate` job (lines ~74–167) — the step recipe this workflow clones: setup-python → Supabase CLI → `supabase start` → `supabase db reset --no-seed` → `pip install -e ".[dev]"` → `seed_eval_fixtures.py` → `python eval.py --eval=all` → upload `evals/runs/*.json` → `supabase stop`.
- `.github/workflows/prod-health.yml` — the standalone-scheduled-workflow precedent, including the deduped issue-on-failure pattern (`actions/github-script@v7`, comment-on-open-issue instead of a fresh issue each run).
- `eval.py` — exit codes (0 = gates met / target-miss warns, 1 = gate breach), `EVAL_JUDGE` toggle (defaults on), per-run JSON shape in `evals/runs/` (each suite's `rows[]` carry the natural key + a `pass` / `*_pass` flag — there is no per-row `id`).
- `memory.md` 2026-05-28 "Scheduled prod-health check is a standalone workflow" — why this is its own file, not `schedule:` bolted onto ci.yml (the post-deploy `needs:`/`if:` chain would skip exactly the jobs a scheduled run wants).

## Deliverables

### 1. `.github/workflows/weekly-eval.yml`

- `on: schedule` (weekly — pick a day/hour clear of the Monday digest cron and the daily 11:00 UTC prod-health, and clear of Day 33b's weekly prod-to-eval; e.g. `0 7 * * 0`, Sunday 07:00 UTC) + `workflow_dispatch`.
- `concurrency: { group: weekly-eval, cancel-in-progress: false }`.
- **Single job** (deviation from prod-health's two-job shape, deliberately): the issue-filing step needs to read the per-run JSON from the workspace, so it runs as a final step in the same job rather than a separate `needs:` job — a separate job runs on a fresh runner with no workspace files. Job permissions: `contents: read`, `issues: write`.
- Steps: clone the `eval-gate` recipe **without** the `paths-filter` step and without the `if: steps.changed...` guards — the weekly run always runs everything. Same secrets: `ANTHROPIC_API_KEY_EVAL`, `GEMINI_API_KEY_EVAL`, `EVAL_USER_PASSWORD`; same `GEMINI_MODEL_DEFAULT: gemini-2.5-flash`; same `ANTHROPIC_JUDGE_MODEL: claude-sonnet-4-6`.
- **Give the eval step an `id`** (e.g. `id: eval`) so the issue step can gate on its outcome (below).
- **Judge runs on every weekly run**: leave `EVAL_JUDGE` at its default (on, per eval.py) — same as ci.yml's eval-gate. No day-of-week gate, no `date -u +%u` step. ~20 Sonnet calls per weekly run; the deterministic suites (which own all gates) run every time too.
- Upload `evals/runs/*.json` as artifact `weekly-eval-run` with `if: always()`.
- **Issue-on-gate-breach step**: gate it on `if: steps.eval.outcome == 'failure'` (NOT job-level `if: failure()`) so an *infrastructure* failure — Supabase didn't boot, `pip install` died — does **not** file a bogus `eval-regression` issue (GitHub still emails the repo owner on scheduled-run failure). `actions/github-script@v7`, label `eval-regression`. Dedup exactly like prod-health: if an open `eval-regression` issue exists, comment on it; else create. If the freshest `evals/runs/*.json` is missing or unreadable (the eval errored before writing it), fall back to a body that says "eval did not complete — see run log" rather than crashing the step. Body must be structured for Day 31's `claude-plan` loop to consume:
  - which suite(s) breached, score vs gate,
  - the failing rows from the freshest `evals/runs/*.json` (read from disk in the script step), each identified by that suite's **natural key** (`merchant` for categorization, `user_message` for chat_extraction, `prompt` for multi_hop — there is no per-row `id`), with the per-row failure detail (`got` vs `expected`),
  - run URL + artifact name,
  - a transient-failure triage line: "If the next scheduled run is green, this was a transient provider blip (e.g. a 5xx burst) — close this issue." (Agreed posture: transient failures just retry on the next weekly run; we don't build infra-vs-drift partitioning.),
  - a trailing line: "To hand this to the plan loop, apply the `claude-plan` label." (**Do not auto-apply the label** — reading the transcripts and deciding it's plan-worthy is the human triage gate.)

### 2. `evals/README.md` — add a "Weekly run" section

When it runs (weekly schedule + `workflow_dispatch`), what it detects (model drift), where results land (artifacts + `eval-regression` issues), and the explicit statement that weekly results are **never committed to the repo** — the artifact + issue stream is the record. Note the transient-failure posture: a flaky run self-heals on the next weekly fire.

### 3. `DESIGN.md` §7.10 sync (same PR)

One short paragraph: the suite runs **weekly** on a schedule in addition to the per-PR gate; purpose is model-drift detection; the judge runs on every weekly run; regression issues deduped under `eval-regression`.

> Note for the PR description (not DESIGN.md body): this weekly cadence supersedes the "nightly / judge Mondays-only" framing in the memory.md 2026-06-23 loop-engineering decision — cadence was changed to weekly for cost. memory.md is append-only; don't edit the live entry, flag the change for the next `/distill`.

## Don't

- Don't bolt `schedule:` onto `ci.yml` — memory.md 2026-05-28 documents why that runs the wrong jobs.
- Don't commit results to `main` from the workflow (no `evals/history.md`, no bot commits). Artifacts + issues are the record; ~52 noise commits/year racing dev branches is the failure mode being avoided.
- Don't change any corpus row, threshold, or prompt in this PR. The constant corpus is the instrument.
- Don't auto-apply `claude-plan` to the regression issue. Human triage stays between detection and planning.
- Don't build infra-vs-drift partitioning or a retry queue. A transient failure (provider 5xx storm) just retries on the next weekly run; the issue body's triage line covers the one-off case.
- Don't add a new Anthropic/Gemini key — reuse the `*_EVAL` keys; their dashboard quotas are the authoritative budget (the weekly run adds ~4 suite runs/month — ~195 calls each, judge included — to the same meter; verify the quotas absorb that).

## Done when

- `workflow_dispatch` on a branch with an intentionally broken prompt (local test: temporarily degrade `app/prompts/categorize.py` below the 0.88 categorization gate) produces a red run AND an `eval-regression` issue whose body names the categorization suite, the score vs gate, and ≥1 failing row by its natural key (the merchant).
- A second consecutive failing dispatch **comments** on that issue instead of opening a new one.
- A green dispatch produces no issue and uploads the `weekly-eval-run` artifact.
- Every run's JSON contains **both** judge scores (multi_hop helpfulness/tone) and the deterministic scores — the judge is no longer day-gated.
- An infrastructure failure (e.g. Supabase fails to start) does **not** file an `eval-regression` issue (the `steps.eval.outcome` gate holds).
- `evals/README.md` and DESIGN.md §7.10 reflect the weekly run in the same PR.
