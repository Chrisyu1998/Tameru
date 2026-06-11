# FIXPLAN — audit items awaiting your decision

Everything else from AUDIT.md is fixed on `fix/audit-2026-06` (see the
commit log). These four change user-facing behavior or doc policy, so per
your instructions they are **not implemented** — each has a recommendation
and the decision it needs.

---

## 1. P2-2 — Single-active-device degenerates into a two-device ping-pong

**The bug.** `onAuthStateChange` runs `refreshHomeCurrency` on every
session-bearing event (including `TOKEN_REFRESHED` and `INITIAL_SESSION`),
and that path unconditionally calls `claimDevice`. With the 300s JWT TTL, a
displaced device silently steals active status back on its next refresh
(~every 4–5 min), un-latches its own modal, then the other device 401s and
steals it back. Both devices are effectively active — the invariant's "no
conflict because there can be no conflict" property is defeated. Separately,
invariant 5's "previous device's session is revoked" is not implemented
anywhere; the gate is API-level only.

**Decision needed: how aggressive should displacement be?**

- **Option A — gate claimDevice to explicit sign-in only.** Call
  `claimDevice` only on `event === 'SIGNED_IN'` and post-bootstrap, never on
  `TOKEN_REFRESHED` / `INITIAL_SESSION`, and never while `store.displaced`
  is latched. The displaced device stays signed in but every mutating call
  401s until the user taps "use here" (which is an explicit re-claim) or
  signs out.
- **Option B — Option A + force `supabase.auth.signOut()` on displacement
  latch.** Closes the "session is revoked" gap in the invariant's wording:
  the displaced device's session actually dies. Harsher UX — a user who
  briefly opens the app on a second device gets fully logged out on the
  first, and Supabase's signOut(scope) semantics need care so signing out
  the displaced device doesn't revoke the *new* device's session too
  (default scope is "global" — it would; needs `scope: 'local'`).

**Recommendation: Option A.** It restores the invariant's real purpose (one
*writer* at a time — reads were never the risk, RLS scopes everything to the
same user) with no new failure modes. Option B's revocation matches the
CLAUDE.md wording but the wording can instead be amended to match the
API-level gate, the same way the MCP revocation entry (memory.md 2026-05-22)
accepted TTL-bounded semantics over literal immediacy. If you want B anyway,
it should be a follow-up after A ships, with the `scope: 'local'` subtlety
tested on two real devices.

Also worth fixing in the same change regardless of A/B: a plain page reload
on a displaced device re-claims via `initAuth` (auth.ts:233) — the
`store.displaced` latch must survive reload (persist it next to the device
id) or the reload path must check `/me` before claiming.

---

## 2. P3-9 — `home_currency` immutability bypassable via owner DELETE + re-INSERT

**The bug.** The `users_meta` RLS policy is `FOR ALL`, and the immutability
trigger is `BEFORE UPDATE` only. A user (or a compromised JWT) can DELETE
their own `users_meta` row via direct PostgREST and re-bootstrap with a
different currency — mutating `home_currency` in two statements while the
ledger amounts stay denominated in the old currency (the exact corruption
invariant 13 exists to prevent).

**Why it's a STOP item.** Invariant 13's documented escape hatch is
"account deletion and re-signup." Account deletion deletes the `auth.users`
row, which cascades to `users_meta` — if owner DELETE on `users_meta` is
dropped, does any *legitimate* flow break? The account-deletion flow goes
through the auth admin API (service role), which bypasses RLS, so dropping
the owner DELETE policy should NOT affect it — but this is exactly the kind
of assumption that deserves your confirmation, because it changes what a
signed-in client is allowed to do.

**Decision needed: which closure?**

- **Option A — split the RLS policy and drop owner DELETE.** Replace
  `FOR ALL` with explicit SELECT / INSERT / UPDATE policies. The row then
  dies only via the `auth.users` cascade (real account deletion).
- **Option B — keep DELETE, add a BEFORE DELETE trigger** that blocks
  deletion while any ledger row (transactions / cards / subscriptions)
  exists for the user.

**Recommendation: Option A.** No app code path issues a `users_meta`
DELETE (bootstrap is INSERT-only; preferences are PATCH), so dropping owner
DELETE is invisible to the product and is one migration. Option B preserves
a capability nothing uses, at the cost of a trigger whose "while ledger rows
exist" predicate must be kept in sync with every future ledger table.

---

## 3. P3-10 — `GET /unsubscribe` mutates on first fetch

**The bug.** The unsubscribe GET flips `weekly_digest_enabled` immediately.
Corporate link scanners (Outlook SafeLinks, Mimecast) GET every link in an
email body — a scanned digest silently unsubscribes the user without any
human click. The RFC 8058 one-click POST flow (what Gmail's native
unsubscribe button uses) is correct and unaffected; the GET is the
user-visible link in the email body.

**Decision needed:** this is a confirm-page redesign — the GET must become
a render-only page whose button POSTs the actual unsubscribe. Three calls
for you to make:

1. **Where the page lives.** The backend serves JSON only (the Day 27 CSP
   decision says CSP lives on Vercel because FastAPI never serves HTML —
   memory.md 2026-05-26). A FastAPI-rendered confirm page would be the
   first backend HTML surface and re-opens that CSP decision. Alternative:
   the GET 302s to a PWA route (`/unsubscribe?token=...`) that renders the
   confirm button and POSTs back to the API. Recommended: **PWA route** —
   no new backend HTML surface, the token stays in the query string (the
   supabase-js hash-scrubbing learning doesn't apply; no session needed).
2. **Copy/UX for the confirm page** (one button, "you're unsubscribing
   <email> from the weekly digest").
3. **Whether the POST half of RFC 8058 stays on the backend as-is** (it
   should — Gmail/Yahoo POST it headlessly; only the human-facing GET
   moves).

**Recommendation:** approve the PWA-confirm-page shape and I'll implement;
it's ~half a day including tests. Until then the exposure is bounded: v1
recipients are ~10 friends and family, and a false unsubscribe is
recoverable from Settings → notifications.

---

## 4. P3-1 — Unenumerated 5th service-role caller (`/unsubscribe`)

**The drift.** `app/routes/unsubscribe.py` uses the service-role client and
is allowlisted in the leak-guard test, but CLAUDE.md invariant 1 and
DESIGN.md §9.1 enumerate exactly four sanctioned callers. The allowlist
comment also mislabels it "caller #3," and DESIGN.md:1264 calls the
unsubscribe/webhook pair "third/fourth." Structurally the caller is
legitimate — the HMAC token is the auth; no user JWT is in scope — this is
pure enumeration drift. memory.md 2026-05-22's *Revisit when* ("a fifth
caller is proposed") fired without the amendment ceremony.

**Decision needed:** CLAUDE.md says substantive doc amendments need your
agreement. The change would be:

- CLAUDE.md invariant 1: add `(5) the one-click unsubscribe route
  (app/routes/unsubscribe.py, RFC 8058) — the request is authenticated by
  the HMAC token in the link, not a user JWT`.
- DESIGN.md §9.1: mirror the same five-caller enumeration; fix the
  "third/fourth" wording at line ~1264.
- Leak-guard allowlist comment: fix the "#3" mislabel (the webhook is #4,
  unsubscribe is #5).

**Recommendation: approve.** The code is already live and allowlisted;
the only question is whether the docs acknowledge it. Note: if P3-10's
redesign keeps the unsubscribe POST on the backend (it should), the
enumeration stays correct after that change too.
