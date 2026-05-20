# Day 21 — Philosophy screen + 4-screen guided tour

## Goal

First-launch onboarding. The philosophy screen sells intentional manual entry *before* the user signs up. The 4-screen tour shows the real `<Dashboard>` / `<ChatThread>` / digest components rendered with fixture data — not mocks — plus a short animated beat for the entry-moment insight.

This day reconciles the existing partial implementation (splash + philosophy + tour routes ship; the tour currently uses hand-rolled illustrations) with DESIGN.md §5.4.2's "they look real because they are real" invariant.

## Read first

- `DESIGN.md` §5.4.1 (philosophy copy — must be used verbatim) and §5.4.2 (tour spec, "real components rendered with fixtures").
- `CLAUDE.md` invariant 10 (no live AI on fake data, no fixture data in Supabase).
- `frontend/src/pages/onboarding.tsx` and `frontend/src/pages/onboarding.tour.tsx` — current state.

## Current state (what already ships)

- Wizard at `/onboarding` with internal step state: `splash → philosophy → signin → currency → addCard → csvImport → csvProcessing`. Step components live in `frontend/src/features/onboarding/`.
- Tour at `/onboarding/tour`, 4 screens with pagination dots and "skip the tour" link.
- Single localStorage flag `tameru-onboarded` (set by `markOnboarded()` in `frontend/src/lib/onboarding.ts`) gates the first-launch redirect via `RequireOnboarded` at `frontend/src/App.tsx`.

What still needs to change (this prompt):

- Splash currently hosts the primary "get started" CTA *before* the user has read the philosophy. Move it.
- Tour screens 1, 3, 4 use hand-rolled illustrations (`DashboardIllustration`, `ChatIllustration`, `DigestIllustration`). Replace with the real components rendered via fixture props.
- Tour screen 2 is a static card with no animation. Replace with a 4-beat animated sequence.
- Philosophy copy is paraphrased; must be the §5.4.1 block verbatim.
- Final CTA points only at "log your first transaction"; needs to also offer CSV import.
- Tour fixtures are inline; extract to `frontend/src/fixtures/tour.ts`.

## Deliverables

### Flow changes (`frontend/src/features/onboarding/`)

- `SplashStep.tsx`: drop the "get started" primary CTA. Splash becomes brand mark + tagline + a single "continue" primary button (→ philosophy) and a secondary "take the tour" text link.
- `PhilosophyStep.tsx`:
  - Replace shipped paraphrased copy with the verbatim DESIGN.md §5.4.1 block (the four `>` paragraphs).
  - Primary CTA: **get started** (→ `signin` step, the same `onContinue` callback).
  - Secondary text link: **take the tour** (→ `/onboarding/tour`, the same `onTour` callback).
  - This is the first screen where the user can sign up. The pitch gates the signup by construction.
- One flag only: `tameru-onboarded` set by `markOnboarded()` after wizard finish *or* after tour finish. No separate `seen_philosophy`. A user who bails mid-philosophy sees it again next launch — that is the desired behavior.

### Tour fixtures (`frontend/src/fixtures/tour.ts`, new)

Single source of truth for everything the tour renders. Shape (illustrative — implementer may adjust field names to match the actual component prop shapes):

```ts
export const tourFixtures = {
  dashboard: { /* DashboardSummaryWire shape — see frontend/src/lib/dashboardApi.ts */ },
  entryNudge: {
    userMessage: "spent $47 at Trader Joe's",
    parseCard: { merchant: "Trader Joe's", amount: 47, category: "groceries", card: "Chase Freedom", date: "today" },
    insight: "4th dining transaction this week — you usually have 2.",
  },
  chat: {
    question: "How much did I spend on dining last month?",
    answer: "$284 — about $54 below your 3-month average. Two restaurant visits and a takeout streak the week of the 10th drove most of it.",
  },
  digest: {
    subject: "your week, in brief",
    from: "Tameru <weekly@tameru.app>",
    bullets: [
      "spent $284, below your usual $340.",
      "dining is trending down for the second week.",
      "one subscription renews tuesday.",
    ],
  },
};
```

### Tour screens (`frontend/src/pages/onboarding.tour.tsx`)

