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
| 1 | `prompt/week-1-foundation/` | Backend foundation (scaffold, schema, RLS, Gemini categorization, transactions API) + PWA shell + auth UI |
| 2 | `prompt/week-2-chat-mvp-and-deploy/` | Claude `tool_use` loop + all typed tools + chat UI (ParseCard) + Railway deploy + SSE streaming + dashboard + cards/Perplexity — end of Week 2 is the first dogfood-able build |
| 3 | `prompt/week-3-polish-and-extras/` | Transaction list + edit sheet + offline queue, cross-session memory + decay, voice input, subscriptions + pg_cron, CSV import, philosophy + tour |
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

Prerequisites: Python 3.11+, Docker Desktop (running, required by `supabase start`), and the Supabase CLI.

```bash
# 1. Environment
cp .env.example .env           # fill in values locally; .env is gitignored

# 2. Install the backend (editable)
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

# 3. Run the API
uvicorn app.main:app --reload
curl localhost:8000/healthz    # -> {"ok": true}

# 4. Install the Supabase CLI (one-time)
brew install supabase/tap/supabase        # macOS (recommended)
# or: npm install -g supabase              # cross-platform fallback

# 5. Boot local Postgres + Auth and apply migrations from scratch
supabase start                 # first run pulls Docker images — takes a few minutes
supabase db reset              # drops the DB and replays supabase/migrations/ + seed.sql

# 6. Generate the next migration after a schema change
supabase db diff -f <short_name>   # writes a timestamped .sql to supabase/migrations/

# 7. Container build (what Railway runs)
docker build -t tameru .

# 8. Secret scan before pushing
brew install gitleaks          # one-time
gitleaks detect --source .
```

Schema changes are **always** made by editing or adding a migration under
`supabase/migrations/` and running `supabase db reset` to verify. Never use
the Supabase dashboard SQL editor for schema changes (CLAUDE.md invariant 6).

## Frontend (PWA)

The Vite + React + Tailwind v4 + Zustand PWA lives under `frontend/` (scaffolded Day 6). It is served statically on Vercel in production and talks to the FastAPI backend on Railway cross-origin via CORS with Bearer-token auth (`DESIGN.md` §5.3).

```bash
cd frontend
cp .env.example .env.local     # .env.local is gitignored; fill in VITE_API_URL + Supabase
npm install
npm run dev                    # http://localhost:5173, proxies API calls to VITE_API_URL
npm run build                  # outputs dist/ (what Vercel deploys)
npm run preview                # serves the built dist/ for Lighthouse runs / offline shell tests
npm run icons                  # regenerate public/icon-192.png etc. after a palette change
```

CORS allowlist in production: set `FRONTEND_ORIGIN=https://tameru.app` (or whatever the Vercel domain is) in Railway. Local dev always allows `http://localhost:5173`. Never use wildcards — Day 11 deploys the Vercel frontend alongside the Railway backend.

## Google OAuth setup

Sign-in is Google OAuth via Supabase Auth; magic link is the fallback
(`DESIGN.md` §9.1).

**Supabase Dashboard → Authentication → Providers → Google:**

1. Enable the Google provider.
2. In Google Cloud Console, create an OAuth 2.0 Client ID (Web application).
3. Authorized redirect URIs — add **both**:
   - Hosted: `https://<your-project-ref>.supabase.co/auth/v1/callback`
   - Local: `http://127.0.0.1:54321/auth/v1/callback`
4. Copy the Client ID and Client Secret into the Supabase provider form.

The frontend initiates sign-in with `supabase.auth.signInWithOAuth({ provider:
"google" })` (lands Day 7). The backend never talks to Google directly — it
only validates the JWT Supabase issues after the user completes the flow.

## JWT validation

FastAPI verifies each request's `Authorization: Bearer <jwt>` locally in
`app/auth.py` against the project's asymmetric JWKS at
`${SUPABASE_URL}/auth/v1/.well-known/jwks.json`. We pin `algorithms=
["ES256"]` and require `audience="authenticated"` plus `issuer=
"${SUPABASE_URL}/auth/v1"`. PyJWT's `PyJWKClient` caches the keys and
refreshes on `kid` miss, so there is no per-request round trip to Supabase
Auth. The verified identity flows into `supabase_for_user`, so PostgREST
forwards the JWT and Postgres enforces RLS on `auth.uid()`.

## RLS contract tests

These tests create throwaway users and assert that user A's rows are
invisible and unwritable to user B — for every RLS-protected table
(`DESIGN.md` §13.1). They run against the **local** Supabase stack only;
`tests/conftest.py` refuses to run if `SUPABASE_URL` is not localhost.

```bash
supabase start                     # once per machine boot
supabase db reset                  # apply migrations
pytest tests/test_rls_contract.py tests/test_no_service_role_leak.py
```

`conftest.py` auto-populates `SUPABASE_URL`, the anon/service-role keys, and
`SUPABASE_JWT_SECRET` from `supabase status -o json` when those env vars are
unset, so the common flow above "just works" without exporting them by hand.

## Privacy

Tameru's live privacy disclosures live **in the app** at
**Settings → Privacy** (desktop) and **/privacy** (mobile). The two surfaces
render the same shared component (`frontend/src/components/PrivacyDisclosure.tsx`)
so they always stay in lockstep.

The disclosure covers what's sent to which AI provider, what PostHog tracks
(structural events only — never transaction data, never chat text), and the
opt-out controls.

You can also **export everything we have on you** as a single JSON file via
the "Export my data" button on either surface. The export covers transactions,
cards, subscriptions, chat history, memory facts, merchant overrides, and
preferences. Internal observability records (per-turn audit, AI call log,
email log) are intentionally excluded; see
[app/routes/export.py](app/routes/export.py) for the v1 inclusion list.

Account deletion is currently handled by emailing **hello@mail.tameru.xyz**
(an in-app button is post-v1 per `DESIGN.md` §17.11). The "Delete my account"
row on either privacy surface opens a pre-filled mail draft.

Anthropic Zero Data Retention is **requested** for the Tameru org; until
it's granted, Anthropic's default 30-day trust & safety retention applies.
See [docs/zdr_request.md](docs/zdr_request.md) for the request log.
