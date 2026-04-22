# Day 6 — PWA scaffold (Vite + React + Tailwind + Zustand + Service Worker)

## Goal

Frontend scaffolded as an installable PWA with offline app shell. Lighthouse PWA score ≥ 90 on a blank page.

## Read first

- `DESIGN.md` §5.1 (frontend stack), §10 (PWA-only mobile strategy).
- `CLAUDE.md` invariant 7 (no Expo).

## Deliverables

- `frontend/` — Vite + React + TypeScript scaffold.
- Tailwind CSS configured with mobile-first defaults. Custom palette per `DESIGN.md` §6.2 dashboard color coding (green/neutral/amber/red for delta states).
- Zustand: a single `useAppStore` placeholder with `{user: null}`. Real state lands in later days.
- `vite-plugin-pwa` configured:
  - App manifest with name "Tameru", short_name "Tameru", theme color, icons (192, 512).
  - Service worker caches the app shell (HTML + JS + CSS) for offline load.
  - Auto-update strategy with a "new version available" toast.
- `frontend/src/lib/api.ts` — fetch wrapper that adds `Authorization: Bearer <jwt>` from the store and points at `VITE_API_URL`.
- `frontend/src/pages/`:
  - `Splash.tsx` — empty for now (Day 21).
  - `Home.tsx` — empty for now (Day 15).
- `frontend/.env.example` with `VITE_API_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`.
- A single `npm run build` and `npm run dev` that just works.
- README updated with frontend dev commands.

## Don't

- Don't add component libraries (shadcn/ui, MUI, Chakra). Tailwind primitives are enough.
- Don't add a router beyond what's needed — `react-router-dom` for 3 routes is fine; no nested layouts yet.
- Don't ship the `frontend/` build to Railway today. Backend still serves only the API. Day 19 or Day 28 wires up static hosting (or a separate Vercel project for the frontend, your call — leave a TODO if you defer).

## Done when

- `npm run dev` opens a Tameru-branded shell.
- `npm run build` produces an installable PWA.
- Lighthouse PWA audit ≥ 90 on the built artifact served locally.
- Add to Home Screen on iOS Safari installs the icon and opens the app standalone.
