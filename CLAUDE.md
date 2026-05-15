# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Tameru** is a mobile-first PWA for spending intelligence. Manual transaction entry + AI-assisted categorization + agentic chat over your spending data + an MCP server. Multi-tenant from day one. **v1 scope: invite-only, ~10 friends and family, free for everyone, no Stripe, no paid tier** (DESIGN.md §3.3). A forward plan for conditional scaling to ~100 users with paid tier via Stripe is documented in DESIGN.md §17 — that plan is not v1 scope and only activates on an explicit later decision. The full design lives in `DESIGN.md` — read it before making non-trivial decisions. This file captures only the invariants future Claude instances will trip on if they don't know.

## Stack

React PWA (Vite + Tailwind + Zustand) · FastAPI (Python) · Supabase (Postgres + Auth + RLS) · Anthropic API (Messages + `tool_use`) · Google Gemini 3.1 Flash-Lite (`gemini-3.1-flash-lite-preview`, currently in preview) · Perplexity Sonar · Resend · PostHog · Sentry · Railway (backend) + Vercel (frontend, static + CDN; cross-origin via CORS with Bearer-token auth — DESIGN.md §5.3, §9.3) · Postgres `pg_cron`.

Stripe is documented in the forward plan only — not a v1 dependency.

The codebase is being scaffolded. If a tool, command, or directory you need doesn't exist yet, check `DESIGN.md` for the intended shape before inventing one.

## Code organization doctrine

- **Docstrings are required on every Python function, class, test, and private helper.** Use docstrings rather than loose comments so IDEs surface the contract and `tests/contracts/test_docstring_doctrine.py` can enforce it. For tests, state the behavior being verified. For helpers, state why the helper exists or what contract it preserves.
- **Primary entry points come before helpers.** Keep route handlers, tool implementations, service entry points, and other file-defining functions above private helpers. Constants, imports, Pydantic models, and registry declarations may stay near the top when they define the public contract. Put private helper functions near the bottom of the file unless Python execution order makes that impossible.
- **Typed boundaries use Pydantic models.** Requests, responses, agent tool inputs, and agent tool results should have explicit Pydantic models where practical. Follow Stripe-style API documentation: describe what the function/endpoint does, list the request shape, list the response shape, and include a compact example when the contract is not obvious.
- **Agent tools document request and response examples.** A tool like `calculate_total` should make it clear what Claude supplies (for example, optional filters) and what the loop returns (for example, `{total, count, truncated}`). The user's auth context is injected by the server-side loop, never supplied by the model.

## Architectural invariants (do not violate without explicit user approval)

These are load-bearing decisions from the design review. Each one was chosen over a tempting alternative for a specific reason — re-deriving the choice without context will produce the wrong answer.

1. **RLS is enforced via the user's JWT, not the service role.** FastAPI receives `Authorization: Bearer <user_jwt>` and instantiates a per-request Supabase client passing that JWT. Postgres enforces `auth.uid() = user_id` automatically. In v1, the `SUPABASE_SERVICE_ROLE_KEY` is reserved for **two callers only**: the `pg_cron` daily auto-logger (DB function, no app context) and Supabase CLI migrations. Application handlers never use the service role. If you find yourself reaching for it in a request handler, stop — you're about to bypass RLS. **Forward-plan addition (only if the §17 scaling plan activates):** a Stripe webhook handler would become a sanctioned third caller, because no user JWT is in scope for a Stripe-initiated request — it resolves `user_id` via `stripe_customer_id` on `users_meta`. Do not add this path until the scaling decision is made.

