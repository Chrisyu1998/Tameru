/**
 * Web Speech API hook for the chat input. Wraps the browser's
 * `SpeechRecognition` (or `webkitSpeechRecognition`) with the same surface
 * `useMockVoice` exposes, plus per-language switching and an error channel.
 *
 * Supported languages (v1): en-US, zh-TW, ja-JP. See DESIGN.md §7.7 for the
 * rationale (matches the v1 user base: English + Taiwan family + Japan family).
 *
 * Behavior:
 *   - `start()` requests mic permission on first call (browser-managed UI).
 *   - Interim and final transcript updates flow into `transcript`. Auto-stops
 *     after `silenceWindowMs` of no new results, then fires `onCommit` with
 *     the final text.
 *   - Offline taps short-circuit with a `network` error before calling
 *     `recognition.start()`. Permission denial surfaces as `not-allowed`.
 *   - Errors land in `error` and are also emitted to PostHog (Day 26) via
 *     the `track()` shim in `./analytics`.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { track } from "./analytics";

export type VoiceLang = "en-US" | "zh-TW" | "ja-JP";

export const VOICE_LANGS: readonly VoiceLang[] = ["en-US", "zh-TW", "ja-JP"];
export const VOICE_LANG_STORAGE_KEY = "tameru.voice.lang";

export type VoiceErrorCode =
  | "not-allowed"
  | "no-speech"
  | "network"
  | "audio-capture"
  | "unknown";

export interface VoiceError {
  code: VoiceErrorCode;
  /** Raw SpeechRecognitionErrorEvent.error string, when surfaced from the API. */
  raw?: string;
}

interface UseVoiceOptions {
  silenceWindowMs: number;
  /**
   * How long to wait for the user to begin speaking before giving up with
   * a `no-speech` error. Users need much longer to start than they do
   * between phrases (read the screen, think, then talk). Defaults to
   * 4 × silenceWindowMs. During this phase `silenceMsLeft` stays 0 so the
   * countdown ring doesn't visually rush the user.
   */
  preSpeechWindowMs?: number;
  onCommit: (finalText: string) => void;
}

export interface UseVoiceReturn {
  transcript: string;
  silenceMsLeft: number;
  start: () => void;
  submitNow: () => void;
  stop: () => void;
  lang: VoiceLang;
  setLang: (next: VoiceLang) => void;
  error: VoiceError | null;
}

/* ─── Feature detection ───────────────────────────────────────── */

interface SpeechRecognitionConstructor {
  new (): SpeechRecognitionLike;
}

// Minimal structural type so we don't pull DOM lib typings the project may
// not have. The only members we actually call are exercised below.
interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives?: number;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
  onstart: (() => void) | null;
}

interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: ArrayLike<{
    isFinal: boolean;
    0: { transcript: string };
  }>;
}

interface SpeechRecognitionErrorEventLike {
  error: string;
}

function getCtor(): SpeechRecognitionConstructor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

/**
 * Evaluated lazily so callers (and tests) can stub `window.SpeechRecognition`
 * after import. The `voiceSupported` const is kept as a one-shot module-time
 * snapshot for callers that don't need to react to runtime changes; prefer
 * `isVoiceSupported()` in component code.
 */
export function isVoiceSupported(): boolean {
  return getCtor() !== null;
}

export const voiceSupported: boolean = isVoiceSupported();

/* ─── Language resolution ─────────────────────────────────────── */

/**
 * Resolve the initial language from localStorage, then navigator.language.
 * Falls back to en-US for anything we don't support.
 */
export function resolveInitialLang(): VoiceLang {
  if (typeof window === "undefined") return "en-US";

  try {
    const stored = window.localStorage.getItem(VOICE_LANG_STORAGE_KEY);
    if (stored && (VOICE_LANGS as readonly string[]).includes(stored)) {
      return stored as VoiceLang;
    }
  } catch {
    // localStorage can throw in privacy modes; fall through to navigator.
  }

  const nav = window.navigator?.language ?? "en-US";
  if ((VOICE_LANGS as readonly string[]).includes(nav)) return nav as VoiceLang;

  const prefix = nav.split("-")[0]?.toLowerCase();
  if (prefix === "zh") return "zh-TW";
  if (prefix === "ja") return "ja-JP";
  return "en-US";
}

