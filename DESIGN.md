# Tameru — Design Document

**Spending Intelligence, Powered by AI**

| | |
|---|---|
| Author | Chris Yu |
| Date | April 2026 |
| Version | 3.1 (adds forward plan for scaling) |
| Status | Approved — implementation |
| Stack | React PWA · FastAPI · Supabase · Anthropic API (Messages + `tool_use` + `web_search`) · Gemini API · PostHog |
| Domain | tameru.xyz (registered 2026-05-23) |

This document supersedes PRD v2.1. Material changes from v2.1 are summarized in §0. v3.1 is a planning-only revision: **current scope is unchanged** — still ~10-user invite-only, free for everyone, no Stripe, no paid tier. v3.1 adds a **forward plan** describing what would need to happen *if* v1 is successful and scaling to ~100 users becomes the next step, plus a reconsidered mobile-strategy stance admitting Swift as a possible future migration. See §0.1 summary and §17 detail.

---

## 0. Changes from PRD v2.1

| Change | Reason |
|---|---|
| Drop Expo entirely; PWA only across all phases | Scope reduction. iOS web push limitations are accepted and disclosed. |
| Replace "Claude Managed Agents" framing with **Messages API + `tool_use`** via the `anthropic` Python SDK | Managed Agents runs in Anthropic's cloud and is designed for long-running autonomous tasks. Tameru chat turns are 4–6 seconds with typed DB-backed tools — wrong fit. The agent loop runs in FastAPI so the user's JWT is in scope when tools execute (RLS fires correctly). |
| Card multiplier lookup uses **Claude Haiku 4.5 + the `web_search` server tool** with an `allowed_domains` allowlist (NerdWallet, The Points Guy, US Credit Card Guide, Doctor of Credit, issuer domain) | Replaces the Perplexity Sonar path. Enforced source allowlist (Perplexity's was Pro-tier only), citations are first-class on the Anthropic Messages API, no new vendor/SDK/sub-processor. Cost is ~$0.01/lookup at $10/1k searches — negligible at ≤10 lookups per user lifetime. The DESIGN.md §16 "Perplexity JSON-mode reliability" open item is resolved by removing the vendor. |
| All Gemini calls use **`gemini-3.1-flash-lite-preview`** | Author decision to move off 2.5. Flash-Lite (not Pro) is the right variant — it's the direct successor to 2.5 Flash for "high-volume, cost-sensitive LLM traffic" per Google's own positioning, supports vision and grounding, and avoids paying for Pro's reasoning capacity on simple extraction tasks. Note: still in **preview** as of March 2026; see §16 Open Items for the stability risk. Model string is configurable via env var. |
| Replace live "demo mode" with a **4-screen guided tour** | Static screens with hardcoded fixture data. Eliminates the question "how does AI chat work on fake data," removes the risk of demo data leaking into Supabase, and is buildable in a day. |
| RLS is enforced by passing the user's JWT to a per-request Supabase client | Service role bypasses RLS. Per-request clients with the user JWT cause Postgres to enforce `auth.uid() = user_id` on every query. The service role is reserved for migrations and the daily auto-logger. |
| MCP server is **read-only**, authenticated via **OAuth 2.1** | Read-only eliminates the "leaked credential = data corruption" risk. OAuth 2.1 — delegated to Supabase Auth's OAuth 2.1 Server — is required because Claude.ai's web connector UI accepts no static bearer token or custom header; delegating to Supabase adds no new vendor. Supersedes the original per-user-bearer-token design — see §7.9. |
| Subscription auto-logger runs as **`pg_cron` SQL function**, not in the FastAPI process | Survives every API deploy. Idempotency via `UNIQUE (subscription_id, date)` constraint. |
| Multi-device: **prevent**, don't reconcile | Track active device on user row; second sign-in displaces the first. Avoids offline conflict resolution entirely. |
| `MerchantCategory` simplified — drop `correction_count`, keep `(merchant, category, updated_at)` | Most recent correction wins. Category itself is the signal Gemini needs. |
| NL transaction entry parses on **submit/blur**, not on debounce | Predictable cost (1 call per transaction, not N). |
| Dropped per-keystroke debounced parse |  See above. |
| Phase 1 AI cost revised from $0.70/month to **~$30/month total** with real token math, plus a **per-user daily Claude token cap** (default 200K/day) as a cost ceiling | Original estimate underweighted prompt size. Daily cap bounds worst-case per-user chat spend to ~$5/month. Real math is in §11. |
| Removed Phase 2 / Phase 3 cost scenarios | Tameru is committed invite-only. No growth projections or paid-tier pricing. |
| Aligned `Spending limits with AI nudges` to **Phase 2** in §6 (was Phase 3) | Resolves inconsistency with §15. Spending limits is post-launch optional, same tier as other nice-to-haves. |
| **Voice input for NL transaction entry promoted to Phase 1** | Implemented via Web Speech API (browser-native, free, on-device transcription). Audio never leaves the user's phone — privacy bonus. Downstream parse uses the same Gemini call as text entry. |
| Schema changes managed via **Supabase CLI migrations** in repo, not the dashboard | Version controlled, reproducible across environments, RLS policies travel with schema. |
| `AICallLog` retention: 90 days raw + daily aggregation table | Detailed for debugging recent calls; summarized for long-term trending. |
| **Sentry free tier** added for non-AI errors | AICallLog covers AI errors only. |
| AI provider data retention disclosed: **Anthropic ZDR requested + Gemini paid tier** | Privacy copy now matches reality. |
| PostHog drops "client-side question classifier" — only structural events tracked | Simpler, no PII surface, sufficient for funnel analysis. |
| SSE streams: **frontend reconnect button + Railway grace period** | Acknowledged failure mode for in-flight chat during deploys. |

---

## 0.1 Changes from v3.0

**v3.1 is a planning-only revision.** Nothing in the v1 build scope changes. v1 still launches to ~10 invite-only friends and family, free for everyone, with no Stripe and no paid tier.

| Change | Type | Reason |
|---|---|---|
| New **§17 — Forward plan for scaling to ~100 users** | Forward plan (not v1 scope) | Documents the operational punch-list (infra, compliance, billing, cost controls) that would need to be completed *if* v1 is successful and scaling to ~100 users is chosen later. Serves as future-self's reference so the decisions are not re-litigated under pressure. **Activation is conditional on an explicit decision to scale** — nothing in §17 is required for v1 launch. |
| Mobile strategy revised in §10: **PWA today, Swift native admitted as a possible future migration** | Stance change (affects forward plan only) | PWA still ships for v1 and stays the only surface at the ~10-user scale. Expo and Capacitor were re-evaluated and rejected — see §10.3. Swift migration is a conditional future path, triggered by real user demand (§10.4 criteria). |
| Schema sections §8.7 and §8.10 document forward-plan billing columns and the `stripe_events` idempotency table | Forward plan (not v1 schema) | The columns and table are **not part of the v1 migrations**. They are documented so the schema shape is pre-agreed; the migration ships only if/when the scaling decision is made. Day-1 `users_meta` does not include the Stripe columns. |
| New §11.6 — cost projection at 100 users | Forward plan | Shows the AI-cost shape at the scaled audience so the freemium-vs-paid economics are explicit before any scaling decision. Not a v1 cost estimate (see §11.3 for that — unchanged). |
| Open items (§16) expanded with: freemium gating rule, paid-tier price, entity registration timing, Swift migration thresholds, Anthropic rate-limit increase request | Forward plan | Each is a decision to close *before* scaling, not before v1 launch. |

Everything outside these additions is unchanged. If you are reading this to understand what v1 builds, read §3.3, §6, §11.3, and §15 Phase 1 — not §17.

---

## 1. Overview

Tameru is a mobile-first Progressive Web App that helps people become more aware and intentional about their spending. The thesis is simple: most people don't overspend because they lack a budget — they overspend because they lack awareness. Tameru closes that gap through fast manual entry, AI-assisted categorization, and a conversational interface that answers spending questions in plain English.

Tameru is not a wealth management tool. There are no investment dashboards, net worth trackers, or credit score monitors. The focus is singular: help users understand where their money is going, surface patterns they would not otherwise see, and nudge them toward more intentional decisions.

Credit card rewards optimization is included as a feature — not the identity — of the app. Users with multiple cards benefit from knowing which card to use per category and from seeing where they left rewards on the table. But this is a means to spending less net, not a separate product.

**Core value proposition:** You tell Tameru what you spent. It tells you what it means. AI helps you spend less on what doesn't matter — and more on what does.

### 1.1 Why Not Rocket Money or Mint?

Both rely on automatic bank sync, which creates two problems: miscategorization (the user spends more time fixing errors than gaining insight) and passivity (auto-pull removes the habit of awareness that is itself valuable). Tameru's positions:

- **Manual entry is intentional** — the 10-second friction of logging a purchase builds spending awareness over time.
- **Gemini suggests categories** — the user confirms in one tap.
- **The AI chat is the primary interface** — users ask questions in natural language instead of navigating dashboards.
- **Rewards-aware** — Tameru knows your specific cards, multipliers, and active offers. Rocket Money treats all spending identically.
- **Privacy-first** — financial data lives in your Supabase project; no third-party financial-data sharing beyond the AI API calls required for categorization and chat (see §9.4 for retention disclosure).

---

## 2. Competitive Positioning

Personal finance in 2026 splits into three buckets:

1. **General AI budgeting (auto-sync):** Monarch, Copilot, Cleo, YNAB, Rocket Money. Strong UX, polished dashboards, zero rewards awareness, stateless or no AI memory.
2. **Credit card rewards optimizers:** MaxRewards, CardPointers, Pointer AI, WalletFlo. Strong on multipliers and SUB tracking, zero spending intelligence, no AI chat.
3. **MCP-native finance (emerging):** Era. Architecturally forward-looking, no built-in agent, no rewards awareness, very early.

### Tameru's edges

- **Only app that connects rewards optimization to spending intelligence.** Budgeting apps treat all transactions identically; rewards apps don't model spending patterns. Tameru asks both questions at once.
- **Manual entry logs true cost.** A $200 group dinner reimbursed via Venmo is logged as $100 from the start — no after-the-fact transaction splitting.
- **Genuinely agentic AI.** Multi-step `tool_use` chains over your full transaction history with cross-session memory. Not a stateless chatbot.
- **Cross-session memory.** Distilled facts persist across conversations — Claude knows your CSR SUB target, your dining card preference, your seasonal spending shape.
- **MCP server.** Spending data is queryable from Claude.ai, Claude Code, or any MCP client.
- **Privacy posture as feature.** Financial data lives in your Supabase project. The only third-party egress is to AI providers, configured for no-retention/no-training.
- **Intentional entry framed as a feature, not an apology.**

### Honest gaps

- Auto-sync convenience: every competitor wins for users who don't buy into intentional entry. CSV import closes the cold-start gap but not the ongoing logging friction.
- Design polish: Copilot is the benchmark. Tameru must execute.
- NL entry alone is not differentiated — Rolly, MonAi, Cleo all do it. The combination (NL + rewards + agentic + memory) is.
- No distribution yet.

---

## 3. Goals & Non-Goals

### 3.1 Goals (v1)

- Manual transaction entry completable in **under 10 seconds**.
- Natural language entry — type "Spent $47 at Trader Joe's on Amex Gold" and Gemini parses all fields.
- Gemini suggests categories; user confirms in one tap. Learns from corrections per merchant.
- Auto-log recurring subscriptions on schedule.
- Surface insights at the right moment: weekly digest + entry-moment contextual nudges + minimal dashboard.
- Generative charts on demand.
- Conversational spending analysis (NL Q&A, retroactive rewards gap, what-if scenarios).
- Recommend the optimal card per purchase based on the user's wallet.
- Work offline — entries queue locally, sync on reconnect.
- Multi-user with full data isolation from day one.
- Portfolio quality: AI-native UX, agentic tool use, MCP, production-grade architecture.

### 3.2 Non-Goals (v1, permanent)

- Net worth, investments, credit score, budgeting forecasts.
- **Per-transaction multi-currency and FX conversion.** v1 supports a single home currency per user (chosen at signup, immutable — see §8.7, CLAUDE.md invariant 13). Transactions made abroad are logged as they will appear on the user's card statement in home currency. No per-transaction currency selector, no FX API, no card-statement reconciliation.
- International card support (non-US reward multipliers, non-USD statement currencies).

### 3.3 Scope limit — invite-only (v1)

Tameru v1 ships as an **invite-only project**. No public launch. No paid tier. No Stripe. No Plaid or Teller.io. Expected user count at v1 launch: **~10 close friends and family**, never more than a few dozen at this scale. Feature additions beyond Phase 1 are built only if the author wants them, not because growth demands them. Cost ceiling is low and bounded (§11.2 daily cap).

**Permanently excluded at every scale:**

- Plaid / Teller.io / any auto-sync that imports compliance scope Tameru doesn't want.
- Public launch, Product Hunt, paid acquisition.
- In-app purchases via App Store IAP — the design-doc stance is Stripe-on-web, even in the forward plan.

**Forward plan (not v1 scope):** if v1 is successful, a decision may be made later to scale to ~100 users via an open invite link, with a free + paid tier billed through Stripe on the web. That plan is documented in **§17**. It is not part of the v1 build. Activating it is an explicit decision, not an automatic progression.

### 3.4 Deferred (nice-to-have, not committed)

- Transfer bonus digest, SUB wishlist alerts.
- Card recommender, proactive insights, recurring detection, receipt photo via Gemini Vision, retroactive rewards gap analysis.
- **Merchant-merge cleanup job.** Users logging via chat will occasionally fragment merchants across spelling variants ("KFC" vs "Kentucky Fried Chicken"). v1 mitigates this on the write path — Claude's system prompt includes the user's top 30 merchants (Day 9 `render_user_merchants()`) and picks the existing canonical form when the user types or speaks a variant. This covers the chat-typed and voice-input paths well but does not cover CSV imports, first-time merchants, or cases where Claude guesses the wrong canonical. A nightly `pg_cron` job that runs Gemini over `merchant_category` row pairs, proposes merges ("'KFC' and 'Kentucky Fried Chicken' look like duplicates — merge?"), and surfaces them in a Settings screen for user confirm is the Phase 2 fix. Not built until real users at real scale produce enough fragmentation to bother them. Options (a) autocomplete on a free-form chat input and (b) deterministic server-side canonicalization via Gemini on every entry were both considered and rejected — (a) doesn't work because chat is free-form and voice has zero keystrokes; (b) is non-deterministic and adds latency to the confirm flow.
- **Expo / React Native** — evaluated and rejected as a migration path. See §10.
- **Native iOS via Swift** — admitted as a *possible* future migration if real user demand justifies the rewrite cost. Not on the roadmap. Trigger criteria is an open item (§16).
- Android — out of scope until an iOS surface exists.

---

## 4. Users

Target: someone with 2–5 credit cards who actively thinks about rewards, wants to reduce unnecessary spending, and is comfortable with a mobile-first tool that requires manual entry in exchange for better intelligence.

- 2–5 credit cards; optimizes which card per category.
- Spends $2,000–$6,000/month across dining, groceries, travel, subscriptions.
- Frustrated by Mint/Rocket Money miscategorization.
- Wants to ask "Am I spending more on dining than last month?" without building a spreadsheet.
- Privacy-conscious — does not want to connect bank accounts to a third-party service.

### 4.1 Multi-User Architecture

V1 begins with one user (Chris). The data model and auth are designed for multi-user from day one. Friends and family can sign up via Supabase Auth without cloning a repo or configuring keys. Every row carries `user_id`. Every Supabase query goes through the user's JWT, so Postgres enforces `auth.uid() = user_id` via RLS at the database layer (see §9.1).

---

## 5. Technical Architecture

### 5.1 Stack

| Layer | Technology | Rationale |
|---|---|---|
| Frontend | React + Vite (PWA) | Fast scaffold, large ecosystem, PWA support |
| Styling | Tailwind CSS | Mobile-first utility classes, minimal bundle |
| State | Zustand | Lightweight global state, no Redux boilerplate |
| Offline | Service Worker + IndexedDB | Queue transactions offline, sync on reconnect |
| Backend | FastAPI (Python) | Async, type-safe, Python for Gemini/Anthropic SDKs |
| Database | Supabase (Postgres) | Hosted Postgres + Auth + RLS + dashboard |
| Auth | Supabase Auth | Google OAuth + magic link; RLS enforced at DB |
| AI — high frequency | Gemini 3.1 Flash-Lite (preview) — `gemini-3.1-flash-lite-preview` | Categorization, NL parse, receipt extraction, CSV parse. Cost-optimized variant; matches 2.5 Flash quality per Google. |
| AI — card lookup | Claude Haiku 4.5 + `web_search_20250305` server tool | Web-grounded card multiplier lookup with citations and a domain allowlist (NerdWallet, TPG, US Credit Card Guide, Doctor of Credit, issuer domain). Replaces Perplexity Sonar; rationale in §6.1 and §0. |
| AI — agent | Claude Haiku 4.5 / Sonnet 4.6 | Tool use, multi-step reasoning, narrative |
| Agent runtime | `anthropic` Python SDK — Messages API with `tool_use` blocks. Loop runs in FastAPI. | Custom typed tools, JWT in scope for RLS, full control over middleware (logging, rate limit backoff, cost gating) |
| MCP | `mcp` Python SDK (Streamable HTTP transport) | Exposes spending data to Claude.ai / Claude Code / Claude Desktop. OAuth 2.1 Resource Server — see §7.9 |
| Streaming | FastAPI SSE + EventSource | Token-by-token Claude responses |
| Backend hosting | Railway | Persistent FastAPI service (SSE streams, 4–6s agent loops, `pg_cron`), GitHub-native CI/CD. ~$10/month. |
| Frontend hosting | Vercel | Static hosting + edge CDN for the Vite-built PWA. Free tier covers v1 and the §17 scaling plan. PR preview URLs for UI iteration. Talks to the Railway API cross-origin via CORS with Bearer-token auth (no cookies). |
| Cron | Postgres `pg_cron` (Supabase) | Daily subscription auto-logger; survives API deploys |
| Observability | `AICallLog` (Postgres) + Sentry (free tier) | AI calls in `AICallLog`; non-AI exceptions in Sentry |
| Product analytics | PostHog | Structural events only — no question text, no financial data |
| Email | Resend | Weekly digest |

### 5.2 Why Supabase over SQLite

Multi-user write concurrency, RLS at the DB layer, built-in auth, hosted backups, and the dashboard all come for free. Migration cost from SQLite later would be high. Free tier handles Phase 1 and Phase 2 entirely.

### 5.3 Hosting split: Railway for backend, Vercel for frontend

**Backend on Railway, not Vercel.** FastAPI on Vercel runs as serverless functions, which breaks three things:

- Persistent processes — needed for SSE streaming.
- Cold starts — make token-by-token streaming unreliable.
- Long-running agent loops — agent loops can take 4–6 seconds; serverless timeouts are aggressive.

Railway runs FastAPI as a persistent service from a Dockerfile. ~$10/month. CI/CD from GitHub.

**Frontend on Vercel, not co-located on Railway.** The Vite-built PWA is static assets; Vercel's edge CDN gives materially better first-paint for global users (the v1 user base includes people outside the US), and Vercel's Git-native deploys provide per-PR preview URLs useful while iterating on the web surface. Co-locating the frontend on Railway would lose both and save effectively nothing — Vercel's free tier covers v1 and the §17 scaling target. A PWA shell is a natural CDN workload: after first load the Service Worker serves it locally anyway, so the edge wins first-paint without adding privacy surface.

**How the two origins talk.** Frontend at `https://tameru.xyz` (Vercel); API at `https://api.tameru.xyz` (Railway). FastAPI's `CORSMiddleware` explicitly allowlists the frontend origin (§9.3). Authentication is Bearer token in the `Authorization` header — never cookies — so `allow_credentials=False` and there is no SameSite or third-party-cookie complexity. This is the same shape a future Swift iOS client expects (one API host, multiple clients, no frontend-specific branching — invariant 12), which means the web/iOS split in §10 reuses this boundary rather than re-litigating it.

**Reversibility.** The frontend is a static `dist/` directory; moving it off Vercel to Cloudflare Pages, Netlify, or Railway static hosting is under an hour of work and changes nothing on the backend. This keeps the hosting-split decision cheap to revisit.

### 5.4 Onboarding Philosophy

Most people downloading a spending app in 2026 expect automatic bank sync. If the first experience is "manually type every transaction," a large percentage churn before seeing value. Tameru solves this with two mechanisms: a philosophy screen that sells intentional entry upfront, and CSV import that provides instant history on day one — without Plaid.

#### 5.4.1 Philosophy Screen (first launch)

A full-screen explanation of why Tameru is intentionally manual. Not a feature disclaimer; a product pitch.

> Most spending apps sync your bank automatically. Tameru doesn't — on purpose.
>
> The act of logging a purchase, even for 10 seconds, is what builds awareness. Mint synced everything automatically. People still overspent, because automatic means invisible.
>
> Tameru asks you to log what you spend. The AI handles the rest — categorization, patterns, questions, nudges. You bring the data. Tameru brings the intelligence.
>
> If that sounds like the right trade, let's get started.

CTAs: **Get Started** (primary) and **Take the Tour** (secondary). Shown once on first launch; never again.

#### 5.4.2 Guided Tour (replaces live demo mode)

Four static screens with hardcoded fixture data, illustrating the dashboard, the entry-moment nudge, the AI chat, and the weekly digest. No backend calls. No fake-data layer in the database. No risk of demo data leaking into the user's account.

- **Screen 1 — Dashboard:** static render of the minimal dashboard with example numbers ("Dining: +$47 above average").
- **Screen 2 — Entry-moment nudge:** short animation showing a transaction being logged + the one-sentence insight that follows.
- **Screen 3 — AI chat:** pre-recorded transcript bubble showing one question and one answer.
- **Screen 4 — Weekly digest:** image of an example email.

Final CTA: "This is Tameru with 3 months of data. Log your first transaction or import a CSV to get there."

The tour is built using real Tameru components rendered with fixture data — they look real because they are real. No fake AI calls. Zero ongoing maintenance.

#### 5.4.3 CSV Bank Import

Every major US bank exports CSVs. CSV import gives new users instant history without Plaid integration, compliance overhead, or per-call costs.

- User downloads CSV from their bank. Tameru shows per-bank instructions (Chase, Amex, Citi, BofA, Capital One, Wells Fargo).
- User uploads. Gemini 3.1 Flash-Lite parses the header row, identifies date/merchant/amount columns, previews the first 5 rows for confirmation.
- User selects which card these transactions belong to.
- Gemini batch-categorizes in groups of 100. Progress indicator: "Categorizing 143 transactions…".
- Result: "143 transactions imported." Dashboard and AI chat are immediately usable.

**Upload caps (v1).** Files > 5 MB or > 5,000 rows are rejected with 413 at the route boundary, *before* parsing — closes the §17.5 "malicious 100MB upload pins a FastAPI worker" surface (line 1520). 5,000 rows covers ~2.5 years of a typical US bank export with headroom; easier to relax than tighten if real users hit it.

**Skip rules.** Two row classes are skipped rather than inserted, counted separately from duplicates, and surfaced in the final SSE event so the user understands the row-count delta:

- *Negative amounts (returns / credits).* Gross-spending breakdown over-counts for users who return things — acceptable at v1's invite-only scale. Refund handling (net-of-returns reporting) is Phase 2.
- *Foreign-currency rows.* When the detected schema includes a currency column and a row's currency code ≠ `users_meta.home_currency`, skip. Aligns with invariant 13 (single immutable home currency, no FX). When no currency column exists, trust the row is in home currency — every US bank export from the supported six is.

**No recurring detection on import.** A merchant appearing 12 times in the CSV lands as 12 one-off `csv_import` transactions, not a single subscription with backfilled charges. Recurring detection is Phase 2 (§6 line 289). This matches the YNAB / Copilot / Monarch posture surfaced in the Day 19 forward-only research.

**Idempotent re-run as the recovery path.** Disconnected mid-stream uploads recover by re-uploading the same file: the dedup quadruple `(user_id, date, normalize_merchant(merchant), amount)` queried against `active_transactions` (so a deleted row does not shadow a re-import) catches every already-committed row, and the final SSE event reports "0 inserted, N duplicates skipped." No partial-import-status table, no resume cursor — the dedup quadruple is the resume key. Re-categorizing the already-imported slice burns negligible Gemini cost (§11.3 amortizes one full import at $0.01–$0.10).

Edge cases: duplicate detection on `(user_id, date, normalize_merchant(merchant), amount)`; unknown column schema (Gemini confidence < 0.8) → manual mapping UI. CSV inserts use `source = 'csv_import'` (§8.2) and `client_request_id = NULL` (§8.2 line 834 — idempotency comes from the dedup quadruple, not crid). Each Gemini call (`detect_columns`, `categorize_batch`) writes one `ai_call_log` row with `task_type = 'csv_import'` (§8.8) under the caller's JWT (invariant 14). CSV import is also available post-onboarding under Settings → Import Data.

---

## 6. Features

| Feature | Phase | Priority |
|---|---|---|
| Philosophy screen + guided tour | 1 | P0 |
| CSV bank import (Gemini batch categorization) | 1 | P0 |
| Card management (Claude `web_search` multiplier lookup) | 1 | P0 |
| Natural language transaction entry | 1 | P0 |
| Gemini categorization with one-tap confirm | 1 | P0 |
| Merchant memory (per-user category overrides) | 1 | P0 |
| Subscription manager (auto-logged via `pg_cron`) | 1 | P0 |
| Spending dashboard (minimal, single screen) | 1 | P1 |
| Entry-moment insight | 1 | P0 |
| Claude agentic chat (Messages API + `tool_use`) | 1 | P0 |
| Streaming responses (SSE) | 1 | P1 |
| Cross-session memory | 1 | P1 |
| Eval harness (categorization + NL parse) | 1 | P1 |
| MCP server (per-user tokens, read-only) | 1 | P1 |
| LLM observability (`AICallLog`) + Sentry | 1 | P1 |
| Multi-user auth + RLS via per-request JWT | 1 | P0 |
| Baseline comparisons (3-month rolling) | 1 | P0 |
| Weekly email digest | 1 | P1 |
| PostHog (structural events only) | 1 | P1 |
| Voice input for NL transaction entry (Web Speech API) | 1 | P1 |
| Card recommender (best card per category) | 2 | P1 |
| Recurring detection (suggest as subscription) | 2 | P2 |
| Proactive insights | 2 | P2 |
| Receipt photo → transaction (Gemini Vision) | 2 | P2 |
| Rewards gap analysis (retroactive) | 2 | P2 |
| Transfer bonus digest, SUB wishlist alerts | 2 | P2 |
| Spending limits with AI nudges | 2 | P2 |
| Plaid / Teller.io auto-sync | — | excluded (§3.3) |

### 6.1 Card Management — Claude `web_search` Lookup

Users add cards by name + network + last 4. Claude Haiku 4.5, invoked with the `web_search_20250305` server tool and an `allowed_domains` allowlist, fetches category multipliers and rewards program in one API call with citations.

> **US-only in v1.** Networks, issuers, reward programs (`UR/MR/TYP/Bilt`), and source domains are all US-centric. International card lookup (Japan, Taiwan) is deferred — see §6.6 and `TODO.md`. Non-US users add cards manually (the fallback path below) without automated multiplier lookup.

**Add card flow:**

1. User enters card name (e.g., "Chase Sapphire Reserve") + network (Visa) + last 4 digits (e.g., 1234). The last 4 is what disambiguates two cards of the same product (a user can have two Amex Platinums on the same account).
2. Backend calls Claude Haiku with `web_search_20250305` enabled, `allowed_domains = ["nerdwallet.com", "thepointsguy.com", "uscreditcardguide.com", "doctorofcredit.com", <inferred issuer domain>]`, and `max_uses = 3`. System prompt: "Extract `program`, `multipliers`, `annual_fee`, `issuer` for the named credit card. Return strict JSON. If sources disagree or data isn't found, set the missing field to null."
3. Claude executes 1–2 web searches against the allowlisted sources, returns structured JSON plus `web_search_result_location` citation blocks (url, title, cited_text).
4. Backend parses the JSON, collects citation URLs, and returns a proposal payload. User reviews, edits any incorrect values, confirms. Card saved to `cards` with citation URLs preserved as `cards.source_urls`.

**Why Claude `web_search` instead of Perplexity Sonar:**

- **Enforced domain allowlist.** Claude's `allowed_domains` parameter strictly restricts citation sources at the API layer. Perplexity Sonar's `search_domain_filter` is Pro-tier only and historically had reliability gaps (citations slipping outside the allowlist).
- **No new vendor.** Tameru already depends on the Anthropic SDK and key for the chat agent. The web_search server tool is an Anthropic-side feature on the same Messages API — one fewer key, SDK, sub-processor, and Privacy Policy entry.
- **Citations are first-class.** Each `web_search_result_location` carries `url`, `title`, and up to 150 chars of `cited_text` — richer than Perplexity's `{url, title}` and drops directly into `cards.source_urls text[]`.
- **Cost is negligible.** ~$0.01/lookup ($10 per 1,000 searches + ~$0.003 in Haiku tokens). At ≤10 lookups per user lifetime, this is rounding error against any other line item.
- **Closes the §16 open item.** "Perplexity Sonar JSON-mode reliability for card lookup" is resolved by no longer using Perplexity.

**Cost-of-decision tradeoffs:**

- The web_search call runs on the same Anthropic key as chat; outages in Anthropic affect both. Mitigation: the existing chat-down fallback messaging applies (§17.9), and the manual-entry path below covers the card-add user surface.
- The web_search tool requires the org admin to enable it in the Claude Console (Privacy settings). One-time setup; documented in §16 and the runbook.

**Fallback path:** if web_search returns `web_search_tool_result_error` (`max_uses_exceeded`, `too_many_requests`, `unavailable`) or extraction confidence is low (key fields null), the proposal payload sets `needs_manual: true`. The UI renders an editable blank form pre-populated with name + network + last_4 so the user types multipliers themselves with a category picker.

### 6.2 Spending Tracker

Manual entry is intentional. The 10-second friction of logging a purchase builds awareness that is itself valuable. All user-initiated writes — transactions, cards, subscriptions — go through a **single chat surface** (UX_PROMPT.md, frames 12–15). There is no separate `+`-button entry form in v1; see CLAUDE.md invariant 8.

**Entry flow (chat-based):**

1. User taps the center chat button in the bottom nav.
2. User types or speaks: "spent $47 at Trader Joe's on my Amex Gold."
3. Claude Haiku receives the message and calls `propose_transaction(...)` via `tool_use`, filling merchant / amount / date / card_name from its own read of the message (not Gemini — see §7.4).
4. The tool implementation resolves `card_name → card_id` from the user's cards, calls `categorize()` (Gemini Flash-Lite — §7.4) for a category suggestion, generates a fresh `client_request_id` (UUIDv4), and returns a `TransactionProposal` payload. **No DB write happens inside the tool.**
5. The React client renders the proposal as an inline parse card in the chat (UX frame 15): merchant, amount, date, card, category. The card is display-only — the **only edit affordance is the "let me fix it" button**, which opens the same `EditTransactionSheet` used elsewhere in the app for ledger rows. Edits in the sheet write back to the chat-store draft (Zustand `messages[].draft`), which is the single source of truth for the proposal. There is no inline pencil affordance on the card itself; the third channel is natural language (the "or just tell me what to change." hint below the buttons). The `client_request_id` is carried opaquely by the client.
6. User taps "looks right" → client calls `POST /transactions/confirm` with the proposal (including the `client_request_id`). The server validates, inserts, and fires the Entry-Moment Insight (below) in the same response.
7. If offline, the confirm call queues in IndexedDB (keyed by `client_request_id`) and syncs on reconnect. A partial unique index on `(user_id, client_request_id)` — see §8.2 — makes duplicate replay a no-op rather than a duplicate row; the server returns the existing transaction with `insight: null` on replay.

This **propose-then-confirm** shape — tool returns proposal, UI commits — is the invariant mutation pattern for all user-facing writes. The same flow applies to `propose_card` and `propose_subscription` (each with its own `client_request_id`). It matches the Intent Preview pattern from agentic-UX design and guarantees no row is written without explicit UI confirmation.

**Currency:** amounts are entered in the user's home currency — a single currency chosen at signup and immutable thereafter (§8.7, CLAUDE.md invariant 13). For foreign purchases, users enter the amount their card statement will show in home currency; v1 does not fetch FX rates or convert on the user's behalf. Per-transaction multi-currency is explicitly out of scope (§3.2).

#### Dashboard — design philosophy

Dashboards with many charts create a false sense of progress without changing behavior. A user who sees their dining spend is 50% of their budget does not eat out less. Tameru's dashboard is deliberately minimal — it surfaces one number that matters, not a gallery of charts to explore.

**Default view (must fit on one screen, no scrolling):**

- One headline number: this month's total vs. 3-month baseline. Color-coded.
- 4–5 category tiles showing **delta vs. baseline only** — "Dining: +$47 above average," not "Dining: $327." The delta is the signal.
- One AI prompt at the bottom: "Ask me about your spending."
- No 6-month bar chart. No toggles. No drilling by default. Historical analysis lives in the AI chat.

**Pie chart (secondary view):** accessible via "Breakdown" tab or tap on any category tile. Recharts (SVG only). The Breakdown page also renders a "this month vs your budgets" progress strip below the donut for any month-scoped goals on the `goals` table (§8.13); when the user has no month-scoped goals, the strip is absent. Editing budgets lives on the dedicated `/goals` page; only the at-a-glance progress visualization surfaces here, so the dashboard stays clean of always-on budget chrome (invariant #9 — one screen, no scrolling).

#### Entry-Moment Insight — primary behavioral intervention

Immediately after a transaction is confirmed, `POST /transactions/confirm` returns at most **one** contextual sentence, rendered as a bubble in the chat thread directly below the committed parse card (`EntryInsightBubble`). It is not a toast and not a modal: it stays in the thread for the rest of the session, with no dismiss affordance — rate limits, enforced server-side in `entry_moment_fires`, are what prevent fatigue. The insight is **ephemeral by design** — it is not written to `chat_messages`, so it does not reappear when the conversation is rehydrated on a later app open. The durable home for an ongoing overspending signal is the dashboard category tile (§6.3) and the weekly digest (§6.4).

A deterministic rule engine (`app/services/entry_moment.py`) evaluates four rules and returns the first that fires:

1. **single_tx_notable** — a new monthly high in a category.
2. **weekly_frequency** — an elevated weekly count vs. the 4-week average.
3. **cumulative_delta** — category spend pulling above the baseline (pace-aware; see below).
4. **card_mismatch** — wrong-card usage with a better-earning option.

**Severity tiers.** Each fired insight carries a `severity` that drives a tiered visual treatment mirroring the §6.3 baseline color scale:

- `calm` — a quiet grey italic aside. Rules 1, 2, and 4.
- `elevated` — amber, with a leading glyph. Rule 3 only, when spend tracks 10–25% over the category baseline.
- `alert` — terracotta, with a warning glyph. Rule 3 only, 25%+ over baseline.

An `alert`-tier rule 3 **outranks the calm rules** — a "you're on pace to overspend" message is not suppressed by a "biggest dinner this month" observation. Below the alert band, rule 3 keeps its original priority-3 slot.

**Rule 3 is pace-aware.** From the 5th of the month onward it straight-lines month-to-date spend to a month-end projection — *"on pace for about $180 over your monthly dining average."* In the first few days, where a projection from 2–3 days of data is noise, it falls back to a retrospective framing — *"this puts you $23 above your monthly dining average with 12 days left."*

Examples by tier:

- calm: *"4th dining transaction this week — you usually have 2."*
- alert: *"on pace for about $180 over your monthly dining average."*

Rules: one sentence; at most two numbers, always framed as a delta vs. the baseline — never a bare absolute, never a judgment ("you spend a lot here" is the wrong tone). No buttons (a severity glyph is not an action affordance). Not shown when there's nothing meaningful to surface — the first transaction in a category, or a within-noise delta.

#### AI Chat — power-user escape valve

Anything not on the dashboard lives in the chat. Generative charts, trend analysis, what-if scenarios, all on demand.

#### Transaction list UX — edit / delete surface

Chat is sufficient for high-confidence mutations ("delete my last transaction" → one turn). It is **not** sufficient for ambiguous retrieval ("change that $10 coffee from two weeks ago" when there are ten coffees in that window) — the resulting disambiguation turns are a known failure mode of chat-first design (2026 consensus; see §7.7 for how chat still contributes via inline candidate lists). v1 therefore ships a dedicated per-category transaction list reached from the Breakdown drill, and the chat surface hands off to the edit sheet (UX frame 11b) for all mutations.

**Entry points:**

- Tap a category in Breakdown → expand to show 3 most recent (UX frame 11) → tap "see all <category>" → per-category transaction list (UX frame 11a).
- In chat, when the user expresses a delete or update intent, Claude calls `get_transactions(...)` with narrow filters:
  - **v1 UX (ship this)** — the result is rendered as tappable candidate cards inline regardless of count. Tapping a card opens the edit sheet (UX frame 11b), where the user taps Save or Delete. A single-row result is a one-card list the user taps once to reach the sheet.
  - **Zero matches** → Claude asks a clarifying question in prose; no card is rendered.

The agent is always read-only on the ledger: `get_transactions(...)` retrieves, never mutates. The HTTP `PATCH` / `DELETE` call is always the user's explicit tap on the edit sheet — not a `tool_use` side-effect (CLAUDE.md invariant 8).

**Post-launch enhancement (not v1 scope).** When `get_transactions(...)` returns exactly one match and the user's prior turn expressed a clear delete/update intent, a future version renders an inline confirm card in the chat ("Delete Costco $47 on 4/22? [Confirm] [Cancel]") — collapsing the candidate-tap → edit-sheet-tap to a single tap. Implementation requires two additional agent tools (`propose_delete_transaction`, `propose_update_transaction`) that return delete/update proposal payloads without mutating, plus a MutationConfirmCard component. Neither the tools nor the component are load-bearing for v1; the candidate-card → edit-sheet path satisfies the functional requirement (one-turn delete in chat-model terms, two taps in UI terms). Add the enhancement after v1 launch if real users flag the extra tap.

**List surface (UX frame 11a):**

- Default filter: current month. Filter chips: month selector (`apr 2026` / `mar 2026` / `last 90d` / `all time`), card.
- Search bar (substring match on merchant).
- Infinite scroll, 50 rows at a time.
- Tap row → edit sheet (UX frame 11b).
- Swipe-left on a row → inline delete confirm.

**Edit sheet (UX frame 11b):**

- Same 5-field layout as the chat parse card (merchant / amount / date / card / category). Each field editable.
- "Save" (accent, disabled until a change is made) · "Delete" (terracotta text) · "Cancel" (secondary).
- Delete from the edit sheet goes through the same confirm dialog as swipe-delete.

Bulk operations ("delete all my Starbucks from last month") are **out of scope for v1** — user can delete one-by-one in the list. Adding bulk delete is a Phase 2 call if real users ask for it.

### 6.3 Baseline Comparisons

Every dashboard metric is shown relative to the user's own personal baseline.

- Window: trailing 3-month average, computed per category.
- Color-coded delta: green (below), neutral (within 10%), amber (10–25% above), red (25%+ above).
- AI chat uses the same baseline automatically.
- New users (<3 months of data): "Baselines will appear after 3 months of data. Keep logging!"

### 6.4 Weekly Digest

The primary delivery mechanism for spending insight. Reaches users who don't open the app.

**Delivery:** Email via Resend, sent **Monday morning in each user's own timezone** (`users_meta.timezone`, §6.6/§8.7) by a Railway scheduled service running `python -m app.cron.digest`. The cron fires **hourly** (`0 * * * *`) and each run sends to users for whom it is currently Monday in the **[09:00, 12:00) local hours** of their zone; users with no zone set fall back to America/New_York (the pre-Day-29 behavior). The three-hour window is a **retry budget**: a failed 09:00 send releases its reservation slot so the 10:00 fire re-attempts, and the UTC-week unique index makes every fire after the first success a no-op — so the user gets the digest at the first hour the send succeeds, exactly once, or misses the week only if the outage lasts past noon local (the documented bounded false-negative). Running hourly + gating on local time is DST-correct for free (ET 09:00 is 13:00 or 14:00 UTC by season). The week summarized is computed Mon–Sun in the user's zone too, so a Sunday-night purchase lands in the week the user experienced it. From `"Tameru" <hello@mail.tameru.xyz>`; Reply-To `hello@mail.tameru.xyz` (aliased to a real inbox — users hitting reply must reach a human).

**Content (≤5 content blocks; one block = one sentence or one data line):**

- Total spend last week vs. trailing 8-week average (the just-ended week excluded from the baseline so the comparison isn't self-referential).
- Top category by sum and whether above or below its own 8-week baseline.
- One AI-generated observation (Sonnet, ≤100 chars, matter-of-fact).
- One nudge if applicable (rewards or category-related, ≤100 chars, optional — Sonnet may return `null`).

If it takes more than 15 seconds to read, it's too long. Brevity is the feature.

**AI provider boundary:** Claude Sonnet 4.6 receives **aggregates only** — category totals, week-over-week deltas. Never merchant names or raw transaction rows. Anthropic has ZDR, but minimum-surface is the privacy posture (CLAUDE.md). The Sonnet call is logged to `ai_call_log` with `task_type='digest'` per CLAUDE.md invariant 14 (service-role write since the cron has no user JWT in scope).

**Eligibility:** an active user is one with (a) a confirmed email, (b) not soft-deleted, (c) `users_meta.weekly_digest_enabled = true`, and (d) at least one `active` transaction in the past 4 weeks (zombie-send filter — silent users at v1 scale are usually inactive accounts, not waiting for nudges). All four predicates live in one SQL query, not a Python loop.

**Idempotency — two layers:**

*Layer 1 — reserve-then-send (DB).* The cron writes an `email_log` row with `success=true` BEFORE calling Resend (via the `email_log_insert_idempotent` RPC against the partial unique index `email_log_dedup_week_uniq` on `(user_id, kind, dedup_week) WHERE success AND dedup_week IS NOT NULL`, where `dedup_week` is the recipient's **local Monday date**). If the insert conflicts, the slot is already taken — skip without composing or sending. If the insert succeeds, send; on Resend rejection flip the reserved row's `success=false` to release the slot for a same-week retry; on success stamp `provider_message_id` so the webhook can look it up. **Failures AFTER Resend accepts the message must NOT release the slot** — releasing would let the next cron run re-send a message the recipient already has. Reserving before sending is what closes the cross-process race; the post-send-no-release rule closes the same-process post-send-failure race. This same release/no-release split is what makes the Monday 09:00–noon **retry budget** (§6.6) work: a failed pre-send or Resend-rejected attempt frees the slot so the next hourly fire retries, while a post-send-ambiguous attempt holds it so a possibly-delivered email isn't resent. The key is the **local** Monday date (not the UTC week): a user is *attempted* up to three times on their Monday morning but *reserved successfully* at most once (the first success blocks the rest via `ON CONFLICT`). Keying on the local Monday — rather than `date_trunc('week', sent_at, UTC)` — is what makes this correct for zones east of UTC+9 (e.g. `Australia/Sydney`, where the 09:00 fire is Sunday UTC and would otherwise get a different UTC-week key than the 10:00/11:00 retries → a double-send). The cron computes the date in Python because a per-row timezone can't live in an IMMUTABLE index expression (memory.md 2026-05-25). See §8.14.

*Layer 2 — `Idempotency-Key` header (provider).* Every `POST /emails` to Resend carries a `digest:{user_id}:{week_start_date}` value in the `Idempotency-Key` header. Resend dedupes by this key for ~24h on their side: if the SDK's underlying `urllib3` retries the POST after a transient TCP error (we don't control its retry policy, and it doesn't coordinate with our DB lock), Resend returns the cached response instead of creating a second send. Closes the in-flight-retry vector the partial unique index can't see.

The tradeoff between the two layers is the false-negative window: a worker crash between reserve and send (Layer 1) means the user gets no digest this week — a documented bounded loss at v1 scale, preferable to duplicates that would generate spam complaints. A stale-reservation reaper is the path if this ever happens in practice.

**Opt-out — three paths, one boolean:**

1. **Settings toggle** — Settings → Notifications "Weekly digest email"; PATCH `/me/preferences` flips `users_meta.weekly_digest_enabled` under the user's JWT (owner-UPDATE RLS).
2. **One-click List-Unsubscribe** per RFC 8058 — every email carries `List-Unsubscribe: <https-url>, <mailto>` + `List-Unsubscribe-Post: List-Unsubscribe=One-Click`. The URL is an HMAC-tokenized `GET /unsubscribe?user=…&kind=digest&token=…` (signed with `DIGEST_UNSUBSCRIBE_SECRET`, no expiry — a year-old link still works) that flips the same boolean via service role, no login required. Also a **visible** Unsubscribe link in the email body (defense in depth — header alone is insufficient; that's what users actually find). Gmail's ≥5K/day threshold for *required* one-click doesn't apply at v1 scale, but ship it anyway — inbox placement benefits and retrofit-after-reputation-damage is much harder than ship-it-now.
3. **Resend webhook** — `POST /webhooks/resend` (Svix-signed against `RESEND_WEBHOOK_SECRET`). `email.bounced` with `bounce.type='hard'` or any `email.complained` flips `weekly_digest_enabled=false` for the affected user (looked up by `provider_message_id` on `email_log`) and records `bounce_type` on the matching row. Soft bounces and `email.delivery_delayed` are not surfaced — Resend retries internally.

All three paths converge on the same `weekly_digest_enabled` boolean which the cron predicate reads — the toggle is the single authoritative gate.

**Deliverability prerequisites (one-time ops, not code):** Tameru owns the sending domain `tameru.xyz` (registered 2026-05-23) and configures SPF + DKIM + DMARC records at the registrar per Resend's setup. Send from the **subdomain** `mail.tameru.xyz` so a deliverability incident on the digest doesn't contaminate the root. Start DMARC at `p=none`; move to `p=quarantine` after one month of clean reports. Resend's open and click tracking are **disabled** in project settings — open-pixel exfiltrates recipient IP to a third party on every email open; click tracking rewrites every link through `resend.com`. Both violate the privacy posture.

**Email shape:** both HTML (inline styles only — Gmail strips `<style>` blocks and class names) and plaintext, generated together. Plaintext is required for deliverability; spam filters score HTML/text similarity.

**"View this week in Tameru" CTA (Day 26b).** The email includes a button-styled link to `${FRONTEND_ORIGIN}/?source=digest` (the Vercel PWA host, NOT `BACKEND_PUBLIC_URL` — `/` is the SPA root, not a FastAPI route). The PWA fires the `weekly_digest_opened` PostHog event (§9.5 whitelist) on landing when the query param is present, then strips it via `history.replaceState`. No open-tracking pixel, no Resend webhook, no per-user token in the URL (the event carries no identity claim — `weekly_digest_opened` props are `Record<string, never>`). The CTA button is a navigation affordance, NOT one of the ≤5 prose content blocks — the prose ceiling still applies to the region above it.

**Cost:** ~$0.07/user/month for the Sonnet call × 4 sends/month (§11). At v1's ~10 users this is ~$0.70/month total.

### 6.5 Subscription Manager

Subscriptions are auto-logged on their billing schedule by a `pg_cron` job — fully isolated from the API service.

**User flow:**

1. User adds subscription via chat: name, amount, frequency (monthly/quarterly/annual/weekly), start_date, category, **optional** card (omit for bank-ACH bills like rent or utilities — §8.3 `card_id` is nullable). `propose_subscription` returns a proposal; `POST /subscriptions/confirm` commits.
2. Backend computes `next_billing_date` at confirm time using the **forward-only rule** (§8.3): if `start_date <= today`, `next_billing_date = today + 1 period`; if `start_date > today`, `next_billing_date = start_date`. Past billing cycles are never backfilled — manual transaction entry covers historical charges.
3. `pg_cron` runs daily; SQL function inserts a Transaction for any active subscription with `next_billing_date <= today` and advances `next_billing_date` by one period. Cardless subscriptions auto-log with `transactions.card_id = NULL` (already supported — §8.2). Auto-logged transactions use `source = 'auto_logged'`.
4. User can pause (stop temporarily), resume, edit (`amount`, `category`, `name`, `card_id` — but never `frequency` or `start_date`; §8.3 immutability rule), or cancel (stop permanently).
5. When the card backing a subscription is soft-deleted, regular subscriptions flip to `paused` with a "needs new card" banner on `/subscriptions`; card annual-fee subscriptions flip to `cancelled` (§8.3 split-cascade rule). Pg_cron skips paused rows, so nothing auto-logs while the user reassigns.

**Idempotency:**

- **Auto-log path (pg_cron):** partial unique index `transactions (subscription_id, date) WHERE status = 'active' AND subscription_id IS NOT NULL` (§8.2). Insert uses `ON CONFLICT (subscription_id, date) WHERE status = 'active' AND subscription_id IS NOT NULL DO NOTHING` — the predicate must match the partial index. `next_billing_date` advances only after a successful insert, in the same transaction. The cron function wraps execution in `pg_try_advisory_lock` to prevent concurrent runs.
- **Chat-confirm path (user-initiated):** `subscriptions.client_request_id` + partial unique index `(user_id, client_request_id) WHERE client_request_id IS NOT NULL`. Same shape as `transactions.client_request_id` (§8.2) and `cards.client_request_id` (§8.1). A replayed `POST /subscriptions/confirm` (e.g. the Day 15 offline queue drains after a lost response) returns the existing row instead of creating a duplicate. Without this, a duplicated subscription would be independently auto-logged each month by the cron above — the dup cost would compound until the user noticed. Subscriptions can't lean on a natural-key index the way cards do (the active-identity uniqueness `(user_id, issuer, last_four)` from §8.1 has no subscriptions analog — family vs. personal Netflix on the same card with the same frequency are both valid), so crid is the *only* dedup defense for the chat-confirm path. Cards run with both: the natural-key 409 enforces "no two physical cards alike," and the crid same-replay shortcut returns the prior row on a network retry.

**Card annual fees participate in this auto-log path.** A companion `subscriptions` row is created by `POST /cards/confirm` when the user supplies an annual-fee renewal date (Day 19b). The row has `frequency = 'annual'`, `category = 'Memberships'`, and a `name` of the form `"{card_name} annual fee"` — pg_cron auto-logs the AF to the ledger on each anniversary. Card soft-delete cancels (not pauses) AF subscriptions per the §8.3 split-cascade rule.

**The AF dual-write at confirm time is atomic** via the `insert_card_with_af(p_card jsonb, p_af jsonb)` SECURITY DEFINER RPC (Day 19b, same pattern as `soft_delete_card`). Both inserts commit or neither does — a failure on the subscription insert rolls back the card insert as well, so the user never lands in the "card created but AF tracking silently missing" state that a best-effort double-write from Python would produce. RLS posture is unchanged: every WHERE clause inside the function filters by `auth.uid()`, so SECURITY DEFINER doesn't widen access (same security model documented for `soft_delete_card`).

**AF rows are hidden from the user-facing subscriptions list by default.** Conceptually, an annual fee is a card consequence — the user can't reassign it to a different card or cancel it independently of cancelling the card itself — so surfacing it next to Netflix and rent on `/subscriptions` would be misleading. `GET /subscriptions` therefore accepts an `include_card_af` query param defaulting to `false`; the `/subscriptions` page calls without it (AF rows are filtered out), while the cards-list AF chip (Day 19b) passes `include_card_af=true` to surface them only where the card itself is the subject. Recognition uses the same `(name LIKE '% annual fee', category='Memberships', frequency='annual')` triple as the soft-delete cascade.

**Editing the AF goes through the cards surface, not the subscriptions surface, and cascades atomically.** `cards.annual_fee` is the canonical source for the live AF amount; the companion subscription's `amount` mirrors it. `PATCH /cards/{id}` accepts `annual_fee` (existing) and a virtual `next_annual_fee_date` field (Day 19b); when either is in the patch, the route routes through the `update_card_af(p_card_id, p_annual_fee, p_set_annual_fee, p_next_annual_fee_date, p_set_next_date)` SECURITY DEFINER RPC that updates `cards.annual_fee` and the active AF subscription's `amount` / `next_billing_date` in one SQL transaction. A `next_annual_fee_date: null` cancels the AF subscription (stop tracking); a date on a card whose AF sub is cancelled re-inserts (re-enable). The frontend's AF-edit sheet (Day 19b `EditCardAfSheet`) hits this single endpoint for all three operations — amount edit, date edit, stop tracking — and never calls `PATCH /subscriptions/{id}` for an AF row. This keeps `EditSubscriptionSheet`'s pause/cancel/card-reassign affordances (which don't apply to AFs) out of an AF-shaped flow.

**Category disambiguation — `Streaming` vs `Memberships`.** The ledger's recurring-charge taxonomy keeps two categories, separated by *what is being paid for*, not by *how often*:

- **`Streaming`** — Netflix, Spotify, Apple Music, Hulu, YouTube Premium, Disney+. Media specifically.
- **`Memberships`** — non-streaming recurring: software (Adobe, Notion, 1Password), gym, Patreon, news (NYT, Substack), cloud storage (iCloud+, Dropbox), Costco-style annual memberships.

The bucket was renamed from `Subscriptions` to `Memberships` in migration `20260519120000` and prompt-bumped to `categorize_v5` — the literal collided with the `subscriptions` *table* name (which holds rows of both categories), causing confusion when reasoning about "what category does a Netflix `subscriptions` row carry." Monarch's precedent uses the same "Memberships and subscriptions" framing. The frontend control order and the Gemini categorizer prompt list `Streaming` first so Netflix-shaped merchants don't get pushed into the catch-all. AF rows are categorised as `Memberships` because they're a recurring non-media charge billed by the card itself; the recognition triple uses the literal `'Memberships'` so any drift here breaks the §8.3 split-cascade.

**Edits on the subscriptions surface.** The `/subscriptions` detail sheet exposes Pause/Resume/Cancel **and** an edit-fields path — name, amount, category, and card (or to `null` for bank ACH) via `PATCH /subscriptions/{id}`. `frequency` and `start_date` remain immutable (§8.3) — the sheet renders them read-only with a hint to cancel-and-re-add to change billing cadence. Mirrors `EditCardSheet`'s field-edit-then-save shape. Chat remains a valid path to edit the same fields (the user can ask "change my Netflix to $19.99"), but the sheet is the primary surface so users don't have to context-switch out of `/subscriptions` for a one-field tweak.

Auto-logged transactions appear in the main list tagged with a 🔄 icon. The AI chat is the primary management interface ("Add Netflix $15.99 monthly on my Amex Gold" creates the subscription conversationally; "track my rent at $2400 monthly, no card" works too).

---

### 6.6 Internationalization (currency, language, locale)

v1's invite-only user base spans the US, Japan, and Taiwan (the author's family). Internationalization is treated as **three independent layers**, deliberately decoupled so the high-value work ships without waiting on the hard part:

1. **Locale correctness** — does the app *behave* right for a JPY/TWD user? Correct currency symbol and decimal rules, locale-aware dates, per-user timezone for scheduled delivery, the chat agent answering in the user's language, and voice capture in the user's language. Mostly mechanical (one nullable `timezone` column the only schema change).
2. **UI translation** — is the *interface chrome* in Japanese / Traditional Chinese? A larger lift (an i18n framework + a CJK typographic treatment), planned but not yet built.
3. **Financial-domain localization** — categories, merchants, and credit cards. Merchant/category extraction already works in any language (Gemini treats merchant names as opaque strings); **international credit-card reward lookup is explicitly deferred** (see below and `TODO.md`).

**What the data layer already supports.** `users_meta.home_currency` (§8.7) already allows `JPY`, `TWD`, and seven others in its CHECK constraint, and the onboarding currency picker offers all nine. Invariant 13 (single immutable home currency per user, no FX) is *load-bearing* here rather than a limitation: each family member picks JPY or TWD once at signup and their entire ledger is coherent in that currency — no per-transaction currency, no FX conversion, no multi-currency reconciliation. A Japan-resident user's card statement is in JPY, their home currency is JPY, and amounts are stored verbatim. Voice capture is already trilingual (`en-US`, `zh-TW`, `ja-JP` — §7.7).

**Amount representation is currency-agnostic and value-safe.** Amounts are stored as Postgres `numeric` in **major units** (¥1,000 is stored as `1000`, $47.50 as `47.50`). The frontend's internal "cents-as-number" representation (`amount × 100`, an integer-precision trick) is value-preserving for every supported currency — including zero-decimal JPY — because the ×100 is precision-handling, *not* a claim that the currency has 100 minor units. So internationalizing currency is purely a **display** concern: the symbol and the number of fraction digits, never the stored value.

**The three axes are independent — currency, timezone, and UI language do not derive from one another.** This is a load-bearing decision, not an implementation detail. A user can legitimately want an English UI, a JPY home currency, and an Asia/Tokyo timezone all at once (the author's sister in Japan). So: `home_currency` is immutable (financial integrity, invariant 13) and chosen at signup; `timezone` and `ui_language` each auto-default from the best available browser signal and are independently overridable. In particular, timezone is **not** guessed from currency (USD spans Hawaii→Maine; a JPY holder could live anywhere) — it comes from the browser's IANA zone, which is simply correct. And display *formatting locale* (number grouping, date layout) follows the **UI language**, not the currency — so the English-UI/JPY user sees English dates with ¥ amounts.

**Tier 1 — locale correctness (shipped).** All display formatting routes through helpers in `frontend/src/lib/format.ts`. The currency *code* comes from `home_currency`; the *formatting locale* comes from a separate `displayLocale()` that follows the UI language (the browser's `navigator.language` until Tier 2 adds an explicit `ui_language` preference) — the two are deliberately decoupled per the axis-independence rule above. `Intl.NumberFormat` then selects the correct symbol (¥, NT$, £, €) and the currency's natural fraction digits (0 for JPY, 2 for USD), so ¥1,000 never renders as "$1000" or "¥1,000.00"; the "drop trailing `.00` when whole" aesthetic is preserved by forcing zero fraction digits only for whole values. The same `displayLocale()` drives `toLocaleDateString`. The chat agent (`app/prompts/chat.py`, `chat_v11`) is instructed to reply in the language the user wrote in (tool args and category values stay canonical English) — Haiku is natively multilingual, so a Japanese question gets a Japanese answer with no model change.

**Tier 1.5 — per-user timezone (shipped).** `users_meta.timezone` (nullable IANA zone, mutable — migration `20260601120000`) is captured at `/auth/bootstrap` from the browser, editable in Settings → Notifications, and validated against `zoneinfo` (`app/util/timezone.py`) at both write paths. It drives the weekly digest (§6.4): the cron sends to each user at ~Monday 09:00 in *their* zone and computes the summarized week's Mon–Sun boundaries there. NULL falls back to the historical America/New_York behavior, so pre-existing users are unaffected.

Supported language set is **`en`, `ja`, `zh-TW`** (Traditional Chinese only — Simplified is out of scope). `<html lang>` is set from `ui_language` (main.tsx) so CSS `:lang()` rules can pick region-appropriate Han glyphs.

**Tier 2a — i18n foundation (shipped).** The `ui_language` axis and the surfaces that key off it, *minus* the broad JSX chrome extraction: the `ui_language` column on `users_meta` (nullable, mutable — same shape as `timezone`, but with a `CHECK` since the set is small and fixed; mirrored in `app/util/language.py`), snapshotted from `navigator.language` at `/auth/bootstrap`, returned on `/me`, editable via `PATCH /me/preferences` and a Settings → Account selector (`LanguageRow`, mirroring the timezone picker); `displayLocale()` (format.ts) reads it, so number/date formatting follows the chosen language (an explicit `en` keeps the browser's regional English; `ja`/`zh-TW` pin a CJK locale); a Noto Sans **JP + TC** font stack added as the CJK fallback (the load-bearing fix — the serif display stack has no CJK glyphs; the `lowercase`/`lowercase-title` transforms are a harmless no-op on Han/Kana and stay), region-ordered per `:lang()`; category *display* labels localized on every read-only spending surface (breakdown index/category/goal, home dashboard tiles, the `/goals` page) via a reactive `useCategoryLabel()` hook while the **stored category enum stays English** (join key / glyph key / contract-test key — §6.2, only the rendered label is translated); and the weekly digest localized end-to-end — the Sonnet narrative is written in `ui_language` (`digest_v2`), the email subject/body/CTA/unsubscribe + top-category label render from a per-language string table, and `_format_money` renders the correct per-currency symbol and decimal places (`¥1,500`, not `JPY 1,500.00`) for the nine `home_currency` currencies (`app/services/digest.py`).

**Tier 2b — UI chrome translation (planned, not built).** The remaining broad work: an i18n framework (e.g. react-i18next) plus extraction of the hardcoded English JSX strings across the app, and wiring category labels into the remaining *interactive/edit* render sites (the edit-sheet category pickers, the chat parse cards, the onboarding tour) — deferred here because they are tightly coupled to the other English chrome strings in those same components and localize naturally with them; the read-only category *displays* are already done in 2a. Until 2b lands, the app chrome (buttons, headings) stays English while currency/date formatting, chat replies, category labels on every read-only spending view, and the digest are localized.

**Chat reply language becomes setting-driven** (a `chat_v12` prompt bump): Haiku replies in the user's `ui_language` regardless of the language they typed in, falling back to the current mirror-the-input behavior (`chat_v11`) only when `ui_language` is unset. Setting-driven is more predictable than mirror-input — short or language-neutral inputs (`"Netflix $15"`, `"ok"`, a voice utterance) force the model to *guess* the language every turn, and a randomly-switching reply language reads as broken. The set language wins; the rare deliberate code-switcher loses auto-follow, an accepted trade at friends-and-family scale. **Chat history is never retroactively translated:** `chat_messages` is append-only and stores prose verbatim, so switching language affects only future replies and the UI chrome — a thread reads English up to the switch point and the new language after, like any messaging app. All Tier 2 work is tracked in `TODO.md`.

**Tier 3 — international credit cards (deferred).** See §6.1 and `TODO.md`. The card reward-multiplier lookup is US-only (US networks, US issuers, US reward programs `UR/MR/TYP/Bilt`, US source domains). JP/TW users can add cards manually (name + last 4) without automated multiplier lookup. Deferred because it is the hardest, lowest-confidence slice — and because the reward-multiplier value proposition is genuinely weaker outside the US (Japanese rewards lean on partner-economy point ecosystems rather than clean category multipliers; Taiwanese cards rotate bonus categories quarterly, so there is often no stable multiplier to look up). This requires a product decision on the target UX before any code, recorded in `TODO.md`.

---

## 7. AI Architecture

**Design principle:** AI is the primary interface, not a feature bolted on. Screens and charts exist for at-a-glance visibility. The AI handles everything else.

### 7.1 Claude Agent — Messages API with `tool_use`

The chat agent uses the Anthropic Messages API directly via the `anthropic` Python SDK. The agent loop runs **in the FastAPI process**. No managed agent service.

**The load-bearing property** is a lifetime symmetry: the user's JWT lifetime equals the HTTP request's lifetime, so `ctx.user_jwt` is a request-local Python variable that typed tools close over directly. No session container is needed, no credential is at rest, and Supabase RLS auto-enforces `auth.uid() = user_id` because the JWT is in scope at the moment the tool runs. Any framework that introduces a session abstraction between the request and the tool (Managed Agents, ADK) breaks this symmetry — the JWT then has to live somewhere other than the request frame, which is the deeper reason "JWT in scope" matters more than it might sound.

**Why not Claude Managed Agents:**

Managed Agents (`/v1/sessions`, `/v1/agents`) is Anthropic's hosted harness for **long-running autonomous tasks** — minutes-to-hours work with bash, file, and web tools running in an Anthropic-provisioned container. Tameru's chat turns are 4–6 seconds with typed DB-backed tools. Managed Agents would force every tool call to round-trip through MCP from Anthropic's container to Tameru's MCP server, adding latency and complicating the per-user JWT path. Per Anthropic's own positioning ("custom agent loops and fine-grained control" → Messages API; "long-running tasks and asynchronous work" → Managed Agents), Tameru is the former.

**Why not LangChain (classic):** General-purpose LLM-app toolbox optimized for vendor neutrality, LCEL chain composition, and the `AgentExecutor` pattern. Tameru is pinned to Claude Haiku 4.5 by §11.4, our control flow is a cyclic loop (not a chain), and `AgentExecutor`'s iteration logic *is* the 80-line loop we want to own. The frictions:

1. **Message-shape translation.** `HumanMessage` / `AIMessage` / `ToolMessage` are LangChain's types, not Anthropic's. `chat_turn_trace`'s wire-shape contract (§8.12) requires a translator on every persist — and that translator becomes part of our surface to maintain across `langchain-core` upgrades.
2. **`langchain-anthropic` feature lag.** Same shape as the ADK/LiteLLM concern above. Prompt caching `cache_control` blocks, `input_json_delta` streaming, `anthropic-beta:` headers all gate on the adapter's version, not Anthropic's SDK.
3. **`AgentExecutor` opacity.** Loop semantics (max iterations, early stopping, intermediate-step formatting, retry behavior) are subclass-to-customize rather than read-the-code-to-understand. Our loop *is* the customization surface.
4. **Indirect observability.** Cost logging routes through `BaseCallbackHandler.on_llm_end` rather than the direct `resp.usage` in our loop. Works, but it's an event-bus pattern over what should be a function return value.
5. **Version churn.** `langchain` → `langchain-core` + `langchain-community` + `langchain-{provider}` split, the LCEL transition, agent-API deprecations — each upgrade is a project.

The one part LangChain handles well is JWT-in-scope: per-request `StructuredTool.from_function(func=closure_over_user_jwt)` instances preserve the lifetime symmetry above, so LangChain doesn't break invariant #1 the way ADK does. But that single property doesn't compensate for the rest.

**Why not LangGraph:** This is the strongest alternative considered. LangGraph (LangChain Inc.'s successor to `AgentExecutor`) provides explicit `StateGraph` state machines with checkpointing, human-in-the-loop interrupts, time-travel debugging, and multi-agent subgraphs — genuine upgrades in expressive power for the right workload. Its sweet spot is multi-step branching workflows (`planner → retriever → reflector → writer`), long-running checkpointed agents that pause and resume across hours-to-days, and mid-call user-approval interrupts. Tameru's chat loop is none of those:

- **Single agent, two states.** `call_model ↔ call_tools` — the graph topology *is* the `while True` loop. The state machine adds inspectability we don't need because there's nothing to inspect beyond one cycle.
- **4–6 second turns.** Checkpoint resumability is solving a problem we don't have. Our state is Postgres (`chat_turn_trace`), and §7.6 keeps the loop stateless from the app's perspective by design.
- **Propose-confirm lives at the HTTP boundary, not mid-graph.** Invariant #8 routes confirms through a separate `POST /<resource>/confirm` endpoint triggered by a user tap on the preview card. The natural granularity is two HTTP calls, not one interrupted graph.

The costs if we adopted it anyway: (a) `PostgresSaver` creates its own `checkpoints` / `writes` tables overlapping with `chat_turn_trace`'s purpose, with weaker wire-shape fidelity — LangGraph checkpoints are state snapshots, not Anthropic-shaped messages, so §11.5 cost math goes through framework internals; (b) JWT-in-state becomes JWT-at-rest in the checkpoint table on every node unless `user_jwt` is carefully configured as an ephemeral input-only channel — a footgun our current loop can't have because there's no persistence layer to leak into; (c) `thread_id` (LangGraph's conversation key) duplicates our existing `conversation_id` (§8.11), forcing either redundancy or schema-coupling to LangGraph's API; (d) we still inherit `langchain-anthropic`'s adapter lag on Anthropic-specific features. Revisit if the chat agent ever grows multi-step branches, gains human-in-the-loop interrupts mid-call, or runs long enough that checkpoint resumability earns its keep. None of those are v1.

**Why not Google ADK:** ADK (Agent Development Kit, open-sourced April 2025) is optimized for multi-agent orchestration deployed on Vertex AI Agent Engine, with LiteLLM providing vendor-neutral model access. Its three biggest wins — multi-agent workflows (`SequentialAgent`, `ParallelAgent`, agent transfers, A2A protocol), one-command Vertex deploy, and Gemini/Claude/GPT swap via a model string — do not apply to Tameru: single agent, Railway-hosted, Claude Haiku 4.5 pinned by §11.4. The costs are real and three-fold:

1. **JWT plumbing.** ADK tools receive a `ToolContext` exposing `session.state`, not request-scoped data. The JWT moves from a request-local variable into `session.state["user_jwt"]`, which depending on the `SessionService` backend (`InMemory`, custom Postgres adapter, `VertexAiSessionService`) means the credential is briefly at rest somewhere — breaking the lifetime symmetry above.
2. **Wire-shape replay erosion.** `chat_turn_trace` (§8.12) stores the literal JSON sent to Anthropic. ADK stores typed events (`LlmRequestEvent`, `ToolCallEvent`, …) and renders them to wire shape via LiteLLM's Anthropic adapter at request time. To preserve §8.12's contract we'd either re-derive Anthropic-shaped messages from ADK events on every turn (maintaining the inverse of LiteLLM) or replace the contract with ADK's own opinionated event-replay model — which makes the §11.5 per-turn token math depend on framework internals.
3. **Distance from the Anthropic API.** LiteLLM is excellent at vendor abstraction; the cost is that Anthropic-specific features (`cache_control` blocks for prompt caching, `input_json_delta` streaming for partial tool input, `anthropic-beta:` headers for preview features) arrive late through it. For a project where the chat agent is the AI surface, paying an abstraction tax on the API we're closest to is the wrong place to pay it.

Revisit ADK if Tameru ever becomes multi-agent, migrates to Vertex, or genuinely A/Bs models across vendors as a product decision.

**Loop sketch:**

```python
# In a FastAPI handler, with the user's JWT in scope
async def chat_turn(user_jwt, user_message):
    history = await load_history(user_jwt)
    memory_block = render_user_memory(user_jwt)

    async with anthropic.messages.stream(
        model="claude-haiku-4-5",
        system=SYSTEM_PROMPT + memory_block,
        tools=TYPED_TOOLS,
        messages=history + [{"role": "user", "content": user_message}],
    ) as stream:
        async for event in stream:
            if event.type == "tool_use":
                await log_tool_call(user_jwt, event)        # AICallLog
                await assert_within_usage_cap(user_jwt)     # cost gate
                result = await execute_tool(event.name, event.input, user_jwt)
                yield tool_result(event.id, result)
            elif event.type == "text":
                yield sse_event(event.text)                 # to React
```

**Middleware Tameru owns:**

- `AICallLog`: every tool invocation and result logged before passed back to Claude.
- Per-user usage tracking: increment counter per turn; gate runaway usage.
- Per-user 429 backoff: retry once after 2s with exponential backoff; fail gracefully after 2 attempts with a user-facing message.
- SSE streaming to React.
- Tool execution: call typed Python functions with the **user's JWT in scope**, so Supabase queries fire RLS.

### 7.2 Typed Tools, Not Raw SQL

The agent's analytical surface is a registry of ~7 typed Python functions (`get_transactions`, `calculate_total`, `get_cards`, …) rather than a single `run_sql(sql)` tool that lets Claude author Postgres directly. This is a deliberate trade-off — raw SQL has real merits and one tempting property (flexibility for arbitrary questions) — but five load-bearing factors push the decision to typed tools for v1.

**The raw-SQL alternative, sketched.** A single `run_sql(sql: str) → rows` tool, with the relevant table schemas dumped into the system prompt so the model can author queries. The model writes SQL on the fly: `"How much on coffee in March?"` becomes `tool_use(run_sql, "SELECT SUM(amount) FROM transactions WHERE category='Dining' AND merchant ILIKE '%coffee%' AND date BETWEEN '2026-03-01' AND '2026-03-31'")`. RLS still scopes rows to the authenticated user.

**The steelman for raw SQL:** flexibility across an open-ended question space, one tool instead of seven (smaller registry), no upfront design work to anticipate query shapes, modern Claude is competent at Postgres given a schema. If Tameru were a general-purpose "talk to your spreadsheet" data-exploration product, raw SQL would be the right primitive.

**Why typed tools win for Tameru:**

1. **Silent wrong answers are the worst failure mode in personal finance, and RLS doesn't protect against them.** RLS stops cross-user reads. It does not stop `WHERE created_at = ...` (wrong column), `WHERE date >= '2025-03-01'` (wrong year), or a `LEFT JOIN cards` that excludes transactions without a card. Each returns a plausible-looking number, confidently, with no error surface. In a finance app, **wrong-but-plausible numbers are worse than errors** — errors get reported, wrong numbers get acted on. Typed tools eliminate the category: each tool's SQL is reviewed, tested against fixtures, and indexed; a wrong number is a bug fixed once, not stochastic per-turn drift.

2. **Invariant #8 (propose-then-confirm) is structurally enforceable only with typed tools.** Today's contract — no ledger row is written from inside a `tool_use` handler — is enforced by the fact that no write tool exists for the model to call (other than `set_goal`, the lone carve-out per §8.13). The structural test `tests/contracts/test_tool_write_invariant.py` fails the build if `ALLOWED_DIRECT_WRITE_TOOLS` widens. A `run_sql` tool that accepts arbitrary SQL would erode this to a Postgres-role configuration (`default_transaction_read_only=on`), which still leaves the model with **affordance to attempt** an `INSERT` — and prompt-injections probe at affordances. Typed tools remove the affordance entirely.

3. **Eval determinism breaks under raw SQL.** §7.10's multi-hop eval harness asserts semantic tool calls: `tool_calls[0] == ("calculate_total", {"category": "Dining", ...})`. That assertion survives prompt rewording (the model can phrase the user's question many ways but the structured call stays stable). With raw SQL, the two alternatives are (a) string-match the SQL — brittle against cosmetic rephrasing (`SUM(amount)` vs `sum(amount)`, table aliases, equivalent date-range expressions); or (b) output-match the final number — passes with wrong SQL that happens to produce the right value on fixtures. Neither survives a real eval lifecycle.

4. **Schema-in-prompt and SQL-in-logs both fight the cost and privacy posture.** Raw SQL requires the table schema in the system prompt (~1–2K tokens/turn forever, ~7% over §11.5's 19K/turn baseline). Worse, every logged SQL string contains filter values — merchant names, amounts, category names — which then have to be regex-scrubbed before they reach PostHog under the privacy posture in CLAUDE.md ("never send transaction amounts or merchant names"). Typed tools log `{tool: "calculate_total", args: {category, start_date, end_date}}` — category names are non-sensitive, dates are non-sensitive, and merchant strings only ever appear inside `propose_transaction` args where they're scrubbed at log time rather than parsed out of free-form SQL.

5. **Invariant #3 (MCP server is read-only) is read-only by construction with typed tools.** The MCP server exposes only the read tools; there is no SQL parser to gate on. A raw-SQL world would need correct, ongoing SQL classification ("is this a SELECT?" — non-trivial with CTEs, `WITH RECURSIVE`, `RETURNING` clauses, subqueries) to maintain the invariant. We'd rather not own that classifier.

**The acknowledged con: typed tools require anticipating query shapes.** This is real — personal finance's question space is bounded (how much on X, what matches Y, recurring charges, goal progress, card list) but the long tail of "merchants I visit most after 10pm" or "categories where YoY growth >20%" doesn't fit any current tool. v1 accepts this gap.

**Phase 2 escape hatch.** A read-only `run_query(sql)` tool may be added in Phase 2 against a Postgres **read replica**, with **separate auth, separate logging, separate rate limits**. The shape matters: read replica (no risk to the write path or to invariant #8), read-only role (no `INSERT`/`UPDATE`/`DELETE` affordance), separate audit trail (SQL strings get their own scrub pipeline rather than commingling with structured tool logs). Adding it is contingent on observing a class of user questions typed tools can't serve — not speculative, not v1.

**Tool definitions (Phase 1):**

**Reads** — return data directly, no user confirmation:

- `get_transactions({ category?, card_id?, merchant_contains?, date_from?, date_to?, amount_min?, amount_max?, limit? }) → Transaction[]`
- `calculate_total({ category?, card_id?, date_from?, date_to? }) → { total, count }`
- `get_subscriptions({ status? }) → Subscription[]`
- `get_spending_summary({ months?, date_from?, date_to? }) → CategoryBreakdown[]` — `months` gives a trailing window; `date_from`/`date_to` give an explicit one. A *specific named month* ("breakdown for March") needs the explicit window — the trailing window cannot isolate a single past month (added Day 22 after the §7.10 eval caught the agent answering a "March" question with current-month data).
- `get_cards() → Card[]` — each card carries a short `ref` handle (`{issuer}-{last_four}`, e.g. `amex-1001`) for the propose-tool `card_ref` arg below.

**Writes (propose-then-confirm)** — the tool returns a proposal payload; the React client renders it as a preview card; a separate `POST /<resource>/confirm` endpoint writes the row after the user taps "looks right." No write happens inside the tool itself (§6.2 entry flow).

- `propose_transaction({ merchant, amount, date, card_ref?, category?, notes? }) → TransactionProposal`
- `propose_card({ network, last4, program, alias? }) → CardProposal`
- `propose_subscription({ name, amount, frequency, start_date, category?, card_ref? }) → SubscriptionProposal`

**Card references use a short handle, not the UUID.** The agent identifies a card via `get_cards` and passes that card's `ref` (`{issuer}-{last_four}`) — not its `id` UUID — to `propose_transaction` / `propose_subscription`'s `card_ref`. The tool resolves the handle to the UUID server-side under RLS. Rationale: the §7.10 chat-extraction eval caught Claude dropping a hex digit while copying a 36-char UUID between `get_cards` and `propose_subscription`, silently losing the card attribution. A short, meaningful handle is reliable to copy, and a slip fails closed (no match → cardless proposal, parse card prompts) rather than mis-resolving. The propose-tool implementations still accept a `card_id` UUID for direct (non-agent) callers and tests, but the agent-facing tool schema exposes only `card_ref`.
- `set_goal({ category?, amount, period }) → Goal` — direct write; goals are low-risk, reversible, and not on the transaction ledger. Creation and amount/period updates flow through this tool (chat is the only create surface, per invariant #8); the `/goals` page reaches the same table via `PATCH /goals/{id}` and `DELETE /goals/{id}` (§8.13) for edits and deletes, which preserves the tool-layer invariant — no *new* direct-write tools beyond `set_goal` itself.

**Why propose-then-confirm instead of a direct-write tool?** Transactions, cards, and subscriptions are visible on the user's ledger. Writing on `tool_use` would mean Claude could create a row from a misread vague message before the user sees what it parsed. The proposal pattern makes the UI the point of commit: no row exists until the user taps a button. It also matches the Intent Preview pattern from 2026 agentic-UX design literature.

**The agent has no direct-mutate tools for ledger rows.** No `edit_transaction`, no `delete_transaction`, no `add_*`. Chat's mutation role is retrieval (`get_transactions(...)`) + proposal (`propose_*`) only; the `tool_use` call itself never commits. The HTTP `PATCH` / `DELETE` call is always made by the client after an explicit user tap. In v1 that tap lives on the edit sheet (UX frame 11b), reached from the per-category list or from a chat-rendered candidate-card list. A post-launch enhancement (§6.2) adds an inline confirm card for the exact-1-match delete/update case, served by two additional `propose_*` tools that return proposals without mutating — same invariant shape, different UI. v1 does not ship those tools or that component.

**Example trace — read:** "How much more did I spend on dining in March vs February?"
→ `calculate_total(category="Dining", date_from="2026-03-01", date_to="2026-03-31")`
→ `calculate_total(category="Dining", date_from="2026-02-01", date_to="2026-02-28")`
→ Computes delta, responds in prose. Both tool calls logged to `AICallLog`.

**Example trace — write:** "spent $47 at Trader Joe's on my Amex Gold"
→ `get_cards()` → finds the Amex Gold, `ref="amex-1001"`
→ `propose_transaction(merchant="Trader Joe's", amount=47.00, date=today, card_ref="amex-1001")`
→ tool impl calls `categorize()` (Gemini) for category suggestion → returns `TransactionProposal`
→ Client renders the parse card (UX frame 15) → user taps "looks right" → `POST /transactions/confirm` writes the row and returns the Entry-Moment Insight.

### 7.2.1 Context window — Haiku 4.5's 200K is sufficient

Per the token math (§11.1), peak per-turn input is ~5,260 tokens at hop 4 of a 3-tool chain. Haiku 4.5's 200K context window leaves ~38× headroom — not a constraint at any expected usage pattern.

The two ways context could blow up are both already bounded by design:

- **Conversation history** is capped at the last 5 turns. The cap is enforced by reading the last 5 rows of `chat_turn_trace` (§8.12) — one row per turn, so the cap maps exactly to "5 turns" regardless of how many tool hops each turn contained. Older turns are summarized into `user_memory` (§7.6) and pruned. A 50-turn marathon session does not balloon the context.
- **Tool results.** A pathological call like `get_transactions(limit=10000)` returning thousands of rows would exceed any context. **All typed tool implementations cap result sizes at sensible limits** (e.g., `get_transactions` defaults to 50 and hard-caps at 500; `calculate_total` returns a single number; `get_spending_summary` is bounded by category count). The cap is enforced inside the tool function, not relied on from the model side.

Flash-Lite's 1M context is therefore not a meaningful differentiator for chat. It would matter for a different use case (full transaction-history dumps, very long autonomous runs) — neither of which Tameru does.

### 7.3 Concurrency & Multi-User Isolation

All API calls go through one Anthropic key, but this is not a bottleneck — the API is stateless and horizontally scalable on Anthropic's side. Two users chatting concurrently produce two independent FastAPI requests with no shared state.

| Concern | Owner | Implementation |
|---|---|---|
| Conversation history isolation | Tameru | Per `user_id` in Supabase, RLS enforced |
| Concurrent FastAPI requests | FastAPI | Async/await, no thread blocking |
| SSE stream isolation | Tameru | Per-user EventSource connection |
| Anthropic rate limits | Shared | ~50 RPM / ~200K TPM default. Sufficient through Phase 2. |
| Per-user cost control | Tameru middleware | `AICallLog` token sums per `user_id` |
| 429 handling | Tameru middleware | Retry once after 2s; graceful failure after 2 attempts |

### 7.4 Why Claude for Agent + Card Lookup, Gemini for Categorization

| Task | Model | Reason |
|---|---|---|
| Category inference | Gemini (env-resolved) | High-frequency, sub-cent cost, speed > quality. Called from inside `propose_transaction` tool impl. |
| Receipt photo parsing | Gemini (env-resolved, multimodal) | Image input supported up to 3,000/prompt. Bulk/async path, not chat. |
| CSV header + batch parse | Gemini (env-resolved) | Unstructured → structured, batched. Bulk/async path, not chat. |
| Chat-based transaction extraction | Claude Haiku 4.5 | In v1, chat is the only user-initiated write surface (CLAUDE.md invariant 8). Claude reads the user's message and fills `propose_transaction(...)` args directly via `tool_use`. There is no separate Gemini NL-parse call in the chat path. Gemini remains the parser for CSV and receipt paths above, because those are bulk/async where per-call cost dominates and no agent loop is involved. |
| Card multiplier lookup | Claude Haiku 4.5 + `web_search_20250305` | Web-grounded with enforced `allowed_domains` allowlist (NerdWallet, TPG, US Credit Card Guide, Doctor of Credit, issuer domain). Replaces the earlier Perplexity Sonar plan — see §6.1 for rationale (vendor reduction, native domain allowlist, citations as first-class on the Messages API). |
| Chat agent | Claude Haiku 4.5 | Multi-step typed-tool reasoning. Public agentic data point: AIME 2025 with tools 96.3% (+16 vs no-tools). Gemini Flash-Lite considered and rejected — see §11.4 for full rationale. |

**Gemini model resolution:** the exact Gemini model for every Gemini task above is resolved at call time from env vars (`GEMINI_MODEL` override, then `GEMINI_MODEL_DEFAULT`). **No model string is hardcoded in the code.** v1 production default is `GEMINI_MODEL_DEFAULT=gemini-2.5-flash` (GA, stable); `gemini-3.1-flash-lite-preview` is available via `GEMINI_MODEL` for eval experiments but is not the default because observed preview instability (503 UNAVAILABLE spikes during Day 4 smoke). Operators rotate the env var when Google deprecates or unstabilizes the current pick; no code ships. Same env-resolution pattern applies to receipt and CSV paths.
| Spending narrative (digest) | Claude Sonnet 4.6 | Prose quality (called weekly) |
| Memory distillation | Claude Haiku 4.5 | Fact extraction + scoring |

### 7.5 Streaming + Reconnect

All Claude chat responses stream via Server-Sent Events. The FastAPI endpoint yields tokens using `anthropic.messages.stream()`. The React frontend uses `EventSource`.

**Failure mode — deploy mid-stream:**

If Railway redeploys while a chat turn is streaming, the SSE connection drops. Two mitigations both ship in v1:

1. **Frontend reconnect button.** `EventSource.onerror` triggers a "Connection lost — retry" UI. User clicks; the request re-fires.
2. **Railway grace period.** `terminationGracePeriodSeconds = 60`. On SIGTERM, FastAPI stops accepting new requests but holds open existing SSE streams until they finish or the 60s window closes.

A resumable-stream design (cache final response server-side, replay on reconnect) is **deferred** until users complain. Not v1.

### 7.6 Session Memory

**Layer 1 — in-session.** Every message in the current conversation is included in the next API call. Stateless from the app's perspective.

**Layer 2 — cross-session profile.** After each chat session ends, a background Claude Haiku call reads the conversation and extracts atomic facts into `user_memory`. Each fact is one row (not a blob), enabling targeted deletion and time-decay pruning.

Categories: `spending_pattern | preference | active_context | card_preference | goal`.

**Memory cleanup:**

- Time decay: facts older than 90 days that haven't been reinforced are pruned.
- Capacity cap: soft limit of 60 facts, enforced by a nightly `pg_cron` sweep (Day 17, 03:00 UTC). Prune order is `relevance_score / (1 + days_since_reinforced / 30.0)` ascending — a pure-SQL recency × relevance ranking where day 0 = full score, day 30 = ½, day 60 = ⅓, day 90 = ¼ (then time decay deletes it anyway). Ties are broken by `reinforced_at ASC` so the oldest of equal-ranked rows goes first. Claude Haiku is not called during pruning — `relevance_score` was set at distillation time (§7.6 layer 2), and an extra LLM tiebreaker would re-run the same scoring it already did. Distillation may briefly push a user over the cap; `render_user_memory` already applies `LIMIT 60` so the over-cap rows are invisible to the agent until the next sweep trims them. The Settings memory page lists by lex order (relevance DESC, reinforced_at DESC) and is the one surface where an over-cap row is briefly visible — a tolerable inconsistency given the 24-hour bound.
- User control: "Show what you remember about me" lists all stored facts. "Forget that I'm planning a trip to Japan" deletes one. Full panel in Settings.

**Why not a vector DB:** at most 60 structured facts. A JSON array in Postgres is faster, simpler, and more debuggable than a vector store. Add a vector DB only if cross-session retrieval ever needs semantic search over long transcripts.

### 7.7 Natural Language Transaction Entry (text + voice)

The user describes a transaction by typing or speaking **in the chat surface**. Both inputs reach the same Claude Haiku agent loop (§7.1); Claude extracts the fields via `tool_use` args — there is no separate Gemini NL-parse call in the chat path (§7.4, CLAUDE.md invariant 8).

**Examples:**

- "Spent $47 at Trader Joe's on my Amex Gold just now" → Claude calls `propose_transaction(merchant="Trader Joe's", amount=47.00, date=today, card_id=<resolved from "Amex Gold">)`. Category suggestion is added by the tool impl via `categorize()` (Gemini).
- "Lunch at Nobu, $85, split with a friend so my half was $42, CSR" → Claude recognizes the split and fills `amount=42.00`.
- Ambiguous input (e.g. "coffee yesterday") → Claude replies in prose asking one targeted question ("how much?") rather than proposing a transaction with holes. No separate fallback form UI — the chat itself is the fallback.
- A missing *date* is not a hole. When the user states a merchant and amount but no time ("$7 at Peet's"), Claude defaults `date` to today and proposes — it does **not** ask "when was this?". The parse card (UX frame 15) is an editable correction surface, so a wrong default costs one tap, whereas a clarifying-question round-trip on every dateless entry is friction on the app's most common action. A missing *amount* still warrants a question — a proposal needs a real number. (Resolved Day 22, prompt `chat_v9`, after the §7.10 chat-extraction eval surfaced the agent over-asking. The blanket "don't fabricate dates" rule is scoped to retrieval windows, not entry.)

**Text trigger:** the chat message submit button is the trigger. One Claude turn per user message. Gemini is not called in this path (it remains in `categorize()` and in CSV / receipt bulk paths).

**Voice trigger:**

- Tap mic in the chat input → Web Speech API (`window.SpeechRecognition` / `webkitSpeechRecognition`) starts on-device speech recognition.
- Browser shows a live transcript as the user speaks. Stop button or 1.5s of silence ends recording.
- Final transcript auto-submits into the chat — identical to typed input from that point on. Same Claude turn, same `propose_transaction` path, same parse card (UX frame 15).
- Web Speech API (not Gemini audio input) because: it's free, on-device on Safari (macOS 14+ / iOS 14.5+) and routed through the browser's Web Speech sandbox on Chrome, works in all major browsers, and the audio quality of short transaction utterances is well within its capability.
- If `SpeechRecognition` is unavailable (rare in 2026, but possible on older browsers), the mic button is hidden and the user is told voice isn't supported in their browser. No silent failures.

**Supported languages (v1):** `en-US`, `zh-TW` (Taiwan Mandarin), `ja-JP`. Chosen to match the v1 user base (English-default, Taiwan family, Japan family). Initial language resolves from `navigator.language` with prefix-fallback to the closest supported (`en-*` → `en-US`, `zh-*` → `zh-TW`, `ja-*` → `ja-JP`); user override is persisted per-device in `localStorage`. A small chip in the Voice Active overlay (UX frame 14) lets the user switch in one tap (`en` / `中` / `日`). `zh-CN` is intentionally excluded for now — adding both Chinese variants doubles the eval surface for no v1-scale benefit; revisit if a CN user shows up.

**Multilingual downstream behavior:** the transcript is submitted to `/chat/turn` verbatim — there is no in-browser translation. Claude Haiku handles `tool_use` argument extraction across these three languages natively. Known limitation: the merchant canonicalization pass (§7.4, Day 9c `render_user_merchants()`) is English-centric and will not deduplicate `"ローソン"` against `"Lawson"` or `"全家"` against `"FamilyMart"`. Acceptable at v1 scale; the post-launch merchant-merge job (§5.5) is the long-term answer. The chat eval harness (§7.10, `multi_hop.yaml`) should include a small number of `zh-TW` and `ja-JP` rows so a Haiku model bump doesn't silently regress multilingual extraction.

**Offline and permission failures:**

- The mic button remains visible and enabled when the user is offline or has previously denied mic permission — hiding it would mask the affordance. Tapping while `!navigator.onLine` surfaces an inline `network` error in the overlay without starting recognition; Chrome will also fire `network` mid-recognition because it routes audio to Google's servers. Safari runs on-device and works offline.
- Every `not-allowed` event shows the same inline instructional error ("voice access denied. enable mic for this site in your browser settings, then try again."). We do not differentiate first-time vs persistent denial — browsers provide no reliable signal, and the right user action is the same. This matches the pattern Discord, ChatGPT, and Google Meet use.

**Cost:** voice transcription is free (browser-native). Downstream token cost is the same Claude chat turn a typed message would trigger — voice adds no incremental LLM cost over typed input.

### 7.8 Generative Charts

- "Chart my grocery spending by week in March" → line chart, weekly buckets.
- "Compare dining vs travel over the last 6 months" → grouped bar.
- "Show me which card I use most by category" → stacked bar or heatmap.

Claude determines chart type, grouping, and time range from the natural language request. Recharts renders inline in the chat thread.

### 7.9 MCP Server — OAuth 2.1

Tameru exposes a read-only MCP server (Streamable HTTP transport) on the Railway backend. Claude.ai (web), Claude Code, Claude Desktop, and any MCP-compatible client can query a user's spending data with their authorization.

**Exposed tools (read-only in v1):**

- `get_spending_summary(date_from, date_to)`
- `get_recent_transactions(limit, category?)`
- `get_subscriptions()`
- `get_card_multipliers(card_name?)`

**Auth model — OAuth 2.1.** The MCP server is an OAuth 2.1 *Resource Server*; it neither mints nor stores its own credentials. Authorization is delegated to **Supabase Auth's OAuth 2.1 Server** (public beta as of 2026-05) — Tameru already depends on Supabase, so this adds no new vendor, SDK, or sub-processor. There is no `mcp_tokens` table (§8.6).

The flow is standard MCP authorization:

1. A client (Claude.ai web, Claude Code, Claude Desktop) is pointed at the MCP URL. On an unauthenticated request the server returns `401` with a `WWW-Authenticate` header and serves OAuth Protected Resource Metadata (RFC 9728) at `/.well-known/oauth-protected-resource/mcp` — registered at the app root, not under the `/mcp` mount, so the advertised discovery URL resolves — pointing the client at the Supabase authorization server.
2. The client registers itself via Dynamic Client Registration (enabled in the Supabase dashboard) and runs the OAuth 2.1 + PKCE authorization-code flow.
3. The user authenticates and approves a consent screen, granting the client read access to their Tameru data. **The consent screen is Tameru-hosted**, at `/oauth/consent` in the PWA. (The OAuth authorize endpoint itself lives on Supabase's Auth Server at `{SUPABASE_URL}/oauth/authorize` — Tameru's `/oauth/consent` is the consent UI that endpoint delegates to. Two different hosts, distinct names, no ambiguity in logs.) Supabase's OAuth 2.1 Server does not ship a hosted consent UI; the page is a thin frontend implementation against the user-facing `supabase.auth.oauth.*` methods (`getAuthorizationDetails` → render `client_name` + read-only reassurance → `approveAuthorization` / `denyAuthorization`). No per-scope picker in v1 — a valid grant is read-only by construction, so one Allow button is the entire decision.
4. The client receives an access token (and refresh token) and presents the access token as `Authorization: Bearer <token>` on every MCP request.
5. The MCP server validates the access token — a Supabase-signed JWT verified locally against the project JWKS, the same machinery as `app/auth.py` — and scopes all queries to the token's user.

**Why OAuth and not static bearer tokens.** Claude.ai's *web* custom-connector UI supports only OAuth — it has no field for a static bearer token or custom header (Claude Code and Claude Desktop do support headers, but the design targets all three). The MCP authorization spec only *mandates* OAuth 2.1 for public third-party servers; a ~10-user server could spec-legally use static per-user bearer tokens. OAuth is adopted specifically so Claude.ai web works, and Supabase's OAuth 2.1 Server makes it a configuration task rather than a build-your-own-IdP task.

**RLS enforcement.** A Supabase-OAuth access token is a standard Supabase user JWT — `aud` and `role` are `authenticated`, `sub` is the user id, with one extra `client_id` claim; per Supabase's OAuth 2.1 Server documentation it has "full access to user data, same as regular session tokens." It verifies through the same JWKS / ES256 path as a browser session JWT (`app/auth.py::verify_supabase_jwt`), and `supabase_for_user(token)` makes Postgres enforce RLS on `auth.uid()` exactly as for a PWA request. So the MCP server needs no service role and no manual `WHERE user_id` filter — CLAUDE.md invariant 1 is untouched. (Resolved during Day 23a, closing the open item the Day 23 plan flagged. A regular browser session JWT also authenticates to the MCP server — same user, same data, not a privilege gain.)

**Read-only by design.** No `add_transaction` over MCP in v1. A leaked or over-scoped credential can read data; it cannot mutate it. Write tools may be added post-launch with explicit per-grant scopes.

**Revocation.** The user revokes a client's access from **Settings → Connected apps**, which calls `supabase.auth.oauth.revokeGrant(client_id)` directly from the PWA using the user's session JWT (no FastAPI bridge — invariant 1 untouched). Revocation deletes the session and invalidates the refresh token immediately at Supabase. The access JWT the MCP client already holds is stateless and remains valid until its `exp`; **Supabase's `JWT expiry limit` is set to 300s (5 min)** specifically to bound this residual window without adding a per-request server-side session lookup at the MCP layer (which would require service-role access). So "Disconnect" is effective within ~5 minutes, not literally instantaneous. Tighter immediacy would require either an `auth.sessions` check on every MCP request (service-role lookup → invariant-1 amendment) or a stateful access-token revocation list — both rejected for v1 at the ~10-user scale.

### 7.10 Eval Harness

Automated test suite measuring AI accuracy across the three highest-stakes tasks. Run via `python eval.py --eval=all` (Day 22). Each run writes a per-run JSON artifact to `evals/runs/<run_id>.json`; `evals/results.db` is a *derived* local SQLite, rebuilt from those JSON files via `python eval.py --report` and gitignored. The per-run JSON is the canonical artifact — committing a shared SQLite would produce binary merge conflicts across concurrent PRs.

**Eval setup.** The chat-extraction and multi-hop suites run the production agent loop (`run_turn`), which needs a JWT and seeded data. The harness provisions a real Supabase user (`eval@tameru.internal`) — not a service-role bypass (invariant 1) — via `scripts/mint_eval_jwt.py`, and seeds deterministic cards / transactions / subscriptions via `scripts/seed_eval_fixtures.py` (pinned in `scripts/_eval_setup.py`). Eval turns write `ai_call_log` rows under that user exactly as a human chat turn does (invariant 14); a weekly `pg_cron` job (`trim_eval_user_ai_call_log`, migration `20260520120000`) trims those rows older than 7 days.

**Eval 1 — categorization accuracy.** ~100+ hand-curated `(merchant, amount) → category` pairs (`evals/categorization.yaml`), scored against `categorize()`. Categories follow the `categorize_v5` taxonomy (`Memberships`, not "Subscriptions"). Target ≥ 90%.

**Eval 2 — chat-extraction accuracy.** Hand-curated NL strings → expected proposer `tool_use` args (`evals/chat_extraction.yaml`). Since the chat-unified UX (invariant 8) removed the standalone `parse_nl_entry` Gemini function, this eval runs the full agent loop against each user message and asserts the resulting tool call. Two proposers are gated: `propose_transaction` (target: amount ≥ 95%, merchant ≥ 90%) and `propose_subscription` (target: per-row pass ≥ 90% — the auto-logger amplifies frequency/start_date errors monthly, so subscription rows are metered holistically). `propose_card` is not gated — it fires rarely and the parse-card UI catches most extraction errors; UAT (Day 28) covers it. Includes `zh-TW` and `ja-JP` rows for multilingual coverage (§7.7).

**Eval 3 — multi-hop tool-use accuracy.** 20 hand-curated chat prompts that require 2+ chained tool calls (`evals/multi_hop.yaml`), e.g. "How much more did I spend on dining in March vs February?". Each row carries the prompt, the expected tool sequence (name + key arguments, matched as an order-insensitive multiset by default), and an optional final-answer pattern + dollar value. Scored by:
- **Tool sequence correctness** (did the agent call the right tools?). Target ≥ 90%.
- **Final answer correctness** (does the prose name the right number within tolerance?). Target ≥ 95%.

Includes `zh-TW` and `ja-JP` rows so a Haiku model bump can't silently regress multilingual extraction (§7.7). This eval exists specifically so the Haiku-vs-Flash-Lite (or future model) decision can be made on Tameru's actual tool surface, not on vendor benchmarks.

**Eval 3b — final-answer quality (LLM-as-judge dashboard).** A *non-gating* quality layer over the multi-hop suite's final-answer prose. Deterministic scoring (Evals 1–3) is the right tool for typed-tool trajectories and owns every CI gate — but it cannot read *helpfulness* or *tone*. For each multi-hop row, a stronger grader (`ANTHROPIC_JUDGE_MODEL`, default `claude-sonnet-4-6` — deliberately not the Haiku student it grades, to avoid the weak-judge / self-preference trap) makes one `temperature=0` forced-tool (`record_judgment`) call and scores two 1–5 dimensions, normalized to 0–1: **helpfulness** (did the answer resolve the question, using the retrieved data) and **tone** (anchored on the chat prompt's Style rules + §6.2/§6.3 voice — warm, delta-framed, no guilt framing, no bare absolutes). Numerical correctness is deliberately **not** a judge dimension — the deterministic `multi_hop.final_answer` check already meters whether the right value appears, so a judge score there would overlap an assertion and blur the split (deterministic owns everything assertable; the judge owns only the unassertable). The judge prompt is versioned (`app/prompts/judge.py`, `judge_v1`) and snapshotted into each run's `prompt_versions`. A judge API/parse error skips the row (shrinks the sample) rather than scoring it zero. The call is eval infrastructure — it is **not** written to `ai_call_log`. The scores are **target-only (gate=None)**: judge drift must never flip CI, so the gates stay on the deterministic scores. `EVAL_JUDGE=0` disables the pass (local fast runs / no Anthropic key). This realizes the hybrid named as the sanctioned future enhancement in the original deterministic-eval decision (the judge grades only the irreducibly-fuzzy qualities; everything assertable stays asserted).

**Run trigger:** locally on demand; CI's `eval-gate` job on PRs that touch `app/prompts/`, `app/agent/`, `app/integrations/gemini.py`, or `evals/`. Not on every commit (token cost); uses separate eval-only API keys with a tight dashboard quota. The judge runs inside this same job (~20 short Sonnet calls per eval-relevant PR, billed on the eval Anthropic key).

**Targets vs gates.** The *target* is the accuracy we want; the *gate* is the floor that blocks a CI merge. `eval.py` exits non-zero only on a gate breach — a target miss prints a warning and still passes. Gates: categorization ≥ 88%, chat-extraction `propose_transaction` amount ≥ 93%, chat-extraction `propose_subscription` per-row ≥ 85%, multi-hop tool sequence ≥ 85%. Merchant accuracy, multi-hop final-answer, and both LLM-as-judge dimensions (helpfulness / tone, target 80% each) are target-only (no gate).

**Refresh cadence:** monthly job mines `merchant_category` corrections for new categorization rows. Chat-extraction and multi-hop suites refreshed manually when new tools are added or new question patterns appear in real chat logs.

**Model A/B usage:** `python eval.py --eval=all --model claude-<id>` runs the suites against an alternate Claude chat model; each run's JSON records the model, so cross-model comparisons are first-class. `--model` is Claude-only — the agent loop constructs an Anthropic client, so the flag rejects a non-`claude-*` id rather than silently sending it to the wrong provider. The Flash-Lite cross-provider A/B (§11.4) stays a post-launch item: it requires the chat loop to first gain a Gemini execution path, which v1 does not build.

---

## 8. Data Models

User-owned tables (`cards`, `transactions`, `subscriptions`, `merchant_category`, `user_memory`, `users_meta`, `chat_messages`, `chat_turn_trace`) include `user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE`. The audit tables `ai_call_log` and `ai_call_log_daily` differ — see §8.8 and §8.9.

Every table has:

- `ENABLE ROW LEVEL SECURITY` **and** `FORCE ROW LEVEL SECURITY` — FORCE closes the table-owner bypass.
- A single `FOR ALL` policy on user-owned tables: `USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid())`.
- `SELECT`-only policy on audit tables (§8.8, §8.9) — all writes go through the service role.

Enum-like text fields carry `CHECK` constraints enforcing the allowed values listed in each section's description column.

**Status column doctrine (cards, transactions, subscriptions).** The three ledger entities express lifecycle through a `status` text column with a `CHECK` constraint, paired with a `deleted_at TIMESTAMPTZ` companion where the column expresses a tombstone (set when `status` flips to `'deleted'`; `NULL` otherwise). Soft-delete is universal — no row is ever hard-deleted by an application handler in v1. Rationale:

- **Consistency with the existing precedent.** `subscriptions.status` already encodes a multi-state lifecycle (`'active' | 'paused' | 'cancelled'`); standardizing cards and transactions on the same shape unifies the three ledger tables under one idiom.
- **Extensibility.** A boolean is a forced binary; future states (`'disputed'`, `'reversed'`, `'pending'`) — plausible if the §17 scaling plan ever activates — extend cleanly with a `CHECK` update rather than another schema migration.
- **Audit + recovery.** A deleted ledger row is recoverable (operator support, undo windows wider than the toast, "what changed?" forensics). Hard-delete forecloses these without saving anything meaningful at v1's data volume.
- **Stripe-style API parity.** Stripe surfaces `status` on every resource that has a lifecycle. Mirroring the convention is the path of least surprise for any caller familiar with fintech APIs.

**Defaults are asymmetric across the three tables and that asymmetry is load-bearing:**

- **`transactions`** — default reads filter to `WHERE status = 'active'`. A deleted transaction is excluded from totals, ledger fetches, dashboard, baselines, entry-moment, the `get_transactions` agent tool, and `pg_cron` reads. Surfaced only for chat-rehydrate state badges (the `deleted.` ParseCard state), future undo/restore UX, and audit queries.
- **`cards`** — totals still include cards in any status (§8.1 frontend filter rule 1). A closed card's prior transactions remain part of "total spend" because the money was actually spent; only the *card identity* is in a different status, not the historical transactions on it.
- **`subscriptions`** — `pg_cron` and the subscription list filter to `status = 'active'`; paused/cancelled rows stay visible in the manager surface (§6.5).

**Partial unique indexes scoping to `status = 'active'`** prevent deleted rows from blocking re-creates: `cards (user_id, issuer, last_four) WHERE status = 'active'`, `transactions (user_id, client_request_id) WHERE status = 'active' AND client_request_id IS NOT NULL`, `transactions (subscription_id, date) WHERE status = 'active' AND subscription_id IS NOT NULL`. Soft-deleted rows do not occupy the unique slot, so re-adding a deleted card or replaying a confirm after a delete creates a fresh row rather than 409-ing on a tombstone.

To make `WHERE status = 'active'` filtering the default-safe path for transactions, an `active_transactions` Postgres view (`SELECT * FROM transactions WHERE status = 'active'`, with `security_invoker = true` so RLS still applies) is exposed to PostgREST. Application read paths target the view; only audit/restore code touches the base table. This pushes the filter into the schema rather than relying on every call site remembering it.

### 8.1 `cards`

| Field | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| user_id | UUID | FK → auth.users |
| name | text | "Chase Sapphire Reserve" |
| issuer | text | Closed CHECK enum: `chase` \| `amex` \| `citi` \| `capital_one` \| `discover` \| `bank_of_america` \| `wells_fargo` \| `usaa` \| `bilt` \| `barclays` \| `us_bank` \| `synchrony` \| `other`. Required. The actual uniqueness tiebreaker — see Constraints. `other` is the escape hatch for unenumerated issuers. |
| network | text | `visa` \| `mastercard` \| `amex` \| `discover` \| `other` (CHECK constraint). Required. Silent metadata, filled by the lookup; not user-asked. Network is NOT a uniqueness tiebreaker (two cards from different banks can share network + last_four). |
| program | text | UR / MR / TYP / Bilt / Other |
| multipliers | JSONB | `{"Dining": 4, "Groceries": 4}` |
| annual_fee | numeric | USD; informational |
| last_four | text | UI identification + active-uniqueness key. Required on the chat / onboarding propose-confirm paths. |
| color | text | Hex for UI card display |
| source_urls | text[] | Web-search citations (Claude `web_search_result_location.url`) |
| status | text | Lifecycle. CHECK enum: `'active' \| 'deleted'`. NOT NULL DEFAULT `'active'`. Per the §8 status-column doctrine; replaces the prior `active boolean` (migration `20260516xxxxxx_cards_status_column.sql`). |
| deleted_at | timestamptz | Set by `DELETE /cards/{id}` when `status` flips to `'deleted'`. Powers the "closed {MMM YYYY}" label on deleted rows in the spending-breakdown filter (§6.1, Day 14 frontend filter semantics). `NULL` for active rows. (Renamed from the prior `deactivated_at` in the same migration.) |
| client_request_id | UUID | Stable per-proposal join key. Server-minted at `propose_card` time (or `crypto.randomUUID()` on the onboarding `AddCardStep`), posted back unchanged at `/cards/confirm`, persisted here. Drives `_annotate_committed_proposals`'s 1:1 join on chat-rehydrate so two same-name cards (e.g. "Amex Gold" 1234 vs "Amex Gold" 5678) don't collide on a name-only match. Also the same-crid idempotency key for `/cards/confirm` (a replayed POST returns the existing row). **Not the structural dedup** — the natural-key partial unique index on `(user_id, issuer, last_four)` still owns that. Added migration `20260517120000_cards_client_request_id.sql`. NOT NULL DEFAULT `gen_random_uuid()` so pre-Day-15 rows backfill cleanly. |
| created_at | timestamptz | |

**Constraints:**
- `CREATE UNIQUE INDEX cards_active_identity_uniq ON cards (user_id, issuer, last_four) WHERE status = 'active';` — **partial** unique index keyed on `issuer` (NOT network). Card numbers are issued per BANK, not per network: a single issuer cannot give one person two cards with the same number, but two different banks (e.g. Chase and Capital One) absolutely can produce same-last_4 collisions across Visa cards. Issuer is the proper tiebreaker. Deleted rows are deliberately exempt so users can re-add a card after deleting it. (Day 14 originally shipped a `(network, last_four)` version; migration `20260516140000_cards_uniqueness_by_issuer.sql` fixed the tiebreaker, and the §8 status-column migration retargets the predicate from `active = true` to `status = 'active'`.)
- `CREATE UNIQUE INDEX cards_active_client_request_id_unique ON cards (user_id, client_request_id) WHERE status = 'active';` — guards the crid join key. Migration `20260517120000_cards_client_request_id.sql`. Two layers of dedup defend different invariants: the identity index above prevents the *same physical card* from being added twice; this index prevents the *same proposal* from creating two distinct rows under a race. The `/cards/confirm` route short-circuits on same-crid replay so the index almost never fires in practice — it's defense-in-depth, mirroring the equivalent `transactions_user_client_request_id_unique` from §8.2.

**Soft-delete / re-add semantics:**

When a user deletes a card and later re-adds the same `(issuer, last_four)`, **insert a new row; do not revive the soft-deleted row.** Rationale:

- Multipliers and annual fees drift over time. A card closed in 2024 and re-added in 2026 should get a fresh lookup, not stale data.
- Historical transactions stay linked to the original soft-deleted `card_id`. That preserves the historical card snapshot (annual fee at the time, multipliers at the time). Reviving the row would commingle pre-deletion and post-deletion transactions under a single ambiguous identity.
- `status = 'deleted'` already means "I closed this card." Reviving negates that.
- One rule ("once deleted, always deleted") is simpler than conditional revival logic.

The cost: two rows can exist in `cards` with the same `(user_id, issuer, last_four)` — one with `status = 'active'`, one with `status = 'deleted'`. The partial unique index permits this. The spending-breakdown filter (Day 14, §6.1 frontend filter semantics) distinguishes them with a "closed {MMM YYYY}" suffix derived from `deleted_at`, rendered in a muted color.

**Frontend filter rules (referenced by Day 14):**

1. **Totals always include deleted cards.** Sum-by-category, sum-by-month, weekly delta, year-to-date math sum across `status = 'active'` AND `status = 'deleted'`. Transaction reads do not filter by `cards.status`. Otherwise "total spend" silently stops matching "sum of per-card spend" the moment a card is deleted. (This is the cards/transactions asymmetry from the §8 doctrine: a deleted *card* keeps its historical transactions in the totals, while a deleted *transaction* leaves them entirely.)
2. **Filter dropdown is dynamic.** Shows all active cards plus deleted cards with ≥1 transaction in the current view's date range. Deleted cards with no transactions in scope are hidden.
3. **Collision labels.** Deleted rows render as `{name} · {last_four} · closed {MMM YYYY}` in muted color; active rows render as `{name} · {last_four}`.

**409 collision flow on `POST /cards/confirm`:**

- If only `status = 'active'` rows match the constraint, the insert fails with a unique violation → return HTTP 409 `{code: "active_card_exists", existing_card: {...}}`. The frontend surfaces an inline "you already have *{name}* ending {last_four} — edit it instead?" affordance linking to PATCH.
- If only a `status = 'deleted'` row matches, the partial index does not fire and the insert succeeds — a new `card_id` is created.

**Annual-fee tracking lives on `subscriptions`, not on `cards`.** The renewal date is stored on the companion subscription's `next_billing_date`, not as a `cards.next_annual_fee_date` column — coupling the renewal date to two tables would create a sync problem with no upside. The *amount*, by contrast, IS on both: `cards.annual_fee` (this table, Day 14) is the canonical source for the live AF; the companion subscription's `amount` mirrors it via the `update_card_af` RPC's cascade (§6.5). Both AF write paths — create (`POST /cards/confirm` → `insert_card_with_af`) and edit (`PATCH /cards/{id}` → `update_card_af`) — go through SECURITY DEFINER RPCs for atomicity, same pattern as `soft_delete_card` (§8.3). The Python route never issues two PostgREST writes to cards + subscriptions; a best-effort double-write would silently produce orphan cards (no AF sub) or drifted amounts (AF sub charges the old fee after the user updated it on the cards surface).

### 8.2 `transactions`

| Field | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| user_id | UUID | FK → auth.users |
| card_id | UUID | FK → cards (nullable) |
| subscription_id | UUID | FK → subscriptions (nullable) |
| merchant | text | As entered or parsed |
| amount | numeric | In the user's `users_meta.home_currency` (§8.7). Stored as `numeric` for money-safe arithmetic — never `float`. |
| date | date | Transaction date |
| category | text | User-confirmed or auto-assigned |
| gemini_suggestion | text | Raw suggestion before user confirm. Self-reported by the client on `/transactions/confirm`; see "Trust posture" below. |
| source | text | manual \| nlp \| receipt_photo \| auto_logged \| csv_import |
| notes | text | Optional |
| client_request_id | UUID | Nullable. Set by the client on the chat-confirm path (§6.2 step 4) for offline-replay idempotency. `NULL` for pg_cron auto-logger (§6.5) and CSV batch inserts (§5.4.3). |
| status | text | Lifecycle. CHECK enum: `'active' \| 'deleted'`. NOT NULL DEFAULT `'active'`. Per the §8 status-column doctrine. |
| deleted_at | timestamptz | Set by `DELETE /transactions/{id}` when `status` flips to `'deleted'`. `NULL` for active rows. Powers the chat parse-card `deleted.` rehydrate badge (§6.2) and any future undo/restore UX. |
| created_at | timestamptz | |
| updated_at | timestamptz | Used for offline sync conflict resolution |

**Constraints:**
- `UNIQUE (subscription_id, date) WHERE status = 'active' AND subscription_id IS NOT NULL` — guarantees subscription idempotency for the pg_cron auto-logger. Scoped to `status = 'active'` so a user-deleted auto-logged charge does not block pg_cron from re-creating the slot in the (unlikely) event the same `(subscription_id, date)` re-fires; `next_billing_date` normally advances past the deleted date, so this is defense-in-depth, not the common path.
- `UNIQUE (user_id, client_request_id) WHERE status = 'active' AND client_request_id IS NOT NULL` — partial unique index for chat-confirm idempotency. Replay of the same confirm (e.g. IndexedDB queue draining after reconnect) returns the existing row instead of creating a duplicate. Scoped to `status = 'active'` so a confirm replayed after the user deleted the original row creates a fresh active row instead of 409-ing on a tombstone. The UI prevents this from happening in normal flow (rehydrated parse cards lock into `deleted.` and disable re-confirm); this scoping is defense-in-depth.

**Soft-delete semantics:**

`DELETE /transactions/{id}` sets `status = 'deleted', deleted_at = now()`; it never issues a SQL `DELETE`. Default read paths target the `active_transactions` view (PostgREST exposes it; RLS still applies via `security_invoker = true`), so a deleted row is invisible to the ledger fetch, the dashboard, baselines, entry-moment, the `get_transactions` agent tool, and `pg_cron` reads. The two surfaces that opt into the base table are:

- **Chat rehydrate annotation** ([`_annotate_committed_proposals`](app/routes/chat.py)) — needs to distinguish "never confirmed" from "confirmed and deleted" to set the parse-card `deleted.` badge correctly.
- **Future undo/restore + audit/forensic queries** — operator support, "what changed in my totals?" reconstruction. Not user-facing in v1.

Restore is **not** wired in v1: there is no UI affordance to flip `status = 'deleted'` back to `'active'`. The `UndoToast` (mobile swipe-delete window) operates by withholding the DELETE call until the toast expires, not by issuing a soft-delete then reverting. Adding restore is a post-launch enhancement when (and if) a real user asks; the schema supports it without further migration.

**Trust posture for `gemini_suggestion`:** the field is reported by the client on the confirm payload and stored as-is. A user tampering with it forges audit data only on their own account and gains nothing; the authoritative record of what Gemini actually said lives in `ai_call_log` (§8.8). Do not server-side re-derive the suggestion or sign proposals for v1 — the cost/complexity does not match the threat. If fleet-wide override-rate analytics later become load-bearing (e.g. under the §17 scaling plan), introduce a short-lived `transaction_proposals` server-storage layer at that point; do not build it speculatively.

### 8.3 `subscriptions`

| Field | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| user_id | UUID | FK → auth.users |
| card_id | UUID | FK → cards; **nullable** (Day 19 design decision — cardless subscriptions cover bank-ACH bills like rent, utilities, and mortgage, which are the largest line items for most users; the asymmetry with the nullable `transactions.card_id` was no longer earning its keep). `ON DELETE CASCADE` when set — but soft-delete of a card does not fire the cascade, so the soft-delete handler (§6.5) flips affected subscriptions explicitly. `RESTRICT` was rejected here because both `cards` and `subscriptions` cascade from `auth.users`, Postgres does not guarantee sibling-cascade order, and RESTRICT is checked immediately. |
| name | text | "Disney+", "Netflix" |
| amount | numeric | Fixed billing |
| frequency | text | monthly \| quarterly \| annual \| weekly |
| start_date | date | First billing |
| next_billing_date | date | Computed next auto-log date |
| category | text | Default category for auto-logged tx |
| status | text | CHECK enum: `'active' \| 'paused' \| 'cancelled'`. NOT NULL DEFAULT `'active'`. The precedent the §8 status-column doctrine follows; cards and transactions adopt the same `status` shape (with their own state sets). No `'deleted'` value — `'cancelled'` already encodes the terminal lifecycle for subscriptions, and adding `'deleted'` would split a single user intent ("I'm done with this subscription") across two semantically overlapping states. |
| client_request_id | UUID | Nullable. Minted by `propose_subscription`; powers the same offline-replay idempotency contract as `transactions.client_request_id` (§8.2). pg_cron-written rows leave it NULL. |
| created_at | timestamptz | |

Partial unique index `subscriptions_user_client_request_id_unique ON subscriptions (user_id, client_request_id) WHERE client_request_id IS NOT NULL` makes the chat-confirm path idempotent under the Day 15 offline-queue drain. Cards carry the same `client_request_id` column (§8.1) but use it primarily as a chat-rehydrate join key — the natural-key partial unique index on `(user_id, issuer, last_four)` is the structural dedup. Subscriptions don't have an equivalent natural key (two valid subscriptions on the same card with the same name and frequency, e.g. family plan vs. personal plan, can't be distinguished structurally), so crid is the only dedup defense. Without it, a duplicated subscription would be auto-logged independently by pg_cron every billing cycle, multiplying the recovery cost monthly — see Day 19 prompt's "Why `client_request_id` on subscriptions" for the asymmetry across all three tables.

**Cancel / re-add semantics:** mirror the §8.1 cards doctrine. When a user cancels Netflix in May and re-adds Netflix in August, **insert a new row; do not revive the cancelled row.** Same three reasons: (1) pricing, plan, and `start_date` drift between billing eras; (2) historical auto-logged transactions stay linked to the cancelled `subscription_id`, preserving the audit-clean bound between "May–June Netflix" and "August onward Netflix"; (3) one rule ("once cancelled, always cancelled") across the three ledger tables is simpler than per-entity revival logic. No constraint change enforces this — subscriptions have no natural-key unique index by design — so the rule is upheld at the application layer: `propose_subscription` mints a fresh `client_request_id` for every confirm, and `POST /subscriptions/confirm` never looks for a cancelled-row match to revive.

**Forward-only auto-log:** the pg_cron auto-logger (§6.5) does not backfill past billing cycles. At create time, if `start_date <= today`, `next_billing_date` is set to `today + 1 period`; if `start_date > today`, `next_billing_date = start_date`. The user is shown a confirm-card note ("we won't auto-log past charges — next auto-log: {next_billing_date}") so the expectation is set up front. This matches the YNAB / Copilot / Rocket Money / Monarch industry pattern (none of them backfill on a backdated start_date) and avoids spamming the dashboard the moment a user creates a long-backdated subscription. Manual one-off transaction entry remains available for historical charges.

**Frequency and start_date are immutable.** `PATCH /subscriptions/{id}` accepts updates to `amount`, `category`, `name`, and `card_id`. It rejects updates to `frequency` and `start_date` with 422 + UI hint "cancel and re-add to change billing cadence." Rationale: changing `frequency` mid-cycle leaves `next_billing_date` semantically ambiguous (was the prior billing the old cadence or the new one?). The cancel-then-re-add path is already the doctrine for subscription lifecycle changes, and amount-level edits (e.g., CSR $550 → $795) don't need the frequency lever.

**Card soft-delete → split cascade.** When a card is soft-deleted (`status = 'deleted'` via `DELETE /cards/{id}` — §8.1), the cascade on `subscriptions.card_id` does not fire (it requires a true SQL `DELETE`), so the soft-delete handler explicitly handles companion subscriptions in two branches:

1. **Card annual-fee subscriptions** (created by the §8.1 / Day 19b AF dual-write) → flip to `status = 'cancelled'`. The fee is billed *by* the card itself; there is no third-party recipient and no other card the fee can be re-pointed at. The cancellation is terminal.
2. **All other subscriptions** (Netflix, gym, ACH'd rent) → flip to `status = 'paused'`. The pg_cron auto-logger skips paused rows, so nothing logs while the user decides. `/subscriptions` surfaces a "needs new card" banner listing affected rows; the user reassigns via `PATCH /subscriptions/{id}` with a new `card_id` (or `null` for ACH), then taps resume. This matches Stripe's "don't auto-cancel on payment-method change" posture and Copilot's manual card-replacement semantic. Auto-cancelling on card delete would destroy the natural "I moved Netflix to my new card" flow.

Both branches plus the card UPDATE itself run inside a single SQL transaction via the `soft_delete_card(p_card_id UUID)` `SECURITY DEFINER` function (migration `20260518130300_soft_delete_card_function.sql`). All three updates commit or none do — without this, a failure between passes would leave the user staring at AF rows cancelled, regular subs paused, and the card still in their wallet, until they retried. The function filters every WHERE by `auth.uid()` so the SECURITY DEFINER posture doesn't widen the access boundary.

How AF subscriptions are recognised: the function looks for subscriptions on the deleted `card_id` whose `name` matches the `"{card_name} annual fee"` template AND whose `category = 'Memberships'` AND whose `frequency = 'annual'`. Same triple used by the §6.5 `GET /subscriptions` hide-AF filter and by the §8.3 AF dual-write. (The literal was `'Subscriptions'` prior to migration `20260519120000`; see §6.5 for the rename rationale.) This heuristic is good enough at the v1 scale; a future tightening would store a `subscription_kind` enum column to remove the name-template dependency, but that's not v1.

### 8.4 `merchant_category` (merchant memory)

Powers the "past corrections" field in the Gemini categorization prompt. Most recent correction wins.

| Field | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| user_id | UUID | FK → auth.users |
| merchant | text | Normalized via `normalize_merchant()` (lowercased, trimmed, interior whitespace collapsed — `app/util/merchant.py`) |
| category | text | User-confirmed category |
| updated_at | timestamptz | |

**Constraint:** `UNIQUE (user_id, merchant)`.

**When the upsert fires** — two sites, one predicate (`category != gemini_suggestion`):

1. **`POST /transactions/confirm`** (Day 5): if the user edits the parse card's category away from `gemini_suggestion` before tapping "looks right," that is a correction and must seed the cache. Pure confirmations (category unchanged from the Gemini guess) are **not** written — caching confirmations would pollute the "past corrections" prompt slot with redundant, low-signal rows.
2. **`PATCH /transactions/{id}`** (Day 5): when the PATCH body contains a `category` different from the stored value. Keyed on the **new** normalized merchant if the PATCH also changed merchant; a merchant-only PATCH (no category change) does not touch this table.

Both sites upsert as `(user_id, normalize_merchant(merchant), category, updated_at=now())` with `ON CONFLICT (user_id, merchant) DO UPDATE`. This is the sole write path; no other code should insert into `merchant_category`.

### 8.5 `user_memory`

| Field | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| user_id | UUID | FK → auth.users |
| fact | text | Distilled fact in plain English |
| category | text | spending_pattern \| preference \| active_context \| card_preference \| goal |
| relevance_score | numeric | 0–1, assigned by Claude Haiku |
| reinforced_at | timestamptz | Last mention; used for time decay |
| created_at | timestamptz | |

### 8.6 MCP authorization — no dedicated table

MCP server authentication is delegated to Supabase Auth's OAuth 2.1 Server (§7.9). Registered OAuth clients, authorization grants, and access/refresh tokens are stored and managed inside Supabase Auth; Tameru owns no MCP credential table.

The earlier `mcp_tokens` design — per-user bearer tokens hashed at rest — was superseded when the auth model moved to OAuth 2.1 (the Claude.ai web connector accepts no static bearer token). The `20260421120600_mcp_tokens.sql` migration is obsolete; Day 23b ships a migration dropping the table.

### 8.7 `users_meta`

**v1 schema (ships with v1):**

| Field | Type | Description |
|---|---|---|
| user_id | UUID | PK / FK → auth.users |
| active_device_id | text | Most recently signed-in device |
| analytics_opted_out | boolean | Default false |
| weekly_digest_enabled | boolean | Default true. The single authoritative gate for the §6.4 weekly digest. Flipped by three paths: Settings toggle (user JWT, owner-UPDATE RLS), one-click List-Unsubscribe (service role via HMAC-token route), and Resend bounce/complaint webhook (service role). |
| home_currency | text | User's single home currency. CHECK constraint on the allowed set (`USD`, `EUR`, `GBP`, `CAD`, `AUD`, `JPY`, `CHF`, `SGD`, `TWD`). Default `USD`. **Immutable** — enforced by a BEFORE UPDATE trigger, not a CHECK, because a CHECK cannot compare OLD to NEW. See CLAUDE.md invariant 13. |
| timezone | text | Nullable IANA zone (e.g. `Asia/Tokyo`). Day 29 (§6.6, migration `20260601120000`). **Mutable** (no immutability trigger — independent of `home_currency`). Set at `/auth/bootstrap` from the browser, editable via `PATCH /me/preferences`; validated against `zoneinfo` in the app layer (`app/util/timezone.py`), no DB CHECK. NULL → the digest's default zone (America/New_York). Drives the §6.4 weekly digest's per-user local send time + week-boundary math. |
| ui_language | text | Nullable UI/display language. Day 29 Tier 2 (§6.6, migration `20260601140000`). **Mutable** — the third independent i18n axis. `CHECK (ui_language IN ('en','ja','zh-TW'))` because the set is small and fixed (unlike `timezone`); the same set is mirrored in `app/util/language.py` for clean 422s. Set at `/auth/bootstrap` from `navigator.language`, editable via `PATCH /me/preferences`. NULL → frontend `displayLocale()` falls back to the browser language, chat replies mirror the input (chat_v11 fallback), and the digest renders in English. Drives the formatting locale, the chat reply language (chat_v12), category display labels, and the digest narrative + email language. |
| created_at | timestamptz | |

**Forward-plan additions (only migrated if the scaling-to-100 decision is made — §17):**

| Field | Type | Description |
|---|---|---|
| plan | text | `free` \| `paid`. Default `free`. CHECK constraint on allowed values. |
| stripe_customer_id | text | Stripe Customer ID. Nullable until the user enters a paid flow. |
| stripe_subscription_id | text | Stripe Subscription ID. Nullable for free-tier users. |
| stripe_subscription_status | text | `active` \| `trialing` \| `past_due` \| `canceled` \| `incomplete` \| NULL. Mirrors Stripe; source of truth is Stripe, this column is a local cache. |
| stripe_current_period_end | timestamptz | End of the current paid billing period. Used to gracefully degrade to free-tier gating when a payment lapses. |

**Note on Stripe vs. Tameru subscriptions:** when the forward-plan columns ship, `stripe_subscription_*` will refer to **the user's paid subscription to Tameru**. They are distinct from the `subscriptions` table (§8.3), which tracks **user-logged recurring charges** like Netflix. Do not conflate the two.

### 8.8 `ai_call_log`

Append-only audit log of every Gemini and Claude API call. (Perplexity is no longer a provider as of §0 — `card_lookup` now goes through Claude `web_search`.)

| Field | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| user_id | UUID | Nullable for system-level calls; `REFERENCES auth.users(id) ON DELETE SET NULL` to preserve audit history after account deletion |
| timestamp | timestamptz | |
| provider | text | `anthropic` \| `google` |
| model | text | `claude-haiku-4-5` \| `claude-sonnet-4-6` \| `gemini-3.1-flash-lite-preview` \| `gemini-2.5-flash` \| ... |
| task_type | text | `categorization` \| `nl_parse` \| `chat_turn` \| `memory_distill` \| `card_lookup` \| `receipt_parse` \| `csv_import` \| `digest` |
| prompt_version | text | e.g. `categorize_v3` |
| prompt_hash | text | SHA-256 of rendered system prompt |
| input_tokens | integer | |
| output_tokens | integer | |
| latency_ms | integer | |
| success | boolean | |
| error_code | text | Nullable |

**RLS shape:** two `SELECT` policies (Postgres OR's them) and a narrow `INSERT` policy. **No** UPDATE or DELETE policies. This preserves CLAUDE.md invariant 1 — the in-handler logger writes with the user's JWT, not the service role. A compromised user JWT can forge token-spend rows on the attacker's own account (not a meaningful threat) but cannot scrub or alter existing audit history. System-level callers that have no user JWT (the `pg_cron` daily rollup, future digest jobs) use the service role, which bypasses RLS entirely.

- **Owner SELECT** — `USING (user_id = auth.uid())`. Every authenticated user sees their own rows.
- **Admin SELECT** — `USING (EXISTS (SELECT 1 FROM admins WHERE user_id = auth.uid()))`. Cross-user visibility for the admin observability endpoint (`app/routes/admin.py`). Admin membership lives in a small `admins` table (`user_id uuid PRIMARY KEY REFERENCES auth.users`), managed by service-role INSERT/DELETE in the Supabase SQL Editor. The same table is the source of truth for the FastAPI route's admittance check, so RLS and route gating cannot drift. Migration `20260522130300_ai_call_log_admin_select.sql`. (An earlier draft used a `current_setting('app.admin_user_ids')` GUC — dropped because `ALTER DATABASE` is denied on Supabase Free tier.)
- **Owner INSERT** — `WITH CHECK (user_id = auth.uid())`. Application loggers attribute every row to the calling JWT.

### 8.9 `ai_call_log_daily` (rollup)

Daily aggregation of `ai_call_log` rows older than 90 days.

| Field | Type | Description |
|---|---|---|
| date | date | |
| user_id | UUID | `NOT NULL` (required by composite PK); `REFERENCES auth.users(id) ON DELETE CASCADE` |
| provider | text | |
| model | text | |
| task_type | text | |
| sum_input_tokens | bigint | |
| sum_output_tokens | bigint | |
| count | integer | |
| avg_latency_ms | integer | |
| error_count | integer | |

**Primary key:** `(date, user_id, provider, model, task_type)`.

**System-level calls (NULL `user_id` in `ai_call_log`) are not rolled up here** — Postgres forbids NULLs in a PK column, and a sentinel UUID would break the FK. The aggregator skips them; they remain queryable in `ai_call_log` during its 90-day hot window. If system-call aggregates become useful later, add a separate rollup table keyed only on `(date, provider, model, task_type)`.

**RLS shape:** same as §8.8 — owner-`SELECT` + admin-`SELECT` (gated by membership in the `admins` table), writes via service role.

### 8.10 `stripe_events` (forward plan only — not in v1)

**Not in v1 schema.** Only migrated if the scaling-to-100 decision is made (§17) and paid billing ships. Documented here so the shape is pre-agreed.

Webhook-idempotency log. Stripe retries on non-2xx responses, so every handler must be idempotent. Each webhook event ID is inserted on first receipt; duplicates are no-ops.

| Field | Type | Description |
|---|---|---|
| event_id | text | Primary key. Stripe event ID (`evt_...`). |
| event_type | text | e.g. `customer.subscription.created`, `invoice.paid`, `invoice.payment_failed` |
| received_at | timestamptz | First observation |
| processed_at | timestamptz | Nullable; set after handler completes |
| payload_hash | text | SHA-256 of the raw webhook body for audit |

**RLS shape:** `ENABLE ROW LEVEL SECURITY` with no policies — the table is reachable only via the service role (webhook handler uses service role because there's no user JWT in scope for a Stripe-initiated request). A user JWT can neither read nor write.

**No `user_id` column** — webhooks sometimes arrive before the corresponding user row is fully hydrated (e.g., `checkout.session.completed` races with the initial customer creation). The handler looks up the user via `stripe_customer_id` on `users_meta` and updates that row; `stripe_events` exists only to deduplicate.

### 8.11 `chat_messages`

Human-visible chat log for the Claude Haiku agent (§7.1, §7.6 layer 1). One user row + one assistant row per turn. The UI/conversation-thread surface (Day 10's `ChatThread.tsx`) reads from this table and only this table. Synthetic intermediate blocks from the agent loop (`assistant` blocks containing `tool_use`, the synthetic `user` blocks carrying `tool_result`) are **not** stored here — they live in `chat_turn_trace` (§8.12). The split keeps UI reads simple: alternating user/assistant rows with prose-shaped `content_blocks`, no filtering needed.

| Field | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| user_id | UUID | FK → auth.users; `ON DELETE CASCADE` |
| conversation_id | UUID | Plain UUID grouper. **Not** an FK to a separate `conversations` table — v1 has no per-conversation metadata (title, archived, shared). Promote to an FK if conversation-level metadata becomes load-bearing. |
| role | text | `user` \| `assistant`. CHECK-constrained. |
| content_blocks | JSONB | The user-visible content. For `role='user'`, a single text block with the typed/spoken message. For `role='assistant'`, the final iteration's blocks (text — tool_use blocks never reach this table). |
| seq | bigserial | Monotonic insertion-order tiebreaker. **Load-bearing:** the user + assistant pair is written in one batched insert and shares `created_at` to microsecond precision; ordering by `created_at` alone returns them non-deterministically. UI reads order by `seq`. |
| created_at | timestamptz | Default `now()`. Used for human-readable timestamps; **not** used for ordering — see `seq`. |

**Index:** `(user_id, conversation_id, seq)`.

**RLS shape:** `FOR ALL` on `user_id = auth.uid()`. Chat content is the user's own data — they read, write, update, and delete their own rows. Audit-style INSERT-only would block a future "clear conversation" feature for no v1 benefit.

**Relationship to §7.6 "stateless from the app's perspective":** §7.6 means the agent loop holds no in-memory state between calls — every turn rebuilds context from the DB. Persisting `chat_messages` (and `chat_turn_trace`) is what makes the conversation survive page reload and enables cross-session memory distillation; it does not contradict the stateless property.

### 8.12 `chat_turn_trace`

Anthropic-shaped replay log for the agent loop. One row per `/chat/turn` call, storing the **full** Anthropic message-list slice contributed by that turn — including the user's typed message, every intermediate `(assistant_with_tool_use, user_with_tool_result)` pair, and the final assistant blocks. The loop reads from this table to reconstruct the exact wire-shape Claude needs on the next turn.

**Why wire-shape fidelity matters:** the stored JSON equals the bytes sent to Anthropic (modulo serialization). This is load-bearing for two things — the per-turn token math in §11.5 (which only stays accurate if no abstraction layer rewrites the message list at send time) and any future prompt-caching work (which requires byte-stable prefixes across turns to actually hit the cache). A framework that owns event-to-message rendering (e.g., ADK + LiteLLM — see §7.1 "Why not Google ADK") loses this property by design; we keep it by storing what we send and sending what we stored.

**Why this is separate from `chat_messages`:**

The two tables answer different questions. `chat_messages` answers "what does the human see" — clean alternation, prose blocks, no synthetic rows. `chat_turn_trace` answers "what does the model need on replay" — the full block sequence including tool plumbing, so a follow-up turn that references prior tool output (e.g., "what about coffee?" after a Dining total) grounds correctly. Putting both behaviors in one table forces UI reads to filter synthetic rows on every fetch and forces a non-obvious "is this row user-typed or a synthetic tool_result?" distinction into the schema. Splitting eliminates that.

| Field | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| user_id | UUID | FK → auth.users; `ON DELETE CASCADE` |
| conversation_id | UUID | Matches `chat_messages.conversation_id`. Same plain UUID grouper rationale (§8.11). |
| messages | JSONB | The full Anthropic message list contributed by this turn, in wire shape: `[{"role":"user","content":"<typed text>"}, {"role":"assistant","content":[{"type":"tool_use",...}]}, {"role":"user","content":[{"type":"tool_result",...}]}, ..., {"role":"assistant","content":[{"type":"text","text":"..."}]}]`. Replay concatenates the `messages` arrays from the last 5 trace rows (oldest first). |
| seq | bigserial | Tiebreaker for the (rare) case where two turns share `created_at` to microsecond precision. |
| created_at | timestamptz | Default `now()`. |

**Index:** `(user_id, conversation_id, seq DESC)` — replay reads `ORDER BY seq DESC LIMIT 5`, then reverses in app code.

**RLS shape:** `FOR ALL` on `user_id = auth.uid()`. Same as `chat_messages`.

**History cap on read:** the chat route loads the last 5 trace rows per conversation per §7.2.1 ("last 5 turns"). With one row per turn, the cap maps exactly to "5 turns" regardless of how many tool hops each turn contained — a deliberate property of the one-row-per-turn shape.

**Atomicity:** the route writes `chat_turn_trace` first (load-bearing for replay), then `chat_messages`. Supabase Python exposes no transaction primitive across two table writes, so a partial write is technically possible. v1 accepts this — the worst case is a brief UI-vs-replay desync that resolves on the next turn. Stronger atomicity (RPC) is a Day 12+ concern when streaming makes persistence asynchronous.

**Coupling to `chat_messages`:** the two tables share `conversation_id` but are not enforced as referentially coupled. A future "clear this conversation" feature must delete from both; today, account deletion via `ON DELETE CASCADE` on `auth.users` cleans both up automatically.

### 8.13 `goals`

Per-user spending budgets. One row per `(user, category, period)` slot. Written by the `set_goal` agent tool (DESIGN.md §7.2 — the lone direct-write carve-out, since goals are low-risk, reversible, and not on the transaction ledger).

| Field | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| user_id | UUID | FK → auth.users; `ON DELETE CASCADE` |
| category | text | Nullable. NULL encodes an overall budget across categories. Closed-enum validation lives at the Pydantic model layer (`SetGoalRequest`), not as a DB CHECK — the DB only enforces uniqueness and CHECKs on `period` and `amount`. |
| amount | numeric | `CHECK (amount > 0)`. |
| period | text | `CHECK (period IN ('week', 'month', 'year'))`. |
| created_at | timestamptz | Default `now()`. |
| updated_at | timestamptz | Default `now()`; maintained by a BEFORE UPDATE trigger so PostgREST upserts that route through the DO UPDATE path still refresh the field. |

**Unique constraint:** `goals_user_cat_period_uniq UNIQUE NULLS NOT DISTINCT (user_id, category, period)`. The `NULLS NOT DISTINCT` modifier (Postgres 15+) folds the overall-budget slot (`category=NULL`) into the uniqueness bucket — without it, Postgres's default NULL-distinct semantics would let two such rows coexist and the "set" verb would silently break for any user without a per-category budget. A named CONSTRAINT (not a bare UNIQUE INDEX) is required so the `set_goal` tool's PostgREST `upsert(..., on_conflict="user_id,category,period")` can route to it; a functional index expression like `COALESCE(category, '')` can't be referenced by column list.

**Latest-wins semantics:** enforced at the schema layer via the constraint + the tool's upsert. Any reader (dashboard, weekly digest, future chat tool) can do `SELECT amount FROM goals WHERE category=? AND period=?` and trust that at most one row matches. The "latest wins" rule lives in one place — the upsert — instead of in every reader.

**RLS shape:** `FOR ALL` on `user_id = auth.uid()` — same pattern as `transactions` and `chat_messages`. The user owns their goals; they can read, set via `set_goal`, edit via `PATCH`, and delete via `DELETE`.

**Index:** `goals_user_idx ON (user_id)`. Per-user reads are the dominant access pattern; the unique constraint already provides the composite-key index for upsert resolution.

**HTTP surface:** `GET /goals`, `PATCH /goals/{id}`, `DELETE /goals/{id}` (`app/routes/goals.py`).

- `GET` returns each goal joined with `spent_period_to_date` computed over `active_transactions` in a calendar-aligned window (`month` = 1st-to-last, `week` = Mon-to-Sun, `year` = Jan 1-to-Dec 31). Server-side spend computation removes a per-goal round-trip from the frontend and keeps the `active_transactions` semantics (soft-deleted rows excluded) consistent with `calculate_total`.
- `PATCH` permits `amount` and `period` only; `category` is fixed because the unique key makes a category change indistinguishable from a new goal, so users delete and re-ask chat to "move" a budget. Period collisions surface as `409 goal_slot_occupied` with a structured `detail.code` so the edit sheet renders the conflict inline.
- `DELETE` is hard delete and idempotent — no FK from `transactions`, so no cascade considerations. RLS makes deleting another user's id a silent no-op.

The `/goals` page (`frontend/src/pages/goals.tsx`, peer to `/cards`, `/subscriptions`, `/memory`) is the management surface — `SwipeableRow`-based list with a 5s pending-delete undo, `EditGoalSheet` `BottomSheet` for amount/period edits. The `/breakdown` page renders a "this month vs your budgets" progress strip below the donut, gated on having month-scoped goals — categories without a goal show nothing, which keeps the dashboard one-screen invariant (#9) intact while still surfacing budget context where it matters. Goal *creation* stays in chat via `set_goal`, preserving invariant #8's "chat is the only user-initiated create surface" rule at the tool layer.

### 8.14 `email_log`

Per-send record for the §6.4 weekly digest (and any future scheduled email type — welcome sequence, etc.). Written by `app/cron/digest.py` after every Resend call attempt; updated by `app/routes/webhooks_resend.py` on bounce/complaint events.

| Field | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| user_id | UUID | `NOT NULL`; `REFERENCES auth.users(id) ON DELETE CASCADE` |
| kind | text | `CHECK (kind IN ('digest'))` — extend the CHECK in a one-line migration when welcome sequence ships |
| sent_at | timestamptz | Default `now()` |
| success | boolean | `NOT NULL`. False on Resend SDK error; true on accepted-for-delivery |
| provider_message_id | text | Nullable on send failure. Resend's `id` field; the webhook lookup key |
| error_code | text | Nullable; populated when `success=false` |
| bounce_type | text | Nullable; one of `('hard', 'soft', 'complaint')`. Set later by the Resend webhook on bounce/complaint events |
| dedup_week | date | Nullable. The recipient's **local Monday date** the email is sent for, computed by the cron in the user's timezone (Day 29, §6.6). The idempotency key. NULL on pre-Day-29 rows |

**Indexes:**

- `email_log_dedup_week_uniq UNIQUE ON (user_id, kind, dedup_week) WHERE success AND dedup_week IS NOT NULL` — the load-bearing idempotency primitive. **Keyed on the user's local Monday date, not the UTC week** (Day 29, migration `20260601130000`). The earlier `email_log_weekly_dedup` keyed on `date_trunc('week', sent_at AT TIME ZONE 'UTC')`, which double-sent users east of UTC+9 once the §6.6 Monday-09:00–noon retry window shipped: for `Australia/Sydney`, Monday 09:00 local is Sunday 23:00 UTC (the *previous* UTC week) while the 10:00/11:00 retries are Monday UTC, so the three fires got distinct keys. The local Monday date is invariant across all three retry fires and across a mid-week timezone change. A per-row tz can't appear in an IMMUTABLE index expression (memory.md 2026-05-25), so the cron computes the date in Python and stores it. Re-running the cron the same Monday is a zero-send no-op because `INSERT … ON CONFLICT DO NOTHING` refuses the duplicate. Partial on `success` is deliberate: a transient Resend 5xx must not lock the user out for the rest of the week; partial on `dedup_week IS NOT NULL` keeps legacy/keyless rows from colliding.
- `email_log_provider_message_id ON (provider_message_id) WHERE provider_message_id IS NOT NULL` — webhook lookup.

**RLS shape:** `ENABLE ROW LEVEL SECURITY` with **no policies** — the table is reachable only via the service role. Same posture as `stripe_events` (§8.10). No user-facing reads in v1; a future "show me my email history" Settings panel would add a narrow owner-SELECT policy then.

**Idempotency-write workaround:** PostgREST's `.upsert(on_conflict=...)` cannot pass the partial-index `WHERE` predicate (memory.md 2026-05-19 — same 42P10 failure that bit the Day 20 CSV import). The cron writes via a SECURITY DEFINER plpgsql RPC `email_log_insert_idempotent(p_user_id, p_kind, p_success, p_provider_message_id, p_error_code, p_dedup_week)` that emits the matching WHERE so Postgres can use the partial index. The function REVOKEs EXECUTE from PUBLIC/anon/authenticated and GRANTs only to service_role (memory.md 2026-05-18 privilege rule) so the DEFINER bypass cannot be invoked by a regular JWT.

**Reserve-then-send usage:** the cron computes `p_dedup_week` (the recipient's local Monday date) and calls this RPC with `p_success=true` and `p_provider_message_id=NULL` BEFORE invoking Resend. Empty SETOF return means the partial unique conflict fired (week slot taken — skip user). One-row return is the freshly-inserted reservation; the cron then sends and either UPDATEs `provider_message_id` on success or flips `success=false` on failure to release the slot for a same-week retry. This ordering is the actual duplicate-send guard — see §6.4 idempotency paragraph for why the alternative (log after send) leaves a real race.

### 8.15 *(future — placeholder)*

When the §16 welcome email sequence ships, extend `email_log.kind` CHECK with `'welcome_d0'`, `'welcome_d1'`, `'welcome_d7'`. No schema change beyond the CHECK; the unique-week idempotency rule still holds (a user can receive at most one of each kind per week, which is correct for the welcome sequence too — they should get the day-0 welcome exactly once).

---

## 9. Security & Privacy

### 9.1 Authentication & Authorization

Supabase Auth handles all authentication. Google OAuth is the primary flow; magic link is a fallback. Sessions persist via refresh token (localStorage). A session expires only after 60+ days of inactivity or explicit sign-out.

**RLS enforcement — the critical pattern:**

The FastAPI backend receives `Authorization: Bearer <user_jwt>` from the frontend. **For each request**, the backend instantiates a Supabase client passing that JWT. PostgREST sets `request.jwt.claims` per query, and Postgres enforces RLS automatically. A bug in the API cannot leak one user's data to another, because the database refuses the row.

**JWT verification:** the backend verifies each incoming JWT locally against the project's asymmetric JWKS at `${SUPABASE_URL}/auth/v1/.well-known/jwks.json` (Supabase issues ES256 tokens signed by rotating EC P-256 keys). `algorithms` is pinned to `["ES256"]` — accepting `RS256` in addition would widen the algorithm-confusion attack surface for zero benefit. `audience` is required to be `"authenticated"` and `issuer` is required to match `${SUPABASE_URL}/auth/v1`, so a token minted by a different Supabase project cannot authenticate. The JWKS is cached in-process and refreshed on a `kid` miss — verification is zero network round trips on the hot path. The shared `SUPABASE_JWT_SECRET` (HS256, legacy) is not used and is deliberately absent from `.env.example`.

The **service role key** is reserved for callers with no user JWT in scope:

1. The `pg_cron` daily auto-logger (runs as DB function, no application context).
2. Schema migrations (run via Supabase CLI from CI).
3. The weekly digest cron job (`app/cron/digest.py`, §6.4) and any future scheduled email job that iterates users — by definition the cron has no user JWT for the recipient.
4. The Resend bounce/complaint webhook (`app/routes/webhooks_resend.py`, §6.4) — the request is from Resend, not a logged-in user, so no JWT is in scope.

Application **request handlers triggered by a user** never use the service role. This is enforced by code review and by `tests/contracts/test_no_service_role_leak.py` — a directory rule excludes `app/cron/` and `app/scripts/` plus a per-file allowlist (with rationale comments) for the webhook above. Widening either requires the same rationale-comment discipline as `ALLOWED_DIRECT_WRITE_TOOLS`.

**Single active device:** `users_meta.active_device_id` is set on each successful sign-in. If a different device signs in, the previous device's session is revoked (user sees: "You signed in on iPhone — this session has ended"). Eliminates multi-device offline sync conflicts.

### 9.2 API Key Management

- `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `SUPABASE_SERVICE_ROLE_KEY` (now also required by the request-serving process — the §6.4 `/unsubscribe` and `/webhooks/resend` routes call `supabase_admin` per invariant 1's third/fourth sanctioned-caller carve-outs), `RESEND_API_KEY`, `RESEND_WEBHOOK_SECRET` (Svix signing secret for the §6.4 bounce/complaint webhook), `DIGEST_UNSUBSCRIBE_SECRET` (HMAC key for one-click unsubscribe tokens; 32 random bytes, base64), `BACKEND_PUBLIC_URL` (public hostname of the Railway backend; distinct from `FRONTEND_ORIGIN` because §6.4's `/unsubscribe` is a FastAPI route, not an SPA route — Vercel's catch-all rewrite would otherwise serve `index.html` and Gmail's one-click POST would never land on the suppression handler), `ANTHROPIC_DIGEST_MODEL` (optional override for the §6.4 Sonnet narrative model; defaults to `claude-sonnet-4-6`. Kept distinct from `ANTHROPIC_MODEL` so a chat-agent downgrade to Haiku doesn't drag the digest down with it), `SENTRY_DSN` — Railway environment variables only. (Perplexity is no longer used as of §0 — card lookup is on the Anthropic key.)
- `.env` in `.gitignore` from day one.
- Pre-publish git history audit (gitleaks) before making the repo public; rotate any leaked keys.
- All keys server-side. Never returned in API responses or exposed to the frontend.

### 9.3 Transport Security

- HTTPS enforced on Railway. Service Worker and PWA install require TLS.
- CSP: `script-src 'self'`. No external script CDNs. Everything bundled via Vite.
- CORS: FastAPI's `CORSMiddleware` allowlists an explicit set of origins — the Vercel frontend (`https://tameru.xyz` in prod, via the `FRONTEND_ORIGIN` env var) plus `http://localhost:5173` for the Vite dev server. No wildcards; no `*.vercel.app` catch-all (any Vercel tenant could then reach the API). `allow_credentials=False` because we authenticate via Bearer token in the `Authorization` header, not cookies — this sidesteps SameSite and third-party-cookie complications entirely. Allowed headers: `Authorization`, `X-Device-Id`, `Content-Type`. Preview-deploy URLs (Vercel PR previews) are not reachable from prod API in v1; if preview-against-prod ever becomes necessary, it goes through a staging backend, not a CORS wildcard.

### 9.4 Privacy Posture & AI Provider Data Retention

Financial data lives in your Supabase project under RLS. The only third-party egress is the AI API calls required for categorization, chat, and card lookup.

**Retention configuration:**

- **Anthropic:** Zero Data Retention (ZDR) requested for the Tameru organization. Default retention is 30 days for trust & safety; ZDR brings this to zero. Not used for training under any tier. Card-multiplier lookups also go through Anthropic via the `web_search` server tool — the public card name + last 4 are sent; no transaction data.
- **Google Gemini:** paid tier only. Paid tier does not use API data for training. Free tier does — never used.

**User disclosure copy.** The in-app copy (rendered by
`frontend/src/components/PrivacyDisclosure.tsx`, shown on both
`/privacy` and `Settings → Privacy`) is hedged until Anthropic confirms
ZDR — the paragraph below is the granted-state version; the in-app copy
mirrors the "requested, not yet active" wording above. When Anthropic
grants ZDR, drop the hedge in both surfaces (this paragraph and the
component) in the same PR; see `docs/zdr_request.md` for the request
log.

> When you chat with Tameru, your message goes to Anthropic — that covers the chat agent (adding transactions, answering questions) and the `web_search` card-multiplier lookup (public card name + last 4 only, never transaction data). Anthropic's default 30-day trust & safety retention drops to zero under ZDR (requested for the Tameru org); not used for training under any tier. Google Gemini receives the merchant name and amount of each transaction to pick a category (the `propose_transaction` Gemini fallback when Claude doesn't supply one), and parses CSV imports and receipt photos when those features are used. Gemini is on its paid tier, which does not use API data for training; the free tier is forbidden.

### 9.5 PostHog — Structural Events Only

PostHog tracks **product usage**, not financial behavior. The "client-side question classifier" from PRD v2.1 is dropped — it added complexity and a privacy story to defend without delivering value.

**Tracked events:**

- `chat_session_started`, `chat_session_ended` (timestamp, turn count, total duration)
- `feature_used` (enum: `dashboard | manual_entry | chat | csv_import | card_added | subscription_added`)
- `onboarding_step_completed` (step name)
- `weekly_digest_opened` — fired client-side from the PWA landing handler (`frontend/src/lib/digestLanding.ts`) when the URL carries `?source=digest`, NOT from Resend. The digest email's CTA links to `${FRONTEND_ORIGIN}/?source=digest`; the handler runs after `initAuth()` resolves (so the PostHog SDK has flipped out of opt-out-by-default) and strips the param via `history.replaceState`. Anonymous-device clicks (no Supabase session on the landing device) are an accepted measurement gap at v1 scale — without a session `setOptOut(false)` never runs, the SDK stays opted-out-by-default, and `track()` no-ops. The underreport is a constant fraction that doesn't bias week-over-week trends; invariant 5 (single active device per user) means most digest taps come from the device that already holds the session. NOT worked around via localStorage-stash-and-replay or anonymous opt-in — both would violate the leak-free-init invariant above for a measurement gain that doesn't gate any Phase 1 decision.
- `error_shown` (error type code only, no message)

**Never tracked:** transaction amounts, merchant names, card details, question text, or any other financial data.

User can opt out entirely in Settings (`users_meta.analytics_opted_out`).

### 9.6 Data Export

`GET /export` (FastAPI route in `app/routes/export.py`) dumps the
caller's user-content tables as a single JSON file, RLS-scoped under
the user's JWT — no service role. The "Export my data" button on both
`/privacy` (mobile) and `Settings → Privacy` (desktop) triggers a
browser-side `fetch` + Blob + synthetic `<a download>` click; no
Supabase Storage and no signed-URL token are involved. The download
filename is `tameru-export-YYYY-MM-DD.json`.

**v1 inclusion list** — every table containing user-typed content or
user preferences:

  - `transactions`, `cards`, `subscriptions` (ledger)
  - `user_memory` (chat-distilled facts)
  - `chat_messages` (full conversation history)
  - `merchant_category` (user's category overrides)
  - `users_meta` (preferences + home currency)

**v1 exclusion list** — internal observability tables, deferred to a
future "full audit export":

  - `chat_turn_trace` (per-turn agent-loop audit)
  - `ai_call_log`, `ai_call_log_daily` (AI cost/audit trail)
  - `email_log` (Resend send/bounce log)

The exclusion list is greppable in the route module docstring; an
inclusion change is a deliberate scope expansion that should update
both this section and the docstring in the same PR.

No automatic cloud backup in v1 — export is manual. There is no
`export_data()` chat tool either; chat answers questions in prose and
points the user to Settings rather than running the export through the
agent loop (cost, ergonomics, no UX gain). No public Privacy Policy
document in v1; that's a §17 scaling-phase deliverable.

---

## 10. Mobile Strategy — PWA Today, Swift Admitted as a Possible Future Migration

Tameru ships as a Progressive Web App. A Swift-native iOS migration is admitted as a *possible* future path if user demand materializes. Expo and Capacitor were evaluated and rejected for the reasons below. There is no Swift work on the Phase 1 roadmap.

### 10.1 PWA requirements (current ship)

- Installable via Safari "Add to Home Screen."
- Service Worker caches the app shell for offline load.
- **Offline scope (chat-unified UX, §6.2 + invariant 8).** A confirm tap (`POST /<resource>/confirm`) that fires while offline queues in IndexedDB and syncs on reconnect via a window-scope `online` listener (Day 15). For transactions the queue is keyed by `client_request_id` and the server returns the existing row idempotently on replay (§8.2). For cards there is no `client_request_id`, but the `cards_active_identity_uniq` partial index on `(user_id, issuer, last_four) WHERE active=true` makes a replay land as a 409 the drain treats as a successful dequeue. **Composing a new transaction, card, or subscription requires connectivity** because the parse step runs server-side in the Claude agent loop (`propose_*` tools are FastAPI route logic, not on-device). v1 does not support fully-offline composition; the queue catches the narrow window between parse-card-render (online) and confirm-tap (offline). The post-launch enhancement, if real-user feedback warrants it, is a client-side regex/heuristic NL parser fallback — not a queue redesign.
- Conflict resolution: not needed — single active device per user (§9.1). Queue entries carry `owner_user_id` so a sign-out / sign-in-as-different-user flow on the same device cannot drain entries under the wrong account.
- Lighthouse PWA score ≥ 90.
- Mobile-first layout — all core flows completable with one thumb.
- Transaction logged in <10 seconds from tap to save.

### 10.2 Push notifications — disclosure

iOS Safari supports web push only on iOS 16.4+ and only after the user installs the PWA to home screen. Even then, opt-in rates are lower than native. The weekly digest is therefore email-first; web push is a supplementary nudge channel for users who opt in.

### 10.3 Alternatives considered for an App Store surface

| Option | Verdict | Reason |
|---|---|---|
| **Capacitor / WebView wrapper** | Rejected as the *primary* native path | App Store Guideline 4.2 ("minimum functionality") scrutinizes web wrappers. Apps with real native-feeling features usually pass, but the UX ceiling is ~85–90% of true native — noticeably off on keyboard, scroll momentum, and navigation gestures. If a native surface is ever built, it should be worth the effort, not a thin shell. |
| **React Native / Expo** | Rejected | Would be viable in isolation — but the web PWA is already the commitment, and RN imposes meaningful architectural tax on features Tameru uses daily: SSE streaming needs `react-native-sse`, Web Speech API has no equivalent (would force server-side Whisper, breaking the §7.7 privacy posture), IndexedDB offline queue must be re-implemented against `expo-sqlite`, and Tailwind maps only ~95% cleanly via NativeWind. Net: two codebases or a rewrite, for ~95% of native feel. |
| **Native Swift / SwiftUI** | **Admitted as possible future migration** | Best iOS UX ceiling. Realistic cost at Tameru's feature scope is ~2× the PWA build time (see §17 rationale). Only worth doing if real users ask for it — not speculatively. |

### 10.4 Swift migration — when it's worth it

Triggered by signal, not schedule. Migrate only if **all three** are true:

- A meaningful fraction of active users explicitly ask for a "real iOS app" in feedback.
- PWA-specific UX limits (push opt-in rate, Safari install friction, offline sync complaints) are measurably hurting retention.
- There is sustained revenue from the paid tier (§17) to justify the 2–3 month build.

Exact thresholds are an open item (§16). Until all three are true, PWA is not a stepping stone — it is the product.

### 10.5 Why the backend is migration-safe regardless

FastAPI + per-request JWT + Supabase is stack-agnostic. Every API is called the same way from a browser, a Capacitor shell, a React Native client, or a Swift `URLSession`. The agent loop, RLS, MCP server, and `pg_cron` scheduler do not assume a web frontend. A future Swift client re-uses 100% of the backend.

This is a property to preserve: **no frontend-specific logic in the backend.** If a request handler ever starts branching on `User-Agent`, stop — the abstraction has leaked.

---

## 11. Cost Estimates

All costs monthly. AI pricing assumes:

- Gemini 3.1 Flash-Lite: pricing not yet published (preview). Estimates below use 2.5 Flash rates as a placeholder: ~$0.075/M input + $0.30/M output. Expected to be lower per Google's positioning.
- Claude Haiku 4.5: **$1.00/M input + $5.00/M output** (confirmed against Anthropic docs April 2026)
- Claude Sonnet 4.6: $3.00/M input + $15.00/M output
- Claude `web_search` server tool: **$10 per 1,000 searches** + standard Haiku token costs for the call

Per-user assumptions: 3 transactions/day, 5 chat turns/day, 1 session/day, 1 CSV import amortized over 6 months, 5 card adds amortized over user lifetime.

### 11.1 Token math for an agentic chat turn

A representative 3-tool-call turn with prompt caching enabled:

| Component | Tokens |
|---|---|
| System prompt (base) | 250 |
| Tool schemas (7 typed tools, ~80 each) | 560 |
| Memory facts (60 × ~25) | 1,500 |
| Conversation history (5 prior turns) | 2,000 |
| User message | 50 |
| **Per-hop fixed input** | **~4,360** |

Per turn:

- Hop 1: 4,360 in, 100 out (tool_use)
- Hop 2: 4,560 in, 100 out
- Hop 3: 4,760 in, 100 out
- Hop 4 (synthesis): 4,960 in, 300 out
- **Totals: ~18,640 input, 600 output**

At Haiku 4.5 prices ($1/M input, $5/M output): **18,640 × $1/M + 600 × $5/M = ~$0.022 per turn raw.** With prompt caching on the system + tools + memory (~2,310 cached tokens per hop, 90% discount on reads): **~$0.018 per turn**.

### 11.2 Per-user daily cap (cost ceiling)

Chat is the only cost that can run away. To bound it, FastAPI middleware enforces a **per-user daily Claude token cap** (default 200,000 tokens/day, env var `CHAT_USAGE_CAP_TOKENS_PER_DAY`). At ~19K tokens per agent turn that's roughly 10 chat turns per user per day. A user hitting the cap sees: *"You've used your daily AI quota — resets at midnight UTC."*

Worst-case per-user chat spend is therefore **~$6/month** (200K tokens × $1/M input and $5/M output blend), even if the user spams the chat until they hit the cap every day. Gemini and the Claude `web_search` card-lookup path aren't capped — they're too cheap per call to matter.

### 11.3 Cost table — invite-only (~10 users)

Tameru is planned as invite-only. No Pro tier, no Stripe, no scaling beyond close friends and family.

| Service | Cost | Notes |
|---|---|---|
| Railway | $10.00 | Hobby plan, persistent FastAPI service (backend only) |
| Vercel | $0.00 | Free (Hobby) tier — static hosting + edge CDN for the PWA; 100 GB-month bandwidth covers v1 with wide margin (§5.3) |
| Supabase | $0.00 | Free tier (500 MAU, 500MB DB) — covers invite-only indefinitely |
| Google OAuth | $0.00 | Free |
| Sentry | $0.00 | Free tier (5K errors/month) |
| Resend | $0.00 | Free up to 3K emails/month |
| PostHog | $0.00 | Free up to 1M events/month |
| Gemini 3.1 Flash-Lite — categorization | $0.05 | 900 calls/month, 200 in + 5 out each. Called from inside `propose_transaction` tool impl. |
| Gemini 3.1 Flash-Lite — CSV (amortized) | $0.01 | 1 import/user/6 months, 150 tx × 500 in. Bulk/async path. |
| Gemini 3.1 Flash-Lite — chat NL parse | $0.00 | **Removed in chat-unified UX** — chat-based NL parse folds into Claude `tool_use` arg extraction (§7.4, §7.7, invariant 8). Gemini is no longer in the chat path. |
| Claude `web_search` — card lookup | $0.05 | ≤10 lookups per user lifetime, amortized. ~$0.01/lookup ($10/1k searches + ~$0.003 Haiku tokens). Replaces the earlier Perplexity Sonar line. |
| Claude Haiku — agent chat | $27.00 | Estimate carried over from pre-unified-chat model (150 Q&A turns/user × 10 users, ~$0.018/turn with caching). **Revisit before scaling (§11.6):** unified chat now also carries transaction-entry turns (previously free on the Gemini path). Transaction turns are short 1-tool-call turns, not multi-hop reasoning, so per-turn cost is below the $0.018 blend. Rough upper bound if ~200 tx turns/user/month land in this bucket: +$10–$15/month. Within the daily cap (§11.2) either way; not a v1 blocker. |
| Claude Haiku — memory distill | $0.20 | 1 distillation per session per user |
| Claude Sonnet — weekly digest | $0.07 | 4 calls/user/month |
| **Total (10 users)** | **~$37.40/month** | **~$3.74 per user per month** |

### 11.4 Why Claude dominates the bill

Claude chat is ~65% of the monthly total; everything else combined (Gemini + Claude web_search + hosting) is ~35%. Two compounding reasons:

- **Per-token rate:** Haiku is ~13× more expensive per input token than Gemini Flash-Lite ($1.00/M vs ~$0.075/M), ~17× more per output token ($5/M vs $0.30/M).
- **Tokens per call:** a Gemini categorization is ~205 tokens (single shot). A Claude agent turn is ~19,000 tokens because the loop replays the full context (system prompt + 60 memory facts + 7 tool schemas + conversation history + tool_use/tool_result blocks) on every hop, and a typical turn has 3–4 hops. ~90× more tokens per call.

Combined, one Claude turn costs ~1,000× what one Gemini categorization call costs. Even though chat fires less often than categorization, the per-call cost wins.

**Alternatives considered and rejected for v1 chat:**

- **`gemini-3.1-flash-lite-preview`** — would drop chat from ~$27 to ~$6/month. Wins on cost (3.4× cheaper), general intelligence benchmarks (MMMLU 88.9% vs Haiku 83%, GPQA 86.9% vs 73%), and output speed (363 tok/s). **Rejected because Google's own developer documentation says** *"Complex Multi-Step Reasoning: If an agent needs deep planning or reasoning across many complex steps, Gemini 3.1 Pro is a better choice"* — and Tameru's chat agent is exactly multi-step typed-tool reasoning over financial data. Haiku 4.5 has the only public agentic data point (AIME 2025 with tools: 96.3%, +16 points over no-tools), which suggests it executes tool chains reliably. For an app where wrong numbers erode trust, $18/month is cheaper than re-establishing trust after an arithmetic error from a misfired tool call.
- **`gemini-3.1-pro-preview`** — Google's recommended model for multi-step reasoning, but at $2.00–$4.00/M input + $12.00–$18.00/M output it is ~2× more expensive than Haiku and fails the cost-optimization premise entirely.

**Re-evaluation plan:** the eval harness (§7.10) includes a multi-hop tool-use suite specifically so Flash-Lite can be A/B tested post-launch on Tameru's actual tool surface, not on vendor marketing. If Flash-Lite scores ≥ 90% of Haiku's accuracy on multi-hop, switch and pocket the savings.

Mitigations already in place:

- **Prompt caching** on the static portion drops real cost from ~$0.018 → ~$0.013 per turn (90% discount on cached reads).
- **Daily cap (§11.2)** bounds worst case to ~$5/user/month.
- **Haiku, not Sonnet**, for chat. Sonnet only for the weekly digest narrative (4 calls/user/month).

### 11.5 Observations

- **Absolute cost is small.** ~$30/month for 10 users is noise. The design is infra-dominated at this scale — Railway alone is a third of the bill.
- **Per-user cost is ~$3/month.** Of that, ~$2 is Claude chat, ~$1 is Railway amortized across users. AI is affordable; hosting is the floor.
- **The daily cap is the insurance policy.** Without it, one user running a chat loop overnight could 10× the month's bill. The cap makes the worst case predictable.
- **The original PRD's $0.40/month for Phase 1 chat was off by ~50×.** Token math (§11.1) is the source of truth — keep the math visible so future-you doesn't repeat the same mistake.

### 11.6 Cost projection — if scaled to 100 users (forward plan only)

**Not a v1 cost estimate.** v1 runs at the ~$37/month shape in §11.3. This table projects what the bill would look like *if* the scaling-to-100 decision is later made (§17), at which point infrastructure upgrades and freemium gating become load-bearing.

Linear-scale projection from §11.3, assuming the same per-user usage profile and **freemium gating not yet applied** (see §17.6 for the gating lever).

| Service | Cost at 100 users | Notes |
|---|---|---|
| Railway Starter | $20.00 | Upgrade from Hobby required — removes sleep, adds RAM headroom. |
| Vercel | $0.00 | Hobby tier still fits ~100 users of a PWA (mostly cached after first load). Upgrade to Pro ($20/mo) only if bandwidth or team seats become a constraint — not projected at this scale. |
| Supabase Pro | $25.00 | Mandatory. Free tier caps at 500 MAU / 500MB; Pro unlocks PITR, PgBouncer, and daily backups. |
| Google OAuth | $0.00 | Free. |
| Sentry | $0.00 | Free tier fits with headroom. |
| Resend | $0.00 | Under 3K emails/month at this scale. |
| PostHog | $0.00 | Under 1M events/month. |
| Stripe | ~2.9% + $0.30/tx | Usage-based; only paid-tier conversions incur. |
| Gemini 3.1 Flash-Lite — categorization | $0.50 | 9,000 calls/month. |
| Gemini 3.1 Flash-Lite — CSV (amortized) | $0.10 | |
| Gemini 3.1 Flash-Lite — NL parse | $0.20 | |
| Claude `web_search` — card lookup | $0.50 | One-time per card add, amortized. ~$0.01/lookup; ~50 lookups/month at 100 users. Replaces the earlier Perplexity Sonar line. |
| Claude Haiku — agent chat | **$270.00** | 15,000 turns/month. Dominant line item; freemium gating is the lever. |
| Claude Haiku — memory distill | $2.00 | 1 per session per user per day. |
| Claude Sonnet — weekly digest | $0.70 | 4 calls/user/month. |
| **Total (100 users, no gating)** | **~$319/month** | **~$3.19 per user per month, as at 10 users — AI cost is near-linear.** |

**Why this number matters:** at 10 users the entire bill is noise. At 100 users, Claude chat alone is $270/mo — crossing the threshold where a freemium gate becomes the difference between a sustainable project and a subsidy from the author's pocket. The gate, the paid tier, and the Flash-Lite A/B (§16) are all responses to this one line item.

**What freemium gating changes:** a rule like "3 chat turns/day free; paid tier unlimited" moves free-tier users from ~150 turns/month to ~90, which at 80% free / 20% paid cuts the chat line roughly in half. Exact numbers depend on the gate; see §17.6.

All schema changes go through the **Supabase CLI** and are checked into the repo under `supabase/migrations/`.

**Workflow:**

1. Make change locally (against `supabase start` Postgres or a dev Supabase project).
2. `supabase db diff -f add_notes_to_transaction` generates a timestamped `.sql` file.
3. Review the diff. Edit if needed (e.g., to add `IF NOT EXISTS` for idempotency).
4. Commit to git. Open PR.
5. CI runs `supabase db push` against production on merge to `main`.

**RLS policies live in migration files**, not the dashboard. Otherwise dev and prod drift and you can't tell which is right. Every new table's migration includes its `ENABLE ROW LEVEL SECURITY` and `CREATE POLICY` statements.

**Production DB changes via the Supabase dashboard SQL editor are forbidden** outside emergencies. If an emergency edit happens, the next PR must reconcile with a migration.

---

## 13. Testing Strategy

Five layers, in order of priority:

### 13.1 RLS contract tests (highest priority)

For every table with RLS, an integration test signs in as user A, attempts to read/write rows belonging to user B, and asserts failure. Run in CI on every PR. A failing RLS contract test blocks merge.

### 13.2 Eval harness

§7.10. Categorization + NL parse accuracy. Runs on PRs touching `app/prompts/` or `app/agent/`. Regression gate.

### 13.3 Backend integration tests

Pytest suite that exercises the FastAPI endpoints against a local Supabase instance. Covers happy paths, auth boundaries, and the subscription auto-logger (idempotency + advisory lock).

### 13.4 Frontend unit tests

Vitest for Zustand stores, formatters, the offline IndexedDB queue. UI component snapshot tests where they earn their keep.

### 13.5 E2E

Playwright covering: sign in, log a transaction, import a CSV, ask the AI chat one question, sign out. Run against a deployed preview environment in CI.

---

## 14. Observability & Ops

### 14.1 `AICallLog` retention

- **Hot:** raw `ai_call_log` rows kept for 90 days. Used for debugging and recent cost analysis.
- **Cold:** daily `pg_cron` aggregator rolls older rows into `ai_call_log_daily` and deletes the raw rows. Aggregate table grows linearly but slowly.

### 14.2 Sentry

Free tier (5K errors/month) for non-AI exceptions. AI failures live in `ai_call_log` with `success = false` — don't double-log.

### 14.3 Cron jobs

The daily subscription auto-logger and the daily AICallLog aggregator both run via **Postgres `pg_cron`** in Supabase. Benefits:

- Survives every Railway deploy.
- No extra hosting cost.
- Atomic with the database — no network failure modes between cron and DB.
- Idempotent by design (UNIQUE constraint + `ON CONFLICT DO NOTHING`).

The auto-logger function wraps execution in `pg_try_advisory_lock` so concurrent invocations cannot double-fire.

### 14.4 Single Railway instance — accepted risks

One process means deploys interrupt in-flight SSE streams. Mitigations:

- Frontend reconnect button.
- Railway `terminationGracePeriodSeconds = 60`.
- Resumable streams: not v1.

If a future SLO requires no dropped streams, scale horizontally (Railway supports replicas) and add a Redis pub/sub for stream session state.

### 14.5 Application logs — structured, correlated, redacted

Three observability surfaces, each owning one job:

| Surface | Source | Audience | For |
|---|---|---|---|
| `ai_call_log` (§8.8) | One row per AI provider call, written under the user's JWT (invariant 14) | Cost accounting, prompt-version regression detection, per-user spend | The author + the eval suite (§7.10) |
| Application logs | `logging.getLogger(__name__).info/warning/exception(...)` → stdout | Debugging, forensics, "what path did this request take" | Railway log viewer |
| Sentry (§14.2) | Unhandled exceptions + explicit `capture_exception` | Catching 5xx the author doesn't know about yet | Author email/alert |

Conflating these is the canonical failure mode — e.g., routing AI failures to Sentry double-pages the author for every Gemini 5xx (`ai_call_log` already records it), or routing application logs into `ai_call_log` pollutes the cost dashboard. Each surface stays in its lane.

**Structured stdout.** A single JSON formatter (`python-json-logger`) on the root logger emits `{timestamp, level, logger, message, correlation_id, user_id, ...extra}` per record. Uvicorn access/error logs are reformatted through the same formatter so the entire stream is one schema. `LOG_LEVEL` defaults to `INFO` in production, `DEBUG` in dev (resolved from `APP_ENV`).

**Correlation IDs.** `asgi-correlation-id` middleware sits outermost in the FastAPI middleware stack. It honors `X-Request-ID` from Railway's edge if present, mints a fresh UUIDv4 otherwise, and echoes the value back in the response header. The formatter reads the id from the library's contextvar; Sentry tags every event with the same id. One value spans stdout, Sentry, and the response header — `grep <id>` in Railway pinpoints every line of one request, and the same id appears in the Sentry alert.

**`user_id` propagation.** A `user_id_var: ContextVar[str | None]` is set inside `get_current_user_jwt` after JWT verification (cleared by middleware on response). The formatter reads it; Sentry's `set_user({"id": ...})` reads it. No email, no IP, no transaction data flows to either.

**PII redaction at the formatter layer.** A logging `Filter` walks every record's `message` + `extra` keys and replaces values matching the redaction set with `<redacted:reason>` rather than dropping the whole record (silent drops hide bugs). Redaction set, mirroring the §13 privacy posture: transaction amounts (decimal-pattern detector), merchant text, chat message text, email addresses, phone numbers, full card numbers, JWTs, Supabase service-role key. Sentry's `before_send` runs the same filter over `event.request.data`, `event.request.query_string`, `event.extra`, and `event.breadcrumbs[*].data`; `send_default_pii=False`.

**Sentry's `before_send` filter rules.**

1. Drop `fastapi.HTTPException` (4xx are expected; 5xx HTTPExceptions are surfaced by the `internal_error` handler in `app/main.py`).
2. Drop events whose originating module starts with `app.integrations.gemini`, `app.integrations.card_lookup`, `app.agent.loop`, or `app.agent.memory` — those failures already write `ai_call_log` rows with `success = false` (§14.2 "don't double-log").
3. **Exception to rule 2:** if the exception class is `AICallLogError`, the event ships. `AICallLogError` means the AI call succeeded but the audit INSERT failed — the audit pipeline itself is broken, which is exactly what Sentry exists to catch.

**Log-level convention.**

- `DEBUG` — dev-only state. Never enabled in production.
- `INFO` — meaningful state transitions: request start/end, JWT verified, cron fired, RLS-scoped query returned N rows.
- `WARNING` — recoverable bad state: rate limit hit, fallback path taken, retried call.
- `ERROR` — caught exception that doesn't crash the request. Always paired with `logger.exception(...)` so the traceback is in the record.
- `CRITICAL` — unused in v1.

**Out of scope at v1 scale.** No separate logging vendor (Datadog, Logtail, Better Stack); Railway stdout is enough. No log aggregation pipeline (Vector, Fluentbit); Railway already does this. No request/response body logging; privacy and cost both vote against it. No `structlog`; stdlib `logging` + the JSON formatter is simpler and a one-line swap if needed later.

---

## 15. Milestones

### Phase 1 — Core App + Portfolio Layer (4 weeks)

- **Week 1:** FastAPI + Supabase + Google OAuth + RLS via JWT + Gemini 3.1 Flash-Lite categorization + CSV bank import with batch categorization.
- **Week 2:** Full PWA UI + philosophy screen + guided tour + spending tracker + subscription manager + dashboard with baselines + entry-moment insight.
- **Week 3:** Claude agent chat (Messages API + `tool_use` + SSE streaming) + cross-session memory.
- **Week 4:** Eval harness + MCP server (per-user tokens) + AICallLog observability + Sentry + prompt versioning + weekly email digest + PostHog (structural events).

### Post-Phase 1 — optional, author-driven only

Because v1 Tameru is invite-only (§3.3), there is no committed Phase 2 or Phase 3. The following features may be added if the author wants them, in any order, with no scaling or pricing pressure:

- Card recommender (best card per category based on current wallet)
- Recurring subscription detection (suggest patterns as new subscriptions)
- Receipt photo → transaction (Gemini Vision)
- Proactive insights (AI-pushed alerts for overspending, missed rewards)
- Retroactive rewards gap analysis ("How many points did I miss last month?")
- Transfer bonus digest, SUB wishlist alerts
- Spending limits with AI nudges

Explicitly excluded at every phase: Plaid / Teller.io auto-sync, public launch / Product Hunt, Expo / React Native, in-app purchases via App Store IAP.

### Conditional future phase — Scaling to ~100 users (not committed)

If v1 runs successfully at the ~10-user invite scale and the author later decides to scale to ~100 users, a "Phase 1.5" scaling-readiness milestone activates before the invite link opens to a wider audience. The full punch-list — infra upgrades, Stripe billing (schema §8.7 + §8.10), Privacy Policy + ToS, account deletion, Anthropic ZDR + rate-limit increase, per-user cost dashboard, incident runbook — is **§17**, gated by the §17.13 checklist.

**This phase is triggered by an explicit decision, not by growth.** It is documented now so the shape is pre-agreed; nothing in it ships with v1.

Admitted as a *possible* post-scaling migration (not on the roadmap): native Swift iOS client (§10.4).

---

## 16. Open Items

These are explicitly acknowledged unknowns that the v1 build will resolve in code:

**v1 open items:**

- **Gemini 3.1 Flash-Lite is in preview** as of March 3, 2026 (`gemini-3.1-flash-lite-preview`). Preview models can change pricing, behavior, or be deprecated on short notice. Risk mitigation: model string is held in a single env var (`GEMINI_MODEL`) so we can fall back to `gemini-2.5-flash` (GA) instantly if Flash-Lite preview becomes unstable.
- **Flash-Lite A/B test for chat agent.** After Phase 1 launch, run the multi-hop eval suite (§7.10) against `gemini-3.1-flash-lite-preview` as an alternate chat model. If accuracy holds within 10% of Haiku, switch to save ~$18/month at the ~10-user v1 scale. The savings grow to ~$180/month *if* the conditional scaling-to-100 phase activates — so this A/B becomes a higher priority at that point.
- **Anthropic ZDR enrollment** — submit request before v1 launch (currently using default 30-day T&S retention).
- **Claude `web_search` org-enablement.** The web search tool must be turned on by the org admin in the Claude Console (Privacy settings) before `lookup_card` will function. One-time setup before Day 14 lands; verified via a smoke test in the Day 14 test suite. Fall back to manual entry if extraction confidence is low.
- **iOS PWA push opt-in rates in practice** — measure via PostHog `weekly_digest_opened` after Phase 1 launch. Email is the primary digest channel regardless. This measurement also feeds the §10.4 Swift-migration decision.

**Conditional open items — decide before activating the §17 scaling plan, not before v1 launch:**

- **Freemium gating rule for chat.** If/when a paid tier ships, the free tier will have some cap on AI chat usage and the paid tier will be higher or unlimited. Candidate rule: "3 turns/day free, paid unlocks the §11.2 daily cap." Enforcement lives in FastAPI middleware (§7.3), keyed on `users_meta.plan`; Stripe only determines which bucket a user is in.
- **Paid-tier price point.** Not committed. Must be set before the first paid-tier flow ships. Price is a product decision — no backend change required to move it later.
- **Business entity registration.** Sole proprietorship is fine for the invite-only v1 scope. Registration (LLC or similar) must precede the first paid flow for liability and Stripe KYC reasons. Target: complete concurrently with the §17 punch-list *if* scaling is chosen.
- **Anthropic rate-limit increase request.** Default 50 RPM / 200K TPM is sufficient for ~10 users. If the scaling decision is made, submit an increase request at least 2 weeks before opening the invite link.
- **Swift migration trigger criteria.** §10.4 lists the three signals. Exact thresholds (how many users asking, what retention delta, what revenue floor) are deferred until real signal arrives.

---

## 17. Forward Plan — Scaling to ~100 Users (conditional)

**This section is not v1 scope.** v1 ships invite-only to ~10 users with no paid tier, no Stripe, and no infrastructure upgrades (§3.3). This section documents the operational punch-list that would activate **if and only if** v1 is successful and the author later decides to open the product to a wider (~100-user) audience. Every bullet is either a build task, a configuration change, or a policy decision that must be closed *before* opening an invite link at that scale. Activation is an explicit choice — nothing here is automatic.

The intent of writing this down now is so future-self has a pre-reviewed plan and does not have to reason from scratch under pressure.

### 17.1 Decisions pre-agreed for this scale (applies only if activated)

| Decision | Choice | Rationale |
|---|---|---|
| Distribution | Open invite link (shareable URL) | Simpler than waitlist/codes at ~100 users. If abuse surfaces, fall back to signed invite codes — the signup endpoint is structured to swap gating cleanly. |
| Pricing | Free tier day 1; paid tier via Stripe on the web | Friends/family pay nothing; paid tier unlocks the chat quota. Exact gating rule and price point are open items (§16). |
| Billing surface | Stripe on the web, not IAP | Permanent stance. Applies even if a native iOS build later ships. The web checkout is the source of truth; native clients link out (Spotify/Netflix model). |
| Business entity | Sole prop now; register before first paid user | Forming an LLC (or equivalent) is required for Stripe KYC on the paid flow and for ToS liability language. Trigger: the day the paid tier goes live. |
| On-call | Solo, best-effort within 24 hours | Documented publicly in ToS. No pager rotation, no uptime SLA. Honesty about limitations is better than promises that can't be kept. |
| Mobile surface | PWA only | Swift migration is a post-100-users question per §10.4, not a scaling prerequisite. |

### 17.2 Schema changes for billing

See §8.7 for `users_meta` additions and §8.10 for the `stripe_events` idempotency table. One migration PR. Workflow per §12.

**Webhook handler responsibility:** the webhook endpoint runs with the Supabase service role (invariant: no user JWT is in scope for a Stripe-initiated request — this is the *third* sanctioned service-role caller alongside `pg_cron` and CLI migrations; CLAUDE.md invariant #1 must be updated to admit this). It looks up the user by `stripe_customer_id`, updates `plan` / `stripe_subscription_status` / `stripe_current_period_end` on `users_meta`, and records the event ID in `stripe_events`. All writes are idempotent against the `stripe_events.event_id` primary key.

### 17.3 Infrastructure

- [ ] **Railway Hobby → Starter (~$20/mo).** Removes sleep-on-inactivity; raises RAM. Hobby's idle sleep would kill SSE streams within seconds of inactivity.
- [ ] **Supabase Free → Pro ($25/mo).** Mandatory. Free tier caps at 500 MAU and 500MB DB, but also omits PITR, PgBouncer, and daily backups — which are the load-bearing upgrades, not the MAU cap.
- [ ] **Enable PgBouncer (transaction-mode pooling)** on Supabase Pro. Per-request JWT clients (§9.1) at 100 concurrent users can exhaust Postgres's direct connection pool. Transaction-mode pooling multiplexes safely.
- [ ] **Verify `terminationGracePeriodSeconds = 60`** on Railway. Documented in §14.4; confirm the Railway YAML reflects it.
- [ ] **Confirm `/healthz` endpoint** exists and returns in <100ms without DB access.
- [ ] **UptimeRobot (free tier)** on `/healthz`, pinging every 5 minutes, with alerts routed to a channel the author actually reads daily (personal phone, Discord DM — not a buried inbox).

### 17.4 Database

- [ ] **Index audit** — confirm indexes exist on every frequent query pattern:
  - `transactions(user_id, date)` (dashboard, chat time-range queries)
  - `transactions(user_id, category)` (category rollups)
  - `transactions(user_id, card_id)` (per-card analysis)
  - `transactions(subscription_id, date)` (already covered by the UNIQUE constraint in §8.2)
  - `subscriptions(user_id, status)`
  - `merchant_category(user_id, merchant)` (already UNIQUE per §8.4)
  - `ai_call_log(user_id, timestamp)` (cost dashboard, rate-limit checks)
- [ ] **RLS load test.** Seed ~100K rows across ~100 synthetic users. `EXPLAIN ANALYZE` the five most frequent API queries and verify the `auth.uid() = user_id` check does not dominate plan cost. RLS policies that look fine at 10 users can add 10–100× cost under load if an index is missing.
- [ ] **`ai_call_log` growth check.** At 100 users × 5 turns/day × 3 hops ≈ 1,500 rows/day; 90-day hot window ≈ 135K rows. Verify the `pg_cron` aggregator (§14.1) runs daily and prunes. Add a heartbeat row that the job updates; alert if stale > 26 hours.
- [ ] **PITR restore drill.** Restore a Supabase snapshot into a scratch project *before* production depends on it. Uncaught errors in the restore path have a habit of surfacing only when you need them to work.

### 17.5 API & Backend

- [ ] **Rate limit every endpoint**, not only `/chat`. Auth, export, CSV upload, MCP — all are abuse surfaces. Per-user and per-IP limits.
- [ ] **Verify per-user daily Claude token cap (§11.2) is enforced in code**, not only documented. Add an integration test that exercises the 429 path.
- [ ] **Anthropic rate-limit increase request.** Default 50 RPM / 200K TPM is tight at 100 active users chatting concurrently. Submit at least 2 weeks before the invite link opens.
- [ ] **Verify 429 backoff** (§7.3) is actually wired in the agent loop, not just a comment.
- [ ] **Server-side CSV upload limits.** Reject files above a size or row-count threshold *before* parsing. Prevents a malicious 100MB upload from pinning a FastAPI worker.
- [ ] **Pydantic validation on every NL-entry / merchant field.** No bare `str` inputs reaching the DB. Closes SQL injection surface even with RLS.
- [ ] **SSE concurrency budget.** 100 users holding 100 open streams on one Railway instance: measure memory. If it doesn't hold, add a connection cap and reject overflow with a retry-after message.
- [ ] **Request timeout on every AI provider call.** Anthropic (chat + web_search card-lookup path), Gemini — all. A hung upstream must not block a FastAPI worker indefinitely.
- [ ] **Stripe webhook handler** with signature verification, idempotent against `stripe_events.event_id`. Test end-to-end: create a Stripe test subscription, observe `users_meta.plan` flip to `paid`; trigger a test `invoice.payment_failed`, observe graceful degradation.

### 17.6 AI cost controls

The shape of the problem: §11.6 shows Claude chat is ~$270/mo at 100 users with no gating — that's the dominant line item by ~10×. Mitigations, in priority order:

- [ ] **Commit to a freemium gating rule.** Candidate default: 3 chat turns per day on free tier; paid tier uses the §11.2 daily cap as the ceiling. Alternative: N messages/week. Exact numbers are an open item (§16). Enforcement is in FastAPI middleware (same place as the §11.2 daily cap), keyed on `users_meta.plan`.
- [ ] **Per-user cost dashboard.** A single SQL query against `ai_call_log` + `ai_call_log_daily` that returns "top 10 users by AI spend this week." Needed *before* 100 users, not after a surprise bill. A Supabase SQL view is sufficient; Metabase is overkill at this scale.
- [ ] **Prioritize the Flash-Lite A/B for chat (§16 open item).** At 10 users the savings was ~$18/mo — not worth prioritizing. At 100 users it's ~$180/mo — worth a week of eval-suite work. Re-evaluation criterion: multi-hop eval accuracy within 10% of Haiku's.
- [ ] **Cost alert.** If daily AI spend crosses a configured threshold, alert the author. Catches runaway usage that slips past the per-user cap (e.g., a new model pricing surprise).

### 17.7 Security & Auth

- [ ] **API key rotation plan.** One-page doc: which keys (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `RESEND_API_KEY`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `SENTRY_DSN`), how to rotate each without full redeploy (Railway env-var hot-update where supported), rotation cadence (quarterly baseline; immediately on any suspected leak).
- [ ] **RLS contract tests (§13.1) passing in CI.** Blocker. A red RLS test cannot ship.
- [ ] **Supabase brute-force protection enabled** on auth endpoints. Enabled in Supabase dashboard; verify it's on.
- [ ] **Service-role key audit.** Grep the codebase. Confirm no request handler imports `SUPABASE_SERVICE_ROLE_KEY`. The CI lint from §9.1 enforces this; confirm it's configured and fails the build on violation. The Stripe webhook handler is now a sanctioned third caller (§17.2) — update the lint's allowlist accordingly.
- [ ] **MCP token revocation tested end-to-end.** User revokes a token; next MCP request with that bearer fails with a clean 401, not a 500.
- [ ] **`gitleaks` scan green** before the repo goes public (already in the §9.2 pipeline from Day 1 scaffold; verify it's still running).

### 17.8 Compliance

At ~100 users across US multi-state, Taiwan, and Japan, most statutory thresholds (CCPA's 100K-resident floor, GDPR territorial scope, etc.) are not crossed. But the baseline below is non-negotiable because (a) paid billing changes the legal posture, (b) international users expect transparent privacy practices, and (c) any future App Store submission requires most of this up front.

- [ ] **Privacy Policy publicly hosted.** Lists every sub-processor (Anthropic, Google, Supabase, PostHog, Sentry, Resend, Stripe). Discloses cross-border transfer to US servers. Names the data categories (transaction data, auth data, product usage events).
- [ ] **Terms of Service publicly posted.** Includes liability language appropriate to the business entity once registered; includes the solo-dev best-effort response SLA.
- [ ] **Account deletion endpoint**, tested end-to-end. Deleting an `auth.users` row cascades through every user-owned table per §8 FK definitions; verify no orphan rows remain. Accessible from in-app Settings — not only by emailing support.
- [ ] **Explicit consent at signup.** Checkbox (not pre-checked, not implied) for the sub-processor list and cross-border transfer. Record consent timestamp on `users_meta`.
- [ ] **Anthropic ZDR enrolled** (§16). Close before the invite link opens.
- [ ] **Google Gemini DPA on file.** Paid tier is already required; confirm DPA is signed, not just implied.
- [ ] **Data export endpoint** (§9.6) verified working at a realistic data size.
- [ ] **User-facing "data we store about you" screen** in Settings. Lists sub-processors, links to Privacy Policy, links to deletion + export flows. Cheap to build, high trust dividend.

### 17.9 Reliability & runbooks

- [ ] **`pg_cron` missed-run alerting.** The subscription auto-logger silently skipping is the highest-cost silent failure mode — users don't notice until month-end. Add a heartbeat row that the cron updates; alert if stale > 26 hours.
- [ ] **Sentry alerts** on 5xx spike and auth-failure spike. Thresholds set conservatively and tuned after a week of baseline traffic.
- [ ] **Per-provider outage behavior** decided and implemented:
  - Anthropic down → chat returns a user-visible "AI is temporarily unavailable, please try again in a few minutes." Not a silent hang.
  - Gemini down → categorization falls back to "Uncategorized" with a hint that the user can set it manually. Saves continue.
  - Anthropic web_search rate-limited or `unavailable` → card add falls back to manual multiplier entry (`needs_manual: true` on the proposal). Anthropic-wide outage already covered by the chat-down message above; the card-add surface degrades the same way.
  - Stripe down → no checkout, show the user a polite retry message.
- [ ] **Gemini 3.1 Flash-Lite preview → 2.5 Flash fallback tested** via the `GEMINI_MODEL` env-var swap in a staging deploy, not just documented.
- [ ] **SSE reconnect tested under a real Railway redeploy**, not only a local simulation.
- [ ] **Incident response runbook.** One page per failure mode (Railway down, Supabase down, Anthropic down, `pg_cron` stalled, Stripe webhook failing, key rotation emergency). "What do I do in the first 15 minutes" bullets. Written in prose the author can actually follow at 2am.

### 17.10 Observability

- [ ] **Per-user AI cost view.** Supabase SQL view over `ai_call_log` + `ai_call_log_daily`. Queryable in the dashboard.
- [ ] **PostHog onboarding funnel.** Measure drop-off: philosophy screen viewed → first transaction logged → first chat turn → returned day 7. Drop-off between steps is the first signal to investigate at 100 users.
- [ ] **Slow query logging enabled** on Supabase. Review weekly during the first month at scale; fix anything slower than 1s.
- [ ] **Sentry error volume review and scrub.** Known-noise errors become a 10× noise floor at 10× volume — fix or ignore them explicitly before scaling.

### 17.11 User-facing

- [ ] **In-app feedback channel.** Pick one and link it: Discord invite, email form, in-app report button. Single source so the author actually sees everything.
- [ ] **Support email publicly listed** in ToS and in-app Settings.
- [ ] **Welcome email sequence** (Resend): day 0 welcome, day 1 nudge to first transaction, day 7 nudge to try the chat. Three emails, no more.
- [ ] **Communication channel for outages.** Status page OR a single designated Discord/X/email channel where outage updates post. Solo-dev is allowed to skip a formal status page as long as users know where to look.
- [ ] **In-app "Delete my account" button** (not email-only). Required for GDPR-adjacent regimes; required by Apple if a native build ever ships. Cheaper to build now than backfill later.
- [ ] **Plan / billing UI.** Settings → Billing shows current plan, period end, invoices, and a "Manage subscription" link to Stripe's customer portal (cheapest path; no custom UI needed).

### 17.12 Biggest risks at this scale, specifically

1. **AI cost explosion.** ~10× jump in Claude chat spend. Mitigation: freemium gating + daily cap. Without both, one runaway user can 10× the bill in a day.
2. **Supabase Free-tier hard caps hit mid-onboarding.** Must upgrade to Pro *before* opening the invite link, not after hitting the limit. Cutover is instant but planning it into a launch day adds unnecessary risk.
3. **Anthropic rate-limit throttling.** Default 50 RPM is not enough headroom for 100 users. Request the increase at least 2 weeks before launch — Anthropic's response time is variable.
4. **RLS under real load.** Policies that are free at 10 users add per-query cost that's invisible at low row counts. Load-test before trusting it.
5. **Compliance stops being optional.** Privacy Policy, deletion endpoint, explicit consent, and ToS move from "nice to have" to legally required the moment there are international users plus paid billing.
6. **Stripe webhook reliability.** A missed webhook is a silent billing desync. Idempotency (§8.10) is the safety net; so is a periodic reconciliation job that compares Stripe subscription state against `users_meta`. The reconciliation job is nice-to-have at 100 users but worth a backlog note.

### 17.13 Go / no-go checklist (only if scaling is activated)

Minimum set before flipping a ~100-user invite link live. None of this applies to the v1 ~10-user launch:

- [ ] Railway Starter + Supabase Pro active.
- [ ] PgBouncer enabled.
- [ ] Privacy Policy + Terms of Service live and linked from signup.
- [ ] Account deletion endpoint tested, cascades verified.
- [ ] Stripe integration tested end-to-end (new subscription, successful renewal, payment failure, cancellation, refund).
- [ ] RLS contract tests green in CI.
- [ ] `/healthz` + UptimeRobot configured with alerts to the author.
- [ ] Per-user AI cost view queryable.
- [ ] Anthropic rate-limit increase approved.
- [ ] Anthropic ZDR enrolled.
- [ ] Freemium gating rule committed and enforced in middleware.
- [ ] Incident response runbook written.
- [ ] Business entity registered (if paid tier ships concurrently with invite link).

If any box is red, the invite link waits.

---

— End of design document — Tameru v3.1 · Chris Yu · April 2026
