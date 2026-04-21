# Tameru — Claude Design Prompt (v3)

You are generating a **single HTML artifact** showing 33 mobile UI frames for Tameru, a spending intelligence app.

## Critical output instruction

Generate ALL 33 frames. Do not stop early. If you are approaching your output limit, compress the remaining frames — reduce their detail — but never skip a frame entirely. Every frame in the list below must appear in the output with at minimum a placeholder showing its label, background color, and basic layout structure.

## Canvas

Each frame: 390px wide, 844px tall, phone chassis with rounded corners. Lay frames out in a CSS grid: 3 per row on wide viewports, 1 per row on narrow. Label each frame above it with its name in small, quiet text. **No internal scrolling — fit content within the frame height.**

Light/dark toggle fixed top-right. Default: light.

---

## Design System

**Palette — light mode (exact hex, locked):**
- Canvas: `#F5EFE4` · Surface: `#FBF6EC` · Elevated: `#FFFBF2` · Sunken: `#EDE5D5`
- Text primary: `#2A2620` · Secondary: `#5C564C` · Tertiary: `#8A8377` · Quaternary: `#B8B0A0`
- Borders: `rgba(42,38,32,0.08)` hairline, `rgba(42,38,32,0.12)` soft · Scrim: `rgba(42,38,32,0.32)`

**Palette — dark mode (exact hex, locked):**
- Canvas: `#1C1A17` · Surface: `#252320` · Elevated: `#2E2B27` · Sunken: `#17150F`
- Text primary: `#F0EADC` · Secondary: `#C4BDAC` · Tertiary: `#8A8377` · Quaternary: `#5C564C`
- Borders: `rgba(240,234,220,0.08)` hairline, `rgba(240,234,220,0.14)` soft · Scrim: `rgba(0,0,0,0.56)`

**Accent:** muted earthy moss green — sits quietly on cream, reads as active/on-brand. Needs 4 tints: base, emphasis (deeper), soft (pale), wash (barely-there).

**Semantic:** warn = warm aged amber · over = muted terracotta · under = same moss as accent · neutral = mid-gray

**Fonts:** Fraunces (serif) for titles/display/numbers — always lowercase for screen titles. Inter (sans) for body/buttons. Load from Google Fonts. All currency/% use `font-variant-numeric: tabular-nums`.

**No drop shadows.** Depth = background color steps + hairline borders. **No sharp corners** anywhere. **No gradients.** **No pure black/white.**

**Bottom nav (every core screen):** Home left · Chat center (raised circular button in accent, speech-bubble + sparkle icon) · More right. Active = accent. Inactive = tertiary.

**Status bar + Dynamic Island:** every frame shows iOS status bar (9:41, signal/wifi/battery) and a black Dynamic Island pill centered at the top.

**Dashboard tile rule (locked):** category tiles show **delta only** — e.g. "Dining: +$47 above average" — not the absolute monthly total. The delta is the signal; the absolute is noise on a dashboard meant to fit one screen.

---

## 33 Frames

Generate every frame below. Keep them simple and clean — wabi-sabi minimalism. If a frame is small enough, render it simply rather than skipping it.

### SECTION 1 — Onboarding

**1. Splash** — 貯 character large and centered, "tameru" serif below, "the mindful ledger" italic tagline, one sentence, two buttons: "get started" (primary) + "take the tour" (text link). No nav.

**2. Philosophy** — back chevron, "why manual?" title, 4 short paragraphs explaining intentional entry, "continue" primary + "take the tour" text link.

**3. Sign-In** — "tameru" wordmark centered, "sign in to begin" italic tagline, Google sign-in button, "or" divider, "continue with email" secondary, legal footer.

**4. Add First Card** — 2-step progress indicator (step 1 active, accent), "SETUP · STEP 1 OF 2" micro-label, "add your first card" title, brief subtitle, card search input (sunken, pill-shape, magnifying glass), 3 suggestion chips below, "add card" primary (disabled) + "skip for now" tertiary. Bottom nav.

**5. CSV Import** — both progress steps filled, "SETUP · STEP 2 OF 2", "backfill your history" title, dashed upload drop zone, "continue with import" primary + "skip for now" tertiary. Bottom nav.

