# Tameru — Design Document

**Spending Intelligence, Powered by AI**

| | |
|---|---|
| Author | Chris Yu |
| Date | April 2026 |
| Version | 3.0 (revised from PRD v2.1) |
| Status | Approved — implementation |
| Stack | React PWA · FastAPI · Supabase · Anthropic API · Gemini API · Perplexity API · PostHog |
| Domain | tameru.app (candidate) |

This document supersedes PRD v2.1. Material changes from v2.1 are summarized in §0.

---

## 0. Changes from PRD v2.1

| Change | Reason |
|---|---|
| Drop Expo entirely; PWA only across all phases | Scope reduction. iOS web push limitations are accepted and disclosed. |
| Replace "Claude Managed Agents" framing with **Messages API + `tool_use`** via the `anthropic` Python SDK | Managed Agents runs in Anthropic's cloud and is designed for long-running autonomous tasks. Tameru chat turns are 4–6 seconds with typed DB-backed tools — wrong fit. The agent loop runs in FastAPI so the user's JWT is in scope when tools execute (RLS fires correctly). |
| Card multiplier lookup uses **Perplexity Sonar** | One API call replaces "search + Gemini-parse-results" pipeline. Citations included. Called once per card add — vendor cost is negligible. |
| All Gemini calls use **`gemini-3.1-flash-lite-preview`** | Author decision to move off 2.5. Flash-Lite (not Pro) is the right variant — it's the direct successor to 2.5 Flash for "high-volume, cost-sensitive LLM traffic" per Google's own positioning, supports vision and grounding, and avoids paying for Pro's reasoning capacity on simple extraction tasks. Note: still in **preview** as of March 2026; see §16 Open Items for the stability risk. Model string is configurable via env var. |
| Replace live "demo mode" with a **4-screen guided tour** | Static screens with hardcoded fixture data. Eliminates the question "how does AI chat work on fake data," removes the risk of demo data leaking into Supabase, and is buildable in a day. |
| RLS is enforced by passing the user's JWT to a per-request Supabase client | Service role bypasses RLS. Per-request clients with the user JWT cause Postgres to enforce `auth.uid() = user_id` on every query. The service role is reserved for migrations and the daily auto-logger. |
| MCP server uses **per-user bearer tokens, read-only** | Per-user tokens scope queries to one user. Read-only eliminates the "leaked token = data corruption" risk. |
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
- Multi-currency or international card support.

### 3.3 Scope limit — invite-only

Tameru is an invite-only project. No public launch. No paid tier. No Stripe. No Plaid or Teller.io. Expected user count: ~10 close friends and family, never more than a few dozen. Feature additions beyond Phase 1 are built only if the author wants them, not because growth demands them. Cost ceiling is low and bounded (§11.2 daily cap).

### 3.4 Deferred (nice-to-have, not committed)

- Transfer bonus digest, SUB wishlist alerts.
- Card recommender, proactive insights, recurring detection, receipt photo via Gemini Vision, retroactive rewards gap analysis.
- Native iOS/Android via Expo — **dropped from roadmap.** PWA covers the mobile case; iOS web push limitations are accepted.

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
| Backend | FastAPI (Python) | Async, type-safe, Python for Gemini/Anthropic/Perplexity SDKs |
| Database | Supabase (Postgres) | Hosted Postgres + Auth + RLS + dashboard |
| Auth | Supabase Auth | Google OAuth + magic link; RLS enforced at DB |
| AI — high frequency | Gemini 3.1 Flash-Lite (preview) — `gemini-3.1-flash-lite-preview` | Categorization, NL parse, receipt extraction, CSV parse. Cost-optimized variant; matches 2.5 Flash quality per Google. |
| AI — card lookup | Perplexity Sonar | Web-grounded card multiplier lookup with citations |
| AI — agent | Claude Haiku 4.5 / Sonnet 4.6 | Tool use, multi-step reasoning, narrative |
| Agent runtime | `anthropic` Python SDK — Messages API with `tool_use` blocks. Loop runs in FastAPI. | Custom typed tools, JWT in scope for RLS, full control over middleware (logging, rate limit backoff, cost gating) |
| MCP | `mcp` Python SDK (HTTP+SSE transport) | Exposes spending data to Claude.ai / Claude Code |
| Streaming | FastAPI SSE + EventSource | Token-by-token Claude responses |
| Hosting | Railway | Persistent FastAPI service, GitHub-native CI/CD |
| Cron | Postgres `pg_cron` (Supabase) | Daily subscription auto-logger; survives API deploys |
| Observability | `AICallLog` (Postgres) + Sentry (free tier) | AI calls in `AICallLog`; non-AI exceptions in Sentry |
| Product analytics | PostHog | Structural events only — no question text, no financial data |
| Email | Resend | Weekly digest |