2. **The Claude agent loop runs in FastAPI using the Messages API with `tool_use` blocks.** Not Claude Managed Agents (designed for long-running autonomous work in Anthropic's container — wrong fit for 4–6s chat turns and DB-backed tools). Not LangChain classic (general-purpose LLM-app toolbox; `AgentExecutor` opacity, `langchain-anthropic` adapter lag on prompt-caching/beta features, message-shape translation tax for `chat_turn_trace`). Not LangGraph (strongest alternative — sweet spot is multi-step branching workflows with checkpointing and human-in-loop interrupts; Tameru's single-agent two-state cycle doesn't earn the state-machine ceremony, and `PostgresSaver` checkpoints overlap with `chat_turn_trace` with weaker wire-shape fidelity). Not Google ADK (multi-agent framework optimized for Vertex AI Agent Engine deploy — Tameru is single-agent on Railway, and ADK's session abstraction would break the JWT-lifetime-matches-request-lifetime property below). Not the standalone Agent SDK as a wrapper. See DESIGN.md §7.1 for the full rationale on each. The loop is ~80 lines and lives in our process so the user's JWT is in scope when typed tools execute — `ctx.user_jwt` is a request-local Python variable whose lifetime equals the HTTP request's lifetime, so no session container is needed and Supabase RLS auto-enforces `auth.uid() = user_id` because the JWT is in scope when the tool runs.

3. **MCP server is read-only and uses per-user bearer tokens.** Tokens are 32 random bytes, stored as `sha256` in `mcp_tokens`, returned to the user once. No `add_transaction` over MCP in v1 — leaked tokens read data; they cannot mutate it.

4. **Subscription auto-logger is a `pg_cron` SQL function, not a FastAPI background task.** Idempotency from `UNIQUE (subscription_id, date)` on `transactions` plus `INSERT ... ON CONFLICT DO NOTHING`. Concurrent runs prevented via `pg_try_advisory_lock`. `next_billing_date` advances only after successful insert in the same transaction.

5. **Single active device per user.** `users_meta.active_device_id` updated on sign-in; previous device's session is revoked. There is no offline multi-device conflict resolution because there can be no conflict.

6. **Schema changes go through Supabase CLI migrations checked into `supabase/migrations/`.** Never edit production schema via the dashboard SQL editor outside emergencies. RLS policies live in migration files alongside the tables they protect.

7. **PWA today. Expo and Capacitor rejected. Swift admitted as a possible future migration — not on the roadmap.** PWA is the ship target for v1 (~10-user invite-only) and remains the only surface unless and until a scaling decision changes that. Expo and Capacitor were evaluated and rejected (see DESIGN.md §10.3). Native Swift may later be worth it if three signals all fire: explicit user demand, measurable PWA UX drag on retention, and sustained paid-tier revenue (§10.4) — none of which can exist at the v1 scale. Do not start Swift work speculatively. iOS web push limitations are accepted; the weekly digest is email-first via Resend.

8. **Chat is the only user-initiated create surface in v1. No transaction, card, or subscription row is ever written or mutated from inside a `tool_use` handler — those flow through propose-then-confirm.** Users create transactions, cards, and subscriptions by typing or speaking in chat → Claude Haiku extracts fields via `tool_use` args (no separate Gemini NL-parse call) → the tool returns a proposal payload → the UI renders it as a preview card (UX frame 15) → a `POST /<resource>/confirm` endpoint writes the row only after the user taps "looks right." There is no separate `+`-button entry form in v1; do not add one. The agent has no direct-mutate tools for ledger rows — no `edit_transaction`, no `delete_transaction`, no `add_*` — its mutation role on the ledger is retrieval + proposal only. Edits and deletes reach the backend via explicit HTTP `PATCH` / `DELETE` calls triggered by a user tap on the edit sheet (UX frame 11b), reached from the per-category list or from a chat-rendered candidate-card list produced by `get_transactions(...)`. An inline chat confirm card for the exact-1-match case is documented in §6.2 as a post-launch enhancement; v1 does not implement it. The load-bearing rule is that the `tool_use` call is never the commit for ledger rows — not which UI surface the confirming tap lives on; future surfaces can be added without violating this invariant as long as ledger mutation flows through an explicit HTTP call. CSV import and receipt photo (deferred) remain Gemini-parsed — bulk/async, not user-typed chat text. **The single exception to the no-tool-write rule is `set_goal`** (DESIGN.md §7.2) — goals are low-risk, reversible, and not on the transaction ledger, so the propose-confirm ceremony isn't worth it. `set_goal` writes directly via the user's JWT (RLS still scopes the upsert) and latest-wins is enforced at the schema layer by a `UNIQUE NULLS NOT DISTINCT` constraint plus PostgREST upsert (§8.13). Adding any *additional* direct-write tool requires explicit user approval; the structural test at `tests/contracts/test_tool_write_invariant.py` fails the build if anyone widens `ALLOWED_DIRECT_WRITE_TOOLS` without a rationale comment.

