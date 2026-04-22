# Day 21 — Philosophy screen + 4-screen guided tour

## Goal

First-launch onboarding. Philosophy screen pitches intentional entry. The 4-screen tour shows static fixture renders of the dashboard, entry-moment nudge, AI chat, and weekly digest — using real components, not screenshots.

## Read first

- `DESIGN.md` §5.4.1 (philosophy copy), §5.4.2 (tour spec — note: not a live demo).
- `CLAUDE.md` invariant 10 (no live AI on fake data, no fixture data in Supabase).

## Deliverables

- `frontend/src/pages/Philosophy.tsx`:
  - Full-screen, single-column, mobile-first.
  - The verbatim copy from `DESIGN.md` §5.4.1.
  - Two CTAs: **Get Started** (primary, → `/signin`), **Take the Tour** (secondary, → `/tour`).
  - Shown on first launch only. Persist `seen_philosophy=true` in `localStorage` after either CTA is clicked.
- `frontend/src/pages/Tour.tsx`:
  - 4 swipeable screens (touch swipe + arrow buttons). Pagination dots at the bottom.
  - **Screen 1 — Dashboard:** render the actual `<Dashboard>` component (built Day 13) with hardcoded fixture props. Numbers from a constant: `Dining +$47 above average`, `Groceries within average`, etc.
  - **Screen 2 — Entry-moment nudge:** a short looping animation showing the chat-based entry flow (UX frame 15 sequence) — user message "spent $47 at Trader Joe's" → parse card renders with five fields → "looks right" tap → confirmed transaction line → below it, the quiet AI insight bubble "4th dining transaction this week — you usually have 2." No toasts — in the chat-unified UX the nudge lives inline in the chat thread (CLAUDE.md invariant 8).
  - **Screen 3 — AI chat:** a static `<ChatThread>` (built Day 10) rendering one Q ("How much did I spend on dining last month?") and one A. No streaming, no API call.
  - **Screen 4 — Weekly digest:** a static rendering of the email layout with example numbers.
  - Final CTA: "This is Tameru with 3 months of data. Log your first transaction or import a CSV to get there." → `/signin`.
- A `frontend/src/fixtures/tour.ts` module exporting all the hardcoded data. Single source of truth so updating the tour data is one file.
- The Dashboard, ChatThread, and Digest components must accept all data via props — no hidden API calls. (Days 13, 10, and 25 build these out; today, build minimal versions sufficient for the tour and reuse them later.)

## Don't

- Don't write tour data to Supabase. Frontend fixtures only.
- Don't make any AI API calls during the tour. The chat-thread render is static.
- Don't make the philosophy screen skippable on first launch — it's the pitch. (Returning users land on `/home`, not `/philosophy`.)

## Done when

- First-time user lands on `/philosophy`, can take the tour or skip to sign in.
- Returning user (with `seen_philosophy=true`) lands on `/signin` or `/home`.
- Tour swipes smoothly on mobile. No network calls in the tour.
