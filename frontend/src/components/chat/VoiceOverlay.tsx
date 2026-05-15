import { useEffect, useRef, useState } from "react";
import { Mic, Square } from "lucide-react";

interface VoiceOverlayProps {
  /** Live transcript shown above the mic. */
  transcript: string;
  /** ms remaining until auto-submit (0 = idle / no countdown). */
  silenceMsLeft: number;
  /** Total silence window in ms — used for the fill-ring progress. */
  silenceWindowMs: number;
  onSubmitNow: () => void;
  onStop: () => void;
}

/**
 * Listening UI replaces the input row. Pulsing accent ring around a large
 * mic icon. In the last moment before auto-submit, the ring shows a fill
 * animation to telegraph the imminent commit.
 */
export function VoiceOverlay({
  transcript,
  silenceMsLeft,
  silenceWindowMs,
  onSubmitNow,
  onStop,
}: VoiceOverlayProps) {
  // Progress 0..1 of the fill-ring (only meaningful when silence is counting).
  const progress =
    silenceMsLeft > 0
      ? Math.min(1, 1 - silenceMsLeft / silenceWindowMs)
      : 0;
  const showFill = progress > 0.05; // last ~95% of the window

  return (
    <div className="border-t border-hairline bg-canvas/95 px-5 py-6 backdrop-blur">
      {/* Live transcript */}
      <p
        className={
          transcript
            ? "min-h-[1.5rem] text-center text-[0.95rem] leading-snug text-ink"
            : "min-h-[1.5rem] text-center text-[0.9rem] italic text-ink-tertiary"
        }
      >
        {transcript || "listening…"}
      </p>

      {/* Mic with pulse + fill ring */}
      <div className="relative mx-auto mt-4 flex h-24 w-24 items-center justify-center">
        {/* Soft pulse */}
        <span className="absolute inset-0 animate-ping-soft rounded-full bg-moss/30" />

        {/* Fill ring (last moment before auto-submit) */}
        {showFill && (
          <svg
            className="absolute inset-0 h-full w-full -rotate-90"
            viewBox="0 0 100 100"
            aria-hidden
          >
            <circle
              cx="50"
              cy="50"
              r="46"
              fill="none"
              stroke="var(--moss)"
              strokeWidth="3"
              strokeLinecap="round"
              strokeDasharray={2 * Math.PI * 46}
              strokeDashoffset={(1 - progress) * 2 * Math.PI * 46}
              style={{ transition: "stroke-dashoffset 80ms linear" }}
            />
          </svg>
        )}

        {/* Inner mic */}
        <div className="relative flex h-16 w-16 items-center justify-center rounded-full bg-moss text-surface">
          <Mic className="h-7 w-7" />
        </div>
      </div>

      {/* Actions */}
      <div className="mt-5 flex items-center justify-between">
        <button
          type="button"
          onClick={onStop}
          className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-hairline bg-surface text-ink-secondary transition-colors hover:bg-sunken/60"
          aria-label="stop"
        >
          <Square className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={onSubmitNow}
          className="text-[0.85rem] text-moss hover:text-moss-deep underline-offset-4 hover:underline"
        >
          submit now
        </button>
        <span
          className="w-10 text-right text-[0.7rem] tabular text-ink-tertiary"
          aria-hidden
        >
          {silenceMsLeft > 0 ? `${(silenceMsLeft / 1000).toFixed(1)}s` : ""}
        </span>
      </div>
    </div>
  );
}

/* ─── Mock transcript hook ───────────────────────────────────────── */

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

/**
 * Drives a fake live transcript that grows token-by-token, then enters a
 * silence window. After silenceWindowMs of "silence", auto-commits.
 */
export function useMockVoice({
  silenceWindowMs,
  onCommit,
}: UseMockVoiceOptions) {
  const [transcript, setTranscript] = useState("");
  const [silenceMsLeft, setSilenceMsLeft] = useState(0);
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

    // Grow transcript token-by-token every ~280ms.
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
        // Begin silence countdown.
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

  return { transcript, silenceMsLeft, start, submitNow, stop };
}
