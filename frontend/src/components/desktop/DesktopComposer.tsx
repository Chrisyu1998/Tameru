import { useEffect, useRef, useState } from "react";
import { Camera, Mic, Send } from "lucide-react";
import { useTranslation } from "react-i18next";
import { VoiceOverlay } from "@/components/chat/VoiceOverlay";
import { chatStore, useChatStore } from "@/lib/chatStore";
import { downscaleImage } from "@/lib/image";
import { isVoiceSupported, useVoice } from "@/lib/voice";
import { cn } from "@/lib/utils";

const SILENCE_WINDOW_MS = 1500;

/** Placeholder keys resolved with t() at render time so they rotate in the current language. */
const PLACEHOLDER_KEYS = [
  "chat.examples.coffee",
  "chat.examples.lunch",
  "chat.examples.editDinner",
  "chat.examples.compare",
] as const;

const PLACEHOLDER_INTERVAL_MS = 3200;

/**
 * Desktop-only persistent composer. Floats near bottom-center of main pane,
 * morphs into the drawer's bottom when chat is open. Reads as infrastructure,
 * not a CTA.
 */
export function DesktopComposer() {
  const { t } = useTranslation();
  const { drawerOpen, drawerExpanded } = useChatStore();
  const [value, setValue] = useState("");
  const [placeholderIdx, setPlaceholderIdx] = useState(0);
  const [focused, setFocused] = useState(false);
  const [voiceMode, setVoiceMode] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);

  // Receipt upload → downscale + JPEG re-encode → POST /receipts/parse (via
  // the store, which also opens the drawer so the parse card is visible). On
  // desktop this is a file picker (no camera); a saved receipt image works.
  const handleCapture = async (file: File) => {
    try {
      const blob = await downscaleImage(file);
      chatStore.sendReceiptFromComposer(blob);
    } catch {
      chatStore.sendReceiptFromComposer(file);
    }
  };

  // Voice transcript flows into chatStore.sendFromComposer, which opens
  // the drawer for the response — same UX as typing + pressing Send.
  const voice = useVoice({
    silenceWindowMs: SILENCE_WINDOW_MS,
    onCommit: (text) => {
      setVoiceMode(false);
      chatStore.sendFromComposer(text);
    },
  });

  const startVoice = () => {
    setVoiceMode(true);
    voice.start();
  };
  const stopVoice = () => {
    voice.stop();
    setVoiceMode(false);
  };
  const micSupported = isVoiceSupported();

  // Rotate placeholder, freeze on focus.
  useEffect(() => {
    if (focused) return;
    const id = window.setInterval(() => {
      setPlaceholderIdx((i) => (i + 1) % PLACEHOLDER_KEYS.length);
    }, PLACEHOLDER_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [focused]);

  // ⌘K → focus this composer.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const isCmdK =
        (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k";
      if (isCmdK) {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const submit = () => {
    const text = value.trim();
    if (!text) return;
    chatStore.sendFromComposer(text);
    setValue("");
  };

  // Width / position morphs based on drawer state.
  // - Closed:   centered floating pill in main pane.
  // - Open:     attached to bottom of right drawer (drawer width ≈ 1/3 main).
  // - Expanded: attached to bottom of fully-expanded chat (≈ main width).
  const containerCls = drawerOpen
    ? drawerExpanded
      ? // Expanded: full main pane width
        "left-0 right-0 mx-auto max-w-3xl px-6 pb-5"
      : // Drawer: bottom of right ~33% pane
        "right-0 w-[33%] min-w-[360px] px-6 pb-5"
    : // Resting: centered in main pane
      "left-1/2 -translate-x-1/2 max-w-2xl w-[min(680px,calc(100%-3rem))] pb-6";

  return (
    <div
      data-desktop-composer
      className={cn(
        "pointer-events-none fixed bottom-0 z-40 hidden md:block transition-all duration-300 ease-out",
        containerCls
      )}
    >
      <div className="pointer-events-auto">
        {voiceMode ? (
          // Replace the input pill with the listening UI. Width tracks
          // the same containerCls — the overlay is just another bottom
          // panel from a layout perspective. Same hook + same overlay
          // as the mobile chat page so language switching and error
          // copy stay consistent.
          <div className="overflow-hidden rounded-2xl border border-hairline bg-surface/95 backdrop-blur-md">
            <VoiceOverlay
              transcript={voice.transcript}
              silenceMsLeft={voice.silenceMsLeft}
              silenceWindowMs={SILENCE_WINDOW_MS}
              lang={voice.lang}
              onChangeLang={voice.setLang}
              error={voice.error}
              onRetry={voice.start}
              onSubmitNow={voice.submitNow}
              onStop={stopVoice}
            />
          </div>
        ) : (
          <>
            {!drawerOpen && (
              <p className="mb-1.5 text-center text-[0.7rem] text-ink-tertiary tracking-wide">
                {t("chat.desktopComposer.hint")}
              </p>
            )}

            <form
              onSubmit={(e) => {
                e.preventDefault();
                submit();
              }}
              className={cn(
                "flex items-center gap-2 rounded-2xl border border-hairline bg-surface/95 px-3 py-2 backdrop-blur-md transition-shadow",
                focused ? "shadow-[0_4px_18px_-14px_rgba(0,0,0,0.12)]" : ""
              )}
            >
              <input
                ref={inputRef}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onFocus={() => setFocused(true)}
                onBlur={() => setFocused(false)}
                placeholder={t(PLACEHOLDER_KEYS[placeholderIdx])}
                className="flex-1 bg-transparent px-2 py-1.5 text-[0.92rem] text-ink placeholder:text-ink-tertiary focus:outline-none"
                aria-label={t("chat.desktopComposer.inputAria")}
              />
              <input
                ref={cameraInputRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (file) void handleCapture(file);
                }}
              />
              <button
                type="button"
                onClick={() => cameraInputRef.current?.click()}
                aria-label={t("chat.captureReceipt")}
                className="flex h-8 w-8 items-center justify-center rounded-full text-ink-tertiary hover:text-ink"
              >
                <Camera className="h-4 w-4" />
              </button>
              {micSupported && (
                <button
                  type="button"
                  onClick={startVoice}
                  aria-label={t("chat.desktopComposer.recordVoice")}
                  className="flex h-8 w-8 items-center justify-center rounded-full text-ink-tertiary hover:text-ink"
                >
                  <Mic className="h-4 w-4" />
                </button>
              )}
              {value.trim() ? (
                <button
                  type="submit"
                  aria-label={t("chat.desktopComposer.send")}
                  className="flex h-8 w-8 items-center justify-center rounded-full bg-moss text-surface hover:bg-moss-deep"
                >
                  <Send className="h-3.5 w-3.5" />
                </button>
              ) : (
                <kbd className="hidden md:inline-flex h-7 select-none items-center gap-1 rounded-md border border-hairline bg-canvas px-2 text-[0.65rem] text-ink-tertiary">
                  ⌘K
                </kbd>
              )}
            </form>
          </>
        )}
      </div>
    </div>
  );
}