9. **The dashboard fits on one screen with no scrolling.** Adding a tile requires removing one. Historical analysis lives in the AI chat (generative charts), not in the dashboard. The entry-moment insight is the primary behavioral intervention — not the dashboard.

10. **Onboarding "demo" is a guided tour with hardcoded fixture data, not a live AI on fake data.** Static screens of real components rendered with fixtures. No fake-data layer in Supabase. No AI calls in the tour.

11. **If billing is ever added, it is Stripe on the web — never IAP.** v1 has no paid tier and no billing. This invariant is a pre-agreed stance for the conditional §17 forward plan: when/if a paid tier ships, subscriptions go through a Stripe web checkout. Even if a native iOS build later ships, the checkout remains on the web (Spotify/Netflix model) — the 30% App Store IAP cut is not paid. Stripe state (`stripe_customer_id`, `stripe_subscription_status`, `stripe_current_period_end`, `plan`) would live on `users_meta`; webhook idempotency would be keyed on `stripe_events.event_id`. The freemium gate would be enforced in FastAPI middleware keyed on `users_meta.plan`, not in Stripe plan logic — Stripe only says which bucket the user is in. Do not implement any billing surface without an explicit scaling decision.

12. **Backend is frontend-stack-agnostic.** FastAPI + per-request JWT + Supabase must work identically for a browser PWA, a Capacitor wrapper, or a future Swift client. No `User-Agent` branching in handlers. No frontend-specific payload shapes. This is the property that makes the Swift-migration decision (§10.4) reversible.

13. **Single home currency per user, chosen at signup, immutable.** `users_meta.home_currency` is set once and never changes. All transaction and subscription amounts are stored in this currency, as `numeric` (never `float`). v1 does not support per-transaction currency selection or FX conversion; foreign purchases are entered as they will appear on the user's card statement in home currency. Immutability is enforced at the DB layer by a BEFORE UPDATE trigger, not by application code. Allowed set lives in the CHECK constraint on the `home_currency` column (DESIGN.md §8.7) — to add a currency, extend the CHECK in the migration; do not add a separate allowlist in code. If a user genuinely needs a different home currency, the escape hatch is account deletion and re-signup, not a migration path.

14. **`ai_call_log` writes from request handlers use the user's JWT and a narrow INSERT policy.** Not the service role. The INSERT policy is `WITH CHECK (user_id = auth.uid())`; there are no UPDATE or DELETE policies, so a compromised JWT cannot scrub audit history (it can only forge rows on its own account, which deceives no one). Service-role writes to `ai_call_log` are reserved for system-level callers with no user JWT in scope — the `pg_cron` rollup and future digest jobs. Do not introduce a service-role `ai_call_log` write path inside a request handler, even if it feels simpler.

## Model usage by task

| Task | Provider/model | Notes |
|---|---|---|
| Categorization, CSV parse, receipt vision | Gemini (env-resolved) | Resolved from `GEMINI_MODEL` (override) or `GEMINI_MODEL_DEFAULT` (platform default) at call time — **no hardcoded model strings in the code**. v1 production default: `GEMINI_MODEL_DEFAULT=gemini-2.5-flash` (GA, stable). The preview model `gemini-3.1-flash-lite-preview` is available via `GEMINI_MODEL` for eval experiments; observed to 503 intermittently, which is why it's not the default. Paid tier only. Do not use Gemini 3.1 Pro — it's a reasoning model, overkill and expensive for simple extraction. Rotate env vars (no code change) if Google deprecates either model. |
| Card multiplier lookup | Perplexity Sonar | One call per card add. Citations stored as `cards.source_urls`. |
| Chat agent | `claude-haiku-4-5` | Messages API + `tool_use`. Gemini 3.1 Flash-Lite was evaluated and rejected for v1 chat — see DESIGN.md §11.4. Re-evaluation is a planned post-launch A/B via the multi-hop eval suite. Do not switch unilaterally. |
| Weekly digest narrative | `claude-sonnet-4-6` | Called weekly; prose quality matters |
| Memory distillation | `claude-haiku-4-5` | Background after each chat session |

