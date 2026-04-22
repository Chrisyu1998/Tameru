# Day 9 — Auth UI + single-device enforcement + home-currency capture at signup

## Goal

Sign in with Google, persist the session, enforce single active device per user, and capture the user's **home currency** at first sign-in (CLAUDE.md invariant 13 — immutable once set). A second sign-in displaces the first, with the copy from `DESIGN.md` §9.1.

## Read first

- `DESIGN.md` §9.1 (single device — read carefully), §8.7 (`users_meta` including `home_currency`).
- `CLAUDE.md` invariants 5, 13.

## Deliverables

- Backend:
  - `app/routes/auth.py`:
    - `POST /auth/bootstrap` — upserts a `users_meta` row for the current user if one doesn't exist: `{user_id, active_device_id, home_currency}`. Called by the frontend on the **first** sign-in after the user picks their currency on the onboarding confirm step. `home_currency` is validated against the CHECK-constrained set from the migration (`USD, EUR, GBP, CAD, AUD, JPY, CHF, SGD, TWD`). Subsequent calls are idempotent — they update `active_device_id` only; any attempt to change `home_currency` is rejected by the DB trigger (invariant 13). Returns `{home_currency, active_device_id}`.
    - `POST /auth/claim_device` — body: `{device_id}`. Updates `users_meta.active_device_id = device_id` for the current user. Called immediately after sign-in for returning users who already have a `users_meta` row.
    - `GET /auth/check_device` — query param `device_id`. Returns `{is_active: bool, active_device_id, active_since}`. Called periodically by the frontend to detect displacement.
    - Extend Day 3's `GET /me` to also return `home_currency` (one `users_meta` SELECT per call). Result shape becomes `{user_id, email, home_currency}`. UI reads this on load to pick currency symbols.
  - On every other authenticated route, add a dependency that compares the request's `X-Device-Id` header to `users_meta.active_device_id`. If mismatch, return 401 with `{code: "DEVICE_DISPLACED"}`.
- Frontend:
  - `frontend/src/lib/auth.ts`:
    - Initializes the Supabase JS client with `persistSession: true`.
    - On sign-in, generates a stable `device_id` (UUID stored in `localStorage`).
    - **New-user path:** if `GET /auth/me` returns no `users_meta` row yet (or `home_currency` is absent), route to the home-currency confirm step (below) before any other bootstrap. On confirm, `POST /auth/bootstrap` with `{device_id, home_currency}`.
    - **Returning-user path:** `POST /auth/claim_device` immediately.
    - Background poll every 60s on `GET /auth/check_device`.
  - `frontend/src/pages/SignIn.tsx`:
    - Single "Sign in with Google" button (UX frame 3).
    - Magic link as a "More options" disclosure.
  - `frontend/src/pages/ConfirmHomeCurrency.tsx` (new, sits between sign-in and onboarding's Add First Card in UX frame 4):
    - Renders only on first sign-in when `home_currency` is not yet set.
    - Copy: "your home currency — this cannot be changed later" + small explainer "all your spending stays in this currency; for trips abroad, enter the amount that shows on your card statement."
    - Single dropdown with the allowed set (`USD · EUR · GBP · CAD · AUD · JPY · CHF · SGD · TWD`). Default from the browser's `navigator.language` + `Intl.NumberFormat`-derived currency when it matches the allowed set; otherwise default to `USD`.
    - "continue" primary → `POST /auth/bootstrap` → proceeds to frame 4 (Add First Card).
  - On 401 `{code: "DEVICE_DISPLACED"}` from any API call, show a full-screen modal: "You signed in on another device. This session has ended." with a single "Sign in again" button.
- Add `X-Device-Id` to the `api.ts` fetch wrapper from Day 8.

## Don't

- Don't try to support concurrent multi-device — design says no.
- Don't store the JWT in a cookie. localStorage via Supabase JS is fine for v1.
- Don't add password sign-in. Google + magic link only.
- Don't offer a "change home currency" UI in Settings. Invariant 13 says immutable. The escape hatch is account deletion + re-signup, not a migration path.
- Don't auto-detect and silently set `home_currency` without a confirm step. The currency picker is a one-time blocking step — users who speed-tap through it are choosing USD by default, and we want that to be a deliberate choice, not a surprise.

## Done when

- Sign in on browser A. Sign in on browser B. Browser A's next API call returns 401 and shows the displacement modal.
- `users_meta.active_device_id` updates correctly.
- Refreshing browser B keeps the session (localStorage persisted).
- First sign-in as a new user lands on `ConfirmHomeCurrency` before Add First Card. Confirming creates the `users_meta` row with the chosen currency.
- Attempting `POST /auth/bootstrap` again with a different `home_currency` for a user who already has one is rejected (DB trigger from Day 2 raises; API returns 409 with a clear error code).
