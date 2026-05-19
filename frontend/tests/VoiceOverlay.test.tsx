/**
 * VoiceOverlay test — Day 18.
 *
 * Covers:
 *   - language chip cycles en → 中 → 日 → en
 *   - error chip renders with code-specific copy
 *   - try-again button calls onRetry
 *   - submit now is disabled while an error is showing
 *   - stop button calls onStop
 */

import { describe, expect, test, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { VoiceOverlay } from "@/components/chat/VoiceOverlay";
import type { VoiceError, VoiceLang } from "@/lib/voice";

function renderOverlay(
  overrides: Partial<React.ComponentProps<typeof VoiceOverlay>> = {},
) {
  const props: React.ComponentProps<typeof VoiceOverlay> = {
    transcript: "spent forty seven",
    silenceMsLeft: 0,
    silenceWindowMs: 1500,
    lang: "en-US" as VoiceLang,
    onChangeLang: vi.fn(),
    error: null,
    onRetry: vi.fn(),
    onSubmitNow: vi.fn(),
    onStop: vi.fn(),
    ...overrides,
  };
  return { props, ...render(<VoiceOverlay {...props} />) };
}

describe("VoiceOverlay — language chip", () => {
  test("cycles through en → 中 → 日 → en", async () => {
    const onChangeLang = vi.fn();
    const user = userEvent.setup();

    const { rerender, props } = renderOverlay({ lang: "en-US", onChangeLang });
    await user.click(screen.getByText("en"));
    expect(onChangeLang).toHaveBeenLastCalledWith("zh-TW");

    rerender(<VoiceOverlay {...props} lang="zh-TW" />);
    await user.click(screen.getByText("中"));
    expect(onChangeLang).toHaveBeenLastCalledWith("ja-JP");

    rerender(<VoiceOverlay {...props} lang="ja-JP" />);
    await user.click(screen.getByText("日"));
    expect(onChangeLang).toHaveBeenLastCalledWith("en-US");
  });
});

describe("VoiceOverlay — errors", () => {
  test("renders not-allowed copy with browser-settings instructions", () => {
    const err: VoiceError = { code: "not-allowed" };
    renderOverlay({ error: err });
    expect(
      screen.getByText(/voice access denied/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/browser settings/i)).toBeInTheDocument();
  });

  test("renders network copy when offline", () => {
    renderOverlay({ error: { code: "network" } });
    expect(screen.getByText(/voice needs internet/i)).toBeInTheDocument();
  });

  test("try-again button calls onRetry", async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    renderOverlay({ error: { code: "no-speech" }, onRetry });
    // The "no-speech" message also contains "try again", so we have to
    // disambiguate by role.
    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  test("submit now is disabled while an error is showing", () => {
    renderOverlay({ error: { code: "no-speech" } });
    const submit = screen.getByText("submit now") as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
  });
});

describe("VoiceOverlay — controls", () => {
  test("cancel button calls onStop", async () => {
    const onStop = vi.fn();
    const user = userEvent.setup();
    renderOverlay({ onStop });
    await user.click(screen.getByLabelText("cancel voice input"));
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  test("submit now calls onSubmitNow when no error", async () => {
    const onSubmitNow = vi.fn();
    const user = userEvent.setup();
    renderOverlay({ onSubmitNow });
    await user.click(screen.getByText("submit now"));
    expect(onSubmitNow).toHaveBeenCalledTimes(1);
  });
});
