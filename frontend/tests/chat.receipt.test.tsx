/**
 * chat.tsx receipt-capture wiring — the camera button in the composer.
 *
 * Page-level glue only: the camera ("scan a receipt") button renders in the
 * input row and is reachable. The store behavior is covered by
 * chatStore.sendReceiptPhoto.test.ts and the shrink by image.test.ts. Mirrors
 * chat.voice.test.tsx's harness.
 */

import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/lib/voice", () => ({
  isVoiceSupported: () => true,
  useVoice: () => ({
    transcript: "",
    silenceMsLeft: 0,
    start: vi.fn(),
    stop: vi.fn(),
    submitNow: vi.fn(),
    lang: "en-US",
    setLang: vi.fn(),
    error: null,
  }),
  VOICE_LANGS: ["en-US", "zh-TW", "ja-JP"] as const,
}));

vi.mock("@/lib/chatStore", () => ({
  chatStore: {
    send: vi.fn(async () => {}),
    sendReceiptPhoto: vi.fn(async () => {}),
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
}));

vi.mock("@/lib/ledger", () => ({
  ledger: { deleteTransaction: vi.fn() },
  useLedger: () => ({ transactions: [], cards: [] }),
}));

vi.mock("@/lib/chatSeed", () => ({ consumeChatSeed: () => null }));

vi.mock("@/lib/chatApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/chatApi")>(
    "@/lib/chatApi",
  );
  return { ...actual, getWeeklyRecap: vi.fn(async () => null) };
});

import ChatPage from "@/pages/chat";

function renderChat() {
  return render(
    <MemoryRouter>
      <ChatPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
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

describe("ChatPage — receipt capture button", () => {
  test("the camera button renders in the composer and is reachable", () => {
    renderChat();
    const btn = screen.getByLabelText("scan a receipt");
    expect(btn).toBeInTheDocument();
    expect(btn).toBeEnabled();
  });
});
