# Day 18 — Voice input in the chat input row (Web Speech API → chat submit)

## Goal

Tap the mic button in the chat input row → on-device Web Speech API transcribes (English, Traditional Chinese, or Japanese) → live interim transcript shows → on final transcript (explicit stop or 1.5s of silence), the transcript auto-submits into the chat as if the user had typed it. From that point on, it flows through the exact same path as typed chat input: Claude Haiku turn → if transactional intent, `propose_transaction(...)` → parse card (frame 15) → user confirms.

This day replaces the **old** "standalone AddTransaction page with Type it toggle + Gemini `parse_nl_entry` endpoint" plan. In the chat-unified UX (CLAUDE.md invariant 8), there is no separate NL-entry surface — voice is just an alternate input mode for the chat, and the parse happens inside Claude's `tool_use` argument-filling, not in a dedicated Gemini call.

## Read first

- `DESIGN.md` §7.7 (natural language transaction entry — chat-based, multilingual).
- `UX_PROMPT.md` frames 12 (Chat Half-Sheet), 13 (Chat Full-Screen), 14 (Voice Active), 15 (Transaction Confirmation).
- `CLAUDE.md` invariant 8.
- Existing files: [frontend/src/components/chat/VoiceOverlay.tsx](frontend/src/components/chat/VoiceOverlay.tsx) (the listening UI is **already built** — frame 14, plus `submit now` and fill-ring countdown — do not rebuild it) and [frontend/src/pages/chat.tsx](frontend/src/pages/chat.tsx) (the inline `InputRow` at line 511 and the `useMockVoice` wiring at line 149 are the call sites you'll be replacing).

## What's already done vs what this day adds

- **Done:** `VoiceOverlay` UI (live transcript + pulsing mic + stop + submit-now + fill-ring), `InputRow` with the mic button (between textarea and send), `useMockVoice` hook driving a fake transcript.
- **This day adds:** a real Web Speech API hook with the **same surface** as `useMockVoice`, multilingual support, offline + permission failure handling. The `VoiceOverlay` UI does not change.

## Deliverables

### Frontend — voice lib

`frontend/src/lib/voice.ts`:

- Feature-detects `window.SpeechRecognition || window.webkitSpeechRecognition` at import time. Exports `voiceSupported: boolean` so callers can hide the mic button.
- Exports a `useVoice(opts)` hook whose return shape **matches the existing `useMockVoice`** so the call site at [chat.tsx:149-164](frontend/src/pages/chat.tsx#L149) keeps working: `{ transcript, silenceMsLeft, start, submitNow, stop, lang, setLang, error }`. The two extra fields beyond the mock are `lang` / `setLang` for runtime language switching and `error` for the inline error chip in the overlay.
- Internally wraps `SpeechRecognition` with `continuous = false`, `interimResults = true`, and `lang` set from the resolved current language (see "Languages" below).
- Auto-stops after `SILENCE_WINDOW_MS` of no new interim/final results (reuse the constant already imported in `chat.tsx`).
- Microphone permission is requested only on the first `start()` call (the browser surfaces the system prompt; we never preempt it).
- Move `useMockVoice` out of `VoiceOverlay.tsx` into `frontend/src/lib/voice.mock.ts` and keep it as the fallback for tests/Storybook (used by `voice.ts` when `import.meta.env.VITEST` is set or when `voiceSupported` is false and a test stub is needed).

### Languages

Supported set for v1: **`en-US`, `zh-TW`, `ja-JP`** (English, Taiwan Mandarin, Japanese). Rationale: the v1 user base includes Taiwan and Japan family. `zh-TW` is chosen over `zh-CN` because the primary Chinese users are in Taiwan.

- **Detection:** on first load, resolve initial language from `navigator.language` — exact match wins (`zh-TW` → `zh-TW`), prefix match falls back to the closest supported (`en-*` → `en-US`, `zh-*` → `zh-TW`, `ja-*` → `ja-JP`), anything else defaults to `en-US`.
- **Persistence:** user override stored in `localStorage` under key `tameru.voice.lang`. Per-device sticky is acceptable for v1 — no DB column, no migration. Cross-device sync can be added later if anyone asks.
- **Switcher UI:** add a small three-state chip to the `VoiceOverlay` (top-right of the overlay, secondary text, lowercase: `en` · `中` · `日`). Tapping cycles → updates `lang` → restarts recognition with the new locale. No separate Settings page entry today.
- **Downstream:** the transcript is submitted to `/chat/turn` as-is. Claude Haiku handles `tool_use` extraction across these languages natively; no system-prompt change is required for this day. Known limitation: merchant canonicalization (Day 9c, `render_user_merchants()`) is English-centric — Chinese/Japanese merchant strings won't deduplicate against English variants. Acceptable for v1; document in §7.7.

### Frontend — chat input row + page integration

Edit [frontend/src/pages/chat.tsx](frontend/src/pages/chat.tsx):

- Swap `import { useMockVoice }` → `import { useVoice } from "@/lib/voice"`. Same call-site shape, so the existing `voice.start() / voice.stop() / onCommit` plumbing keeps working.
- Pass `lang` and `setLang` from the hook into `VoiceOverlay` (new props — see below).
- In `InputRow`, hide the mic button entirely when `voiceSupported === false`. Don't disable it when offline — the offline-tap path is handled inside the hook (it surfaces an error rather than starting recognition), and the user gets a clearer signal that way.

Edit [frontend/src/components/chat/VoiceOverlay.tsx](frontend/src/components/chat/VoiceOverlay.tsx):

- Add props: `lang: 'en-US' | 'zh-TW' | 'ja-JP'`, `onChangeLang: (next) => void`, `error: VoiceError | null`.
- Add the language chip described above. Tapping cycles through the three options.
- When `error` is non-null, render an inline error chip above the mic in `ink-tertiary` italic, with a "try again" button that calls `start()` again. Error copy by code:
  - `not-allowed` (every occurrence, first or persistent — we do not differentiate): "voice access denied. enable mic for this site in your browser settings, then try again."
  - `no-speech`: "didn't catch that. try again."
  - `network`: "voice needs internet — try again when you reconnect."
  - `audio-capture`: "no mic detected. check your device."
  - default: "voice failed. try again."

### Offline behavior

- Don't hide or disable the mic button when `!navigator.onLine`. Discoverability matters more than preempting one tap; the inline error gives a clearer signal.
- Inside `useVoice.start()`: if `!navigator.onLine`, skip `recognition.start()` and synthesize an `error = { code: 'network' }` immediately.
- If the browser fires a `network` error mid-recognition (Chrome's Web Speech sends audio to Google's servers and fails offline; Safari on macOS 14+/iOS 14.5+ runs on-device and works offline), surface the same `network` error.

### Permission denial (first and persistent)

- Treat every `not-allowed` event identically — show the inline error with browser-settings instructions. Do not try to detect "first denial vs persistent denial"; the browser provides no reliable signal, and the right user action is the same either way.
- Do not hide the mic button after denial. Users who misclick "block" can fix it in site permissions and the next tap will work. This matches the Discord / ChatGPT pattern.

### No backend work today

There is no `POST /transactions/nl_parse` endpoint. There is no Gemini `parse_nl_entry` function. The NL parse for chat input is what Claude does natively in `tool_use` arg-filling (Day 8 and Day 9). Voice is purely an alternate input mode for the chat — same Claude loop, same tools, same propose-confirm flow.

If you find yourself reaching for a separate Gemini parse, stop — that's the old architecture. Gemini's role in the chat path is `categorize()` (Day 4), called from inside `propose_transaction` (Day 9), after Claude has already extracted the fields.

### PostHog calls

PostHog ships on Day 26 — `posthog-js` is not yet installed, and there's no `lib/analytics.ts` yet. For today, stub a no-op `track()` import from `@/lib/analytics` and emit the error codes through it. Day 26's `track()` will pick them up automatically once it exists. Codes to emit:

- `voice_unsupported` (once per session — gate on a module-level boolean so we don't spam on every render)
- `voice_not_allowed`
- `voice_no_speech`
- `voice_network`
- `voice_audio_capture`

The Day 26 `error_shown { code: string }` event accepts any string, so no Day 26 whitelist change is needed.

### Tests

- `frontend/src/lib/voice.test.ts` — mock `SpeechRecognition`; assert (a) lifecycle (start → interim → final → onEnd) fires correctly, (b) `voiceSupported = false` cleanly disables the hook, (c) language change calls `recognition.abort()` and restarts with the new `lang`, (d) `!navigator.onLine` short-circuits with a `network` error before calling `start()`, (e) `not-allowed` from the browser surfaces an error and the next `start()` re-attempts cleanly.
- `frontend/src/components/chat/VoiceOverlay.test.tsx` (the existing UI test, expanded) — language chip cycles `en → 中 → 日 → en`; error chip renders for each code; "try again" calls `start()`.
- `frontend/src/pages/chat.voice.test.tsx` — tapping mic in `InputRow` enters the listening state; stop button cancels; final transcript submits to `/chat/turn`; silence auto-stops after `SILENCE_WINDOW_MS`.

### Evals (voice fidelity is downstream of Claude's extraction, not a separate suite)

Voice-transcribed messages are just text messages, so their parsing is covered by the existing chat eval harness (Day 22). No separate `nl_parse.yaml` eval file — that was part of the old Gemini-parse plan. **Do** add 3–5 multilingual rows to the eval harness so we notice if Haiku regresses on `zh-TW` or `ja-JP` extraction.

## Don't

- Don't build a standalone AddTransaction page. Chat is the only user-initiated write surface (invariant 8).
- Don't add a `POST /transactions/nl_parse` endpoint. Chat-based parse is Claude's `tool_use` arg extraction.
- Don't upload audio to Gemini. Web Speech API only — audio stays on-device (on Safari) or stays inside the browser's Web Speech sandbox (on Chrome), which is both a privacy win and a cost win.
- Don't auto-parse while listening. The transcript submits once, on final, and then runs through the normal Claude turn.
- Don't bypass the parse card (frame 15) even at 100% confidence. Claude's proposal is still a proposal — the UI confirm is the point of commit, same as for typed input.
- Don't fail silently when `SpeechRecognition` is unavailable. Hide the mic button and emit `voice_unsupported` once per session.
- Don't rebuild `VoiceOverlay` — only extend its props.
- Don't add a `zh-CN` option in v1. The Chinese-speaking users are in Taiwan; adding both `zh-TW` and `zh-CN` doubles the test surface for no real benefit at this scale.
- Don't auto-translate the transcript. Submit the user's words verbatim — Claude handles the multilingual extraction.
- Don't add a Settings page entry for voice language in this day. The in-overlay chip is enough; a Settings entry is a follow-up if anyone asks.
- Don't try to detect "first vs persistent" permission denial. The browser gives no reliable signal, and the right error message is identical.

## Done when

- Tapping mic in the chat input row, speaking "spent $47 at Trader Joe's on my Amex Gold," and going silent for 1.5s: final transcript appears briefly, auto-submits to chat, Claude turns, proposes the transaction, and the parse card (frame 15) renders with the expected five fields.
- Same flow in Japanese ("ローソンで六百円") and Traditional Chinese ("全家買咖啡七十塊") after switching the overlay chip: transcript reaches `/chat/turn` in the right language, and the Claude turn produces a parse card with the right amount + merchant (category and card may be lower-confidence — that's fine; the parse card is editable).
- Tapping stop mid-sentence cancels cleanly without submitting.
- "Got coffee" via voice produces a Claude turn that asks a clarifying question (e.g. "how much was it?") rather than a parse card with holes — because Claude decides how to handle ambiguity, and the chat itself is the fallback.
- Mic button is hidden in browsers without `SpeechRecognition` (test by stubbing the global).
- With the network offline (devtools throttling → offline), tapping mic shows the `network` inline error and does not start recognition.
- With mic permission denied at the site level, tapping mic shows the `not-allowed` inline error with browser-settings instructions; re-enabling permission and tapping again works without a reload.
- Language preference persists across reload (verified via `localStorage.getItem('tameru.voice.lang')`).
