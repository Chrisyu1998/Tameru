# TODO

Tracked-but-deferred work. Not a backlog dump — each item is something we
consciously decided to do *later*, with enough context to pick it up cold.

For shipped architecture and the *why* behind decisions, see `DESIGN.md` and
`memory.md`. This file is only for "we agreed to defer this."

---

## Internationalization — credit cards (DEFERRED)

**Status:** deferred. The rest of the i18n effort (currency/number formatting,
dates/timezone, chat + voice language, translated UI) is proceeding without it.
JP/TW users can add cards manually (name + last 4); they just won't get
automated reward-multiplier lookup until this lands.

**Why deferred:** international card support is the hardest, lowest-confidence
slice of i18n, and the reward-lookup value proposition is genuinely weaker
outside the US (see caveat below). Decoupling it lets the high-value
locale/language work ship fast.

**What's US-only today** (the surfaces that need to change):

- **Network enum** — `app/models/cards.py`, `cards` table CHECK
  (`supabase/migrations/20260421120100_cards.sql`): `visa/mastercard/amex/discover`.
  Missing **JCB** (dominant in Japan, common in Taiwan) and Diners.
- **Issuer enum** — closed US-bank CHECK (`chase`, `amex`, `citi`, …). Does not
  scale to 楽天/Rakuten, 三井住友/SMBC, JCB, イオン/AEON, エポス/Epos,
  セゾン/Saison, View (JP) or 國泰世華, 玉山/E.SUN, 中信/CTBC, 台新/Taishin,
  富邦/Fubon, 聯邦 (TW). Likely move to region-partitioned enums or free text
  + a known-issuer lookup table.
- **Program enum** — `UR/MR/TYP/Bilt` is US-specific. JP: 楽天ポイント, Vポイント,
  Oki Dokiポイント, dポイント, ANA/JAL miles. TW: mostly 現金回饋 (cash rebate),
  LINE Points, 街口. Probably becomes a freer "rewards currency" string.
- **Allowed-domains allowlist** — `app/integrations/card_lookup.py`
  (`CARD_LOOKUP_ALLOWED_DOMAINS`): NerdWallet, The Points Guy, US Credit Card
  Guide, Doctor of Credit — all US. Needs region-aware allowlists:
  - JP candidates: 価格.com (kakaku.com), クレジットカードの読みもの, mybest
  - TW candidates: 良心理財, 符碼記憶, Mr.Market, 卡優, PTT credit-card board
- **Lookup prompt** — `_SYSTEM_PROMPT` in `card_lookup.py` is English with US
  framing; needs to be region-/language-aware.
- **Annual fee** — prompt says "USD numeric"; should resolve in the user's
  `home_currency`.

**Schema impact:** widening `cards_network_check`, `cards_issuer_check`,
`cards_program_check` are migrations. The issuer change also touches card
color/branding logic and the annual-fee recognition heuristic
(`name LIKE '% annual fee'` triple — see memory.md 2026-05-19 entries).

**The product decision that gates this (resolve before building):** JP/TW card
rewards don't map cleanly to stable category multipliers the way US cards do.
Japanese rewards lean on partner-economy point ecosystems (spend at 楽天市場 →
bonus) rather than "3x dining," and **Taiwanese cards rotate bonus categories
quarterly** — there is often no stable multiplier to look up. The feature likely
has to degrade gracefully to "base cash-back %" + "best-effort current promos."
Decide the target UX before touching code.

**No new vendors needed:** Gemini, Haiku, and Claude `web_search` are all already
multilingual — the work is enums, allowlists, prompts, and the UX decision above.

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

  **Remaining (small, optional follow-ups, not blocking):**
  - Add `ui_language` selection to the mobile More → Notifications sheet if a
    second mobile entry point is wanted (today reachable via Settings → Account
    on both desktop and mobile).
  - `npm audit` flagged vulnerabilities in the dep tree when `react-i18next`
    was added — review/triage separately.
