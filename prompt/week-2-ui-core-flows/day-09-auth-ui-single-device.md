# Day 9 — Auth UI + single-device enforcement

## Goal

Sign in with Google, persist the session, and enforce single active device per user. A second sign-in displaces the first, with the copy from `DESIGN.md` §9.1.

## Read first

- `DESIGN.md` §9.1 (single device — read carefully), §8.7 (`users_meta`).

## Deliverables

- Backend:
  - `app/routes/auth.py`:
    - `POST /auth/claim_device` — body: `{device_id}`. Updates `users_meta.active_device_id = device_id` for the current user. Called by the frontend immediately after sign-in.
    - `GET /auth/check_device` — query param `device_id`. Returns `{is_active: bool, active_device_id, active_since}`. Called periodically by the frontend to detect displacement.
  - On every other authenticated route, add a dependency that compares the request's `X-Device-Id` header to `users_meta.active_device_id`. If mismatch, return 401 with `{code: "DEVICE_DISPLACED"}`.
- Frontend:
  - `frontend/src/lib/auth.ts`:
    - Initializes the Supabase JS client with `persistSession: true`.
    - On sign-in, generates a stable `device_id` (UUID stored in `localStorage`), POSTs `/auth/claim_device`.
    - Background poll every 60s on `GET /auth/check_device`.
  - `frontend/src/pages/SignIn.tsx`:
    - Single "Sign in with Google" button.
    - Magic link as a "More options" disclosure.
  - On 401 `{code: "DEVICE_DISPLACED"}` from any API call, show a full-screen modal: "You signed in on another device. This session has ended." with a single "Sign in again" button.
- Add `X-Device-Id` to the `api.ts` fetch wrapper from Day 8.

## Don't

- Don't try to support concurrent multi-device — design says no.
- Don't store the JWT in a cookie. localStorage via Supabase JS is fine for v1.
- Don't add password sign-in. Google + magic link only.

## Done when

- Sign in on browser A. Sign in on browser B. Browser A's next API call returns 401 and shows the displacement modal.
- `users_meta.active_device_id` updates correctly.
- Refreshing browser B keeps the session (localStorage persisted).