- **Screen 1 — Dashboard.** Render the real `<Dashboard>` component with `tourFixtures.dashboard`. Requires the Day 13 component to accept its data via props (see "Component refactors" below). No hand-rolled tiles.
- **Screen 2 — Entry-moment nudge.** 4-beat animated sequence on mount, no user interaction required:
  1. User message bubble fades in: `tourFixtures.entryNudge.userMessage`.
  2. Parse card slides in below with five fields from `tourFixtures.entryNudge.parseCard`.
  3. "Looks right" tap visualization (the button highlights then a confirmed transaction line replaces the parse card).
  4. Quiet insight bubble fades in below the confirmed line: `tourFixtures.entryNudge.insight`.
  Pure CSS keyframes / Framer Motion are both acceptable; loop the sequence every ~6s so a user lingering on the screen sees it repeat.
- **Screen 3 — AI chat.** Render the real chat thread component (Day 10) with the one Q + A from `tourFixtures.chat`. No streaming, no API call, no input box (or a disabled input box). Requires the chat component to accept a static message list via props.
- **Screen 4 — Weekly digest.** Static rendering shaped like an email, not a card: subject line + from line + body container with email-feeling padding/typography. Reuse whatever digest body component Day 25 will ship if it already exists; otherwise build a minimal version now and Day 25 reuses it.
- **Final CTA (after screen 4).** Two buttons:
  - Primary: **import a CSV** → navigate to `/onboarding` and jump the wizard to the `csvImport` step (or to a signin-first path if the user isn't authed yet).
  - Secondary: **log my first transaction** → finish the tour, navigate to `/` (or to `/onboarding` if not yet signed in).
  Replace the current single-button "log your first transaction →" CTA.
- The "SAMPLE DATA" pill in the tour header stays — it's the correct affordance for fixture content.

### Component refactors (in-scope for Day 21)

The "render real components with fixtures" requirement only works if the components themselves accept data via props. Today they read from hooks. Refactor:

- `frontend/src/pages/home.tsx` (Dashboard page): the route component owns the `useDashboardSummary()` hook call. Extract the presentational `<Dashboard data={...} />` component (props-only, no hook). Page passes `data={summary}`; tour passes `data={tourFixtures.dashboard}`.
- The chat thread message list: extract the presentational `<ChatThread messages={...} />` component from wherever it currently couples to the chat store / SSE stream. Props-only. Tour passes `messages={[q, a]}`.
- These refactors should not change the behavior of `/` or `/chat` — the page-level components keep their existing data wiring; we're only pulling out the dumb-render layer.

## Don't

- Don't write tour data to Supabase. Frontend fixtures only.
- Don't make any AI API calls during the tour. The chat-thread render is static; the entry-moment animation is pre-scripted.
- Don't keep the primary "get started" CTA on splash — the philosophy screen has to gate signup.
- Don't add a separate `seen_philosophy` localStorage key. Single flag: `tameru-onboarded`.
- Don't paraphrase the §5.4.1 copy. If the wording needs to change, edit `DESIGN.md` first.
- Don't introduce hand-rolled mock components alongside the real ones. If a real component is hard to render with fixtures, fix the real component.

## Done when

- First-time user (no `tameru-onboarded` flag, no JWT) lands on `/onboarding`, sees splash → philosophy → signin in that order. Cannot reach signin without passing philosophy.
- Returning user (`tameru-onboarded=1` + JWT + `home_currency` set) lands on `/`. The `RequireOnboarded` gate enforces this.
- `/onboarding/tour` renders 4 screens. Screens 1 and 3 use the real `<Dashboard>` and `<ChatThread>` components with `tourFixtures` data. Screen 2 plays the 4-beat animation on mount and loops. Screen 4 looks like an email preview, not a card.
- Final tour CTA shows both "import a CSV" (primary) and "log my first transaction" (secondary).
- Philosophy screen shows the DESIGN.md §5.4.1 four paragraphs verbatim (a grep against the §5.4.1 block matches the rendered text).
- `frontend/src/fixtures/tour.ts` exists and is the only source of tour fixture data — no inline literals in the tour page.
- No network calls in the tour (verifiable in DevTools Network panel: 0 XHR on a full 4-screen pass).
- Tour swipes smoothly on mobile; touch swipe + arrow buttons + pagination dots all work.
