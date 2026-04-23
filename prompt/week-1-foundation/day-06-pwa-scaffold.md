# Day 6 — PWA scaffold (Vite + React + Tailwind v4 + Zustand + Service Worker) + backend CORS

## Goal

Frontend scaffolded as an installable PWA with an offline **app shell** (not offline transaction queue — that's Week 3). Lighthouse PWA score ≥ 90 on a blank page. Backend `CORSMiddleware` configured so the Vite dev server (and the future Vercel deploy) can talk to FastAPI cross-origin with Bearer-token auth.

## Read first

- `DESIGN.md` §5.1 (frontend stack), §5.3 (hosting split: Railway backend + Vercel frontend, and how they talk), §9.3 (transport security, CORS), §10 (PWA strategy).
- `UX_PROMPT.md` design system section — **the locked hex palette lives here, not in DESIGN.md §6.2**. Every downstream UI day assumes the Tailwind theme matches it; if this day's palette drifts, every subsequent screen has to be retouched.
- `CLAUDE.md` invariants 7 (PWA only), 12 (backend is frontend-stack-agnostic — no `User-Agent` branching).

## Deliverables

### Frontend (`frontend/`)

- Vite + React + TypeScript scaffold.
- **Tailwind CSS v4** (CSS-first `@theme` config in a `.css` file, not a v3 `tailwind.config.js`). Mobile-first defaults.
- Theme tokens matching `UX_PROMPT.md` — do not invent new ones:
  - **Neutrals (light):** canvas `#F5EFE4` · surface `#FBF6EC` · elevated `#FFFBF2` · sunken `#EDE5D5`.
  - **Neutrals (dark):** canvas `#1C1A17` · surface `#252320` · elevated `#2E2B27` · sunken `#17150F`.
  - **Text (light):** primary `#2A2620` · secondary `#5C564C` · tertiary `#8A8377` · quaternary `#B8B0A0`. (Dark values in UX_PROMPT.md.)
  - **Accent — muted earthy moss green.** Four tints: `base`, `emphasis` (deeper), `soft` (pale), `wash` (barely-there). Pick hex values that read "muted moss on cream"; leave a CSS comment with the tuning rationale so future days can adjust without re-deriving the brief.
  - **Semantic tokens:** `warn` = warm aged amber · `over` = muted terracotta · `under` = same moss as accent · `neutral` = mid-gray. These are the color-coded delta states §6.2 refers to ("Dining +$47 warn", "Groceries -$12 moss", etc.) — there is **no generic red** in this design system.
  - **Borders / scrim (light):** hairline `rgba(42,38,32,0.08)` · soft `rgba(42,38,32,0.12)` · scrim `rgba(42,38,32,0.32)`.
  - **Base rules (enforce at the Tailwind config level):** no drop shadows (shadow tokens = `none`), no gradients, no pure black/white, no sharp corners (rounded-* baseline ≥ `rounded-md`).
- Fonts: Fraunces (serif — titles, display, numeric headlines; lowercase for screen titles per UX_PROMPT.md) + Inter (sans — body, buttons). Load from Google Fonts with a font-display swap. Set `font-variant-numeric: tabular-nums` as a default on numeric elements.
- Zustand: a single `useAppStore` placeholder with `{ user: null, jwt: null, deviceId: null }`. Real wiring lands Day 7.
- `vite-plugin-pwa`:
  - Web App Manifest: `name: "Tameru"`, `short_name: "Tameru"`, `theme_color` matches canvas, `display: "standalone"`, icons `192×192` and `512×512` PNG.
  - **Also `public/apple-touch-icon.png` at 180×180** referenced via `<link rel="apple-touch-icon" href="/apple-touch-icon.png">` in `index.html`. iOS Safari ignores manifest icons for Add-to-Home-Screen — without this, the install test in "Done when" fails silently.
  - `<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">` in `index.html` for iOS safe-area insets.
  - Service Worker caches the **app shell only** (HTML + hashed JS/CSS + fonts). **Do not cache any API responses** — the SW must not sit in front of `/transactions`, `/me`, `/chat`, etc. This is a load-bearing rule; an SW caching stale user data is a privacy bug.
  - Auto-update strategy with a "new version available" toast (component can be a Zustand-backed banner).
- `frontend/src/lib/api.ts` — fetch wrapper pointed at `import.meta.env.VITE_API_URL`. Reads headers from the store on each call:
  - `Authorization: Bearer <jwt>` if `useAppStore.jwt` is set.
  - `X-Device-Id: <deviceId>` if `useAppStore.deviceId` is set.
  - Sends neither if unset. Includes a `// TODO(Day 7): populated from Supabase session after sign-in; X-Device-Id header enforcement lands Day 7.`
  - `credentials: "omit"` explicitly — we use Bearer tokens, not cookies.
- `frontend/src/pages/`:
  - `Splash.tsx` — empty placeholder (Day 21 owns content).
  - `Home.tsx` — empty placeholder (Day 15 owns content).
  - `SignIn.tsx` and `ConfirmHomeCurrency.tsx` are owned by Day 7 — not in this day's scope.
- `react-router-dom` with **2 routes today**: `/` → Splash, `/home` → Home. Day 7 adds `/signin` and `/confirm-currency`.
- `frontend/.env.example` with:
  - `VITE_API_URL=http://localhost:8000` (dev default)
  - `VITE_SUPABASE_URL=`
  - `VITE_SUPABASE_ANON_KEY=`
- `frontend/vercel.json`:
  ```json
  {
    "rewrites": [{ "source": "/(.*)", "destination": "/index.html" }]
  }
  ```
  SPA fallback so client-side routes resolve on direct URL visits once deployed.
- `npm run dev` and `npm run build` both work; `dist/` is gitignored.
- Root `README.md` updated with frontend dev commands (in the existing stack-agnostic section, not a new top-level doc).

### Backend (`app/`)

- Add `CORSMiddleware` to `app/main.py` (before the app's routes are included):
  - `allow_origins` resolved at startup: `[os.environ["FRONTEND_ORIGIN"], "http://localhost:5173"]` if `FRONTEND_ORIGIN` is set; otherwise just `["http://localhost:5173"]` for local dev.
  - `allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"]`.
  - `allow_headers=["Authorization", "X-Device-Id", "Content-Type"]`.
  - `allow_credentials=False` — Bearer-in-`Authorization` doesn't need credentials mode, and keeping it `False` avoids SameSite/third-party-cookie questions entirely (§9.3).
  - `expose_headers=[]` unless a specific endpoint needs one; today, none.
- Add `FRONTEND_ORIGIN` placeholder to `.env.example` at the backend root, with a comment `# set to https://tameru.app (or whatever the prod Vercel domain is) in Railway; leave unset in local dev`.
- No `User-Agent` branching. No frontend-specific payload shapes. This is invariant 12 — the CORS allowlist is the only frontend-aware line in the backend.

## Don't

- Don't add component libraries (shadcn/ui, MUI, Chakra, etc.). Tailwind primitives + a few handwritten components are enough for v1.
- Don't hand-roll a Tailwind v3 `tailwind.config.js`. Use v4's `@theme` directive in a `.css` file.
- Don't implement the IndexedDB offline transaction queue today. Week 3 ships it (§10.1). Today's offline scope is the app shell only.
- Don't wire real JWTs into `api.ts` from a live Supabase session — Day 7 does that. `useAppStore.jwt` stays `null` today; the fetch wrapper simply sends no `Authorization` header.
- Don't deploy the frontend to Vercel today. Day 11 is the deploy day and ships frontend Vercel + backend Railway together.
- Don't `allow_origins=["*"]` on the CORS middleware, even "just for dev." Explicit list only. If a new origin is needed later, add it explicitly.
- Don't cache API responses in the Service Worker. Shell-only caching today.

## Done when

- `npm run dev` opens a Tameru-branded shell on `localhost:5173`. Fraunces and Inter both render; palette tokens (canvas/surface/moss/amber/terracotta) all resolve in devtools.
- `npm run build` produces `frontend/dist/`.
- Lighthouse PWA audit ≥ 90 on the built artifact served locally (e.g. `npx serve frontend/dist`).
- **App-shell offline test:** load the served build in Chrome, open DevTools → Network → Offline, reload — shell still renders.
- Add to Home Screen on iOS Safari installs the 180×180 icon and opens the app standalone with no browser chrome.
- CORS preflight check from a running FastAPI dev server:
  ```
  curl -i -X OPTIONS http://localhost:8000/me \
    -H 'Origin: http://localhost:5173' \
    -H 'Access-Control-Request-Method: GET' \
    -H 'Access-Control-Request-Headers: authorization,x-device-id'
  ```
  returns `200` with `Access-Control-Allow-Origin: http://localhost:5173` and `Access-Control-Allow-Headers` listing `authorization, x-device-id, content-type`.
