# 04 — Design Trade-offs

[← Data & Security](./03-data-and-security.md) · [Back to index](./README.md)

This is the doc that matters. Every decision below was made over a *tempting* alternative for a specific
reason; re-deriving the choice without the context usually produces the wrong answer. Each entry states
the decision, the alternative, an honest pros/cons table, and (where useful) what would make me revisit
it.

Twelve deep-dives, then an [appendix](#appendix) indexing ~25 more.

---

## 1. RLS via the user's JWT, not the service role

**Decision.** FastAPI receives `Authorization: Bearer <user_jwt>`, builds a *per-request* Supabase
client with that JWT, and lets Postgres enforce `auth.uid() = user_id` via Row-Level Security. The
elevated service-role key is reserved for the four callers that have *no user JWT in scope* (cron jobs,
migrations, the Resend webhook).

**Alternative.** Use the service-role key everywhere and write `WHERE user_id = ...` filters in
application code (the common pattern when people find RLS "annoying").

| | Pros | Cons |
|---|---|---|
| **Chosen: RLS via user JWT** | Tenant isolation is a *database* guarantee — a route-handler bug can't leak across tenants; authorization isn't something the API can forget; zero authz code to review | Requires the JWT to be in scope wherever a query runs (shapes the agent loop); service-role exceptions need discipline to keep narrow |
| **Rejected: service role + manual filters** | Simpler client setup; one connection | *Every query* is one forgotten `WHERE` away from a cross-tenant leak; the security boundary lives in the most error-prone place (handwritten filters in every handler) |

**Why it's load-bearing.** This is the decision the agent loop is *built around* — the loop runs
in-process specifically so the user's JWT is a request-local variable when a tool executes. A structural
test (`test_no_service_role_leak.py`) fails the build if the service-role key leaks into a request path.
**Revisit when:** never, at v1 scale. A per-request session check at the MCP layer (for instant
revocation) would be the first amendment, and only if the scaling plan activates.

---

## 2. Typed tools over raw SQL

**Decision.** The agent gets ~7 typed Python tools (`get_transactions`, `calculate_total`, …), not a
`run_sql(sql)` tool with the schema dumped in the prompt.

**Alternative (steelmanned).** A single `run_sql` tool is *more flexible* (any question), *smaller*
(one tool, not seven), needs *no upfront query-shape design*, and modern Claude writes competent
Postgres. If Tameru were a general "talk to your spreadsheet" product, raw SQL would win.

| | Pros | Cons |
|---|---|---|
| **Chosen: typed tools** | RLS-blind wrong answers become impossible — each tool's SQL is reviewed, fixture-tested, indexed; propose-then-confirm and MCP-read-only are *structurally* true (no write/SQL affordance exists); evals assert stable `tool_use(name, args)` calls; logs carry non-sensitive structured args, not free-form SQL with merchant names in it | Must anticipate query shapes — the long tail ("merchants I visit most after 10pm") doesn't fit a current tool; v1 accepts that gap |
| **Rejected: raw SQL** | Flexible across an open question space; one tool | **Silent wrong-but-plausible numbers** — RLS stops cross-user reads but not `WHERE date >= '2025'` (wrong year) or a join that drops uncarded rows; eval becomes brittle (string-match SQL) or unsound (output-match passes wrong SQL); SQL strings leak filter values into logs; "is this read-only?" needs a real SQL classifier (CTEs, `RETURNING`…) |

**The core argument:** in personal finance a *wrong number is worse than an error* — errors get
reported, wrong numbers get acted on. Typed tools turn a whole failure class into a bug fixed once.
**Revisit when:** a real class of user questions can't be served — then add a read-only `run_query(sql)`
in Phase 2 against a *read replica*, with separate auth, logging, and rate limits.

---

## 3. Custom agent loop over LangChain/LangGraph/ADK/Managed Agents

**Decision.** The chat agent is an ~80-line `tool_use` loop on the Anthropic Messages API, running
in-process. No agent framework.

**Alternatives.** Claude Managed Agents; LangChain classic; LangGraph; Google ADK; the standalone Agent
SDK as a wrapper.

| Option | Why rejected |
|---|---|
| **Managed Agents** | Built for long-running autonomous work in Anthropic's container; Tameru's turns are 4–6 s with DB-backed tools. Every tool call would round-trip through MCP from Anthropic's container, breaking the per-user JWT path |
| **LangChain (classic)** | Message-shape translation tax on the `chat_turn_trace` wire contract; `langchain-anthropic` lag on caching/beta features; `AgentExecutor` opacity — *its loop is exactly the 80 lines we want to own* |
| **LangGraph** (strongest alt) | Sweet spot is multi-step branching + checkpoint resume + human-in-loop interrupts; Tameru's loop is a single agent, two states, 4–6 s. `PostgresSaver` checkpoints overlap `chat_turn_trace` with weaker wire fidelity, and risk JWT-at-rest in the checkpoint table |
| **Google ADK** | Multi-agent orchestration for Vertex; ADK tools get `session.state`, not request-scoped data — the JWT moves into `session.state["user_jwt"]` and is briefly at rest, **breaking the lifetime symmetry** |

| | Pros | Cons |
|---|---|---|
| **Chosen: own the loop** | JWT stays a request-local variable → RLS just works; full control of middleware (cost cap, backoff, audit logging as a function return, not a callback bus); `chat_turn_trace` stores the literal Anthropic wire shape; closest to Anthropic-specific features (caching, streaming deltas) with zero adapter lag | We maintain the loop ourselves (~80 lines); no free multi-agent / checkpoint machinery if we ever need it |
| **Rejected: a framework** | Batteries included; community patterns | A session abstraction between request and tool breaks the JWT symmetry; adapter lag on the API we're closest to; framework internals leak into our cost math and audit contract |

**The single criterion that decides it:** *does the framework keep the JWT in request scope?* LangChain
happens to (per-request tool closures); ADK and Managed Agents don't. But none of the others' benefits
apply to a single-agent, Claude-pinned, 4–6 s loop. **Revisit when:** the chat agent becomes
multi-agent, gains mid-call human-in-loop interrupts, or runs long enough that checkpoint resume earns
its keep — none of which are v1.

---

## 4. Propose-then-confirm: the agent never commits a ledger row

**Decision.** The agent can `propose_transaction(...)` (returns a preview payload) but the row is
written only by a separate `POST /<resource>/confirm` after an explicit user tap. The `tool_use` call
is never the commit. Lone exception: `set_goal` (low-risk, reversible, off-ledger).

**Alternative.** A direct-write tool — `add_transaction` — that commits inside the handler.

| | Pros | Cons |
|---|---|---|
| **Chosen: propose-then-confirm** | No ledger row exists until the user sees what was parsed and taps OK; matches the "intent preview" agentic-UX pattern; structurally enforced (no write tool exists → can't be misused by prompt injection) | One extra HTTP round-trip; the UI must render preview cards |
| **Rejected: direct-write tool** | Fewer round-trips; "just works" feel | The agent can create a row from a *misread* vague message before the user sees it; the write affordance is then permanently exploitable by injection |

Enforced by `test_tool_write_invariant.py`, which fails the build if `ALLOWED_DIRECT_WRITE_TOOLS` widens
without a rationale comment. The invariant is about *the commit not being a tool call* — future UI
surfaces are fine as long as ledger mutation flows through an explicit HTTP call.

---

## 5. Claude Haiku for chat over Gemini Flash-Lite

**Decision.** Chat runs on Claude Haiku 4.5, not the ~3.4× cheaper Gemini 3.1 Flash-Lite.

| | Pros | Cons |
|---|---|---|
| **Chosen: Haiku** | Reliable multi-step *agentic tool* execution (the public AIME-with-tools data point is strong); the chat agent *is* multi-step reasoning over money | ~65% of the bill; ~$27/mo at 10 users, ~$270 projected at 100 |
| **Rejected: Flash-Lite** | 3.4× cheaper, wins general-IQ benchmarks, faster output | Google's *own* docs route multi-step agentic reasoning to the pricier Gemini Pro; a misfired tool call → a wrong number → eroded trust, which costs more than the savings |

**The asymmetry that decides it:** the failure mode isn't "slightly worse prose," it's "confidently
wrong financial number." For a trust product, $18/mo is cheaper than re-earning trust. **Not permanent:**
the multi-hop eval suite exists so Flash-Lite can be A/B'd post-launch on Tameru's *real* tool surface;
if it scores ≥90% of Haiku's multi-hop accuracy, switch and pocket the savings. Model strings are
env-resolved, so the switch is a config change.

---

## 6. `pg_cron` for scheduled jobs over a worker framework

**Decision.** The subscription auto-logger and the memory-prune sweep are `pg_cron` SQL functions. The
weekly email digest is a separate Railway cron service (it needs Python + Resend). No Celery/Sidekiq.

**Alternative.** A real background-job framework (Celery + a broker), or FastAPI `BackgroundTasks` for
scheduled work.

| | Pros | Cons |
|---|---|---|
| **Chosen: pg_cron (+ Railway cron)** | DB-resident → survives API deploys and Railway worker recycling; idempotency lives in the schema (`UNIQUE … ON CONFLICT DO NOTHING`, advisory locks for concurrency); zero new infrastructure | No retries, no backoff, no SLA, no missed-run catch-up — so jobs must be *designed* to tolerate a missed run |
| **Rejected: worker framework** | Retries, backoff, DAGs, sub-minute scheduling | Real infra (broker, workers) for two jobs that fire daily/weekly; overkill at this scale |

**The design principle this forces (and that's a feature):** jobs are **missable-recoverable**. The
auto-logger is idempotent on `UNIQUE (subscription_id, date)`; the memory cap is *soft* (overflow
trimmed next sweep, hidden by `LIMIT 60`); the digest dedupes on a per-user-local-week unique index.
Design idempotent state + accept eventual consistency, rather than bolting retries onto a scheduler.
**Revisit when:** a job needs sub-minute timing, external-API backoff, or multi-step dependencies — then
host it externally (GitHub Actions cron / Cloud Scheduler).

---

## 7. MCP auth: OAuth 2.1 via Supabase over static bearer tokens

**Decision.** The MCP server is an OAuth 2.1 *Resource Server*; Supabase Auth's OAuth 2.1 Server is the
Authorization Server. No `mcp_tokens` table.

**Alternative.** Per-user 32-byte bearer tokens stored as `sha256` (the original design).

| | Pros | Cons |
|---|---|---|
| **Chosen: OAuth 2.1 (Supabase)** | Works with Claude.ai's *web* connector (which accepts only OAuth); the access token *is* a Supabase user JWT → verifies through the existing JWKS path → **RLS works, no service role**; no new vendor/SDK/sub-processor | OAuth dance is more moving parts (DCR, consent screen — which Tameru must host); revocation is bounded by JWT TTL, not instant |
| **Rejected: static bearer tokens** | Dead simple; spec-legal for a ~10-user server | Claude.ai web has *no field* for a static token/header → the headline "works with Claude.ai" criterion is unmeetable |

**The forcing function:** the *client* (Claude.ai web) dictated the auth model, not the server's scale.
Because a Supabase OAuth token is just a user JWT, adopting OAuth cost no new infrastructure and left the
RLS invariant untouched. Revocation is made "effective within ~5 minutes" by setting the JWT TTL to
300 s rather than adding a per-request session lookup (which would need the service role). **Revisit
when:** scaling tightens the revocation promise enough to justify that per-request check.

---

## 8. Deterministic evals that gate + a non-gating LLM judge

**Decision.** Three deterministic suites (exact category match, `Decimal`-exact extraction,
tool-sequence multiset match) own every CI gate. A separate LLM-as-judge (Sonnet grading Haiku) scores
the *prose tone/helpfulness* of the final answer as a **non-gating** dashboard.

**Alternative.** A full LLM-as-judge with 0–5 dimensions (correctness/reasoning/completeness/tool-usage)
gating CI — the common "agentic systems use a judge" pattern.

| | Pros | Cons |
|---|---|---|
| **Chosen: deterministic gate + non-gating judge** | Typed tools make every action exactly assertable; reproducible (no judge drift); zero extra LLM cost on the gate; "88% is 88%" gates cleanly; the judge still covers the one fuzzy surface (tone) without ever flipping CI | The deterministic check can't read helpfulness/tone (hence the judge); requires upfront assertion design |
| **Rejected: judge gates CI** | Reads nuance; less assertion design | A graded score can't gate cleanly ("is 3.7/5 a pass?"); judge drift makes CI flaky; cost per run; redundant where typed tools are already assertable |

**The split is the asset:** deterministic owns everything assertable; the judge owns *only* the
unassertable, and is structurally barred from gating (`gate=None`). This is the hybrid that the original
"deterministic only" decision named as the sanctioned future enhancement — realized without compromising
gate reproducibility.

---

## 9. Manual entry over bank auto-sync (Plaid/Teller)

**Decision.** No automatic bank sync, at any scale. Entry is manual (chat-based), with CSV import to
close the cold-start gap.

**Alternative.** Plaid / Teller.io auto-sync — what every competitor does.

| | Pros | Cons |
|---|---|---|
| **Chosen: manual entry** | The 10-second act of logging *is* the awareness intervention (the product thesis); logs true cost (a reimbursed $200 dinner is logged as your $100); no bank credentials, no Plaid compliance scope, no per-call sync cost; privacy as a feature | Ongoing logging friction; loses users who won't buy intentional entry; CSV closes cold-start but not daily friction |
| **Rejected: auto-sync** | Zero-friction onboarding; the market expectation | Miscategorization tax (users spend more time fixing than gaining insight); *passivity* — automatic means invisible, which is the exact behavior the product is trying to change; compliance + cost scope Tameru doesn't want |

This is a *product* decision with a clean engineering payoff: no Plaid means no financial-data
sub-processor, no OAuth-to-banks surface, and a much smaller compliance footprint. It's permanently out
of scope, not deferred.

---

## 10. Cross-table writes by shape: RPC vs trigger vs direct

**Decision.** Because `supabase-py` has no multi-statement transaction primitive, the write mechanism is
picked by the *shape* of the write, not by convenience.

| Shape | Mechanism | Why |
|---|---|---|
| User-intent, multi-table, must be atomic | `SECURITY DEFINER` plpgsql RPC | One implicit transaction; intent visible at the call site |
| Derived value shadowing another column | DB trigger on the source | Expresses "this column follows that one" |
| Single-table, no side effects | direct PostgREST `.update()` | Minimal ceremony |

**Alternative.** Best-effort sequential writes from Python with `try/except`.

| | Pros | Cons |
|---|---|---|
| **Chosen: by shape** | Atomicity where it's needed (no orphaned rows); the mechanism documents the intent | plpgsql to maintain; `SECURITY DEFINER` needs careful `REVOKE`/`GRANT` + `auth.uid()` filters |
| **Rejected: Python try/except** | No SQL to write | **Silent partial-failure** — a card could be created with its annual-fee subscription orphaned, and the route still returns 200 |

This generalizes a recurring Postgres-via-PostgREST reality: anything needing "both writes or neither"
moves into a plpgsql function. The same pattern reappears for partial-unique-index upserts (PostgREST
can't pass the `WHERE` predicate, so a `SECURITY INVOKER` function emits the `ON CONFLICT … WHERE …`
directly).

---

## 11. Single immutable home currency + three decoupled i18n axes

**Decision.** Each user has one home currency, chosen at signup and **immutable** (DB trigger, not app
code); all amounts stored in it as `numeric`. Internationalization is modeled as *three independent
axes*: currency, timezone (IANA, mutable), and UI language (`en`/`ja`/`zh-TW`) — none derived from
another.

**Alternative.** Per-transaction multi-currency with FX; or deriving timezone/locale from currency.

| | Pros | Cons |
|---|---|---|
| **Chosen: single currency + 3 axes** | No FX engine, no statement reconciliation, no dual-currency ledger; supports the real cases — a JP-resident with an English UI and JPY currency, or a wallet mixing US and TW *cards* (region is per-card, used only for reward-lookup routing) | A genuine home-currency change means delete + re-signup (the documented escape hatch); no FX for foreign purchases (entered as they'll hit the statement) |
| **Rejected: multi-currency + FX** | Handles travelers natively | An FX API, rate-sourcing, per-transaction currency UI, and reconciliation — large scope for a v1 whose users have one home currency each |
| **Rejected: derive tz/locale from currency** | One fewer setting | Wrong guesses — USD spans Hawaii→Maine; deriving date formatting from currency gave a JP-resident Japanese dates despite an English UI |

**The proof case that forced the decoupling:** a family member in Japan wants an *English UI + JPY
currency + Asia/Tokyo timezone*. That combination is only representable if the three axes are
independent. Card *region* is a fourth, per-card property (reward-routing only) — it never affects the
single-home-currency rule.

---

## 12. PWA today; Expo and Capacitor rejected, Swift admitted later

**Decision.** Ship a PWA. Expo and Capacitor evaluated and rejected. Native Swift admitted as a
*possible* future migration — not on the roadmap.

| | Pros | Cons |
|---|---|---|
| **Chosen: PWA** | One codebase, instant deploys, installable, no App Store gatekeeping; the backend is already client-agnostic so a future native client reuses the API verbatim | iOS web-push limits (accepted — digest is email-first); no native-feel polish ceiling |
| **Rejected: Expo / Capacitor** | Native shell, push | A wrapper layer + build pipeline for marginal gain at ~10 users; another surface to maintain |
| **Deferred: Swift** | Best UX ceiling | Only worth it if *three* signals fire together — explicit user demand, measurable PWA UX drag on retention, sustained paid-tier revenue — none of which can exist at v1 scale |

The backend's client-agnosticism (one API host, Bearer auth, no `User-Agent` branching) is what keeps
the Swift option *reversible* — the migration decision never has to touch the server.

---

## Appendix

The decision log behind this project runs to ~40 dated entries. The twelve above are the headline ones;
here's an index of the rest, grouped by area, so the depth is visible without bloating the main read.

**Agent & AI**
- *Card refs are short handles, not UUIDs* — LLMs mis-copy long random strings; a slip fails closed.
- *Memory distillation piggybacks the next chat turn* — no timer/daemon; survives worker recycling and iOS unload flakiness.
- *Soft, not hard, memory cap* — nightly SQL recency×relevance trim; no inline LLM tiebreaker.
- *Chat reply language is setting-driven, not mirror-input* — per-user directive injected into the dynamic prompt tail to preserve the shared prompt-cache prefix.
- *Prompt-cache prefix discipline* — per-user text never goes in the cached system block, or the 90%-cache-read economics break for every user.

**Data & schema**
- *`client_request_id` plays three roles* (idempotency / join key / both) depending on whether the table has a natural uniqueness key.
- *Soft-delete cascade is split* — deleting a card pauses its regular subs (reassignable) but cancels its annual-fee sub (no reassignment target), atomically via RPC.
- *Subscriptions are forward-only* — the auto-logger never backfills a backdated start date (matches YNAB/Copilot/Monarch).
- *Subscription `frequency`/`start_date` immutable* — cadence change = cancel-then-re-add, to keep `next_billing_date` unambiguous.
- *Cardless subscriptions* — `card_id` nullable so bank-ACH bills (rent, utilities) are first-class.
- *Card annual fee is one canonical number* — the companion AF subscription mirrors `cards.annual_fee`; clearing it cancels the sub rather than auto-logging $0.

**Security & ops**
- *`SECURITY DEFINER` functions must `REVOKE` from `anon, authenticated` explicitly* — `REVOKE FROM PUBLIC` alone leaves them reachable via PostgREST.
- *Admin access is a table, not a Postgres GUC* — Supabase Free tier denies custom `ALTER DATABASE … SET app.*`.
- *Single active device per user* — eliminates offline multi-device conflict resolution by construction.
- *Fail-fast env boot, tiered* — unconditional vars vs `production`-only vars, keyed on "does its absence break dev?".
- *Deploy from CI, not the PaaS Git integration* — to stop the migration-vs-code deploy race.
- *Daily prod-health workflow* — scheduled E2E against live prod, since push-CI is blind to outages between deploys.

**Infrastructure patterns**
- *CSV import uses a stateless HMAC `import_token`* for the preview→commit handoff — zero server-side state, tamper-proof.
- *Bulk insert via `SECURITY INVOKER` plpgsql* — PostgREST can't target a partial unique index's `WHERE` predicate (42P10).
- *CSV sign convention is detected, not assumed* — US issuers split into charges-positive vs charges-negative families; Gemini infers it.
- *Weekly digest delivers at per-user local 09:00* via an hourly cron + local-time gate, with a 3-hour retry window and a per-user-local-week dedup key (fixes a far-east-timezone double-send the retry window exposed).
- *`email_log` idempotency on a partial-on-success unique index* — a transient send failure can retry within the week.

**Product/UX**
- *Entry-moment insight has severity tiers* — calm/elevated/alert, with a pace-aware month-end projection, so a real overspend warning doesn't look like a mild fact.
- *Dashboard fits one screen, no scrolling* — adding a tile means removing one; historical analysis lives in chat.
- *Onboarding "demo" is a static guided tour* with fixture data — no fake-data layer in Supabase, no AI calls.
- *Chat is the only create surface in v1* — no `+`-button entry form.

*Each of these has a full Decision / Context / Rationale / Alternatives / Revisit-when block in the
project's internal decision log.*

---

[← Data & Security](./03-data-and-security.md) · [Back to index](./README.md)