**6. CSV Processing** — both steps filled, "reading your transactions" title, circular progress arc (~60% complete), "87 of 143" tabular counter, "categorizing transactions…" status, filename pill, "keep the app open" note. No buttons.

### SECTION 2 — First Launch

**7. First Launch Drawer** — empty home screen dimmed behind scrim, chat sheet half-open, drag handle, close-X, "welcome, chris." title, subtitle, **2 one-time chips** (log by voice · just say what you spent), quiet note they disappear after first transaction, input row at bottom (text · mic).

### SECTION 3 — Home

**8. Home Default** — top bar: "home" Fraunces lowercase left, "↗ Breakdown" quiet text link right in accent color (small, understated). Hero "$2,340" as the largest element on screen, "+14% vs your avg" amber delta pill below it, calm observation sentence in secondary. "CATEGORIES" label, 2×2 tile grid showing **delta only** (Dining +$47 warn / Groceries -$12 moss / Travel +$120 over / Subscriptions -$5 moss). No absolute totals on tiles. No toggle anywhere on this screen. Bottom nav.

**9. Home Empty State** — top bar: "home" left, no Breakdown link (nothing to break down). Centered vertically: ledger icon + "your ledger is empty" + subtitle directing user to the chat button + downward arrow in accent pointing to center nav. Chat button in nav has subtle pulse ring. Bottom nav.

**10. Home Breakdown** — separate screen reached by tapping "↗ Breakdown" on Home Default. Top bar: back chevron left + "breakdown" Fraunces lowercase centered. No toggle — its own destination. Donut chart in upper portion with moss-family segments + neutral for other, "$2,340 this month" in donut center. Tappable category list below: each row has colored dot + category name left, absolute amount + chevron-down right. Rows sorted by descending spend. Bottom nav.

**11. Breakdown Expanded** — same screen, but "Dining" row tapped and expanded: row gets soft accent-tinted background, chevron rotates up. Exactly 3 inline transaction rows appear below it (Blue Bottle Coffee $6.50 · Nobu $85.00 · Tartine $24.00). Below those: "most recent 3. ask tameru for more." as a quiet accent link. All other rows unchanged and tappable. Back chevron still present. Bottom nav.

### SECTION 4 — Chat

**12. Chat Half-Sheet** — home dimmed, bottom sheet half-height, drag handle + close-X, sparkle + "hey chris." + subtitle, 3 suggestion chips, input row (text center "log a transaction…" · mic right in accent). No bottom nav visible.

**13. Chat Full-Screen** — sheet fills screen, drag handle, top bar: down-chevron left + "tameru" centered + new-chat icon right. Conversation: user bubble right (accent-tinted) asking about dining spend, AI response left (no fill) with prose + 2-bar moss chart, "via calculate_total" attribution. Input row at bottom.

**14. Voice Active** — chat full-screen, conversation dimmed above, input row transformed into a listening state: pulsing accent ring around a large mic glyph (center), live interim transcript shown in lowercase secondary text ("spent forty seven dollars at trader joe's on my amex gold just now…"), stop button (terracotta, square glyph) to the right, "listening…" micro-label in accent. No send button while listening — transcript auto-submits 1.5s after user stops speaking.

**15. Transaction Confirmation** — chat full-screen, user bubble with input text "spent forty seven at trader joe's on my amex gold" (either typed or voice-transcribed), AI prose "got it — here's what i parsed...", elevated parse card (5 rows: merchant/amount/date/card/category each with pencil glyph), "let me fix it" secondary + "looks right" primary, "or just tell me what to change" micro-text, second AI bubble with insight "that's your 3rd grocery run this week — a bit more than usual." Input row.

