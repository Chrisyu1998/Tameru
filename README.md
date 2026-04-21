# Tameru

Spending intelligence, powered by AI. Mobile-first PWA, invite-only.

You tell Tameru what you spent. It tells you what it means.

## Source-of-truth documents

- **`DESIGN.md`** — full system design. Read before making non-trivial decisions.
- **`CLAUDE.md`** — invariants and guardrails for Claude Code sessions working in this repo. Loaded automatically.
- **`prompt/`** — 28 day-by-day build prompts derived from `DESIGN.md` §15 (Milestones).

## Build status

The codebase is being scaffolded one day at a time. See `prompt/` for the plan.

## How to use the prompt folder

1. Open a fresh Claude Code session in the project root.
2. Paste the day's prompt verbatim. Claude Code will read `CLAUDE.md` automatically; the prompt references `DESIGN.md` sections so you don't need to paste those.
3. When the day's deliverables are done, commit. Move to the next day.
4. If a day's prompt fails or runs over, **don't skip ahead** — finish it before starting the next one. Days build on each other.

Each prompt follows the same structure:

- **Goal** — one sentence.
- **Read first** — `DESIGN.md` sections + `CLAUDE.md` invariants relevant to that day.
- **Deliverables** — concrete files / endpoints / behaviors.
- **Don't** — scope guards (do not add things outside the day's scope).
- **Done when** — acceptance criteria you can check.

### Phases

| Week | Folder | Theme |
|---|---|---|
| 1 | `prompt/week-1-foundation/` | Backend, schema, RLS, Gemini categorization, CSV import, deploy |
| 2 | `prompt/week-2-ui-core-flows/` | PWA scaffold, philosophy + tour, cards, manual entry, dashboard, subscriptions |
| 3 | `prompt/week-3-agent-memory/` | Claude `tool_use` agent, SSE streaming, chat UI, cross-session memory, NL entry |
| 4 | `prompt/week-4-eval-mcp-observability/` | Eval harness, MCP server, observability, weekly digest, PostHog, privacy, E2E launch |

The 28-day plan assumes ~4–6 hours of focused work per day. If your real days are shorter, stretch the calendar — the dependency order matters, the calendar doesn't.

## Hard rules carried across every day

Duplicated from `CLAUDE.md` because they're easy to forget under build pressure:

- **RLS via JWT.** FastAPI handlers use a per-request Supabase client initialized with the user's JWT. Never `SUPABASE_SERVICE_ROLE_KEY` in handler code.
- **Messages API + `tool_use`** for the chat agent. Not Managed Agents. Not LangChain.
- **MCP read-only**, per-user bearer tokens.
- **`pg_cron`** for the subscription auto-logger and the AICallLog rollup. Not FastAPI background tasks.
- **Single device** per user.
- **Schema changes via Supabase CLI migrations** under `supabase/migrations/`. Never the dashboard SQL editor.
- **No Expo. PWA only.**
- **NL entry parses on submit/blur**, not debounced keystrokes.
- **PostHog gets structural events only.** No transaction amounts, merchant names, or chat text.
- **Daily Claude token cap per user** (200K). Bounds runaway cost.

## When something goes wrong during a build day

- A prompt mid-week assumes earlier days are done. If you skipped ahead, back up.
- If a deliverable is genuinely blocked (API returns the wrong shape, env doesn't exist), pause and fix before continuing — don't paper over it.
- If you find a contradiction between a prompt and `DESIGN.md`, **`DESIGN.md` wins**. Update the prompt and note what changed.

## Local development

Prerequisites: Python 3.11+, Docker, and (optional) the Supabase CLI.

```bash
# 1. Environment
cp .env.example .env           # fill in values locally; .env is gitignored

# 2. Install the backend (editable)
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

# 3. Run the API
uvicorn app.main:app --reload
curl localhost:8000/healthz    # -> {"ok": true}

# 4. Local Postgres for schema work (Day 2+)
supabase start

# 5. Container build (what Railway runs)
docker build -t tameru .

# 6. Secret scan before pushing
brew install gitleaks          # one-time
gitleaks detect --source .
```

The frontend lives under `frontend/` starting Day 8.
