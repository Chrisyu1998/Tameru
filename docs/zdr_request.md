# Anthropic Zero Data Retention — request log

Status as of last update: **requested, not yet granted.**

Until Anthropic confirms ZDR for the Tameru org, the in-app privacy copy on
the `/privacy` page and `Settings → Privacy` panel reads:

> Tameru sends the merchant name and amount of each transaction to Anthropic
> in order to categorize it and answer your questions. Default Anthropic
> retention is 30 days for trust & safety review; ZDR has been requested and
> brings this to zero. Anthropic does not use API data for training under any
> tier.

When Anthropic confirms, update the copy in
[frontend/src/components/PrivacyDisclosure.tsx](../frontend/src/components/PrivacyDisclosure.tsx)
and `DESIGN.md` §9.4 in the same PR — drop "has been requested" and stop
mentioning the 30-day window.

## Filing record

| Field | Value |
|---|---|
| Filing date | 2026-05-26 |
| Filed by | Chris Yu (chrisyu0620@gmail.com) |
| Org | Tameru |
| Method | Anthropic Console → Settings → Privacy → "Request Zero Data Retention". If the toggle is not surfaced for the org's current tier, fall back to emailing `privacy@anthropic.com` with org id + use case. |
| Use case stated | "Personal-finance app handling US users' transaction merchant + amount. Need ZDR to honor user-facing privacy claims; data is never used for training under any tier." |
| Anthropic SLA | "A few business days" per public docs; no firm commitment. |
| Expected response | 2026-06-02 (one week) |

## Follow-up cadence

- **Day 14 (2026-06-09)** — if no response, re-ping the same channel. Reference
  the original filing date in the follow-up.
- **Day 30 (2026-06-25)** — if still no response, escalate via the org's
  account contact (sales / customer success channel if available).
- On grant — update the in-app copy and DESIGN.md §9.4 in the same PR; add a
  one-line "Granted 2026-MM-DD" row to this file.
- On denial — record the reason; the in-app copy stays as the hedged
  "30-day Anthropic trust & safety retention" version, and the deny rationale
  informs whether to escalate or live with it.

## Notes

- The request covers **both** Anthropic surfaces Tameru uses: the chat agent
  (`claude-haiku-4-5` Messages API) and card-multiplier lookup
  (`claude-haiku-4-5` + `web_search_20250305`). One ZDR grant covers both.
- Gemini is on its paid tier — Google's paid tier already excludes API data
  from training, so no separate request is needed for Gemini.
- This file is checked in. It contains no credentials. If a future filing
  includes anything sensitive (signed agreements, internal-rep email
  addresses), keep that off this file or move it to a gitignored location.
