# Tameru — Claude Design Prompt · Web (v1)

You are generating a **single HTML artifact** showing 33 desktop-web UI frames for Tameru, a spending intelligence app. This is the sibling of `UX_PROMPT.md` (mobile/iOS, 36 frames) — same product, same design system, adapted for a desktop browser. Tameru ships as a PWA; the mobile frames target the installed-to-home-screen iPhone experience, these frames target the same app loaded in a desktop browser at `tameru.app`.

## Critical output instruction

Generate ALL 33 frames. Do not stop early. If you are approaching your output limit, compress the remaining frames — reduce their detail — but never skip a frame entirely. Every frame in the list below must appear in the output with at minimum a placeholder showing its label, background color, and basic layout structure. Frame 3a sits in Section 1 (Onboarding) between frames 3 and 4; frames 11a and 11b sit in Section 3 (Home) between frames 11 and 12.

## Canvas

Each frame: **1440px wide, 900px tall**, representing a desktop browser viewport. Wrap each frame in a subtle browser chrome mock — a 48px-tall bar at the top showing traffic-light dots (muted, not literal red/yellow/green), a centered pill-shape address bar reading `tameru.app` in tertiary text, and an optional right-side icon cluster (extensions/profile) rendered quietly. The chrome is ambient — it signals "this is a web app in a browser" without competing with the content. Below the chrome is the 1440×852 app region; measurements in the rest of this doc refer to that region unless noted.

Lay frames out in a CSS grid: **1 per row**, vertically stacked, labeled above with their name in small, quiet text. **No internal scrolling within a frame** — fit content within the frame height (when a list implies scroll, show it truncated with a gradient fade at the bottom edge; do not add a live scrollbar).

Light/dark toggle fixed top-right of the page (outside the frames). Default: light.

---

## Design System

The palette, typography, and material rules are **identical** to `UX_PROMPT.md`. Restating so this prompt stands alone:

**Palette — light mode (exact hex, locked):**
- Canvas: `#F5EFE4` · Surface: `#FBF6EC` · Elevated: `#FFFBF2` · Sunken: `#EDE5D5`
- Text primary: `#2A2620` · Secondary: `#5C564C` · Tertiary: `#8A8377` · Quaternary: `#B8B0A0`
- Borders: `rgba(42,38,32,0.08)` hairline, `rgba(42,38,32,0.12)` soft · Scrim: `rgba(42,38,32,0.32)`

**Palette — dark mode (exact hex, locked):**
- Canvas: `#1C1A17` · Surface: `#252320` · Elevated: `#2E2B27` · Sunken: `#17150F`
- Text primary: `#F0EADC` · Secondary: `#C4BDAC` · Tertiary: `#8A8377` · Quaternary: `#5C564C`
- Borders: `rgba(240,234,220,0.08)` hairline, `rgba(240,234,220,0.14)` soft · Scrim: `rgba(0,0,0,0.56)`

**Accent:** muted earthy moss green — sits quietly on cream, reads as active/on-brand. 4 tints: base, emphasis (deeper), soft (pale), wash (barely-there).

**Semantic:** warn = warm aged amber · over = muted terracotta · under = same moss as accent · neutral = mid-gray

**Fonts:** Fraunces (serif) for titles/display/numbers — always lowercase for screen titles. Inter (sans) for body/buttons. Load from Google Fonts. All currency/% use `font-variant-numeric: tabular-nums`.

**No drop shadows.** Depth = background color steps + hairline borders. **No sharp corners** anywhere (8–16px radii on surfaces, 20–999px on pills/buttons). **No gradients.** **No pure black/white.**

---

## Web-specific layout rules

These are the adaptations from the mobile prompt — cite them when an iOS-only affordance (bottom nav, status bar, swipe-left, Dynamic Island) would have appeared on a mobile frame.