function persistLang(lang: VoiceLang): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(VOICE_LANG_STORAGE_KEY, lang);
  } catch {
    // Best-effort; non-persisting browsers still work for the current session.
  }
}

/* ─── Hook ────────────────────────────────────────────────────── */

let unsupportedTracked = false;

export function useVoice({
  silenceWindowMs,
  preSpeechWindowMs,
  onCommit,
}: UseVoiceOptions): UseVoiceReturn {
  const preSpeechMs = preSpeechWindowMs ?? silenceWindowMs * 4;
  const [transcript, setTranscript] = useState("");
  const [silenceMsLeft, setSilenceMsLeft] = useState(0);
  const [lang, setLangState] = useState<VoiceLang>(() => resolveInitialLang());
  const [error, setError] = useState<VoiceError | null>(null);

  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const silenceIntervalRef = useRef<number | null>(null);
  const finalTranscriptRef = useRef("");
  const interimTranscriptRef = useRef("");
  const langRef = useRef<VoiceLang>(lang);
  // Tracks whether we've already committed/stopped so that the trailing
  // `onend` callback doesn't double-commit when the user taps submit-now.
  const settledRef = useRef(false);
  // False until the first interim/final result arrives. While false we use
  // the longer pre-speech window and suppress the visible countdown.
  const hasSpokenRef = useRef(false);

  const clearSilenceTimer = () => {
    if (silenceIntervalRef.current !== null) {
      window.clearInterval(silenceIntervalRef.current);
      silenceIntervalRef.current = null;
    }
    setSilenceMsLeft(0);
  };

  const teardown = useCallback(() => {
    clearSilenceTimer();
    const rec = recognitionRef.current;
    if (rec) {
      rec.onresult = null;
      rec.onerror = null;
      rec.onend = null;
      rec.onstart = null;
      try {
        rec.abort();
      } catch {
        // No-op: some browsers throw if abort is called before start.
      }
    }
    recognitionRef.current = null;
  }, []);

  const commit = useCallback(
    (finalText: string) => {
      if (settledRef.current) return;
      settledRef.current = true;
      teardown();
      setTranscript("");
      finalTranscriptRef.current = "";
      interimTranscriptRef.current = "";
      if (finalText.trim().length > 0) {
        onCommit(finalText);
        return;
      }
      // Silence elapsed (or submit-now tapped) with no transcript. Without
      // this branch the overlay would sit open with recognition already
      // aborted and no feedback — the parent only leaves voice mode from
      // its onCommit handler. Surface no-speech so the overlay renders the
      // retry chip and the user can either try again or tap stop.
      const next: VoiceError = { code: "no-speech", raw: "empty" };
      setError(next);
      track("error_shown", { code: "voice_no_speech" });
    },
    [onCommit, teardown],
  );

  const beginSilenceCountdown = useCallback(() => {
    clearSilenceTimer();
    const totalMs = hasSpokenRef.current ? silenceWindowMs : preSpeechMs;
    let remaining = totalMs;
    // Only show the countdown ring once the user has actually said
    // something — otherwise the ring rushes a user who's still reading
    // the screen.
    setSilenceMsLeft(hasSpokenRef.current ? remaining : 0);
    silenceIntervalRef.current = window.setInterval(() => {
      remaining -= 100;
      if (remaining <= 0) {
        clearSilenceTimer();
        const finalText =
          finalTranscriptRef.current.trim() ||
          interimTranscriptRef.current.trim();
        commit(finalText);
      } else if (hasSpokenRef.current) {
        setSilenceMsLeft(remaining);
      }
    }, 100);
  }, [silenceWindowMs, preSpeechMs, commit]);

  const start = useCallback(() => {
    setError(null);
    settledRef.current = false;
    hasSpokenRef.current = false;
    finalTranscriptRef.current = "";
    interimTranscriptRef.current = "";
    setTranscript("");
    clearSilenceTimer();

    const Ctor = getCtor();
    if (!Ctor) {
      const next: VoiceError = { code: "unknown", raw: "unsupported" };
      setError(next);
      if (!unsupportedTracked) {
        unsupportedTracked = true;
        track("error_shown", { code: "voice_unsupported" });
      }
      return;
    }

    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      const next: VoiceError = { code: "network", raw: "offline" };
      setError(next);
      track("error_shown", { code: "voice_network" });
      return;
    }

    teardown();
    const rec = new Ctor();
    rec.lang = langRef.current;
    rec.continuous = false;
    rec.interimResults = true;
    rec.maxAlternatives = 1;

    rec.onresult = (event) => {
      let interim = "";
      let final = finalTranscriptRef.current;
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        const text = result[0].transcript;
        if (result.isFinal) {
          final += text;
        } else {
          interim += text;
        }
      }
      finalTranscriptRef.current = final;
      interimTranscriptRef.current = interim;
      setTranscript((final + interim).trim());
      // First audio: flip to the shorter post-speech window and start
      // showing the visible countdown ring.
      hasSpokenRef.current = true;
      beginSilenceCountdown();
    };

    rec.onerror = (event) => {
      const code = mapErrorCode(event.error);
      const next: VoiceError = { code, raw: event.error };
      setError(next);
      track("error_shown", { code: `voice_${code.replace(/-/g, "_")}` });
      clearSilenceTimer();
      settledRef.current = true;
    };

    rec.onstart = () => {
      // Start the silence countdown immediately so a user who taps mic and
      // stays silent still gets a clean auto-stop instead of an indefinite
      // listening state. The first interim result will reset it.
      beginSilenceCountdown();
    };

    rec.onend = () => {
      // If the browser ended the recognition without an error and we
      // haven't committed yet, commit whatever we have.
      if (settledRef.current) return;
      const finalText =
        finalTranscriptRef.current.trim() ||
        interimTranscriptRef.current.trim();
      commit(finalText);
    };

    recognitionRef.current = rec;
    try {
      rec.start();
    } catch (err) {
      // Chrome throws InvalidStateError if start() is called twice — treat
      // that as a no-op rather than surfacing a confusing error.
      const message = err instanceof Error ? err.message : String(err);
      if (!/already started/i.test(message)) {
        setError({ code: "unknown", raw: message });
      }
    }
  }, [beginSilenceCountdown, commit, teardown]);

  const submitNow = useCallback(() => {
    const finalText =
      finalTranscriptRef.current.trim() ||
      interimTranscriptRef.current.trim();
    commit(finalText);
  }, [commit]);

  const stop = useCallback(() => {
    settledRef.current = true;
    teardown();
    setTranscript("");
    setError(null);
    finalTranscriptRef.current = "";
    interimTranscriptRef.current = "";
  }, [teardown]);

  const setLang = useCallback(
    (next: VoiceLang) => {
      langRef.current = next;
      setLangState(next);
      persistLang(next);
      // If we're mid-recognition, restart so the new lang takes effect.
      if (recognitionRef.current) {
        const wasListening = true;
        teardown();
        if (wasListening) {
          // Defer to next tick so any pending onend doesn't fire after
          // start() has set up the new recognition.
          window.setTimeout(start, 0);
        }
      }
    },
    [start, teardown],
  );

  useEffect(() => {
    langRef.current = lang;
  }, [lang]);

  useEffect(() => () => teardown(), [teardown]);

  return {
    transcript,
    silenceMsLeft,
    start,
    submitNow,
    stop,
    lang,
    setLang,
    error,
  };
}

/* ─── Helpers ─────────────────────────────────────────────────── */

/**
 * Map raw `SpeechRecognitionErrorEvent.error` strings to the small set of
 * codes the UI cares about. Unknown strings fall through to `unknown` so
 * the overlay still surfaces a generic retry affordance.
 */
function mapErrorCode(raw: string): VoiceErrorCode {
  switch (raw) {
    case "not-allowed":
    case "service-not-allowed":
    case "permission-denied":
      return "not-allowed";
    case "no-speech":
      return "no-speech";
    case "network":
      return "network";
    case "audio-capture":
      return "audio-capture";
    default:
      return "unknown";
  }
}

/** Reset module state — used by tests that need a clean unsupportedTracked flag. */
export function __resetVoiceTrackingForTests(): void {
  unsupportedTracked = false;
}
