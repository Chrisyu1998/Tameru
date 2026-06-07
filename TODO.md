# TODO

Tracked-but-deferred work. Not a backlog dump — each item is something we
consciously decided to do *later*, with enough context to pick it up cold.

For shipped architecture and the *why* behind decisions, see `DESIGN.md` and
`memory.md`. This file is only for "we agreed to defer this."

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
