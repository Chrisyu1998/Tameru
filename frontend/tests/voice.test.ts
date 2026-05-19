/**
 * voice.ts test — Day 18.
 *
 * Stubs window.SpeechRecognition with a controllable fake so we can drive
 * onresult / onerror / onend lifecycle events from tests. Covers:
 *   - lifecycle: start → interim → final → onCommit
 *   - silence auto-stop
 *   - submitNow short-circuits the countdown
 *   - language change restarts recognition with the new lang
 *   - offline tap surfaces network error and does not call recognition.start()
 *   - not-allowed surfaces an error and re-start works cleanly
 *   - resolveInitialLang reads localStorage + navigator.language with fallback
 *   - voice_* error codes are emitted to the analytics shim
 */

import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

vi.mock("@/lib/analytics", () => ({
  track: vi.fn(),
}));

import { track } from "@/lib/analytics";
import {
  __resetVoiceTrackingForTests,
  resolveInitialLang,
  useVoice,
  VOICE_LANG_STORAGE_KEY,
} from "@/lib/voice";

/* ─── Fake SpeechRecognition ─────────────────────────────────── */

interface FakeResult {
  isFinal: boolean;
  0: { transcript: string };
}

class FakeRecognition {
  lang = "";
  continuous = false;
  interimResults = false;
  maxAlternatives = 0;
  onresult: ((e: { resultIndex: number; results: ArrayLike<FakeResult> }) => void) | null = null;
  onerror: ((e: { error: string }) => void) | null = null;
  onend: (() => void) | null = null;
  onstart: (() => void) | null = null;
  started = false;
  aborted = false;
  stopped = false;

  start() {
    if (this.started) throw new Error("recognition already started");
    this.started = true;
    this.onstart?.();
    fakeInstances.push(this);
  }

  stop() {
    this.stopped = true;
  }

  abort() {
    this.aborted = true;
  }

  // Test helpers.
  emitInterim(text: string) {
    this.onresult?.({
      resultIndex: 0,
      results: [{ isFinal: false, 0: { transcript: text } }] as unknown as ArrayLike<FakeResult>,
    });
  }

  emitFinal(text: string) {
    this.onresult?.({
      resultIndex: 0,
      results: [{ isFinal: true, 0: { transcript: text } }] as unknown as ArrayLike<FakeResult>,
    });
  }

  emitError(code: string) {
    this.onerror?.({ error: code });
  }

  emitEnd() {
    this.onend?.();
  }
}

const fakeInstances: FakeRecognition[] = [];

function installMemoryStorage() {
  // Node 25's experimental localStorage is half-mounted without a file path
  // and shadows jsdom's Storage with one missing setItem/getItem/etc. We
  // replace window.localStorage with a deterministic in-memory shim for the
  // duration of each test so the assertions are stable across Node versions.
  const store: Record<string, string> = {};
  const shim: Storage = {
    get length() {
      return Object.keys(store).length;
    },
    clear() {
      for (const k of Object.keys(store)) delete store[k];
    },
    getItem(key: string) {
      return Object.prototype.hasOwnProperty.call(store, key)
        ? store[key]
        : null;
    },
    key(i: number) {
      return Object.keys(store)[i] ?? null;
    },
    removeItem(key: string) {
      delete store[key];
    },
    setItem(key: string, value: string) {
      store[key] = String(value);
    },
  };
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: shim,
  });
}

beforeEach(() => {
  fakeInstances.length = 0;
  __resetVoiceTrackingForTests();
  (track as unknown as { mockClear: () => void }).mockClear();
  vi.useFakeTimers();
  installMemoryStorage();
  (window as unknown as { SpeechRecognition?: unknown }).SpeechRecognition =
    FakeRecognition;
  // Default to online; individual tests can override.
  Object.defineProperty(navigator, "onLine", {
    configurable: true,
    value: true,
  });
});

afterEach(() => {
  vi.useRealTimers();
  delete (window as unknown as { SpeechRecognition?: unknown }).SpeechRecognition;
});

/* ─── Lifecycle ──────────────────────────────────────────────── */

describe("useVoice — lifecycle", () => {
  test("start → interim → final → onCommit", () => {
    const onCommit = vi.fn();
    const { result } = renderHook(() =>
      useVoice({ silenceWindowMs: 1500, onCommit }),
    );

    act(() => result.current.start());
    expect(fakeInstances).toHaveLength(1);
    const rec = fakeInstances[0];
    expect(rec.started).toBe(true);

    act(() => rec.emitInterim("spent forty seven"));
    expect(result.current.transcript).toContain("spent forty seven");

    act(() => rec.emitFinal("spent $47 at Trader Joe's"));
    // Fast-forward past the silence window to trigger auto-commit.
    act(() => {
      vi.advanceTimersByTime(1500);
    });

    expect(onCommit).toHaveBeenCalledWith("spent $47 at Trader Joe's");
    expect(result.current.transcript).toBe("");
  });

  test("submitNow commits the current transcript immediately", () => {
    const onCommit = vi.fn();
    const { result } = renderHook(() =>
      useVoice({ silenceWindowMs: 5000, onCommit }),
    );

    act(() => result.current.start());
    act(() => fakeInstances[0].emitFinal("coffee five fifty"));

    act(() => result.current.submitNow());
    expect(onCommit).toHaveBeenCalledWith("coffee five fifty");
  });

  test("silence with no transcript surfaces no-speech instead of stalling", () => {
    const onCommit = vi.fn();
    const { result } = renderHook(() =>
      useVoice({ silenceWindowMs: 1500, onCommit }),
    );

    act(() => result.current.start());
    // Recognition started but the user said nothing — onstart kicks off the
    // silence countdown. After it elapses, the hook must surface an error
    // (not silently abort), or the overlay sits open with no recovery path.
    act(() => {
      vi.advanceTimersByTime(1500);
    });

    expect(onCommit).not.toHaveBeenCalled();
    expect(result.current.error?.code).toBe("no-speech");
    expect(track).toHaveBeenCalledWith("error_shown", { code: "voice_no_speech" });
  });

  test("submitNow with no transcript also surfaces no-speech", () => {
    const onCommit = vi.fn();
    const { result } = renderHook(() =>
      useVoice({ silenceWindowMs: 5000, onCommit }),
    );

    act(() => result.current.start());
    act(() => result.current.submitNow());

    expect(onCommit).not.toHaveBeenCalled();
    expect(result.current.error?.code).toBe("no-speech");
  });

  test("stop aborts recognition and clears state without committing", () => {
    const onCommit = vi.fn();
    const { result } = renderHook(() =>
      useVoice({ silenceWindowMs: 5000, onCommit }),
    );

    act(() => result.current.start());
    act(() => fakeInstances[0].emitInterim("hello"));
    act(() => result.current.stop());

    expect(fakeInstances[0].aborted).toBe(true);
    expect(onCommit).not.toHaveBeenCalled();
    expect(result.current.transcript).toBe("");
  });
});