### 5.2 Why Supabase over SQLite

Multi-user write concurrency, RLS at the DB layer, built-in auth, hosted backups, and the dashboard all come for free. Migration cost from SQLite later would be high. Free tier handles Phase 1 and Phase 2 entirely.

### 5.3 Why Railway over Vercel

Vercel is frontend-first; FastAPI on Vercel runs as serverless functions, which breaks three things:

- Persistent processes — needed for SSE streaming.
- Cold starts — make token-by-token streaming unreliable.
- Long-running agent loops — managed agent loops can take 4–6 seconds; serverless timeouts are aggressive.

Railway runs FastAPI as a persistent service from a Dockerfile or Procfile. ~$10/month. CI/CD from GitHub.

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

Edge cases: duplicate detection (date + merchant + amount); unknown column schema → manual mapping UI. CSV import is also available post-onboarding under Settings → Import Data.

---

## 6. Features

| Feature | Phase | Priority |
|---|---|---|
| Philosophy screen + guided tour | 1 | P0 |
| CSV bank import (Gemini batch categorization) | 1 | P0 |
| Card management (Perplexity multiplier lookup) | 1 | P0 |
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

### 6.1 Card Management — Perplexity Lookup

Users add cards by name. Perplexity Sonar fetches category multipliers and rewards program in one API call with citations.

**Add card flow:**

1. User types a card name (e.g., "Chase Sapphire Reserve").
2. Backend calls Perplexity Sonar: "What are the current category multipliers, rewards program, and annual fee for the Chase Sapphire Reserve credit card? Return as structured JSON."
3. Perplexity returns answer with citations.
4. Backend parses the JSON. User reviews, edits any incorrect values, confirms. Card saved to wallet with citation URLs preserved as `card.source_urls`.

Why Perplexity: search + extraction in one API call. Citations included. Cost is negligible (≤10 calls per user lifetime). Eliminates separate search-API integration.

If Perplexity returns ambiguous or low-confidence results, fall back to manual entry — the user types multipliers themselves with a category picker.

### 6.2 Spending Tracker

Manual entry is intentional. The 10-second friction of logging a purchase builds awareness that is itself valuable.

**Entry flow:**

1. Tap **+** on home screen.
2. Enter merchant, amount, date (defaults to today), card.
3. Gemini suggests a category. User confirms in one tap or selects from dropdown.
4. Transaction saved. If offline, queued in IndexedDB; synced on reconnect.

#### Dashboard — design philosophy

Dashboards with many charts create a false sense of progress without changing behavior. A user who sees their dining spend is 50% of their budget does not eat out less. Tameru's dashboard is deliberately minimal — it surfaces one number that matters, not a gallery of charts to explore.

**Default view (must fit on one screen, no scrolling):**

- One headline number: this month's total vs. 3-month baseline. Color-coded.
- 4–5 category tiles showing **delta vs. baseline only** — "Dining: +$47 above average," not "Dining: $327." The delta is the signal.
- One AI prompt at the bottom: "Ask me about your spending."
- No 6-month bar chart. No toggles. No drilling by default. Historical analysis lives in the AI chat.

**Pie chart (secondary view):** accessible via "Breakdown" tab or tap on any category tile. Recharts (SVG only).

#### Entry-Moment Insight — primary behavioral intervention

Immediately after a transaction is saved, replace the standard confirmation toast with **one** contextual sentence. Disappears after 3 seconds. No tap. No action.

- "4th dining transaction this week — you usually have 2."
- "This puts you $23 above your monthly dining average with 12 days left."
- "You've used Chase Freedom for dining 3 times this week — Amex Gold earns 4x there."

Rules: one sentence max. No numbers beyond one. No buttons. Auto-dismiss at 3s. Not shown when there's nothing meaningful to surface (e.g., first transaction in a category). Framed as a delta vs. baseline — never as an absolute number.

#### AI Chat — power-user escape valve

Anything not on the dashboard lives in the chat. Generative charts, trend analysis, what-if scenarios, all on demand.

#### Transaction list UX

