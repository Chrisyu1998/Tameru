/**
 * Mock voice hook used by Storybook, eval fixtures, and tests that don't
 * want to exercise the real Web Speech API. The shape mirrors `useVoice`
 * from `./voice` so the chat page can swap them without changes at the
 * call site.
 *
 * Behavior:
 *   - `start()` picks a random canned phrase, grows it token-by-token to
 *     simulate live interim results, then enters a silence countdown.
 *   - At the end of the countdown, calls `onCommit(finalText)` and resets.
 *   - `submitNow()` and `stop()` short-circuit the countdown.
 *
 * The mock ignores `lang` / `setLang` and never raises errors. Use
 * `useVoice` to exercise the real failure modes.
 */
import { useEffect, useRef, useState } from "react";
import type { VoiceLang } from "./voice";

const MOCK_PHRASES = [
  "coffee five fifty",
  "lunch with M twenty four dollars",
  "uniqlo forty nine fifty",
  "amtrak one thirty eight",
  "groceries at whole foods sixty four twenty",
];

const PHRASE_TO_TEXT: Record<string, string> = {
  "coffee five fifty": "coffee $5.50",
  "lunch with M twenty four dollars": "lunch with M $24",
  "uniqlo forty nine fifty": "Uniqlo $49.50",
  "amtrak one thirty eight": "Amtrak $138",
  "groceries at whole foods sixty four twenty": "Whole Foods $64.20",
};

interface UseMockVoiceOptions {
  silenceWindowMs: number;
  onCommit: (finalText: string) => void;
}

export interface UseMockVoiceReturn {
  transcript: string;
  silenceMsLeft: number;
  start: () => void;
  submitNow: () => void;
  stop: () => void;
  /** Stub fields kept for API parity with useVoice. */
  lang: VoiceLang;
  setLang: (next: VoiceLang) => void;
  error: null;
}

export function useMockVoice({
  silenceWindowMs,
  onCommit,
}: UseMockVoiceOptions): UseMockVoiceReturn {
  const [transcript, setTranscript] = useState("");
  const [silenceMsLeft, setSilenceMsLeft] = useState(0);
  const [lang, setLang] = useState<VoiceLang>("en-US");
  const phraseRef = useRef("");
  const tokenIdxRef = useRef(0);
  const tokensRef = useRef<string[]>([]);
  const growIntervalRef = useRef<number | null>(null);
  const silenceIntervalRef = useRef<number | null>(null);

  const cleanup = () => {
    if (growIntervalRef.current !== null) {
      window.clearInterval(growIntervalRef.current);
      growIntervalRef.current = null;
    }
    if (silenceIntervalRef.current !== null) {
      window.clearInterval(silenceIntervalRef.current);
      silenceIntervalRef.current = null;
    }
  };

  const start = () => {
    cleanup();
    const phrase =
      MOCK_PHRASES[Math.floor(Math.random() * MOCK_PHRASES.length)];
    phraseRef.current = phrase;
    tokensRef.current = phrase.split(" ");
    tokenIdxRef.current = 0;
    setTranscript("");
    setSilenceMsLeft(0);

    growIntervalRef.current = window.setInterval(() => {
      tokenIdxRef.current += 1;
      const next = tokensRef.current
        .slice(0, tokenIdxRef.current)
        .join(" ");
      setTranscript(next);
      if (tokenIdxRef.current >= tokensRef.current.length) {
        if (growIntervalRef.current !== null) {
          window.clearInterval(growIntervalRef.current);
          growIntervalRef.current = null;
        }
        let remaining = silenceWindowMs;
        setSilenceMsLeft(remaining);
        silenceIntervalRef.current = window.setInterval(() => {
          remaining -= 100;
          if (remaining <= 0) {
            cleanup();
            setSilenceMsLeft(0);
            const finalText =
              PHRASE_TO_TEXT[phraseRef.current] ?? phraseRef.current;
            onCommit(finalText);
          } else {
            setSilenceMsLeft(remaining);
          }
        }, 100);
      }
    }, 280);
  };

  const submitNow = () => {
    cleanup();
    setSilenceMsLeft(0);
    const finalText =
      PHRASE_TO_TEXT[phraseRef.current] ??
      tokensRef.current.slice(0, tokenIdxRef.current).join(" ") ??
      transcript;
    if (finalText.trim().length > 0) {
      onCommit(finalText);
    }
  };

  const stop = () => {
    cleanup();
    setTranscript("");
    setSilenceMsLeft(0);
  };

  useEffect(() => () => cleanup(), []);

  return {
    transcript,
    silenceMsLeft,
    start,
    submitNow,
    stop,
    lang,
    setLang,
    error: null,
  };
}