Model strings are read from environment variables, not hardcoded — change a model by changing the env, not the code.

## Privacy posture (matches user-facing copy)

- Anthropic: Zero Data Retention requested for the org. Default 30-day T&S retention drops to 0 with ZDR. Not used for training.
- Gemini: paid tier only — no training use. Free tier is forbidden.
- Perplexity: receives only the public card name. No transaction data.
- PostHog: structural product events only. **Never** send transaction amounts, merchant names, card details, or chat question text. There is no client-side question classifier — that idea was rejected for being a privacy surface that didn't earn its keep.

If you find yourself adding any field to a PostHog event that contains user-generated text or numeric financial data, stop.

## What is in scope and what is not

**Permanently out of scope:** net worth tracking, investments, credit score, budgeting forecasts, per-transaction multi-currency / FX conversion (single home currency per user is supported — see invariant 13), international cards, Plaid / Teller.io auto-sync, in-app purchases via App Store IAP, public launch / Product Hunt, Expo / React Native.

**v1 target (current build):** ~10 users via invite-only, free for everyone, no paid tier, no Stripe. See DESIGN.md §3.3 and §11.3 (v1 cost math).

**Deferred (do not build proactively):** transfer bonus digest, SUB wishlist alerts, spending limits, receipt photo, card recommender. All post-launch, author-driven.

**Conditional future plan (not v1 scope):** scaling to ~100 users via open invite link with a paid tier via Stripe on the web. Documented in DESIGN.md §17 and §11.6. Activation requires an explicit later decision — do not build the punch-list items proactively. Swift iOS migration is admitted as a possible further-future path (§10.4).

**Phase 1 only (what ships in v1):** everything in §6 of `DESIGN.md` marked Phase 1. Nothing from §17.

## Commit message rules

- **Never include `Co-Authored-By: Claude` or any "generated by Claude" footer in commit messages.** The user has explicitly opted out of this attribution style. Write commit messages as if a human author wrote them. The same applies to PR descriptions.

## Keeping `DESIGN.md` in sync with decisions

`DESIGN.md` is the source of truth. If a discussion in a session resolves an ambiguity, adds a constraint, or changes an architectural decision — and the user agrees to it — update `DESIGN.md` in the **same change** that touches the code, migration, or prompt reflecting that decision. Don't leave the design doc lagging behind the schema or the build prompts.

Specifically:

- If a schema migration deviates from what §8 says (column nullability, FK on-delete behavior, RLS shape, indexes that affect behavior, CHECK constraints) — update §8.
- If an architectural invariant is added, relaxed, or clarified — update both the relevant `DESIGN.md` section and the "Architectural invariants" list in this file.
- If a design-doc contradiction is discovered and resolved pragmatically (e.g., two sections disagree and we pick one) — record the resolution in `DESIGN.md` so future Claude doesn't re-litigate it.

Small typo fixes don't need ceremony. Substantive architectural changes still need explicit user agreement before editing (see "Things to ask the user before doing").

## Things to ask the user before doing

- Adding any new third-party AI vendor or SDK.
- Switching from `tool_use` to a different agent pattern (Agent SDK wrapper, Managed Agents, LangChain classic, LangGraph, Google ADK).
- Using the Supabase service role outside migrations and `pg_cron` (the Stripe webhook path in invariant #1 only activates if the §17 scaling plan is explicitly activated).
- Adding a new MCP tool that mutates data.
- Adding any new direct-write agent tool beyond `set_goal` (propose-then-confirm is the default for any user-visible row; the structural test enforces this).
- Putting any user content into a PostHog event.
- Starting any work on the §17 scaling punch-list (Stripe integration, infra upgrades, Privacy Policy / ToS, etc.) — the scaling decision must be made explicitly first; v1 does not need any of it.
- Starting any Swift / native-iOS work — the trigger criteria in DESIGN.md §10.4 must be met first, and the scaling phase must already be active.
- Adding any in-app purchase / App Store IAP path — invariant #11 says never.
- Editing `DESIGN.md` substantively (small typo fixes are fine; architectural changes need agreement).