- Paginated, 50 rows at a time, infinite scroll.
- Search bar (substring match on merchant).
- Filter chips: card, category, date range.
- Tap row to edit. Long-press / swipe-left → delete confirmation.

### 6.3 Baseline Comparisons

Every dashboard metric is shown relative to the user's own personal baseline.

- Window: trailing 3-month average, computed per category.
- Color-coded delta: green (below), neutral (within 10%), amber (10–25% above), red (25%+ above).
- AI chat uses the same baseline automatically.
- New users (<3 months of data): "Baselines will appear after 3 months of data. Keep logging!"

### 6.4 Weekly Digest

The primary delivery mechanism for spending insight. Reaches users who don't open the app.

**Delivery:** Email via Resend, every Monday morning.

**Content (≤5 lines):**

- Total spend last week vs. weekly average.
- Top category and whether above or below baseline.
- One AI-generated observation.
- One nudge if applicable (rewards or category-related).

If it takes more than 15 seconds to read, it's too long.

### 6.5 Subscription Manager

Subscriptions are auto-logged on their billing schedule by a `pg_cron` job — fully isolated from the API service.

**User flow:**

1. User adds subscription: name, card, amount, frequency (monthly/quarterly/annual/weekly), start_date, category.
2. Backend computes `next_billing_date` from start_date + frequency.
3. `pg_cron` runs daily; SQL function inserts a Transaction for any subscription with `next_billing_date <= today` and advances `next_billing_date` by one period.
4. User can pause (stop temporarily) or cancel (stop permanently).

**Idempotency:**

- `UNIQUE (subscription_id, date) WHERE subscription_id IS NOT NULL` on `transactions`.
- Insert uses `ON CONFLICT DO NOTHING`.
- `next_billing_date` advances only after successful insert, in the same transaction.
- The cron function wraps execution in `pg_try_advisory_lock` to prevent concurrent runs.

Auto-logged transactions appear in the main list tagged with a 🔄 icon. The AI chat is the primary management interface ("Add Netflix $15.99 monthly on my Amex Gold" creates the subscription conversationally).

---

## 7. AI Architecture

**Design principle:** AI is the primary interface, not a feature bolted on. Screens and charts exist for at-a-glance visibility. The AI handles everything else.

### 7.1 Claude Agent — Messages API with `tool_use`

The chat agent uses the Anthropic Messages API directly via the `anthropic` Python SDK. The agent loop runs **in the FastAPI process**. No managed agent service.

**Why not Claude Managed Agents:**

Managed Agents (`/v1/sessions`, `/v1/agents`) is Anthropic's hosted harness for **long-running autonomous tasks** — minutes-to-hours work with bash, file, and web tools running in an Anthropic-provisioned container. Tameru's chat turns are 4–6 seconds with typed DB-backed tools. Managed Agents would force every tool call to round-trip through MCP from Anthropic's container to Tameru's MCP server, adding latency and complicating the per-user JWT path. Per Anthropic's own positioning ("custom agent loops and fine-grained control" → Messages API; "long-running tasks and asynchronous work" → Managed Agents), Tameru is the former.

**Why not LangChain:** abstracts away exactly the mechanics that benefit from being explicit (`tool_use`/`tool_result` block protocol, streaming, system prompt injection). Adds a dependency and a moving target without solving any concrete Tameru problem.

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

| Approach | Pros | Cons |
|---|---|---|
| **Typed tools (Tameru)** | Safe — Claude can't generate bad SQL. Testable. Evaluable. No injection surface. | Must anticipate query patterns upfront. |
| Raw `run_sql(sql)` | Flexible — handles arbitrary questions. | Silent failures. Injection surface. Hard to eval. |

Phase 1 uses typed tools only. A read-only `run_query(sql)` tool may be added in Phase 2 against a Postgres read replica.

**Tool definitions (Phase 1):**

- `get_transactions({ category?, card_id?, date_from?, date_to?, limit? }) → Transaction[]`
- `calculate_total({ category?, card_id?, date_from?, date_to? }) → { total, count }`
- `get_subscriptions({ status? }) → Subscription[]`
- `add_transaction({ merchant, amount, date, card_id, category, notes? }) → Transaction`
- `get_spending_summary({ months? }) → CategoryBreakdown[]`
- `get_cards() → Card[]`
- `set_goal({ category?, amount, period }) → Goal`

**Example trace:** "How much more did I spend on dining in March vs February?"
→ `calculate_total(category="Dining", date_from="2026-03-01", date_to="2026-03-31")`
→ `calculate_total(category="Dining", date_from="2026-02-01", date_to="2026-02-28")`
→ Computes delta, responds in prose. Both tool calls logged to `AICallLog`.