**1. Left sidebar replaces bottom nav.** Persistent 272px rail on every core screen (onboarding frames 1–6 are full-bleed and do not show it). Sidebar structure, top → bottom:
- 48px top padding · `貯` wordmark + "tameru" serif lowercase (small)
- 32px gap · nav items, each ~40px tall, 14px Inter, hairline rule only under the section, no filled pills: `home` · `my cards` · `subscriptions` · `ai memory`
- hairline divider
- `settings`
- flex spacer
- user block at the bottom: small CY avatar + name primary + email tertiary + a quiet `sign out` link in terracotta (text, not a button).

Active nav item: serif-weight shifted to bold + a 2px moss vertical bar on the left edge flush to the sidebar's inner padding. Inactive: tertiary text. Hover on inactive: text shifts to secondary; no background fill.

**The sidebar has no "Ask Tameru" button.** The chat entry point is the persistent bottom composer (rule 2) — always visible, always typable. `⌘K` from anywhere focuses the composer. This is deliberate: an Intercom-style sidebar button would duplicate the composer's affordance and clutter the nav.

**2. Persistent bottom composer + right-drawer conversation.** The chat entry point is a composer bar floating at the bottom-center of the main pane on every core screen — not a hidden button waiting to be opened. It is the web analogue of the mobile bottom-nav chat button, but always-on-and-typable.

- **At rest:** ~600px wide (max, shrinks to fit narrower panes), 52px tall, centered horizontally *within the main pane* (not the full viewport — alignment follows content, not chrome), floating 24px above the bottom edge of that pane. Styling: elevated surface + soft hairline border, no shadow. Inside, left → right: a tertiary italic placeholder that **rotates through example prompts** (see rule 11), a muted mic glyph in a small moss-wash circle, and a `⌘K` kbd hint in tertiary mono. The composer is quiet — it reads as infrastructure, not a CTA.
- **On focus or typing:** the composer border shifts to a 1px accent stroke, the placeholder rotation pauses, and the mic glyph becomes interactive.
- **On submit (enter key or send glyph):** a 440px-wide right drawer slides in from the right edge with a 200ms ease, and the composer animates from bottom-center into the drawer's bottom — it is *one continuous element that relocates*, not a duplicate. The conversation appears in the drawer above the composer.
- **Drawer styling:** elevated surface + 1px hairline left-border, **no scrim on the main pane**. The main content behind it stays fully visible and interactive — clicking anywhere on the main pane collapses the drawer and animates the composer back to bottom-center. Inside the drawer: thin top hairline + close-X top-right + "new chat" icon beside it + an "↗ expand" glyph that promotes the drawer into full chat mode (frame 13) + conversation body + composer pinned to the drawer bottom.
- **Full-width chat mode (frame 13)** is opt-in via the expand glyph and occupies the full main-pane width beside the sidebar, for focused longer sessions. The sidebar stays visible. Most turns happen in the drawer; full mode is for history review and long-form exchanges.

**3. No iOS status bar, no Dynamic Island.** Those are phone-only. The browser chrome mock at the top of every frame (see Canvas) does that signaling job on web.

**4. Hover replaces swipe.** On transaction rows, card rows, subscription rows, memory tiles — hovering reveals two inline glyph buttons flush to the right edge of the row: a pencil (edit) and a terracotta trash (delete). No swipe-to-reveal. Frames that showed a mid-swipe state on iOS instead show one row in a hover state with the icons exposed.

**5. Modals are center-screen dialogs, not bottom sheets.** Width ~520px, vertically centered, with the same scrim/elevated surface/hairline-border treatment. Drag handles are removed; close-X top-right is retained. Bottom-sheet-style frames in the mobile prompt (12, 22, 27, 28, 29, 30, 11b) become centered dialogs here — except frame 12 (chat), which is the right drawer described above.

**6. Keyboard affordances are first-class.** Show them, quietly. `⌘K` from anywhere focuses the bottom composer. `↵` glyph in the composer's send icon. `esc` hint near modal close-X when relevant. `⌘\` collapses full-width chat mode back to the drawer. Use the monospace fallback family; keep them in tertiary text, never primary.

