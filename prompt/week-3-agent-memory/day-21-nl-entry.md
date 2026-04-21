# Day 21 — Natural language transaction entry (text + voice; parse on submit, not debounced)

## Goal

User types or speaks "Spent $47 at Trader Joe's on my Amex Gold just now" → Gemini parses merchant/amount/date/card/category → confirmation form appears pre-filled. Text parse fires **on submit/blur**, not on every keystroke pause. Voice uses the **Web Speech API** (browser-native, on-device transcription) — final transcript flows through the same parse path as text.

## Read first

- `DESIGN.md` §7.7 (NL entry — read both text and voice triggers carefully).
- `CLAUDE.md` invariant 8.

## Deliverables

- Backend:
  - `app/integrations/gemini.py` — add `parse_nl_entry(text, user_jwt) -> ParsedTransaction`. Calls Gemini with a structured-output prompt that returns:
    ```json
    {
      "merchant": "string|null",
      "amount": "number|null",
      "date": "YYYY-MM-DD|null",
      "card_id": "uuid|null",
      "category": "string|null",
      "confidence": 0.0-1.0,
      "missing_fields": ["amount", "card"]
    }
    ```
  - The prompt is given the user's cards (name → card_id mapping) so it can resolve "my Amex Gold" → the right card_id.
  - `POST /transactions/nl_parse` — body: `{text}`. Returns the parsed structure. Logs to `ai_call_log` with `task_type="nl_parse"`.
- Frontend — text mode:
  - `frontend/src/pages/AddTransaction.tsx` — add a "Type it" toggle at the top:
    - When enabled, the form is replaced by a single textarea + "Parse" button + mic button (voice mode).
    - **Parse fires only on button click** (submit) or on blur of the textarea. Not on debounced keystrokes.
    - On parse: if any `missing_fields`, render the standard form pre-filled with what was parsed and highlight the missing fields. The user fills them in and submits as usual.
    - If everything is present and confidence ≥ 0.8, skip the form and submit directly with a confirm screen showing the parsed values.
- Frontend — voice mode:
  - `frontend/src/lib/voice.ts`:
    - Wraps `window.SpeechRecognition || window.webkitSpeechRecognition`. Single-language (en-US for v1).
    - Exposes `startRecognition({onInterim, onFinal, onError, onEnd})`. Auto-stops after 1.5s of silence or on explicit stop.
    - Feature-detects on import: if `SpeechRecognition` is unavailable, exports a `voiceSupported = false` flag so callers can hide the mic button.
  - In `AddTransaction.tsx`:
    - Mic button visible only when `voiceSupported`. Tapping it enters "listening" state: large pulsing accent ring + live interim transcript displayed in the textarea + stop button.
    - On final transcript: text drops into the textarea and **automatically triggers the same parse path as a manual submit**. No extra confirmation step before parse — the confirm screen shows the parsed values and is the user's review.
    - Errors (`no-speech`, `not-allowed`, etc.) shown inline with a "try again" button. Microphone permission is requested only on first mic tap.
- Tests:
  - `tests/test_nl_entry.py` — 10 NL strings, mocked Gemini responses, assert correct field extraction.
  - `frontend/src/lib/voice.test.ts` — mock `SpeechRecognition`, assert lifecycle (start → interim → final → onEnd) fires correctly.
  - `evals/nl_parse.yaml` — add 10 hand-curated NL strings (full 50 lands Day 22).

## Don't

- Don't auto-parse text on every keystroke or pause. **Submit/blur only.**
- Don't bypass the confirm screen even at 100% confidence — the user must see what's about to be saved.
- Don't try to parse multiple transactions in one input. v1 = single transaction. ("$10 at Starbucks AND $50 at Whole Foods" → return error or parse the first only.)
- **Don't upload audio to Gemini for transcription.** Use the browser's Web Speech API. Two reasons: (1) it's free vs Gemini audio input which would add cost per call, (2) audio never leaves the user's device — material privacy improvement over the alternative. Gemini still does the parse on the resulting text, but it never sees the raw audio.
- Don't fail silently when `SpeechRecognition` is unavailable. Hide the mic button and log a one-time `error_shown` PostHog event with code `voice_unsupported`.

## Done when

- "Spent $47 at Trader Joe's on my Amex Gold just now" parses cleanly with no missing fields, both via typed input and via voice.
- "Got coffee" (typed or spoken) → returns `missing_fields: ["amount", "card", "date"]` and the form opens with merchant pre-filled as "coffee".
- Typing rapidly in the textarea fires zero Gemini calls until you tab out or click Parse.
- Tapping mic, speaking a transaction, going silent for 1.5s → final transcript appears, parse fires once, confirm screen shows the parsed values.
- Mic button is hidden in browsers without `SpeechRecognition` (test by stubbing the global).