### 7.2.1 Context window — Haiku 4.5's 200K is sufficient

Per the token math (§11.1), peak per-turn input is ~5,260 tokens at hop 4 of a 3-tool chain. Haiku 4.5's 200K context window leaves ~38× headroom — not a constraint at any expected usage pattern.

The two ways context could blow up are both already bounded by design:

- **Conversation history** is capped at the last 5 turns. Older turns are summarized into `user_memory` (§7.6) and pruned. A 50-turn marathon session does not balloon the context.
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

### 7.4 Why Claude for Agent, Gemini for Categorization, Perplexity for Card Lookup

| Task | Model | Reason |
|---|---|---|
| Category inference | Gemini 3.1 Flash-Lite | High-frequency, sub-cent cost, speed > quality |
| Receipt photo parsing | Gemini 3.1 Flash-Lite (multimodal) | Image input supported up to 3,000/prompt |
| CSV header + batch parse | Gemini 3.1 Flash-Lite | Unstructured → structured, batched |
| NL transaction parse | Gemini 3.1 Flash-Lite | Structured field extraction |
| Card multiplier lookup | Perplexity Sonar | Web-grounded, citations, single API call |
| Chat agent | Claude Haiku 4.5 | Multi-step typed-tool reasoning. Public agentic data point: AIME 2025 with tools 96.3% (+16 vs no-tools). Gemini Flash-Lite considered and rejected — see §11.4 for full rationale. |
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
- Capacity cap: hard limit of 60 facts. Claude Haiku scores each fact by recency × relevance and drops the lowest-scoring when over capacity.
- User control: "Show what you remember about me" lists all stored facts. "Forget that I'm planning a trip to Japan" deletes one. Full panel in Settings.

**Why not a vector DB:** at most 60 structured facts. A JSON array in Postgres is faster, simpler, and more debuggable than a vector store. Add a vector DB only if cross-session retrieval ever needs semantic search over long transcripts.

### 7.7 Natural Language Transaction Entry (text + voice)

The user can describe a transaction by typing or speaking. Both inputs flow through the same Gemini parse path.

**Examples:**

- "Spent $47 at Trader Joe's on my Amex Gold just now" → Gemini parses merchant, amount, date, card, category.
- "Lunch at Nobu, $85, split with a friend so my half was $42, CSR" → parses split amount.
- Ambiguous input falls back to a pre-filled form with missing fields highlighted.

**Text trigger:** parse fires on **submit or blur**, not on keystroke debounce. One Gemini call per entry, predictable cost.

**Voice trigger:**

- Tap mic → Web Speech API (`window.SpeechRecognition` / `webkitSpeechRecognition`) starts on-device speech recognition.
- Browser shows a live transcript as the user speaks. Stop button or 1.5s of silence ends recording.
- Final transcript is treated identically to typed input — same `parse_nl_entry()` call, same confirm screen.
- Web Speech API is used (not Gemini audio input) because: it's free, runs on-device (transcription audio never leaves the user's phone — privacy bonus), works in iOS Safari 14.5+ and all major desktop browsers, and the audio quality of short transaction utterances is well within its capability.
- If `SpeechRecognition` is unavailable (rare in 2026, but possible on older browsers), the mic button is hidden and the user is told voice isn't supported in their browser. No silent failures.

**Cost:** voice transcription is free (browser-native). The downstream Gemini parse call is the same one a typed entry triggers — no incremental cost from voice as input mode.

### 7.8 Generative Charts

- "Chart my grocery spending by week in March" → line chart, weekly buckets.
- "Compare dining vs travel over the last 6 months" → grouped bar.
- "Show me which card I use most by category" → stacked bar or heatmap.

Claude determines chart type, grouping, and time range from the natural language request. Recharts renders inline in the chat thread.

### 7.9 MCP Server — Per-User Tokens

Tameru exposes an MCP server (HTTP+SSE transport) at `https://tameru.app/mcp`. Claude.ai, Claude Code, and any MCP-compatible client can query a user's spending data with their authorization.

**Exposed tools (read-only in v1):**

- `get_spending_summary(date_from, date_to)`
- `get_recent_transactions(limit, category?)`
- `get_subscriptions()`
- `get_card_multipliers(card_name?)`

**Auth model:**

