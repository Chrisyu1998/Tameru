# Day 34 — Loop-engineering writeup (the artifact an interviewer actually reads)

## Goal

`docs/loop-engineering.md`: a writeup mapping Tameru's dev and product loops onto the loop-engineering primitives (Addy Osmani, "Loop Engineering"), with the full pipeline diagram and the one-pattern-two-layers argument. The code is the evidence; this document is the exhibit — no interviewer reads `ci.yml`, everyone reads a 1,500-word doc with a diagram. Write it **after** Days 30–33 ship so every claim is checkable against the repo.

## Read first

- https://addyosmani.com/blog/loop-engineering/ — the primitives and the warnings; the doc quotes its framing accurately rather than from memory.
- The shipped artifacts being described: `.github/workflows/weekly-eval.yml`, `claude-plan.yml`, `prod-to-eval.yml`, `prod-health.yml`, `eval.py` (quarantine), `app/services/hygiene.py`, `memory.md` (now committed), CLAUDE.md, `evals/prod_patterns.json`.

## Deliverables

### `docs/loop-engineering.md`

Structure:

1. **Thesis** (short): loop engineering is designing the system that prompts the agent — and the load-bearing prerequisite is machine-checkable verification, which Tameru built first (deterministic eval gates, contract tests, RLS tests) and the loops then stand on.

2. **The primitive map** — a table, each row linking to the real file:
   | Primitive | Tameru implementation |
   |---|---|
   | Skills (knowledge agents don't re-derive) | CLAUDE.md invariants + contract tests that *enforce* them |
   | Persistent state ("memory has to be on disk") | `memory.md` decision log (committed), `evals/prod_patterns.json` |
   | Writer/grader separation | Codex (different vendor) reviews Claude's code; Sonnet judge grades the Haiku student |
   | Automations | weekly-eval, prod-health, prod-to-eval schedules |
   | Connectors | `gh`-driven deduped issues, label-triggered plan/PR workflows |
   | Sub-agents | plan stage vs implement stage; finder (judge) vs verifier (reproduction check) |

3. **The composed pipeline** — one diagram (Mermaid): production failure → trace judge → quarantined eval case (PR, human merge) → regression issue → `claude-plan` (human label) → plan comment (human approve) → draft PR → eval gate + Codex review (human merge) → un-quarantine → permanent regression guard. Annotate every human gate explicitly.

4. **One pattern, two layers** — propose-then-confirm is the same shape at both altitudes: in-product, tools propose and the user's tap commits (invariant 8; the Day 32 hygiene loop); in the dev process, agents propose (plan comments, draft PRs, eval-case PRs) and the human label/merge commits. The loops added no new commit authority anywhere — that's the design stance, not an accident.

5. **Honest limits** — quiet weeks at 10 users (the backtest was the harvest; the cadence proves the loop), judge false-positive modes, comprehension-debt and cognitive-surrender risks per the article, and which gates exist because of them. This section is what makes the document credible rather than promotional.

6. **Numbers** — corpus growth (114/61/21 hand-written → +N prod-derived), loop cadences, cost posture (eval-key quotas, weekly judge), gates passed/issues filed to date.

### README.md

A short "How this is built" pointer to the doc, next to the existing dev-loops section from Day 31.

## Don't

- Don't write it before the loops ship — a writeup describing intended behavior rots into resume risk the first time an interviewer checks.
- Don't inflate: no claims of full autonomy, no hiding the human gates — the gates *are* the sophistication argument.
- Don't quote user data, real trace content, or specific findings in examples; structural descriptions only, same redaction bar as the pipeline itself.
- Don't let it sprawl past ~1,500 words. One table, one diagram, six sections.

## Done when

- Every claim in the doc resolves to a file, workflow, or issue that exists in the repo.
- The diagram renders on GitHub and every human gate is visibly annotated.
- Someone who has read Osmani's post and not this repo can name, after one read: the three dev loops, the product loop, where the gates are, and why the suite can't be bricked by a born-failing case.
- README links to it.
