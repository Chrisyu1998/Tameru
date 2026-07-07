# TODO

Tracked-but-deferred work. Not a backlog dump — each item is something we
consciously decided to do *later*, with enough context to pick it up cold.

For shipped architecture and the *why* behind decisions, see `DESIGN.md` and
`memory.md`. This file is only for "we agreed to defer this."

---

## Credit / Perk Tracking (PLANNED — author-driven, phased)

**Status:** **Phase 1 + Phase 2 shipped 2026-07-05** (local: full backend suite
green incl. 11 new `test_credit_bridge.py` + `get_card_credits` tool/MCP +
digest tests; `supabase db reset` applies all migrations clean; frontend build +
170 vitest green — **not yet deployed**). **OPERATOR:** apply the new
`reset-card-credits` schedule from `supabase/snippets/production_cron.sql` to prod
(daily `0 5 * * *`). A manual per-card statement-credit tracker (Amex Plat's
$75/qtr Lululemon, $100/qtr Resy, etc.) so users see which use-it-or-lose-it
credits they've burned this period without opening each issuer's app. Green-lit
as a post-Phase-1, author-driven feature (§15). Design + rationale: DESIGN.md
**§6.7**; schema **§8.17** (`card_credits`) / **§8.18** (`card_credit_history`).

**Decisions already made (build to these — don't re-litigate):**

- **Manual usage entry** — no bank linking (Plaid/Teller permanently out of
  scope, §3.2). Competitors (CardPointers, MaxRewards) auto-detect usage via
  account linking; Tameru asks the user to tap "$60 used." That's the product,
  not a gap — the value is the consolidated privacy-preserving view + reminders.
- **Calendar-anchored reset only** — monthly / quarterly / semiannual / annual,
  all on calendar boundaries (covers Amex Platinum's whole credit set).
  Anniversary / cardmember-year anchoring is **deferred** (default calendar; add
  a per-credit anchor override later). Guessing calendar-vs-anniversary wrong
  resets on the wrong day — a trust hit, same logic as Tier-3 base-rate-only.
- **Under-claim on lookup** — propose-then-confirm every lookup, store
  `source_urls` + `verified_at`, editable anytime; the user is the verifier.
  Prefer a missed credit (user adds it) over a phantom one (terms drift yearly).
- **Nav** — a "credits" chip on the card row in `cards.tsx` (sibling to the AF
  chip on `CardTile`), NOT a BottomNav tab and NOT a "card detail" page (none
  exists in v1). The chip **navigates to a dedicated Credits page route**
  (`/cards/:cardId/credits`) — a full page, not a bottom sheet, since a card can
  carry ~10 credits (Amex Plat). DESIGN §6.7's "button on the card detail"
  wording means exactly this; update it to "chip on the card row → Credits page"
  when built.
- **Invariant 8 not implicated** — `card_credits` is an auxiliary table, not a
  ledger table; propose-then-confirm + HTTP edits (AF-sheet pattern), no
  `tool_use` writes. See §6.7.
- **Surfaced in the weekly digest + read by the agent** — the payoff isn't the
  tracker, it's the reminders: a digest "expiring soon, unused" line + a
  "value captured" line (Phase 2), and the agent answering "how much Amex credit
  do I have left?" via a read tool.
- **Credits are tool-read live state, NOT `user_memory`** — the agent reads them
  via a `get_card_credits` typed tool (Phase 2), never distilled as facts. A
  credit balance in memory goes stale at period reset and decays on the wrong
  clock (same stale-inventory reason `"User has Amex 1007"` is excluded from
  distillation, memory.md 2026-07-03). A durable *trait* ("optimizes credits")
  may distill naturally — that's a pattern, not inventory.
- **Reset sweep is a sanctioned service-role caller** — `reset_card_credits()` is
  a new `pg_cron` DB function with no user JWT, same category as the auto-logger
  (invariant 1 caller #1). Add it by name to CLAUDE.md invariant 1 + DESIGN §9.1
  in the migration that ships it (memory.md 2026-05-22 enumerates callers by
  name). `test_no_service_role_leak.py` is NOT implicated — SQL in a migration,
  not `app/` Python — so this is invariant-doc sync only, no test change.
- **Lookup amount fail-closed on currency mismatch** — if the lookup returns a
  credit amount in a currency ≠ `home_currency`, store `amount = null` and let the
  user type it; never convert (mirrors the AF fail-closed-to-null, invariant 13 /
  the Tier-3 "Annual fee" note below). No separate home-currency gate — the AF
  path already set the pattern.
- **Lookup failure returns `[]` + HTTP 200, never 503** —
  `POST /card-credits/lookup` catches lookup errors and returns an empty proposal
  list; the UI renders "no credits found — add manually." Mirrors `lookup_card`'s
  never-raises contract (`app/integrations/card_lookup.py`); deliberately unlike
  the receipt route's 503, because credits always have a manual-add fallback and
  receipt does not.

**Phase 1 — standalone manual tracker:**

- [x] `card_credits` table + RLS + the two partial unique indexes (§8.17);
  migration (`20260705120000`).
- [x] `reset_card_credits()` `pg_cron` daily sweep — calendar boundaries per
  cadence, forward-only, advisory-locked *against concurrent cron runs only*,
  service role (the §6.5 auto-logger shape). Migration + `pg_cron` schedule.
  **No two-sided lock** with the `used_amount` write path: the sweep zeroes
  `used_amount` only at a period boundary, so a Phase-1 set-absolute `PATCH`
  landing at that instant is period-ambiguous, visible, and self-correcting
  (user re-enters if zeroed) — missable-recoverable, NOT the 2026-05-18
  memory-prune lock case (that was silent, permanent loss). Documented benign in
  §8.17.
- [x] `credit_period_bounds(cadence, on_date)` plpgsql helper →
  `(period_start, next_reset)`: the single source of truth for calendar-anchored
  boundaries (monthly = 1st, quarterly = Jan/Apr/Jul/Oct 1, semiannual =
  Jan/Jul 1, annual = Jan 1), in the user's `timezone` else UTC. Consumed by
  BOTH `reset_card_credits()` (advance) AND the confirm upsert (seed) — one SQL
  implementation, no Python↔SQL mirror drift; it's also the field the Phase-2
  period-guard `WHERE :tx_date >= current_period_start` depends on.
- [x] Extend `app/integrations/card_lookup.py` with a credit-list lookup prompt
  (`lookup_card_credits`, `credit_lookup_v1`); new `ai_call_log` `task_type`
  `credit_lookup` (CHECK widen on `ai_call_log` + `ai_call_log_daily`,
  `20260705120500`).
- [x] Endpoints (typed Pydantic boundaries per CLAUDE.md doctrine):
  `POST /card-credits/lookup` (→ proposal list), `POST /card-credits/confirm`
  (writes via a SECURITY INVOKER plpgsql upsert — required by the expression/
  partial `card_credits_active_name_uniq` index, §8.17 — which seeds
  `used_amount = 0` + `current_period_start` / `next_reset_date` from
  `credit_period_bounds()`), `GET /card-credits?card_id=`,
  `PATCH /card-credits/{id}` (used_amount / name / amount / cadence / status; a
  cadence edit recomputes the period bounds via the same helper),
  `DELETE /card-credits/{id}` (→ archive).
- [x] Cards surface: a "credits" chip on `CardTile` (AF-chip sibling) →
  navigates to the Credits page. First-time setup is that page's empty state —
  "look up this card's credits" (→ lookup → propose-confirm) + "add manually" —
  mirroring the dashed "+ track AF" empty-state affordance (memory.md 2026-05-19).
- [x] Credits page: new route `/cards/:cardId/credits`
  (`frontend/src/pages/cards.credits.tsx`) — per-card progress bars +
  set-used-amount + edit + archive + manual-add + empty state (lookup /
  manual-add). i18n keys added to en (final) + ja/zh-TW (drafts per Tier-2
  translation ownership — Chris to review the CJK).
- [x] `soft_delete_card` RPC: archive companion credits on card soft-delete
  (§8.3 split-cascade sibling; `20260705120400`).
- [x] Tests: `tests/routes/test_card_credits.py` (15 passing) — RLS (read /
  patch / delete / confirm-onto-foreign-card); `credit_period_bounds` seeding at
  confirm AND recompute on a cadence PATCH; propose-confirm idempotency (crid +
  name partial index) through the plpgsql upsert; lookup fail-closed
  (empty → `[]`); soft-delete cascade. (Deferred: a pure DST-boundary unit on
  the reset-advance path — the seed path exercises `credit_period_bounds`
  per cadence.)

**Phase 2 — the value multipliers (the differentiator):**

- [x] **Ledger bridge** — `POST /transactions/confirm`: on a merchant+card match
  to an active credit, return a "count $X toward {credit}?" suggestion as a
  **separate `credit_suggestion` field** on the confirm response — NOT the single
  `insight` slot; the entry-moment insight and the credit affordance are
  orthogonal and must not suppress each other. **Match: exact-substring** —
  lowercased `merchant_hint` ⊂ lowercased merchant (hints are distinctive brand
  tokens, so false positives are near-zero; the chat merchant-canonicalization is
  a deferred enhancement for messy statement/CSV descriptors, not a blocker).
  Computed **only on the fresh-insert path**, so an idempotent re-confirm (same
  crid → route returns the existing row early, `transactions.py:56/91`) returns
  `credit_suggestion: null` — same suppression as `insight`; a double-submit
  doesn't re-offer an already-tapped credit. A tap goes through the
  **idempotent** `card_credit_apply_usage` RPC: it records the
  `(card_credit_id, transaction_id)` application in a unique-keyed ledger
  (`card_credit_applications`, §8.19) `ON CONFLICT DO NOTHING`, and increments
  `used_amount = GREATEST(0, LEAST(amount, used_amount + delta))` only when a
  *new* row lands. So a lost-response retry / double-tap / offline replay of the
  same transaction is a no-op (fixes the double-count Codex flagged 2026-07-05),
  and concurrent applies of *different* transactions serialize on the row lock
  with no lost update. The period guard is a **lower bound only** (`t.date >=
  current_period_start`) and lives **inside the UPDATE's WHERE** (joining the
  transaction), so it is re-checked under the row lock via EvalPlanQual — if a
  reset advances the period between the apply's snapshot and its write, the
  old-period spend is rejected instead of landing in the fresh period (a plain
  `SELECT … FOR UPDATE` guard would NOT fix this: a plpgsql function shares its
  caller's snapshot, so only the updating statement's own EvalPlanQual re-read
  sees the new period — Codex round 2, 2026-07-05). A spend dated *before* the
  current period (old receipt / a spend from the period that just reset) doesn't
  count; a future-dated spend counts toward the current period (benign, <24h
  pre-sweep edge). Do NOT add a `t.date < next_reset_date` upper bound — it makes
  the tap a silent no-op, worse than the benign misattribution. Refunds floor at
  0; over-cap clamps at `amount`. **Done-when:**
  `tests/routes/test_credit_bridge.py` — match →
  suggestion present; no match → null; idempotent re-confirm → null; **re-apply
  same transaction → counted once**; tap over-cap clamps at `amount`; spend <
  `current_period_start` → no-op; refund path.
- [x] Weekly-digest integration in `compose_digest`: an "expiring soon, unused"
  reminder line (credits with `next_reset_date` within **N = 14 days** and
  `used_amount < amount`) + an optional positive "value captured this period"
  line ($ used vs available across the wallet). Reuses the per-user-timezone
  send (§6.4). **N = 14, not 7**: the digest sends weekly, so a 7-day window
  contains exactly one send (one reminder, maybe only 1 day before reset) while a
  14-day window contains two (earliest 8–14 days out — real lead time + a
  follow-up). Cadence-aware tuning (monthly 7d / quarterly 14d / annual 30d) is a
  deferred refinement; 14 flat for v1. **Done-when:** a credit 10 days from reset
  with `used_amount < amount` appears in the digest fixture; one 20 days out does
  not; a fully-used one does not.
- [x] `card_credit_history` (§8.18) + "last {period} you used $X" on the Credits
  page; `reset_card_credits()` snapshots the closing period.
- [x] Read-only `get_card_credits` chat tool — the agent-awareness path ("how
  much Amex credit do I have left?"). Reads live from `card_credits`, **not**
  `user_memory` (credits are live state, not distilled facts — see decisions
  above). Read-only: no direct-write tool, no invariant-8 widening. Return shape
  `{credits: [{name, amount, used_amount, next_reset_date, card_ref}]}`. **Also
  wire it into the MCP read-only server** (`app/mcp_server.py`) via one shared
  `app/agent/tools.py` read fn, so the chat-agent and MCP read surfaces don't
  drift (memory.md 2026-05-20); read-only, so no invariant-3 / consent change.
  **Done-when:** `tests/test_tools.py` asserts RLS-scoped return (caller's active
  credits only); `tests/test_mcp.py` asserts the MCP tool exists + returns the
  same shape.

**Deferred beyond Phase 2:**

- Anniversary / cardmember-year anchoring (non-calendar reset) + per-credit
  anchor date.
- Entry-moment credit nudge (vs. digest-only reminders).
- Auto term-refresh (periodic re-lookup to catch changed terms) — needs a
  refresh cron the lookup pipeline doesn't have today (same "no refresh
  mechanism" constraint that cut Tier-3 promos).
- Localized credit-lookup sources/prompts for JP/TW cards (follows the Tier-3
  pattern; JP/TW premium-card credits are rarer anyway).

**Open questions to resolve at build time:**

- (none remaining for Phase 2 — see Resolved.)

**Resolved (were listed open; decided — don't re-litigate):**

- Bridge channel → **separate `credit_suggestion` field** on the confirm response,
  NOT the `insight` slot (orthogonal affordances; must not suppress each other).
  DESIGN §6.7 synced.
- `merchant_hint` match → **exact-substring** (lowercased `merchant_hint` ⊂
  lowercased merchant); chat merchant-canonicalization deferred (helps messy
  statement/CSV descriptors, not a Phase-2 blocker).
- Digest "expiring soon" threshold → **N = 14 days** (2× the weekly send cadence;
  cadence-aware tuning deferred).
- Reset-boundary timezone → **user `timezone` when set, else UTC** (§8.17; reuses
  the digest machinery).
- Credit lookup separate vs. bundled into card-add → **separate, opt-in at
  "track credits"** (§6.7 — keeps card-add fast).

---

## Internationalization — credit cards (DEFERRED)

**Status:** the **minimal-done slice is implemented** (2026-06-02, local +
tests green; not yet deployed/verified against live JP/TW lookups). JCB +
Diners networks, the top ~6 JP and ~6 TW issuers, a per-card `region`, the
`card_issuers` reference table, region-routed base-rate-only lookup for JP/TW,
and a `home_currency`-aware annual-fee prompt all ship. **Still deferred:** the
long tail of JP/TW issuers, promos / category multipliers outside the US, and
per-card branding + localized AF-recognition templates for JP/TW issuers.

**Why deferred:** international card support is the hardest, lowest-confidence
slice of i18n, and the reward-lookup value proposition is genuinely weaker
outside the US (see the scope decision below). Decoupling it let the high-value
locale/language work ship fast.

**Scope decision (resolved 2026-06-02 — build to this, don't re-litigate):**

- **Multi-region cards in one wallet: YES.** `region` is a property of the
  card/issuer, not the user, so a single wallet can hold a US card and a TW
  card at once (the "moved back to Taiwan, still carries US cards" case). The
  lookup routes per card: US card → US sources; JP/TW card → the base-rate path
  below. There is no per-user region; nothing requires a wallet's cards to share
  one.
- **Multi-currency spend: NO.** Invariant 13 stands — one immutable
  `home_currency`, all amounts stored in it, no per-transaction currency, no FX.
  A user holding a foreign-denominated card hand-converts its purchases into
  home currency (the existing invariant-13 friction, now permanent for that
  user rather than trip-only). A true dual-currency ledger (native per-card
  totals + FX to a display currency) is the big "Option C" and stays
  **permanently out of scope** (CLAUDE.md "What is in scope and what is not").
- **Relocation → new account.** Someone who permanently moves countries and
  needs a different home currency deletes + re-signs-up (the invariant-13 escape
  hatch). We do **not** add a home-currency migration path. Deliberate product
  call, not a gap.
- **Outside the US: base rate only — no category multipliers, no promos.**
  Research (2026-06-02) confirmed JP rewards are partner-point-ecosystem driven
  (Rakuten ~1% base, 10–12% only inside Rakuten's own services; SMBC/Olive only
  at specific convenience stores) and TW bonuses are user-*selected* mutable
  plans (Cathay CUBE lets you switch the bonus category monthly) or mobile-pay
  binding driven (E.SUN + 街口/LINE Pay). The only number a one-shot, at-card-add
  web lookup can capture stably is the **base earn rate**. Category multipliers
  go stale the moment the user spends off-partner, and the lookup pipeline has
  **no refresh mechanism** (one call per card add, no cron), so "best-effort
  promos" would silently rot — they are cut from scope. Store a nullable
  `base_reward_rate` (numeric %) + a free-text `rewards_currency` label
  ("Rakuten Points", "現金回饋", "LINE Points").

**Surfaces that change (US-only today) — and how to widen each:**

- **Network enum** — `CardNetwork` Literal in `app/models/cards.py` + CHECK in
  `supabase/migrations/20260516130000_cards_network_and_deactivated_at.sql`
  (`visa/mastercard/amex/discover/other`). Add **`jcb`** (dominant in JP, common
  in TW) and **`diners`**. Bounded universe (~6 global networks) → just widen the
  CHECK; keep it an enum.
- **Issuer enum** — **DONE (minimal slice).** Chose to **widen the CHECK +
  Literal** and add a `card_issuers` *metadata* table, rather than replace the
  CHECK with a free-text + table-validated key (the lower-risk path for ~12
  issuers; the `Literal` keeps compile-time safety and the
  `(user_id, issuer, last_four)` unique index untouched). `CardIssuer` Literal
  in `app/models/cards.py` + CHECK in
  `supabase/migrations/20260602120000_cards_intl_enums_and_columns.sql` gained
  the top JP (rakuten, smbc, jcb, aeon, epos, saison) and TW (cathay, esun,
  ctbc, taishin, fubon, union) issuers. The `card_issuers` table
  (`20260602120100`, `key`/`region`/`display_name`/`domain`) holds region +
  domain metadata, mirrored in `app/integrations/card_regions.py` (backend) and
  `frontend/src/lib/cardsApi.ts` (`ISSUER_REGION`/`ISSUER_LABELS`). **Deferred:**
  the long tail (View JP; more TW banks) — add issuer values to the CHECK +
  Literal + `card_issuers` seed + frontend maps in lockstep. If the list ever
  grows past what a CHECK comfortably holds, revisit the free-text + table
  approach.
- **Program enum** — `CardProgram` Literal (`UR/MR/TYP/Bilt/Other`) is
  US-specific. Becomes the free-text `rewards_currency` string above (point
  ecosystems are open-ended: 楽天ポイント, Vポイント, dポイント, ANA/JAL miles;
  TW mostly 現金回饋 / LINE Points / 街口).
- **Allowed-domains allowlist** — `CARD_LOOKUP_ALLOWED_DOMAINS` in
  `app/integrations/card_lookup.py`: NerdWallet, The Points Guy, US Credit Card
  Guide, Doctor of Credit — all US. Needs region-aware allowlists:
  - JP candidates: 価格.com (kakaku.com), クレジットカードの読みもの, mybest
  - TW candidates: 良心理財, 符碼記憶, Mr.Market, 卡優, PTT credit-card board
- **Lookup prompt** — `_SYSTEM_PROMPT` in `card_lookup.py` is English with US
  framing; needs to be region-/language-aware, and for JP/TW should ask for the
  base rate + rewards-currency label rather than category multipliers.
- **Annual fee** — `card_lookup.py` hardcodes "USD numeric". Resolve in the
  user's `home_currency` — **prompt wording only, store the numeric as-is. No
  FX, no conversion** (invariant 13 intact). If the lookup finds the fee quoted
  in a currency ≠ `home_currency`, **fail closed to null** (leave blank, let the
  user type it); never convert.

**Schema impact (as built, migrations `20260602120000`–`20260602120200`):**
widened `cards_network_check` (added jcb/diners) and `cards_issuer_check`
(added JP/TW issuers); added `cards.region` (NOT NULL, default 'US', CHECK
US/JP/TW) + `cards.base_reward_rate` + `cards.rewards_currency`; created +
seeded the `card_issuers` metadata table (no FK from `cards.issuer` — the CHECK
already enforces membership); amended `insert_card_with_af` to persist the
three new columns. `cards_program_check` was left as-is (program stays an enum;
JP/TW cards just store `'Other'`). **Not yet done:** the AF-recognition
heuristic (`name LIKE '% annual fee'` triple — memory.md 2026-05-19) is still
English-shaped; localized templates are a follow-up, and JP/TW card
branding/color is unchanged.

**Minimal-done slice — DONE (2026-06-02):** JCB + Diners on the network CHECK;
`card_issuers` seeded with the top ~6 JP + ~6 TW issuers; per-card `region`
column + region-routed base-rate-only lookup for `region != 'US'` (no
multipliers, no promos); `home_currency`-aware annual-fee prompt with
fail-closed-to-null; per-card region resolution — `propose_card` takes an
optional `region` Claude infers from the issuer/card name (chat is the only
reachable add surface in v1, invariant 8), with a home-currency fallback and a
known-issuer server pin at confirm; JP/TW base-rate UI on the chat parse card
and cards page (the onboarding `AddCardStep` form also has a region selector,
but that surface is bypassed on the natural flow — latent). Promos, the issuer
long tail, JP/TW branding, and localized AF-recognition templates remain
follow-ups.

**No new vendors needed:** Gemini, Haiku, and Claude `web_search` are all already
multilingual — the work is enums, a reference table, allowlists, prompts, and the
data-model additions above. The product/UX question that used to gate this is
resolved by the scope decision.

---

## Internationalization — remaining tiers (in progress / planned)

Captured here so the deferred-cards item has context. These are NOT deferred.

- **Tier 1 — locale correctness (English chrome): SHIPPED.**
  - ~~Fix `formatMoney` to honor `home_currency`~~ — **DONE.** The cents
    representation is value-safe for all 9 currencies (it stores major units
    ×100 as a precision trick, not a 100-minor-unit claim), so this was a
    display fix only: symbol + fraction digits via `Intl.NumberFormat`
    (`frontend/src/lib/format.ts`). Formatting locale comes from
    `displayLocale()` (browser language today, `ui_language` in Tier 2) —
    decoupled from currency per the 2026-06-01 axis-independence reversal, so
    an English-browser JPY user gets English dates with ¥ amounts.
  - ~~Fix `subscriptions.tsx` hardcoded `Intl.NumberFormat("en-US", USD)`~~ — DONE.
  - ~~Localize date helpers in `format.ts`~~ — DONE (`formatShortDate`,
    `formatMonth`, new shared `formatFullDate`).
  - ~~Swap the literal `$` amount-input prefix in the five edit sheets~~ — DONE
    (new `currencySymbol()` helper → ¥ / NT$ / £ / € as appropriate).
  - ~~Chat agent replies in the user's language~~ — DONE (`chat_v11`; tool args
    and category values stay canonical English).
  - (Voice was already trilingual — `frontend/src/lib/voice.ts`. No work.)

- **Tier 1.5 — per-user timezone for the digest: SHIPPED.**
  - ~~Add per-user `timezone` to `users_meta`; send digest at local 9am~~ —
    **DONE.** Migration `20260601120000`; nullable, mutable, validated via
    `app/util/timezone.py`. Captured at `/auth/bootstrap` from the browser,
    editable in Settings → Notifications. Cron now fires hourly (`0 * * * *`)
    and gates each user on their local Monday **09:00–noon** (a 3-hour retry
    budget: a failed 09:00 send is re-attempted at 10:00/11:00; a partial
    unique index keyed on the recipient's **local Monday date**
    (`email_log.dedup_week`, migration `20260601130000`) keeps it
    exactly-once — even for zones east of UTC+9 like Sydney where the retry
    window straddles the UTC week boundary). Week bounds computed in the
    user's zone. **OPERATOR:** the Railway `digest-cron` schedule must be
    changed from `0 14 * * 1` to `0 * * * *`.
  - ~~Localize the digest **narrative** + email template together~~ — **DONE**
    in Tier 2a (see below): `digest_v2` writes the narrative in `ui_language`,
    and the email chrome renders from a per-language string table.

  Supported language set: **`en`, `ja`, `zh-TW`** (Traditional Chinese only —
  Simplified Chinese is out of scope for now).

- **Tier 2a — i18n foundation: SHIPPED.** The `ui_language` axis and everything
  that keys off it, *minus* the broad JSX chrome extraction (which is Tier 2b).
  - ~~Add `ui_language` to `users_meta`~~ — **DONE.** Migration
    `20260601140000`; nullable, mutable, `CHECK (ui_language IN
    ('en','ja','zh-TW'))` (small fixed set, so a CHECK — unlike timezone's
    app-layer validation); mirrored in `app/util/language.py`. Snapshotted
    from `navigator.language` at `/auth/bootstrap`, on `/me`, editable via
    `PATCH /me/preferences` + a Settings → Account selector (`LanguageRow`).
  - ~~`displayLocale()` reads `ui_language`~~ — **DONE** (`format.ts`). Explicit
    `en` keeps the browser's regional English; `ja`/`zh-TW` pin a CJK locale;
    null falls back to the browser language.
  - ~~CJK font stack~~ — **DONE.** Noto Sans **JP + TC** added as the CJK
    fallback in `index.css`, region-ordered via `:lang()` (which keys off
    `<html lang>`, set from `ui_language` in `main.tsx`). The `lowercase`
    transforms stay (no-op on Han/Kana).
  - ~~Category *display* labels~~ — **DONE (all read-only surfaces).** Reactive
    `useCategoryLabel()` hook + `CATEGORY_LABELS` map in
    `frontend/src/lib/categories.ts`; backend mirror in
    `app/prompts/categories.py` for the digest. Stored enum stays English.
    Wired on the breakdown index/category/goal surfaces, the **home dashboard
    tiles** (`Dashboard.tsx`), and the **`/goals` page Pill**. The **edit-sheet
    category pickers, chat parse cards, and onboarding tour are deferred to Tier
    2b** (interactive/edit surfaces coupled to other English chrome strings).
  - ~~Chat reply language → setting-driven~~ — **DONE** (`chat_v12`). Replies in
    the user's `ui_language` regardless of input, via a directive in the
    prompt's dynamic tail (block[1], not the hashed preamble); mirror-input
    fallback (`chat_v11` behavior) only when `ui_language` is unset. Tool args
    and category values stay canonical English. Chat history is **not**
    retroactively translated (`chat_messages` is append-only).
  - ~~Localize the digest narrative + email template~~ — **DONE** (the Tier 1.5
    deferral above). Sonnet writes in `ui_language` (`digest_v2`); email
    subject/body/CTA/unsubscribe + top-category label render from a per-
    language string table in `app/services/digest.py`. `_format_money` renders
    the correct per-currency symbol + decimals (`¥1,500`, `NT$500.00`,
    `CHF 99.00`) for all nine `home_currency` currencies — JPY is zero-decimal.

- **Tier 2b — UI chrome translation: SHIPPED.**
  - ~~Add an i18n framework + extract hardcoded English JSX strings~~ — **DONE.**
    `i18next` + `react-i18next`, initialized in `frontend/src/lib/i18n.ts`,
    language driven by the store's `uiLanguage` (single source of truth — no
    language-detector). ~600 keys per language in
    `frontend/src/locales/{en,ja,zh-TW}.json` (one `translation` namespace,
    nested by surface). `en` captured verbatim (English rendering unchanged);
    `fallbackLng: 'en'`. Every page + component converted. Vitest setup
    initializes i18n so assertions resolve English.
  - ~~Wire category labels into the interactive/edit sites~~ — **DONE.** The
    edit-sheet pickers (`EditTransactionSheet`, `EditSubscriptionSheet`) and the
    chat parse cards display localized category via `useCategoryLabel()`; the
    selected/stored value stays the English enum.
  - **ja/zh-TW are DRAFTS** — generated for native-speaker (family) review; `en`
    is final. Refining specific translations is a copy-edit pass, not code.

  - ~~Mobile entry point for the language picker~~ — **DONE.** The mobile More
    menu never links to `/settings`, so the desktop Settings → Account placement
    was unreachable on the PWA. Added a "language" row in More → secondary that
    opens a sheet wrapping the shared `LanguageRow` (`more.tsx::LanguageSheet`),
    mirroring the Notifications-sheet pattern. Regression-pinned by
    `tests/more.language.test.tsx`.

  **Remaining (small, optional follow-ups, not blocking):**
  - `npm audit` flagged vulnerabilities in the dep tree when `react-i18next`
    was added — review/triage separately.