**7. Dashboard stays calm and single-screen.** The wider viewport does not mean more tiles — the design-doc rule ("one screen, adding a tile requires removing one") still applies. The extra horizontal room is absorbed by generous whitespace and a larger hero number; do not fill it with new widgets.

**8. Dashboard tile rule (locked, unchanged from mobile):** category tiles show **delta only** — e.g. "Dining: +$47 above average" — not the absolute monthly total. Delta is the signal; absolute is noise on a dashboard meant to fit one screen.

**9. Content column max-width.** Inside the main pane (1168px wide after the sidebar), center content at a comfortable ~840px max-width for dashboard/settings/detail pages, with the remaining gutter as negative space. Lists (cards, subs, memory, transactions) may go wider (up to ~1040px) when rows benefit from horizontal room. The goal is a calm reading experience, not a filled viewport.

**10. Bottom nav does not exist on these frames.** If a mobile frame's description in `UX_PROMPT.md` says "Bottom nav," ignore that line on web — the left sidebar is the nav, and it is always visible on core screens.

**11. Rotating placeholder as built-in onboarding.** The bottom composer's placeholder cycles through real example prompts on a ~3.2s interval, with a ~200ms crossfade between values. The cycle:

1. `ask tameru or log a transaction…`
2. `how is my spending in march?`
3. `spent $47 at trader joe's on my amex gold`
4. `what's my top category this month?`
5. `add netflix $15.99 monthly on my amex gold`

This replaces the mobile "welcome chips" pattern (mobile frame 7) as the primary way new users learn what the chat can do — they see real, plausible inputs rather than being told. Rotation pauses when the composer is focused or when the drawer is open. Show the composer in any one randomly-selected placeholder state per frame; do not animate inside the static HTML artifact, but specify the rotation in source comments so a future implementation knows the intended behavior.

---

## 33 Frames

Generate every frame below. Keep them simple and clean — wabi-sabi minimalism. Same content semantics as the iOS frames of the same number (see `UX_PROMPT.md` for the underlying meaning of each screen); this section describes the **web adaptation** only. Where a mobile-only affordance is explicitly replaced, say how.

### SECTION 1 — Onboarding

**1. Landing (Splash)** — full-bleed canvas, no sidebar yet. Centered column, ~560px wide: 貯 character large and centered, "tameru" serif below (larger than the mobile version — this is the hero moment), "the mindful ledger" italic tagline, one sentence of quieter body. Two buttons stacked: "get started" (primary, ~320px wide) + "take the tour" (text link below). No nav, no chrome beyond the browser bar. Subtle moss-wash radial vignette centered behind the mark is acceptable if kept under 5% alpha.

**2. Philosophy** — full-bleed, no sidebar. Centered column ~680px wide. Top-left quiet back chevron + "back" tertiary text link. "why manual?" title in Fraunces lowercase, centered. 4 short paragraphs of body copy, left-aligned within the column, comfortable line length (~64ch). Actions centered at bottom: "continue" primary + "take the tour" text link to the right.

**3. Sign-In** — full-bleed, no sidebar. Centered card-less column, ~420px wide. "tameru" wordmark centered. "sign in to begin" italic tagline. Google sign-in button (surface + hairline border + G-glyph, full-width within the column). "or" divider (hairline segments). "continue with email" secondary button full-width. Legal footer in tertiary micro — "by signing in you agree to…" with underlined terms/privacy links.