**16. Daily Cap Reached** — chat full-screen with one prior turn visible above. Inline elevated card in place of input row: amber-tinted background + cap icon + "you've used your daily ai quota" title + "resets at midnight utc" subtitle in secondary. No retry button (retry won't help). Other Tameru features (dashboard, manual entry) still work — micro-text below: "you can still log transactions and view your dashboard." Down-chevron in top bar still present.

### SECTION 5 — More

**17. More Menu** — "more" title, user identity row (CY avatar + name + email), divider, primary section: "my cards" + "subscriptions" + "ai memory" + "connect to claude.ai" (all with chevrons), divider, secondary section: "import data" + "notifications" + "privacy" + "export data" + "sign out" (terracotta), version micro-text. Bottom nav More active.

### SECTION 6 — Cards

**18. Cards List** — back chevron + "my cards", 3 card tiles each with colored left-edge stripe + card name + last-4 + program chip + multiplier chips, AI hint footer "✨ add a new card via tameru ai →". Bottom nav.

**19. Cards Swipe Delete** — same list, top card shifted left revealing terracotta delete panel on right. Bottom nav.

**20. Cards Empty** — centered: card icon + "no cards yet" + subtitle + "add via tameru ai" primary. Bottom nav.

### SECTION 7 — Subscriptions

**21. Subscriptions List** — back chevron + "subscriptions", 4 rows (netflix active / spotify active / disney+ paused dimmed / notion active), paused row at reduced opacity with "paused · no upcoming charges", auto-logged 🔄 badge on the next-billing row, AI hint footer. Bottom nav.

**22. Subscription Detail** — subscriptions list behind scrim, bottom sheet with "netflix" title, two-column info block (amount/frequency/next billing/card/category/started), "pause subscription" secondary + "cancel subscription" destructive text, "to edit, ask tameru ai" micro-text.

### SECTION 8 — AI Memory

**23. Memory List** — back chevron + "ai memory", intro paragraph "what tameru remembers about you · you can edit or remove anything", capacity counter ("38 / 60 facts") in tertiary text, fact tiles with category chips (card preference / goal / spending pattern / active context / preference), fact text. AI hint footer. Bottom nav.

**24. Memory Delete** — same list, one tile highlighted (elevated + terracotta border tint), "remove this fact" terracotta + "cancel" beside it, other tiles dimmed. Bottom nav.

### SECTION 9 — Privacy & Integrations

**25. MCP Tokens (Connect to Claude.ai)** — back chevron + "connect to claude.ai", explanatory paragraph "create a token to query your spending from claude.ai or claude code · read-only", "generate token" primary button, divider, list of existing tokens (each row: name + last-used + revoke link in terracotta). Empty state below if no tokens: "no tokens yet". Footer hint "tokens are read-only · revoke any time". Bottom nav More active.

**26. Token Generated (one-time view)** — same screen with a sheet expanded from bottom: "token created" title, alarm icon + warning "this is the only time you'll see this token" in amber tint, monospace token string in elevated tile, "copy" primary button, instructions footer "in claude.ai → settings → connectors → add → URL: https://tameru.app/mcp · header: Authorization: Bearer …", "done" secondary at bottom.

### SECTION 10 — Modals

**27. Import Data** — more menu behind scrim, bottom sheet, "import history" title, subtitle, dashed drop zone, card chips, "upload and categorize" primary + "see per-bank instructions" tertiary.

**28. Notifications** — more behind scrim, bottom sheet, "notifications" title, subtitle, **2 toggle rows** (weekly digest ON / entry nudges ON), "save" primary. (No anomaly alerts toggle — not in v1.)

**29. Export Data** — more behind scrim, compact bottom sheet, "export your data" title, privacy subtitle "everything tameru knows about your spending, in one file", JSON-only (no segmented selector), "download" primary + "email to me" secondary.

**30. Sign Out** — centered dialog over scrim, "sign out?" title, reassuring body, "cancel" secondary + "sign out" terracotta destructive side-by-side.

### SECTION 11 — System States

**31. Device Displaced Modal** — full-screen modal over scrim. Centered: device icon + "you signed in on another device" title + "this session has ended" subtitle in secondary + reassuring body "your data is safe in your account · sign in here to continue". Single "sign in again" primary button. No close-X (this is a hard interrupt).

**32. Offline Banner** — Home Default with a banner inserted between the top app bar and the hero block: sunken background + cloud-off icon + "offline — entries queue locally and sync when you're back". The "↗ Breakdown" link is still visible in the top bar but dimmed. Near "THIS MONTH": "· 1 pending sync" in small tertiary. Bottom nav.

**33. Weekly Digest Email** — faux email client: inbox bar top (back/archive/more), sender block (tiny 貯 avatar in accent + "tameru" + "hello@tameru.app" + timestamp), "your week in spending · apr 14–20" subject in Fraunces, hairline divider, email body: "$487 spent last week" + "+8% vs your avg" pill + observation sentence + nudge in accent-tinted card + "sent weekly · manage in settings" footer. Everything fits without scroll.
