/**
 * chat.tsx voice wiring — Day 18.
 *
 * Verifies the page-level glue, not the recognizer itself (voice.test.ts
 * covers the recognizer). Specifically:
 *   - The mic button in the input row is hidden when isVoiceSupported() is false.
 *   - The mic button is visible (and tappable) when isVoiceSupported() is true.
 *   - Tapping the mic enters voice mode (the VoiceOverlay renders, the input
 *     row is replaced).
 *   - A simulated voice commit invokes chatStore.send with the transcript.
 */

import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/lib/voice", () => {
  const supported = { value: true };
  const lang = { value: "en-US" as const };
  let onCommitRef: ((t: string) => void) | null = null;

  const useVoice = ({ onCommit }: { onCommit: (t: string) => void }) => {
    onCommitRef = onCommit;
    return {
      transcript: "",
      silenceMsLeft: 0,
      start: vi.fn(),
      stop: vi.fn(),
      submitNow: () => onCommitRef?.("coffee five fifty"),
      lang: lang.value,
      setLang: vi.fn(),
      error: null,
    };
  };

  return {
    isVoiceSupported: () => supported.value,
    voiceSupported: true,
    useVoice,
    VOICE_LANGS: ["en-US", "zh-TW", "ja-JP"] as const,
    __setSupported: (v: boolean) => {
      supported.value = v;
    },
    __getOnCommit: () => onCommitRef,
  };
});

vi.mock("@/lib/chatStore", async () => {
  const send = vi.fn(async () => {});
  return {
    chatStore: {
      send,
      hydrateMessages: vi.fn(async () => {}),
      newChat: vi.fn(),
      setCapEngaged: vi.fn(),
      retry: vi.fn(),
      dismissError: vi.fn(),
      commitDraft: vi.fn(),
      commitCardDraft: vi.fn(),
      discardDraft: vi.fn(),
      updateDraft: vi.fn(),
    },
    useChatStore: () => ({
      messages: [],
      busy: false,
      capEngaged: false,
      streamingText: "",
      lastError: null,
    }),
  };
});

vi.mock("@/lib/ledger", () => ({
  ledger: {
    deleteTransaction: vi.fn(),
  },
  useLedger: () => ({ transactions: [], cards: [] }),
}));

vi.mock("@/lib/chatSeed", () => ({
  consumeChatSeed: () => null,
}));

import ChatPage from "@/pages/chat";
import { chatStore } from "@/lib/chatStore";
import * as voiceModule from "@/lib/voice";

const setSupported = (
  voiceModule as unknown as { __setSupported: (v: boolean) => void }
).__setSupported;

function renderChat() {
  return render(
    <MemoryRouter>
      <ChatPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  setSupported(true);
  // jsdom doesn't implement scrollTo; chat.tsx auto-scrolls on mount.
  if (!Element.prototype.scrollTo) {
    Element.prototype.scrollTo = (() => {}) as unknown as Element["scrollTo"];
  } else {
    vi.spyOn(Element.prototype, "scrollTo").mockImplementation(() => {});
  }
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("ChatPage — mic visibility", () => {
  test("mic button is rendered when voice is supported", () => {
    setSupported(true);
    renderChat();
    expect(screen.getByLabelText("record voice")).toBeInTheDocument();
  });

  test("mic button is hidden when voice is not supported", () => {
    setSupported(false);
    renderChat();
    expect(screen.queryByLabelText("record voice")).toBeNull();
  });
});

describe("ChatPage — voice flow", () => {
  test("tapping mic enters voice mode and renders the overlay", async () => {
    const user = userEvent.setup();
    renderChat();
    await user.click(screen.getByLabelText("record voice"));
    // The overlay is identified by its stop button.
    expect(screen.getByLabelText("stop")).toBeInTheDocument();
  });

  test("voice commit invokes chatStore.send with the transcript", async () => {
    const user = userEvent.setup();
    renderChat();
    await user.click(screen.getByLabelText("record voice"));
    // Trigger the simulated commit via the overlay's "submit now" button —
    // our mocked useVoice maps submitNow to onCommit("coffee five fifty").
    await user.click(screen.getByText("submit now"));
    expect(chatStore.send).toHaveBeenCalledWith("coffee five fifty");
  });
});
