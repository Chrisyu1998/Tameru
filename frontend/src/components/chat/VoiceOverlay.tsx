import { Mic, RefreshCw, Square } from "lucide-react";
import type { VoiceError, VoiceLang } from "@/lib/voice";
import { VOICE_LANGS } from "@/lib/voice";

interface VoiceOverlayProps {
  /** Live transcript shown above the mic. */
  transcript: string;
  /** ms remaining until auto-submit (0 = idle / no countdown). */
  silenceMsLeft: number;
  /** Total silence window in ms — used for the fill-ring progress. */
  silenceWindowMs: number;
  /** Current recognition language. */
  lang: VoiceLang;
  /** Called when the user taps the language chip — cycles to the next supported language. */
  onChangeLang: (next: VoiceLang) => void;
  /** Latest error from the recognizer, or null. */
  error: VoiceError | null;
  /** Retry recognition after an error. */
  onRetry: () => void;
  onSubmitNow: () => void;
  onStop: () => void;
}

/**
 * Listening UI replaces the input row. Pulsing accent ring around a large
 * mic icon. In the last moment before auto-submit, the ring shows a fill
 * animation to telegraph the imminent commit. A language chip in the
 * top-right cycles through supported recognition languages. When an error
 * is set, an inline retry chip replaces the live transcript.
 */
export function VoiceOverlay({
  transcript,
  silenceMsLeft,
  silenceWindowMs,
  lang,
  onChangeLang,
  error,
  onRetry,
  onSubmitNow,
  onStop,
}: VoiceOverlayProps) {
  const progress =
    silenceMsLeft > 0
      ? Math.min(1, 1 - silenceMsLeft / silenceWindowMs)
      : 0;
  const showFill = progress > 0.05;

  const cycleLang = () => {
    const idx = VOICE_LANGS.indexOf(lang);
    const next = VOICE_LANGS[(idx + 1) % VOICE_LANGS.length];
    onChangeLang(next);
  };

  return (
    <div className="relative border-t border-hairline bg-canvas/95 px-5 py-6 backdrop-blur">
      {/* Language chip */}
      <button
        type="button"
        onClick={cycleLang}
        aria-label={`voice language: ${LANG_ARIA_LABEL[lang]}. tap to change.`}
        className="absolute right-4 top-3 rounded-full border border-hairline bg-surface px-2.5 py-1 text-[0.7rem] font-medium tracking-wide text-ink-secondary hover:bg-elevated hover:text-ink"
      >
        {LANG_CHIP_LABEL[lang]}
      </button>

      {/* Error chip or live transcript */}
      {error ? (
        <ErrorChip error={error} onRetry={onRetry} />
      ) : (
        <p
          className={
            transcript
              ? "min-h-[1.5rem] text-center text-[0.95rem] leading-snug text-ink"
              : "min-h-[1.5rem] text-center text-[0.9rem] italic text-ink-tertiary"
          }
        >
          {transcript || "listening…"}
        </p>
      )}

      {/* Mic with pulse + fill ring */}
      <div className="relative mx-auto mt-4 flex h-24 w-24 items-center justify-center">
        <span className="absolute inset-0 animate-ping-soft rounded-full bg-moss/30" />
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
          disabled={!!error}
          className="text-[0.85rem] text-moss hover:text-moss-deep underline-offset-4 hover:underline disabled:opacity-40 disabled:hover:no-underline"
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

/* ─── Subcomponents ───────────────────────────────────────────── */

/**
 * Inline error chip rendered above the mic when recognition fails. Copy is
 * keyed on the error code so the user gets actionable instructions
 * (e.g. how to re-enable mic permission) rather than a generic failure.
 */
function ErrorChip({ error, onRetry }: { error: VoiceError; onRetry: () => void }) {
  return (
    <div className="mx-auto flex max-w-md items-center justify-center gap-2 rounded-lg border border-hairline bg-sunken px-3 py-2 text-center text-[0.8rem] text-ink-secondary">
      <span className="flex-1 leading-snug">{ERROR_MESSAGE[error.code]}</span>
      <button
        type="button"
        onClick={onRetry}
        className="inline-flex items-center gap-1 rounded-full bg-moss px-2.5 py-1 text-[0.7rem] text-surface hover:bg-moss-deep"
      >
        <RefreshCw className="h-3 w-3" />
        try again
      </button>
    </div>
  );
}

/* ─── Copy ────────────────────────────────────────────────────── */

const LANG_CHIP_LABEL: Record<VoiceLang, string> = {
  "en-US": "en",
  "zh-TW": "中",
  "ja-JP": "日",
};

const LANG_ARIA_LABEL: Record<VoiceLang, string> = {
  "en-US": "english",
  "zh-TW": "chinese",
  "ja-JP": "japanese",
};

const ERROR_MESSAGE: Record<VoiceError["code"], string> = {
  "not-allowed":
    "voice access denied. enable mic for this site in your browser settings, then try again.",
  "no-speech": "didn't catch that. try again.",
  network: "voice needs internet — try again when you reconnect.",
  "audio-capture": "no mic detected. check your device.",
  unknown: "voice failed. try again.",
};
