# Day 21 — Voice input in the chat input bar (Web Speech API → chat submit)

## Goal

Tap the mic button in the chat input bar → on-device Web Speech API transcribes → live interim transcript shows → on final transcript (explicit stop or 1.5s of silence), the transcript auto-submits into the chat as if the user had typed it. From that point on, it flows through the exact same path as typed chat input: Claude Haiku turn → if transactional intent, `propose_transaction(...)` → parse card (frame 15) → user confirms.

This day replaces the **old** "standalone AddTransaction page with Type it toggle + Gemini `parse_nl_entry` endpoint" plan. In the chat-unified UX (CLAUDE.md invariant 8), there is no separate NL-entry surface — voice is just an alternate input mode for the chat, and the parse happens inside Claude's `tool_use` argument-filling, not in a dedicated Gemini call.

## Read first

- `DESIGN.md` §7.7 (natural language transaction entry — chat-based).
- `UX_PROMPT.md` frames 12 (Chat Half-Sheet), 13 (Chat Full-Screen), 14 (Voice Active), 15 (Transaction Confirmation).
- `CLAUDE.md` invariant 8.

## Deliverables

### Frontend — voice lib

`frontend/src/lib/voice.ts`:

- Wraps `window.SpeechRecognition || window.webkitSpeechRecognition`. Single-language for v1 (`en-US` by default; read from browser locale if a supported language is set there).
- Exports `startRecognition({onInterim, onFinal, onError, onEnd})`. Auto-stops after 1.5s of silence or on explicit stop call.
- Feature-detects on import: if `SpeechRecognition` is unavailable, exports `voiceSupported = false` so callers can hide the mic button.
- Microphone permission is requested only on first mic tap (not on page load).

### Frontend — chat input bar integration

`frontend/src/components/ChatInputBar.tsx` (built Day 18; this day adds the voice-mode UI state to it):

- Mic button visible only when `voiceSupported`. Right-aligned in the input row, in accent color.
- Tapping the mic enters **Voice Active** state (UX frame 14):
  - Input row transforms: large pulsing accent ring around a mic glyph in the center, live interim transcript displayed in lowercase secondary text above the glyph, terracotta square stop button on the right, "listening…" micro-label in accent.
  - Send button is hidden while listening — transcript will auto-submit.
- On final transcript (explicit stop or 1.5s silence):
  - Text auto-submits into the chat thread as a user message. Same endpoint as typed input (`POST /chat/turn`, Day 15).
  - The chat UI reverts to the normal input row.
- Errors (`no-speech`, `not-allowed`, network, etc.) inline with a "try again" link and a `PostHog event error_shown { code: "voice_<type>" }`.

### No backend work today

There is no `POST /transactions/nl_parse` endpoint. There is no Gemini `parse_nl_entry` function. The NL parse for chat input is what Claude does natively in `tool_use` arg-filling (Day 15 and Day 16). Voice is purely an alternate input mode for the chat — same Claude loop, same tools, same propose-confirm flow.

If you find yourself reaching for a separate Gemini parse, stop — that's the old architecture. Gemini's role in the chat path is `categorize()` (Day 4), called from inside `propose_transaction` (Day 16), after Claude has already extracted the fields.

### Tests

- `frontend/src/lib/voice.test.ts` — mock `SpeechRecognition`, assert lifecycle (start → interim → final → onEnd) fires correctly; assert `voiceSupported = false` path cleanly disables the mic.
- `frontend/src/components/ChatInputBar.voice.test.tsx` — tapping mic enters the listening state; stop button cancels; final transcript submits to `POST /chat/turn`; silence auto-stops after 1.5s.

### Evals (voice fidelity is downstream of Claude's extraction, not a separate suite)

Voice-transcribed messages are just text messages, so their parsing is covered by the existing chat eval harness (Day 22). No separate `nl_parse.yaml` eval file — that was part of the old Gemini-parse plan.

## Don't

- Don't build a standalone AddTransaction page. Chat is the only user-initiated write surface (invariant 8).
- Don't add a `POST /transactions/nl_parse` endpoint. Chat-based parse is Claude's `tool_use` arg extraction.
- Don't upload audio to Gemini. Web Speech API only — audio stays on-device, which is both a privacy win and a cost win.
- Don't auto-parse while listening. The transcript submits once, on final, and then runs through the normal Claude turn.
- Don't bypass the parse card (frame 15) even at 100% confidence. Claude's proposal is still a proposal — the UI confirm is the point of commit, same as for typed input.
- Don't fail silently when `SpeechRecognition` is unavailable. Hide the mic button and log a one-time PostHog `error_shown { code: "voice_unsupported" }`.

## Done when

- Tapping mic in the chat input bar, speaking "spent $47 at Trader Joe's on my Amex Gold," and going silent for 1.5s: final transcript appears briefly in the input, auto-submits to chat, Claude turns, proposes the transaction, and the parse card (frame 15) renders with the expected five fields.
- Tapping stop mid-sentence cancels cleanly without submitting.
- "Got coffee" via voice produces a Claude turn that asks a clarifying question (e.g. "how much was it?") rather than a parse card with holes — because Claude decides how to handle ambiguity, and the chat itself is the fallback.
- Mic button is hidden in browsers without `SpeechRecognition` (test by stubbing the global).
