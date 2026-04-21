# Day 12 — Manual transaction entry + offline IndexedDB queue

## Goal

Tap **+** → enter merchant/amount/date/card → Gemini suggests category → one-tap confirm → saved. Offline entries queue in IndexedDB and sync on reconnect. Total time tap-to-save: under 10 seconds.

## Read first

- `DESIGN.md` §6.2 (entry flow + 10s target), §10.1 (offline requirements).

## Deliverables

- Frontend:
  - `frontend/src/pages/AddTransaction.tsx`:
    - Form: merchant (autocomplete from past merchants — fetch top 50 from `/transactions?search=` debounced 300ms), amount (numeric keypad on mobile), date (defaults today, tap to change), card (dropdown, recent first).
    - On merchant + amount filled: fire `POST /transactions/categorize_preview` (a new lightweight endpoint that calls Day 4's `categorize()` without inserting a row) to get the suggestion.
    - Display the suggestion as a one-tap confirm button + a "change" link that opens a category picker.
    - Submit button: large, thumb-reachable, bottom of viewport.
  - `frontend/src/lib/offline_queue.ts`:
    - IndexedDB-backed queue (`idb` library). Store: `pending_transactions`.
    - On `POST /transactions` failure due to network error, push to queue.
    - Service worker `online` event handler: drains the queue, POSTs each, removes on success.
    - UI: persistent badge "3 pending sync" while queue is non-empty.
  - The "+" button on `Home.tsx` opens this page.
- Backend:
  - `POST /transactions/categorize_preview` — body: `{merchant, amount}`. Returns `{category, confidence, gemini_suggestion}` from `categorize()`. Does **not** insert.

## Don't

- Don't auto-submit on category confirm. Submit is a separate explicit tap (avoids accidental saves).
- Don't store the JWT in IndexedDB. Pending entries are user-scoped because the queue itself is per-device-per-user (cleared on sign-out).
- Don't add receipt photo input today — Phase 2.

## Done when

- A transaction can be logged in under 10 seconds on a real phone (time it).
- Airplane mode → log 3 transactions → re-enable network → all 3 sync, badge clears.
- The category picker matches Gemini's suggestion 90% of the time on personal sample data (tracked in evals later).