/* ─── Language ───────────────────────────────────────────────── */

describe("useVoice — language", () => {
  test("setLang persists to localStorage and restarts mid-recognition", () => {
    const onCommit = vi.fn();
    const { result } = renderHook(() =>
      useVoice({ silenceWindowMs: 1500, onCommit }),
    );

    act(() => result.current.start());
    const first = fakeInstances[0];
    expect(first.lang).toBe("en-US");

    act(() => result.current.setLang("ja-JP"));
    expect(window.localStorage.getItem(VOICE_LANG_STORAGE_KEY)).toBe(
      "ja-JP",
    );

    // The setTimeout(start, 0) used to restart the recognition.
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(fakeInstances.length).toBeGreaterThan(1);
    const next = fakeInstances[fakeInstances.length - 1];
    expect(next.lang).toBe("ja-JP");
  });

  test("resolveInitialLang prefers stored value", () => {
    window.localStorage.setItem(VOICE_LANG_STORAGE_KEY, "zh-TW");
    expect(resolveInitialLang()).toBe("zh-TW");
  });

  test("resolveInitialLang falls back to navigator prefix match", () => {
    window.localStorage.removeItem(VOICE_LANG_STORAGE_KEY);
    Object.defineProperty(navigator, "language", {
      configurable: true,
      value: "ja",
    });
    expect(resolveInitialLang()).toBe("ja-JP");

    Object.defineProperty(navigator, "language", {
      configurable: true,
      value: "zh-HK",
    });
    expect(resolveInitialLang()).toBe("zh-TW");

    Object.defineProperty(navigator, "language", {
      configurable: true,
      value: "fr-FR",
    });
    expect(resolveInitialLang()).toBe("en-US");
  });
});

/* ─── Failure modes ──────────────────────────────────────────── */

describe("useVoice — failure modes", () => {
  test("offline tap surfaces network error and does not start recognition", () => {
    Object.defineProperty(navigator, "onLine", {
      configurable: true,
      value: false,
    });
    const onCommit = vi.fn();
    const { result } = renderHook(() =>
      useVoice({ silenceWindowMs: 1500, onCommit }),
    );

    act(() => result.current.start());
    expect(fakeInstances).toHaveLength(0);
    expect(result.current.error?.code).toBe("network");
    expect(track).toHaveBeenCalledWith("error_shown", { code: "voice_network" });
  });

  test("not-allowed surfaces error, then retry start() works", () => {
    const onCommit = vi.fn();
    const { result } = renderHook(() =>
      useVoice({ silenceWindowMs: 1500, onCommit }),
    );

    act(() => result.current.start());
    act(() => fakeInstances[0].emitError("not-allowed"));
    expect(result.current.error?.code).toBe("not-allowed");
    expect(track).toHaveBeenCalledWith("error_shown", { code: "voice_not_allowed" });

    act(() => result.current.start());
    expect(result.current.error).toBeNull();
    expect(fakeInstances.length).toBeGreaterThan(1);
  });

  test("no-speech and audio-capture surface their codes", () => {
    const onCommit = vi.fn();
    const { result } = renderHook(() =>
      useVoice({ silenceWindowMs: 1500, onCommit }),
    );

    act(() => result.current.start());
    act(() => fakeInstances[0].emitError("audio-capture"));
    expect(result.current.error?.code).toBe("audio-capture");

    act(() => result.current.start());
    act(() => fakeInstances[1].emitError("no-speech"));
    expect(result.current.error?.code).toBe("no-speech");
  });

  test("unsupported browser emits voice_unsupported once", () => {
    delete (window as unknown as { SpeechRecognition?: unknown }).SpeechRecognition;
    delete (window as unknown as { webkitSpeechRecognition?: unknown })
      .webkitSpeechRecognition;

    const onCommit = vi.fn();
    const { result } = renderHook(() =>
      useVoice({ silenceWindowMs: 1500, onCommit }),
    );

    act(() => result.current.start());
    act(() => result.current.start());

    expect(result.current.error?.code).toBe("unknown");
    const unsupportedCalls = (track as unknown as { mock: { calls: unknown[][] } })
      .mock.calls.filter(
        (call) => (call[1] as { code: string }).code === "voice_unsupported",
      );
    expect(unsupportedCalls).toHaveLength(1);
  });
});