1. User goes to **Settings → Integrations → Connect to Claude.ai**.
2. Server generates a 32-byte random token, stores `sha256(token)` in `mcp_tokens(id, user_id, token_hash, name, created_at, last_used_at, revoked_at)`, returns plaintext **once**. Display copy-paste UI.
3. User configures Claude.ai (or Claude Code) with `Authorization: Bearer tameru_<token>` header pointing at the MCP URL.
4. On each MCP request, server hashes the bearer token, looks up `user_id`, updates `last_used_at`, scopes all queries to that user.
5. Revocation: user clicks "Revoke" in Settings (`UPDATE mcp_tokens SET revoked_at = now()`).

**Read-only by design.** No `add_transaction` over MCP in v1. A leaked token (screenshot, GitHub commit) can read data; it cannot mutate it. Write tools may be added in Phase 2 with per-token scopes.

### 7.10 Eval Harness

Automated test suite measuring AI accuracy across the three highest-stakes tasks. Run via `python eval.py`. Results in `evals/results.db` (SQLite, in-repo).

**Eval 1 — categorization accuracy.** 100 hand-curated `(merchant, amount) → category` pairs. Target ≥ 90%. Per-category precision/recall tracked.

**Eval 2 — NL parse accuracy.** 50 hand-curated NL strings → `(merchant, amount, date, card)`. Target: amount ≥ 95%, merchant ≥ 90%.

**Eval 3 — multi-hop tool-use accuracy.** 20 hand-curated chat prompts that require 2+ chained tool calls (e.g., "How much more did I spend on dining in March vs February?"). Each row has the prompt + the expected sequence of tool calls (name + key arguments) + an acceptable final-answer pattern. Scored by:
- **Tool sequence correctness** (did the agent call the right tools in a sensible order?). Target ≥ 90%.
- **Final answer correctness** (is the prose answer numerically right within ±$1?). Target ≥ 95%.

This eval exists specifically so the Haiku-vs-Flash-Lite (or future model) decision can be made on Tameru's actual tool surface, not on vendor benchmarks.

**Storage:** `evals/categorization.yaml`, `evals/nl_parse.yaml`, `evals/multi_hop.yaml` in repo. Version controlled.

**Run trigger:** locally on demand; CI on PRs that touch `app/prompts/` or `app/agent/`. Not on every commit (token cost).

**Regression gate:** PR cannot merge if categorization drops below 88%, NL parse amount below 93%, or multi-hop tool sequence below 85%.

**Refresh cadence:** monthly job mines `MerchantCategory` corrections for new categorization rows. Multi-hop suite refreshed manually when new tools are added or new question patterns appear in real chat logs.

**Model A/B usage:** `python eval.py --model gemini-3.1-flash-lite-preview` runs the same suite against an alternate chat model. Results stored in `results.db` per-model so cross-model comparisons are first-class.

---

## 8. Data Models

User-owned tables (`cards`, `transactions`, `subscriptions`, `merchant_category`, `user_memory`, `mcp_tokens`, `users_meta`) include `user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE`. The audit tables `ai_call_log` and `ai_call_log_daily` differ — see §8.8 and §8.9.

Every table has:

- `ENABLE ROW LEVEL SECURITY` **and** `FORCE ROW LEVEL SECURITY` — FORCE closes the table-owner bypass.
- A single `FOR ALL` policy on user-owned tables: `USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid())`.
- `SELECT`-only policy on audit tables (§8.8, §8.9) — all writes go through the service role.

Enum-like text fields carry `CHECK` constraints enforcing the allowed values listed in each section's description column.

### 8.1 `cards`

| Field | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| user_id | UUID | FK → auth.users |
| name | text | "Chase Sapphire Reserve" |
| issuer | text | Chase, Amex, Citi, Bilt |
| program | text | UR / MR / TYP / Bilt / Other |
| multipliers | JSONB | `{"Dining": 4, "Groceries": 4}` |
| annual_fee | numeric | USD; informational |
| last_four | text | UI identification |
| color | text | Hex for UI card display |
| source_urls | text[] | Perplexity citations |
| active | boolean | Soft delete |
| created_at | timestamptz | |

### 8.2 `transactions`

| Field | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| user_id | UUID | FK → auth.users |
| card_id | UUID | FK → cards (nullable) |
| subscription_id | UUID | FK → subscriptions (nullable) |
| merchant | text | As entered or parsed |
| amount | numeric | USD |
| date | date | Transaction date |
| category | text | User-confirmed or auto-assigned |
| gemini_suggestion | text | Raw suggestion before user confirm |
| source | text | manual \| nlp \| receipt_photo \| auto_logged \| csv_import |
| notes | text | Optional |
| created_at | timestamptz | |
| updated_at | timestamptz | Used for offline sync conflict resolution |