**3a. Confirm Home Currency** — full-bleed, no sidebar (identity not yet anchored to a nav rail). Centered column ~520px wide. Tiny tertiary micro-label "SETUP · PREFERENCE." Title Fraunces lowercase "your home currency." Subtitle secondary "this can't be changed later." Brief tertiary explainer: "all your spending stays in this currency. for trips abroad, enter the amount your card statement will show." **Currency selector: a single pill-shape dropdown** centered, showing the default (detected from browser locale, defaulting to "USD"); an inline popover below (not a bottom sheet) lists the nine allowed currencies — USD · EUR · GBP · CAD · AUD · JPY · CHF · SGD · TWD — each row showing code and full name with a quiet check for the current selection. "continue" primary full-width (accent) below the selector. Tertiary reassurance footer: "to change this later, you'll need to create a new account." No back chevron.

**4. Add First Card** — sidebar now present but nav items dimmed (nothing to click yet) except "home" which is quietly active. Main pane centered ~680px. Top: 2-step progress indicator (step 1 active, accent), "SETUP · STEP 1 OF 2" micro-label. "add your first card" Fraunces title. Brief subtitle. Card search input (sunken, pill-shape, magnifying glass, full column width). 3 suggestion chips below in a row. "add card" primary (disabled) + "skip for now" tertiary link beside it.

**5. CSV Import** — sidebar present, dimmed as in frame 4. Main pane centered ~680px. Both progress steps filled. "SETUP · STEP 2 OF 2." "backfill your history" Fraunces title. Subtitle tertiary. Dashed upload drop zone, full column width, ~200px tall, with a small cloud-up glyph and copy "drop a csv here, or click to browse." Per-bank instruction chips row below the drop zone (chase · amex · citi · bofa · capital one · wells fargo) — tappable hints, not filters. "continue with import" primary + "skip for now" tertiary.

**6. CSV Processing** — sidebar present, dimmed. Main pane centered. Both progress steps filled. "reading your transactions" Fraunces title. **Horizontal progress bar** (thin, ~560px wide, sunken track + moss fill at ~60%) — not the mobile's circular arc. "87 of 143" tabular counter below the bar, tertiary. "categorizing transactions…" status. Filename pill (sunken, file-glyph + "chase_activity_apr26.csv"). Tertiary note at the bottom: "keep this tab open while we finish." No buttons.

### SECTION 2 — First Launch

**7. First Launch · composer-as-focus** — sidebar active (home highlighted). Main pane shows the empty home (ledger icon + "your ledger is empty" centered, no dim, no scrim — the dashboard is genuinely empty, not hidden). The **bottom composer is the focal element**: rendered with a quiet 1px accent ring (a single static glow, not animated) and a higher contrast placeholder reading the second rotation value `how is my spending in march?` to demonstrate the rotation pattern. A small one-time tertiary caption sits ~12px above the composer: `try typing — examples rotate every few seconds.` This caption disappears after the user's first submitted message. No drawer is open yet; this frame shows what a new user sees on first sign-in.

### SECTION 3 — Home

**8. Home Default** — sidebar (home active). Main pane centered. Top bar inside the pane: "home" Fraunces lowercase left, "↗ breakdown" quiet accent text link right (small, understated), current month label "apr 2026" in tertiary beside it. **Hero block**: "$2,340" as the largest element on screen — larger than the mobile version, Fraunces serif, tabular-nums. Below it, an amber delta pill "+14% vs your avg." Below that, one calm observation sentence in secondary italic — a single line, ~60ch. Generous vertical whitespace. "CATEGORIES" tertiary micro-label below the observation. **2×2 tile grid showing delta only** (Dining +$47 warn / Groceries -$12 moss / Travel +$120 over / Subscriptions -$5 moss), tile width ~400px each, stacked on a neutral surface with hairline borders. No absolute totals on tiles. No toggle anywhere. Fits in one viewport without scroll.

**9. Home Empty State** — sidebar (home active). Main pane centered. Top bar: "home" left, no breakdown link. Centered vertically in the pane: ledger icon (~56px) + "your ledger is empty" Fraunces lowercase + subtitle secondary "type your first transaction below — tameru handles the rest." A short accent down-arrow from the subtitle pointing toward the bottom composer. The composer sits at its usual position with a soft 1px accent ring to draw the eye.

