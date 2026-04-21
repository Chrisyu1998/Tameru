# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Tameru** is a mobile-first PWA for spending intelligence. Manual transaction entry + AI-assisted categorization + agentic chat over your spending data + an MCP server. Single-user today, multi-tenant from day one. The full design lives in `DESIGN.md` — read it before making non-trivial decisions. This file captures only the invariants future Claude instances will trip on if they don't know.

## Stack

React PWA (Vite + Tailwind + Zustand) · FastAPI (Python) · Supabase (Postgres + Auth + RLS) · Anthropic API (Messages + `tool_use`) · Google Gemini 3.1 Flash-Lite (`gemini-3.1-flash-lite-preview`, currently in preview) · Perplexity Sonar · Resend · PostHog · Sentry · Railway hosting · Postgres `pg_cron`.

The codebase is being scaffolded. If a tool, command, or directory you need doesn't exist yet, check `DESIGN.md` for the intended shape before inventing one.

## Architectural invariants (do not violate without explicit user approval)

These are load-bearing decisions from the design review. Each one was chosen over a tempting alternative for a specific reason — re-deriving the choice without context will produce the wrong answer.

1. **RLS is enforced via the user's JWT, not the service role.** FastAPI receives `Authorization: Bearer <user_jwt>` and instantiates a per-request Supabase client passing that JWT. Postgres enforces `auth.uid() = user_id` automatically. The `SUPABASE_SERVICE_ROLE_KEY` is reserved for **two callers only**: the `pg_cron` daily auto-logger (DB function, no app context) and Supabase CLI migrations. Application handlers never use the service role. If you find yourself reaching for it in a request handler, stop — you're about to bypass RLS.

2. **The Claude agent loop runs in FastAPI using the Messages API with `tool_use` blocks.** Not Claude Managed Agents (designed for long-running autonomous work in Anthropic's container — wrong fit for 4–6s chat turns and DB-backed tools). Not LangChain. Not the standalone Agent SDK as a wrapper. The loop is ~80 lines and lives in our process so the user's JWT is in scope when typed tools execute.

3. **MCP server is read-only and uses per-user bearer tokens.** Tokens are 32 random bytes, stored as `sha256` in `mcp_tokens`, returned to the user once. No `add_transaction` over MCP in v1 — leaked tokens read data; they cannot mutate it.

4. **Subscription auto-logger is a `pg_cron` SQL function, not a FastAPI background task.** Idempotency from `UNIQUE (subscription_id, date)` on `transactions` plus `INSERT ... ON CONFLICT DO NOTHING`. Concurrent runs prevented via `pg_try_advisory_lock`. `next_billing_date` advances only after successful insert in the same transaction.

5. **Single active device per user.** `users_meta.active_device_id` updated on sign-in; previous device's session is revoked. There is no offline multi-device conflict resolution because there can be no conflict.

6. **Schema changes go through Supabase CLI migrations checked into `supabase/migrations/`.** Never edit production schema via the dashboard SQL editor outside emergencies. RLS policies live in migration files alongside the tables they protect.

7. **No Expo. No native app. PWA only.** This was an explicit scope decision. iOS push limitations are accepted; the weekly digest is email-first via Resend.

8. **NL transaction parse fires on submit/blur, not on debounced keystrokes.** One Gemini call per transaction. Predictable cost. Do not add a debounced parse-as-you-type even if it feels like a UX win.

9. **The dashboard fits on one screen with no scrolling.** Adding a tile requires removing one. Historical analysis lives in the AI chat (generative charts), not in the dashboard. The entry-moment insight is the primary behavioral intervention — not the dashboard.

10. **Onboarding "demo" is a guided tour with hardcoded fixture data, not a live AI on fake data.** Static screens of real components rendered with fixtures. No fake-data layer in Supabase. No AI calls in the tour.

## Model usage by task

| Task | Provider/model | Notes |
|---|---|---|
| Categorization, NL parse, CSV parse, receipt vision | `gemini-3.1-flash-lite-preview` | Configurable via `GEMINI_MODEL` env var. Paid tier only. **Preview model** — fall back to `gemini-2.5-flash` (GA) if Flash-Lite preview becomes unstable. Do not use Gemini 3.1 Pro — it's a reasoning model, overkill and expensive for simple extraction. |
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

**Permanently out of scope:** net worth tracking, investments, credit score, budgeting forecasts, multi-currency, international cards.

**Deferred (do not build proactively):** Plaid/Teller.io auto-sync (Phase 3), transfer bonus digest, SUB wishlist alerts (Phase 2), spending limits (Phase 3), receipt photo (Phase 2), card recommender (Phase 2).

**Phase 1 only:** everything in §6 of `DESIGN.md` marked Phase 1.

## Commit message rules

- **Never include `Co-Authored-By: Claude` or any "generated by Claude" footer in commit messages.** The user has explicitly opted out of this attribution style. Write commit messages as if a human author wrote them. The same applies to PR descriptions.

## Things to ask the user before doing

- Adding any new third-party AI vendor or SDK.
- Switching from `tool_use` to a different agent pattern (Agent SDK wrapper, Managed Agents, LangChain).
- Using the Supabase service role outside migrations and `pg_cron`.
- Adding a new MCP tool that mutates data.
- Putting any user content into a PostHog event.
- Editing `DESIGN.md` substantively (small typo fixes are fine; architectural changes need agreement).