**Constraint:** `UNIQUE (subscription_id, date) WHERE subscription_id IS NOT NULL` — guarantees subscription idempotency.

### 8.3 `subscriptions`

| Field | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| user_id | UUID | FK → auth.users |
| card_id | UUID | FK → cards; `ON DELETE CASCADE`. A subscription without a card to charge is meaningless, and `RESTRICT` here would deadlock account deletion (both `cards` and `subscriptions` cascade from `auth.users`; Postgres does not guarantee sibling-cascade order, and RESTRICT is checked immediately) |
| name | text | "Disney+", "Netflix" |
| amount | numeric | Fixed billing |
| frequency | text | monthly \| quarterly \| annual \| weekly |
| start_date | date | First billing |
| next_billing_date | date | Computed next auto-log date |
| category | text | Default category for auto-logged tx |
| status | text | active \| paused \| cancelled |
| created_at | timestamptz | |

### 8.4 `merchant_category` (merchant memory)

Powers the "past corrections" field in the Gemini categorization prompt. Most recent correction wins.

| Field | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| user_id | UUID | FK → auth.users |
| merchant | text | Normalized (lowercased, trimmed) |
| category | text | User-confirmed category |
| updated_at | timestamptz | |

**Constraint:** `UNIQUE (user_id, merchant)`.

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

### 8.6 `mcp_tokens`

| Field | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| user_id | UUID | FK → auth.users |
| token_hash | text | SHA-256 of plaintext token |
| name | text | User-supplied label, e.g. "Claude.ai laptop" |
| created_at | timestamptz | |
| last_used_at | timestamptz | |
| revoked_at | timestamptz | NULL = active |

**Constraint:** `UNIQUE (token_hash)`.

### 8.7 `users_meta`

| Field | Type | Description |
|---|---|---|
| user_id | UUID | PK / FK → auth.users |
| active_device_id | text | Most recently signed-in device |
| analytics_opted_out | boolean | Default false |
| created_at | timestamptz | |

### 8.8 `ai_call_log`

Append-only audit log of every Gemini, Claude, and Perplexity API call.

| Field | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| user_id | UUID | Nullable for system-level calls; `REFERENCES auth.users(id) ON DELETE SET NULL` to preserve audit history after account deletion |
| timestamp | timestamptz | |
| provider | text | `anthropic` \| `google` \| `perplexity` |
| model | text | `claude-haiku-4-5` \| `gemini-3.1-flash-lite-preview` \| `sonar` \| ... |
| task_type | text | `categorization` \| `nl_parse` \| `chat_turn` \| `memory_distill` \| `card_lookup` \| `receipt_parse` \| `csv_import` \| `digest` |
| prompt_version | text | e.g. `categorize_v3` |
| prompt_hash | text | SHA-256 of rendered system prompt |
| input_tokens | integer | |
| output_tokens | integer | |
| latency_ms | integer | |
| success | boolean | |
| error_code | text | Nullable |

**RLS shape:** `SELECT`-only policy (`USING (user_id = auth.uid())`). **No** INSERT/UPDATE/DELETE policies — all writes come from the backend logger and the `pg_cron` aggregator via the service role, which bypasses RLS. A compromised user JWT cannot forge or scrub audit entries.

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

**RLS shape:** same as §8.8 — `SELECT`-only, writes via service role.

---

## 9. Security & Privacy

### 9.1 Authentication & Authorization

Supabase Auth handles all authentication. Google OAuth is the primary flow; magic link is a fallback. Sessions persist via refresh token (localStorage). A session expires only after 60+ days of inactivity or explicit sign-out.

**RLS enforcement — the critical pattern:**

The FastAPI backend receives `Authorization: Bearer <user_jwt>` from the frontend. **For each request**, the backend instantiates a Supabase client passing that JWT. PostgREST sets `request.jwt.claims` per query, and Postgres enforces RLS automatically. A bug in the API cannot leak one user's data to another, because the database refuses the row.

The **service role key** is reserved for exactly two callers:

1. The `pg_cron` daily auto-logger (runs as DB function, no application context).
2. Schema migrations (run via Supabase CLI from CI).