**10. Home Breakdown** — separate screen reached by clicking "↗ breakdown" on Home Default. Sidebar (home still active — breakdown is a sub-route of home). Main pane top bar: back chevron left + "breakdown" Fraunces lowercase centered. **Donut chart** in the upper portion (~360px diameter, centered or left-anchored with the legend to the right) with moss-family segments + neutral for other; "$2,340 this month" in the donut center, Fraunces, tabular-nums. Clickable category list below the chart, ~840px wide: each row has colored dot + category name left, absolute amount right-aligned + chevron-down glyph far right. Rows sorted by descending spend. Hairline dividers between rows.

**11. Breakdown Expanded** — same screen, "dining" row clicked and expanded: row gets soft accent-tinted background, chevron rotates up. Exactly 3 inline transaction rows appear below it (Blue Bottle Coffee $6.50 · Nobu $85.00 · Tartine $24.00) — slightly indented, each showing date-day in tertiary micro on the left. Below those: "most recent 3 · see all dining" as a quiet accent link (clicks into frame 11a). All other rows unchanged and clickable.

**11a. Category Transaction List** — reached by clicking "see all" in a Breakdown Expanded row. Sidebar (home active). Main pane top bar: back chevron left + category name in Fraunces lowercase centered ("dining"). Filter chips row below the top bar: month-selector pill ("apr 2026" with down-chevron, accent when current, secondary when past) · "all cards" pill with down-chevron. Sunken pill-shape search bar below the chips (full column width), magnifying glass, placeholder "search merchant…" in tertiary. Main area: transaction rows ~1040px wide — each row has date-day left in tertiary micro ("Apr 18"), merchant name primary center-left, amount right-aligned with tabular-nums, card last-4 in tertiary micro below the merchant. Hairline dividers. Show ~10 rows fitting on screen with a gradient fade at the bottom edge to imply more. **One row in hover state** revealing a pencil glyph and a terracotta trash glyph flush to the right edge (replacing the mobile's swipe-left terracotta panel).

**11b. Edit Transaction Dialog** — category transaction list (frame 11a) behind scrim. **Center-screen dialog** ~520px wide (not a bottom sheet), elevated surface + hairline border. Title row: "edit transaction" Fraunces lowercase left + close-X top-right + `esc` hint. Five editable field rows, same order as the chat parse card: merchant (text) · amount (numeric, tabular-nums, home-currency symbol inline) · date (pill with calendar glyph, inline date-picker popover affordance) · card (pill with card name, popover dropdown) · category (pill with down-chevron opening the closed enum: Groceries / Dining / Transportation / Travel / Entertainment / Shopping / Utilities / Health / Subscriptions / Other). Each row has a subtle pencil glyph on the right. Action row at the bottom: "save" primary (accent, shown in disabled state since nothing changed yet) + "cancel" secondary + "delete" in terracotta as a quieter text link on the far right. Save is disabled until any field changes.

### SECTION 4 — Chat

**12. Drawer · welcome state (no conversation yet)** — the moment the drawer first opens but before any user message. Sidebar visible (no scrim, no dimming on the main pane — the home dashboard is faintly visible to the left). Right drawer open at 440px wide. Drawer top bar: close-X left + "tameru" Fraunces centered + "new chat" icon + "↗ expand" glyph right. Drawer body, centered: small sparkle glyph + "hey chris." Fraunces lowercase + italic subtitle "what would you like to ask?" + 3 suggestion chips stacked vertically (`how much did i spend on dining this month?` · `which card earns the most for groceries?` · `add a new card`). Composer pinned at the drawer bottom: same morphed element from the page bottom, now ~408px wide inside the drawer, same placeholder rotation behavior, mic + `↵` hint on the right.

**13. Full-width chat mode** — opt-in expansion of the drawer via the "↗ expand" glyph. The drawer's 440px width animates out to fill the entire main pane (~1008px wide beside the 272px sidebar). The bottom composer of the page (which had been morphed into the drawer) stays at the bottom of this expanded chat region. Top bar of the chat region: close-X left (collapses back to drawer at 440px) + "tameru" Fraunces centered + "new chat" icon right + `⌘\` hint next to it. **Conversation body** centered at ~760px max-width inside the wider region, bubble layout: user bubble right (accent-tinted, asymmetric corners), AI response left (no fill, just prose) with a 2-bar moss chart inline + "via calculate_total" attribution in tertiary mono micro. Composer pinned at the bottom of the chat region, ~720px wide centered.

**14. Voice Active** — chat full-screen layout. Conversation above is dimmed slightly. Input row replaced by a listening state: centered pulsing accent ring around a large mic glyph (~72px), live interim transcript in lowercase secondary text ("spent forty seven dollars at trader joe's on my amex gold just now…") above the mic, stop button (terracotta, square glyph) on the right of the input region, "listening…" micro-label in accent above the mic. No send button while listening — transcript auto-submits 1.5s after user stops speaking. Optionally show a keyboard-shortcut hint "`esc` to cancel" in tertiary micro below the mic.

**15. Transaction Confirmation (in the drawer)** — sidebar visible, main pane fully visible (no scrim) showing the home dashboard at the left. Right drawer open at 440px. Inside the drawer, top → bottom: drawer top bar (close-X · tameru · new-chat · ↗expand) · user bubble right with input text `spent forty seven at trader joe's on my amex gold` · AI prose below `got it — here's what i parsed:` · **elevated parse card** spanning the drawer's content width (~376px), 5 rows (merchant / amount / date / card / category) each with a pencil glyph on the right · action row at the bottom of the card: `let me fix it` secondary + `looks right` primary (accent) side-by-side · tertiary micro `or just tell me what to change.` · a second AI bubble after the card with the Entry-Moment Insight: `that's your 3rd grocery run this week — a bit more than usual.` · composer pinned at the drawer bottom, the same morphed element from the page bottom, ready for the next message. The fact that the dashboard is still visible to the left — not hidden behind a scrim — is the load-bearing rendering choice for this frame: it shows the user that confirming this transaction will update the home view they can already see.

**16. Daily Cap Reached** — chat full-screen with one prior turn visible above. Inline elevated card in place of the input row: amber-tinted wash background + cap icon + "you've used your daily ai quota" title + secondary subtitle "resets at midnight utc." No retry button. Micro-text below in tertiary: "you can still log transactions and view your dashboard." Close-X in the chat top bar still present so the user can exit the chat.

### SECTION 5 — Settings

**17. Settings** — the web analogue of the mobile "More" menu, expanded into a proper two-pane Settings page (macOS System Settings pattern). Sidebar (settings active). Main pane laid out as two sub-columns:
- **Left rail (~260px)** inside the main pane: user identity row at top (CY avatar + name primary + email tertiary), hairline divider, settings categories as a vertical list — "account" · "integrations" · "notifications" · "privacy" · "import data" · "export data," each ~40px tall, active item shows the 2px moss bar on its left edge. "sign out" at the very bottom of the rail in terracotta.
- **Right pane (remaining width, content centered at ~560px)**: default shows the "account" section — "account" Fraunces title, rows for email (read-only), home currency (read-only, shows the chosen code + a quiet "immutable" tertiary note), and "delete account" terracotta text link as the last row. Other categories (my cards, subscriptions, ai memory, connect to claude.ai) are reachable from the sidebar's top-level nav, **not** from this Settings page — the sidebar is the primary map of the app.

Version micro-text in tertiary pinned to the bottom of the right pane.

### SECTION 6 — Cards

**18. Cards List** — sidebar ("my cards" active). Main pane: top bar with "my cards" Fraunces lowercase left, tertiary count "3 cards." Card rows ~1040px wide, each ~88px tall with a colored left-edge stripe (4px accent-tinted per card) + card name (Fraunces) + last-4 tertiary micro below + program chip (UR / MR / Bilt) on the right + multiplier chips row below the name (e.g., "4x dining · 3x travel"). Hairline dividers between rows. AI hint footer below the list in tertiary: "✨ add a new card via tameru ai →" — clicks open the chat drawer with a pre-seeded prompt.

**19. Cards Row Hover (delete affordance)** — same list, second card row in hover state: the row's background shifts to elevated, and two inline glyph buttons appear flush to the right edge — pencil (edit) and terracotta trash (delete). Replaces the mobile swipe-delete panel. Clicking trash opens a small confirm popover anchored to the trash icon, not a full modal.

**20. Cards Empty** — sidebar ("my cards" active). Main pane centered vertically: card icon (~56px) + "no cards yet" Fraunces lowercase + subtitle secondary "add one so tameru can track your rewards." Primary accent button "ask tameru to add a card" centered below — clicking opens the chat drawer with a seeded prompt. The tertiary "you can also type `add card` in chat anytime" micro-note below.

### SECTION 7 — Subscriptions

**21. Subscriptions List** — sidebar ("subscriptions" active). Main pane: top bar with "subscriptions" Fraunces lowercase left, tertiary count "4 active." 4 rows ~1040px wide (netflix active / spotify active / disney+ paused dimmed / notion active). Each row: name Fraunces + amount tabular-nums right-aligned + next-billing date tertiary micro + auto-logged 🔄 badge on the next-to-bill row. Paused row at ~55% opacity with "paused · no upcoming charges" in tertiary italic. Hairline dividers. AI hint footer: "✨ add or edit via tameru ai →".

**22. Subscription Detail Dialog** — subscriptions list behind scrim. Center-screen dialog ~520px wide with "netflix" Fraunces title + close-X + `esc`. Two-column info block inside the dialog (label left in tertiary micro, value right in primary): amount · frequency · next billing · card · category · started. Action row at the bottom: "pause subscription" secondary + "cancel subscription" destructive text link on the far right. Tertiary note: "to edit details, ask tameru ai."

### SECTION 8 — AI Memory

**23. Memory List** — sidebar ("ai memory" active). Main pane: top bar with "ai memory" Fraunces lowercase left + capacity counter "38 / 60 facts" in tertiary far right. Intro paragraph in secondary below the top bar, ~560px wide: "what tameru remembers about you · you can edit or remove anything." **Fact tiles grid**, 2 columns of tiles at ~500px wide each (the wider viewport fits a 2-col grid; keep gutters generous, ~24px). Each tile: category chip at top (card preference / goal / spending pattern / active context / preference) + fact text below in primary. AI hint footer: "✨ add or correct facts via tameru ai →".

**24. Memory Delete** — same list. One tile highlighted (elevated + terracotta border tint) with an inline confirm row below its text: "remove this fact" terracotta primary + "cancel" secondary beside it. Other tiles dimmed to ~55%.

### SECTION 9 — Privacy & Integrations

**25. MCP Tokens (Connect to Claude.ai)** — sidebar ("settings" active, and within settings the "integrations" rail item highlighted — show the two-pane Settings layout here). Right pane centered at ~640px: "connect to claude.ai" Fraunces lowercase title. Explanatory paragraph in secondary: "create a token to query your spending from claude.ai or claude code · read-only." "generate token" primary button. Hairline divider. List of existing tokens below — each row: name (Fraunces small) + last-used tertiary micro + "revoke" link in terracotta on the right. Empty state below if no tokens: "no tokens yet" in tertiary italic centered. Tertiary footer hint: "tokens are read-only · revoke any time."

**26. Token Generated Dialog (one-time view)** — the same Integrations pane behind scrim. Center-screen dialog ~560px wide. "token created" Fraunces title + close-X. Alarm icon + amber warning row: "this is the only time you'll see this token." Monospace token string in an elevated tile spanning the dialog width, truncated middle with a copy glyph on the right. "copy" primary button full-width below. Instructions footer in tertiary: "in claude.ai → settings → connectors → add → URL: `https://tameru.app/mcp` · header: `Authorization: Bearer …`". "done" secondary at the very bottom of the dialog.

### SECTION 10 — Modals

**27. Import Data Dialog** — sidebar + Settings behind scrim. Center-screen dialog ~560px wide with "import history" Fraunces title + close-X. Subtitle secondary. Dashed drop zone inside the dialog. Per-bank chips row below. "upload and categorize" primary + "see per-bank instructions" tertiary link.

**28. Notifications Dialog** — Settings behind scrim. Center-screen dialog ~480px wide with "notifications" title + close-X. Subtitle secondary. **2 toggle rows** (weekly digest ON / entry nudges ON) — iOS-style switches in the moss tint when on. "save" primary at the bottom + "cancel" secondary beside it. No anomaly alerts toggle — not in v1.

**29. Export Data Dialog** — Settings behind scrim. Center-screen dialog ~480px wide, compact. "export your data" title + close-X. Privacy subtitle secondary: "everything tameru knows about your spending, in one file." JSON-only label (no format selector). "download" primary + "email to me" secondary side-by-side.

**30. Sign Out Dialog** — any core screen behind scrim. **Centered alert dialog** ~400px wide (smaller than other modals, emphatic). "sign out?" title. Reassuring body in secondary: "you can sign back in any time. your data stays safe in your account." "cancel" secondary + "sign out" terracotta destructive side-by-side.

### SECTION 11 — System States

**31. Device Displaced Modal** — full-viewport modal over deep scrim (the whole app including sidebar is scrimmed). Centered dialog ~480px wide: device icon (~56px) + "you signed in on another device" Fraunces lowercase title + secondary subtitle "this session has ended" + reassuring body in tertiary "your data is safe in your account · sign in here to continue." Single "sign in again" primary button, full-width within the dialog. **No close-X** (this is a hard interrupt).

**32. Offline Banner** — Home Default (frame 8) with a banner inserted between the top app bar and the hero block, full-width across the main pane: sunken background + cloud-off icon + "offline — entries queue locally and sync when you're back." The "↗ breakdown" link in the top bar is visible but dimmed. Near "CATEGORIES": "· 1 pending sync" in small tertiary. The bottom composer is rendered at ~55% opacity with its placeholder replaced by the static tertiary line `chat needs a connection — entries still queue locally`; mic and `⌘K` hint are dimmed; rotation is paused.

**33. Weekly Digest Email** — render the digest as it would appear in a desktop Gmail-style client (this frame does **not** show the Tameru app itself — it is the email the app sends). Outer browser chrome still present. Inside: a faux email client layout — thin left rail with Gmail-like folders (muted greys, no brand colors) + main column with inbox bar top (back/archive/more) + sender block (tiny 貯 avatar in accent + "tameru" + "hello@tameru.app" + timestamp) + "your week in spending · apr 14–20" subject in Fraunces + hairline divider + email body: "$487 spent last week" + "+8% vs your avg" pill + observation sentence in secondary + nudge in an accent-tinted card + "sent weekly · manage in settings" tertiary footer. Everything fits without scroll.

---

## Parity note

Frames 1 through 33 in this web prompt correspond **one-to-one** to frames 1 through 33 of `UX_PROMPT.md` with the same numbers. Mobile frame 7 (First Launch Drawer) → web frame 7 (First Launch with Chat Drawer Open). Mobile frame 11a (Category Transaction List) → web frame 11a. Etc. The content semantics and flow identity are preserved; only the canvas, navigation, and gesture affordances change. If there is ever a conflict between this prompt and the design invariants in `DESIGN.md` (dashboard shape, chat as the only write surface, delta-only tiles, single home currency, propose-then-confirm mutation flow), the design document wins — this prompt is a visual specification, not an architectural one.
