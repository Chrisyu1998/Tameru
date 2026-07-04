# Day 31 — Issue → plan → draft-PR loop (propose-then-confirm for the dev process)

## Goal

Labeling an issue `claude-plan` makes Claude Code post a **design plan as a comment** — restated goal, explicit invariant check against CLAUDE.md, clarifying questions, step breakdown, **no code**. Replying and applying `plan-approved` makes it open a **draft PR** implementing the approved plan. The human gates are exactly the two judgment points: plan approval and merge. Combined with Day 30, this completes the pipeline: the weekly eval detects drift → issue auto-filed → you label `claude-plan` → plan comment → you approve → draft PR → eval gate + CI + your review.

## Depends on

- Day 30 merged (the `eval-regression` issue body is written to be `claude-plan`-ready). The loop also works on hand-written issues — Day 30 is the supplier, not a hard dependency.

## Setup — commit `memory.md` first (prerequisite, ~30 min, separate commit)

A CI-side Claude gets CLAUDE.md's invariants but **none** of the decision history if `memory.md` stays local — exactly the "re-deriving the choice without context produces the wrong answer" failure mode CLAUDE.md warns about. The file is also the public demonstration of the persistent-state loop primitive.

1. Scrub-check `memory.md` (and `memory-archive.md` if present): grep for key material (`sk-`, `eyJ`, `SUPABASE_SERVICE_ROLE`), emails, and anything PII-shaped. Expected result: clean — it's technical narrative. If anything trips, redact before committing.
2. Remove the `memory.md` entry from `.gitignore` (the "Personal project memory" block, ~lines 60–61).
3. Update the CLAUDE.md "Project memory" section in the **same commit** — it currently says "Local-only (gitignored, missing for collaborators)"; rewrite to reflect that the file is committed and why (CI agents need the decision history).

## Read first

- `CLAUDE.md` — the full invariants list; the plan comment's invariant-check section is graded against it.
- The `anthropics/claude-code-action` README (current major version) — input names (`prompt`, `claude_args`, etc.) change between majors; pin to a major version tag and consult the docs rather than trusting this prompt's memory of the API.
- `.github/workflows/prod-health.yml` — house style for workflow comments and permissions hygiene.

## Deliverables

### 1. `.github/workflows/claude-plan.yml`

- Trigger: `issues: [labeled]`. Two label branches in one workflow (or two jobs): `claude-plan` → plan stage; `plan-approved` → implement stage.
- **Gate both stages on the labeler**: `github.event.label.name == '<label>' && github.actor == github.repository_owner`. Cheap backstop so only you can invoke the loop, even if the repo later gains collaborators.
- Secret: a **separate** `ANTHROPIC_API_KEY_DEVLOOP` with a dashboard quota (same key-isolation pattern as the eval keys — a runaway loop bills its own meter, not prod's).
- Permissions: plan stage `issues: write`, `contents: read`. Implement stage adds `contents: write`, `pull-requests: write`.

### 2. Plan-stage prompt contract

The action's prompt instructs Claude to post **one comment** containing, in order:
1. **Restated goal** — what the issue is asking for, in its own words (catches misreads at the cheapest point).
2. **Invariant check** — for each CLAUDE.md invariant the work touches: named, and either "compatible because …" or "⚠ conflicts — needs explicit approval." memory.md decisions that bear on the approach are cited by date.
3. **Clarifying questions** — anything blocking a confident plan. If questions exist, the plan below is explicitly conditional on the answers.
4. **Plan** — files to touch, migration/RLS implications, test plan, DESIGN.md sync items, estimated size.
5. The handoff line: "Apply `plan-approved` to open a draft PR, or reply with corrections and re-label `claude-plan` to re-plan."

Hard rule in the prompt: **no code edits, no branches, no PRs at this stage.** The comment is the only output.

### 3. Implement-stage prompt contract

- Reads the issue thread (plan comment + your replies — the replies are the approved spec, including any corrections).
- Branch `claude/issue-<n>-<slug>`, implements the plan, runs the local test suite, opens a **draft** PR linking the issue.
- PR body: plan summary, deviations from the plan (if any, with reasons), test results. No attribution footer (CLAUDE.md commit rules apply to the bot too — the action's default Co-Authored-By must be disabled/overridden).
- The PR rides the existing gates: CI, eval-gate (if eval-relevant paths changed), your Codex review. The loop adds **no merge authority** — draft PRs only, never auto-merge, never auto-approve.

### 4. Docs

- `DESIGN.md` §15-adjacent (or a new short subsection): the dev-loop exists, its two human gates, its label contract.
- A short "Dev loops" section in the repo `README.md` naming the three labels (`eval-regression`, `claude-plan`, `plan-approved`) and what each transition does — this is also writeup fodder for Day 34.

## Don't

- Don't let the plan stage write code or open PRs. The whole point is the design gate.
- Don't auto-merge, auto-approve, or mark the PR ready-for-review from the workflow. Draft only.
- Don't auto-apply `claude-plan` to `eval-regression` issues (Day 30's Don't, restated from the consumer side).
- Don't skip the labeler gate "because the repo is private." It's one expression; defense in depth.
- Don't reuse the prod or eval Anthropic keys for the action.
- Don't add Claude attribution footers to the bot's commits or PR bodies — CLAUDE.md commit rules bind the bot exactly as they bind interactive sessions.

## Done when

- `memory.md` is committed, gitignore updated, CLAUDE.md "Project memory" section rewritten — as its own commit, before the workflow lands.
- A test issue ("add a `--suite-summary` flag to eval.py" or similar small-but-real ask) labeled `claude-plan` receives a plan comment with all five sections, including a non-empty invariant check.
- Applying `plan-approved` produces a draft PR on a `claude/issue-*` branch that passes CI.
- An issue labeled by a non-owner account (or a simulated `github.actor` mismatch) produces no run.
- A Day 30 `eval-regression` issue, hand-labeled `claude-plan`, produces a plan that correctly identifies the failing suite from the structured body.