Application-handler code never uses the service role. This is enforced by code review and by a CI lint that flags imports of `SUPABASE_SERVICE_ROLE_KEY` outside the migrations and cron directories.

**Single active device:** `users_meta.active_device_id` is set on each successful sign-in. If a different device signs in, the previous device's session is revoked (user sees: "You signed in on iPhone — this session has ended"). Eliminates multi-device offline sync conflicts.

### 9.2 API Key Management

- `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `PERPLEXITY_API_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `RESEND_API_KEY`, `SENTRY_DSN` — Railway environment variables only.
- `.env` in `.gitignore` from day one.
- Pre-publish git history audit (gitleaks) before making the repo public; rotate any leaked keys.
- All keys server-side. Never returned in API responses or exposed to the frontend.

### 9.3 Transport Security

- HTTPS enforced on Railway. Service Worker and PWA install require TLS.
- CSP: `script-src 'self'`. No external script CDNs. Everything bundled via Vite.
- CORS: FastAPI restricts allowed origins to the app's own Railway domain. No wildcards.

### 9.4 Privacy Posture & AI Provider Data Retention

Financial data lives in your Supabase project under RLS. The only third-party egress is the AI API calls required for categorization, chat, and card lookup.

**Retention configuration:**

- **Anthropic:** Zero Data Retention (ZDR) requested for the Tameru organization. Default retention is 30 days for trust & safety; ZDR brings this to zero. Not used for training under any tier.
- **Google Gemini:** paid tier only. Paid tier does not use API data for training. Free tier does — never used.
- **Perplexity:** API calls do not include user financial data; only the public card name. No PII egress.

**User disclosure copy:**

> Tameru sends the merchant name and amount of each transaction to Anthropic and Google in order to categorize it and answer your questions. Both providers are configured for no data retention beyond the API call itself, and neither uses your data for training. Card multiplier lookups go to Perplexity but include only the public card name — no transaction data.

### 9.5 PostHog — Structural Events Only

PostHog tracks **product usage**, not financial behavior. The "client-side question classifier" from PRD v2.1 is dropped — it added complexity and a privacy story to defend without delivering value.

**Tracked events:**

- `chat_session_started`, `chat_session_ended` (timestamp, turn count, total duration)
- `feature_used` (enum: `dashboard | manual_entry | chat | csv_import | card_added | subscription_added`)
- `onboarding_step_completed` (step name)
- `weekly_digest_opened`
- `error_shown` (error type code only, no message)

**Never tracked:** transaction amounts, merchant names, card details, question text, or any other financial data.

User can opt out entirely in Settings (`users_meta.analytics_opted_out`).

### 9.6 Data Export

`/export` endpoint dumps all transactions, cards, subscriptions as a single JSON file. Accessible from chat ("Export my data"). No automatic cloud backup in v1 — export is manual.

---

## 10. Mobile Strategy — PWA Only

Tameru ships and stays as a Progressive Web App. No Expo migration on the roadmap. The author accepts the trade-off of weaker iOS push notifications in exchange for simpler shipping and one codebase.

### 10.1 PWA requirements

- Installable via Safari "Add to Home Screen."
- Service Worker caches the app shell for offline load.
- Transactions made offline stored in IndexedDB; synced on reconnect.
- Conflict resolution: not needed — single active device per user (§9.1).
- Lighthouse PWA score ≥ 90.
- Mobile-first layout — all core flows completable with one thumb.
- Transaction logged in <10 seconds from tap to save.

### 10.2 Push notifications — disclosure

iOS Safari supports web push only on iOS 16.4+ and only after the user installs the PWA to home screen. Even then, opt-in rates are lower than native. The weekly digest is therefore email-first; web push is a supplementary nudge channel for users who opt in.

---

## 11. Cost Estimates

All costs monthly. AI pricing assumes:

- Gemini 3.1 Flash-Lite: pricing not yet published (preview). Estimates below use 2.5 Flash rates as a placeholder: ~$0.075/M input + $0.30/M output. Expected to be lower per Google's positioning.
- Claude Haiku 4.5: **$1.00/M input + $5.00/M output** (confirmed against Anthropic docs April 2026)
- Claude Sonnet 4.6: $3.00/M input + $15.00/M output
- Perplexity Sonar: ~$3/M input + per-search fee (negligible at our volume)

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

Worst-case per-user chat spend is therefore **~$6/month** (200K tokens × $1/M input and $5/M output blend), even if the user spams the chat until they hit the cap every day. Gemini and Perplexity aren't capped — they're too cheap per call to matter.

### 11.3 Cost table — invite-only (~10 users)

Tameru is planned as invite-only. No Pro tier, no Stripe, no scaling beyond close friends and family.

| Service | Cost | Notes |
|---|---|---|
| Railway | $10.00 | Hobby plan, persistent FastAPI service |
| Supabase | $0.00 | Free tier (500 MAU, 500MB DB) — covers invite-only indefinitely |
| Google OAuth | $0.00 | Free |
| Sentry | $0.00 | Free tier (5K errors/month) |
| Resend | $0.00 | Free up to 3K emails/month |
| PostHog | $0.00 | Free up to 1M events/month |
| Gemini 3.1 Flash-Lite — categorization | $0.05 | 900 calls/month, 200 in + 5 out each |
| Gemini 3.1 Flash-Lite — CSV (amortized) | $0.01 | 1 import/user/6 months, 150 tx × 500 in |
| Gemini 3.1 Flash-Lite — NL parse | $0.02 | 1 call per submitted entry, ~200 tx/user/month |
| Perplexity — card lookup | $0.05 | ≤10 lookups per user lifetime, amortized |
| Claude Haiku — agent chat | $27.00 | 1,500 turns/month (150/user × 10), ~$0.018/turn with caching |
| Claude Haiku — memory distill | $0.20 | 1 distillation per session per user |
| Claude Sonnet — weekly digest | $0.07 | 4 calls/user/month |
| **Total (10 users)** | **~$37.40/month** | **~$3.74 per user per month** |

### 11.4 Why Claude dominates the bill

Claude chat is ~65% of the monthly total; everything else combined (Gemini + Perplexity + hosting) is ~35%. Two compounding reasons:

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

---

## 12. Schema Migrations

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

---

## 15. Milestones

### Phase 1 — Core App + Portfolio Layer (4 weeks)

- **Week 1:** FastAPI + Supabase + Google OAuth + RLS via JWT + Gemini 3.1 Flash-Lite categorization + CSV bank import with batch categorization.
- **Week 2:** Full PWA UI + philosophy screen + guided tour + spending tracker + subscription manager + dashboard with baselines + entry-moment insight.
- **Week 3:** Claude agent chat (Messages API + `tool_use` + SSE streaming) + cross-session memory.
- **Week 4:** Eval harness + MCP server (per-user tokens) + AICallLog observability + Sentry + prompt versioning + weekly email digest + PostHog (structural events).

### Post-Phase 1 — optional, author-driven only

Because Tameru is invite-only (§3.3), there is no committed Phase 2 or Phase 3. The following features may be added if the author wants them, in any order, with no scaling or pricing pressure:

- Card recommender (best card per category based on current wallet)
- Recurring subscription detection (suggest patterns as new subscriptions)
- Receipt photo → transaction (Gemini Vision)
- Proactive insights (AI-pushed alerts for overspending, missed rewards)
- Retroactive rewards gap analysis ("How many points did I miss last month?")
- Transfer bonus digest, SUB wishlist alerts
- Spending limits with AI nudges

Explicitly excluded: Plaid / Teller.io auto-sync, Stripe / paid tier, public launch, Expo / native apps.

---

## 16. Open Items

These are explicitly acknowledged unknowns that the v1 build will resolve in code:

- **Gemini 3.1 Flash-Lite is in preview** as of March 3, 2026 (`gemini-3.1-flash-lite-preview`). Preview models can change pricing, behavior, or be deprecated on short notice. Risk mitigation: model string is held in a single env var (`GEMINI_MODEL`) so we can fall back to `gemini-2.5-flash` (GA) instantly if Flash-Lite preview becomes unstable.
- **Flash-Lite A/B test for chat agent.** After Phase 1 launch, run the multi-hop eval suite (§7.10) against `gemini-3.1-flash-lite-preview` as an alternate chat model. If accuracy holds within 10% of Haiku, switch to save ~$18/month. Decision recorded in `evals/results.db` and a follow-up doc note.
- Anthropic ZDR enrollment — submit request before public launch (currently using default 30-day T&S retention).
- Perplexity Sonar JSON-mode reliability for card lookup — fall back to manual entry if extraction confidence is low.
- iOS PWA push opt-in rates in practice — measure via PostHog `weekly_digest_opened` after Phase 1 launch. Email is the primary digest channel regardless.

---

— End of design document — Tameru v3.0 · Chris Yu · April 2026
