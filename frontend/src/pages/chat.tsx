import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Camera, ChevronLeft, Mic, RefreshCw, Send, SquarePen, WifiOff, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { CandidateCards } from "@/components/chat/CandidateCards";
import { CardParseCard } from "@/components/chat/CardParseCard";
import { Chart } from "@/components/chat/Chart";
import { DailyCapCard } from "@/components/chat/DailyCapCard";
import { EntryInsightBubble } from "@/components/chat/EntryInsightBubble";
import { MessageBubble, ToolAttribution } from "@/components/chat/MessageBubble";
import { MiniBarChart } from "@/components/chat/MiniBarChart";
import { ParseCard } from "@/components/chat/ParseCard";
import { ServiceBanner } from "@/components/chat/ServiceBanner";
import { SubscriptionParseCard } from "@/components/chat/SubscriptionParseCard";
import { VoiceOverlay } from "@/components/chat/VoiceOverlay";
import { WeeklyRecapCard } from "@/components/chat/WeeklyRecapCard";
import { isVoiceSupported, useVoice } from "@/lib/voice";
import { EditTransactionSheet } from "@/components/EditTransactionSheet";
import { ledger, useLedger } from "@/lib/ledger";
import { consumeChatSeed } from "@/lib/chatSeed";
import { getWeeklyRecap, type WeeklyRecap } from "@/lib/chatApi";
import {
  parseTransaction,
  type ChatMessage,
} from "@/lib/chat";
import { chatStore, useChatStore } from "@/lib/chatStore";
import { downscaleImage } from "@/lib/image";
import type { Card, Transaction } from "@/lib/fixtures";
import { track } from "@/lib/analytics";
import { cn } from "@/lib/utils";

const SILENCE_WINDOW_MS = 1500;

export default function ChatPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { transactions, cards } = useLedger();
  const { messages, busy, capEngaged, streamingText, lastError } = useChatStore();

  const [input, setInput] = useState("");
  const [voiceMode, setVoiceMode] = useState(false);
  const [serviceDown, setServiceDown] = useState(false);
  const [online, setOnline] = useState(true);
  const [editingTx, setEditingTx] = useState<Transaction | null>(null);
  // When the edit sheet is open on a parse-card draft (not a ledger row),
  // we record the source message id here so save/delete route back to the
  // chat draft instead of the ledger. Null when editing a real row.
  const [editingDraftMsgId, setEditingDraftMsgId] = useState<string | null>(
    null,
  );

  // Pre-fill the input from a session-scoped seed (set by /cards, /subscriptions).
  useEffect(() => {
    const seed = consumeChatSeed();
    if (seed) setInput(seed + " ");
    // feature_used: chat — fires once per page mount. The actual
    // chat_session_started analytics event is gated on a successful
    // first turn (see chatStore._streamOnce onDone); this is the
    // "user opened the chat surface" measure.
    track("feature_used", { feature: "chat" });
  }, []);

  // On mount, if the store has a persisted conversation id but no in-memory
  // thread (page refresh), pull history from the server so the user doesn't
  // lose context. Fire-and-forget; failures fall back to an empty thread.
  useEffect(() => {
    void chatStore.hydrateMessages();
  }, []);

  // Fetch this week's recap for the pinned "This week" card (DESIGN.md §6.2).
  // Fire-and-forget: a failed/empty recap just means no card. The server
  // returns null for a brand-new/dormant user (no wasted Sonnet call).
  const [recap, setRecap] = useState<WeeklyRecap | null>(null);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const r = await getWeeklyRecap();
        if (!cancelled) setRecap(r);
      } catch {
        // A recap fetch failure never breaks the chat surface.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Track online status for the offline notice.
  useEffect(() => {
    if (typeof window === "undefined") return;
    setOnline(window.navigator.onLine);
    const onOnline = () => setOnline(true);
    const onOffline = () => setOnline(false);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, []);

  // Auto-scroll on new messages.
  const scrollerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages.length, voiceMode]);

  /* ─── Send pipeline (delegates to shared chatStore) ─────────── */

  const handleSend = (raw: string) => {
    const text = raw.trim();
    if (!text || capEngaged) return;
    chatStore.send(text);
    setInput("");
  };

  // Receipt photo → downscale + JPEG re-encode on-device → POST /receipts/parse
  // (via the store) → parse card. If downscale fails (undecodable file), fall
  // back to the original so the backend still gets a shot.
  const handleCapture = async (file: File) => {
    try {
      const blob = await downscaleImage(file);
      await chatStore.sendReceiptPhoto(blob);
    } catch {
      await chatStore.sendReceiptPhoto(file);
    }
  };

  /* ─── Parse-card actions ────────────────────────────────────── */

  const commitDraft = (
    msgId: string,
    draft: ReturnType<typeof parseTransaction>
  ) => {
    chatStore.commitDraft(msgId, draft);
  };

  const fixDraft = (msgId: string) => {
    const m = messages.find((x) => x.id === msgId);
    if (!m || m.role !== "assistant" || m.kind !== "parse") return;
    // The sheet works in terms of Transaction, so we synthesize one with a
    // stable id rooted in the message. The id is only used as a React key
    // here — the override callbacks below route by msgId, not id.
    const tempTx: Transaction = {
      id: `draft-${msgId}`,
      merchant: m.draft.merchant,
      amountCents: m.draft.amountCents,
      date: m.draft.date,
      cardId: m.draft.cardId,
      category: m.draft.category,
    };
    setEditingDraftMsgId(msgId);
    setEditingTx(tempTx);
  };

  /* ─── Candidate selection ──────────────────────────────────── */

  const handleSelectCandidate = (tx: Transaction) => {
    setEditingDraftMsgId(null);
    setEditingTx(tx);
  };

  const closeEdit = () => {
    setEditingTx(null);
    setEditingDraftMsgId(null);
  };

  const requestDelete = (tx: Transaction) => {
    if (editingDraftMsgId) {
      // Draft path: discard the parse card rather than DELETE a server row
      // that doesn't exist.
      chatStore.discardDraft(editingDraftMsgId);
    } else {
      void ledger.deleteTransaction(tx.id);
    }
    closeEdit();
  };

  // When editing a draft, save mutates the parse card in chatStore so the
  // user's tweaks survive but no row is written until they tap "looks right"
  // (which then flows through commitDraft → POST /transactions/confirm).
  const saveOverride = editingDraftMsgId
    ? (_tx: Transaction, patch: Partial<Transaction>) => {
        chatStore.updateDraft(editingDraftMsgId, patch);
      }
    : undefined;

  /* ─── Voice ─────────────────────────────────────────────────── */

  const voice = useVoice({
    silenceWindowMs: SILENCE_WINDOW_MS,
    onCommit: (text) => {
      setVoiceMode(false);
      handleSend(text);
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

  /* ─── New chat ──────────────────────────────────────────────── */

  const newChat = () => chatStore.newChat();

  /* ─── Dev: daily-cap toggle (hidden behind the title) ───────── */

  const isDev = import.meta.env.DEV;
  const toggleCap = () => chatStore.setCapEngaged(!capEngaged);


  /* ─── Render ────────────────────────────────────────────────── */

  return (
    <div className="flex h-[100dvh] flex-col bg-canvas">
      {/* Top bar */}
      <header className="flex items-center justify-between border-b border-hairline bg-canvas/90 px-3 py-3 backdrop-blur">
        <button
          type="button"
          onClick={() => navigate("/")}
          aria-label={t("chat.back")}
          className="flex h-9 w-9 items-center justify-center rounded-full text-ink-secondary hover:bg-sunken/60 hover:text-ink"
        >
          <ChevronLeft className="h-5 w-5" />
        </button>
        {isDev ? (
          <button
            type="button"
            onClick={toggleCap}
            title="dev: toggle daily cap"
            className="font-serif text-[1.05rem] text-ink lowercase-title"
          >
            tameru
          </button>
        ) : (
          <span className="font-serif text-[1.05rem] text-ink lowercase-title">
            tameru
          </span>
        )}
        <button
          type="button"
          onClick={newChat}
          aria-label={t("chat.newChat")}
          className="flex h-9 w-9 items-center justify-center rounded-full text-ink-secondary hover:bg-sunken/60 hover:text-ink"
        >
          <SquarePen className="h-4.5 w-4.5" />
        </button>
      </header>

      {/* Conversation body */}
      <div
        ref={scrollerRef}
        className="flex-1 overflow-y-auto px-4 py-5"
      >
        <div className="mx-auto flex max-w-md flex-col gap-4">
          {/* Service-down banner */}
          {serviceDown && (
            <ServiceBanner
              message={t("chat.serviceBanner.aiMoment")}
              onDismiss={() => setServiceDown(false)}
            />
          )}

          {/* Weekly recap — pinned "This week" card above the thread. Not a
              chat_messages row (the thread stays append-only, DESIGN.md §6.2). */}
          {recap && <WeeklyRecapCard recap={recap} />}

          {/* Empty state */}
          {messages.length === 0 && (
            <EmptyChat
              onPrompt={(p) => handleSend(p)}
              onSimulateOutage={
                isDev ? () => setServiceDown(true) : null
              }
            />
          )}

          {/* Messages */}
          {messages.map((m) => (
            <MessageRow
              key={m.id}
              msg={m}
              transactions={transactions}
              cards={cards}
              onConfirmDraft={(draft) => commitDraft(m.id, draft)}
              onFixDraft={() => fixDraft(m.id)}
              onSelectCandidate={handleSelectCandidate}
            />
          ))}

          {/* Live SSE stream — Day 12. Tokens flow into this bubble until
              `done` (at which point _renderTurn replaces it with the
              final ParseCard / CandidateList / text bubble) or `error`
              (at which point streamingText clears and the retry banner
              below the messages appears). */}
          {busy && streamingText && (
            <MessageBubble role="assistant" bubble={false}>
              {streamingText}
            </MessageBubble>
          )}
        </div>
      </div>

      {/* Bottom: voice overlay, daily cap, or input row.
          The retry banner overlays the InputRow rather than replacing
          it so the user can either tap Retry on the failed turn OR
          start a fresh message. */}
      {capEngaged ? (
        <DailyCapCard />
      ) : voiceMode ? (
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
      ) : (
        <>
          {lastError && (
            <RetryBanner
              message={lastError.message}
              busy={busy}
              onRetry={() => void chatStore.retry()}
              onDismiss={() => chatStore.dismissError()}
            />
          )}
          <InputRow
            value={input}
            onChange={setInput}
            onSend={() => handleSend(input)}
            onMic={startVoice}
            onCapture={handleCapture}
            micSupported={isVoiceSupported()}
            offline={!online}
            busy={busy}
          />
        </>
      )}

      {/* Edit sheet (used by both candidate selection AND "let me fix it") */}
      <EditTransactionSheet
        open={editingTx !== null}
        transaction={editingTx}
        cards={cards}
        onClose={closeEdit}
        onRequestDelete={requestDelete}
        onSave={saveOverride}
      />
    </div>
  );
}

/* ─── Message row dispatcher ──────────────────────────────────── */

function MessageRow({
  msg,
  transactions,
  cards,
  onConfirmDraft,
  onFixDraft,
  onSelectCandidate,
}: {
  msg: ChatMessage;
  transactions: Transaction[];
  cards: Card[];
  onConfirmDraft: (draft: ReturnType<typeof parseTransaction>) => void;
  onFixDraft: () => void;
  onSelectCandidate: (tx: Transaction) => void;
}) {
  if (msg.role === "user") {
    return <MessageBubble role="user">{msg.text}</MessageBubble>;
  }

  if (msg.kind === "text") {
    return (
      <div>
        <MessageBubble role="assistant" bubble={false}>
          {msg.text}
        </MessageBubble>
        {msg.via && <ToolAttribution name={msg.via} />}
      </div>
    );
  }

  if (msg.kind === "insight") {
    return <EntryInsightBubble text={msg.text} severity={msg.severity} />;
  }

  if (msg.kind === "chart") {
    return (
      <div>
        <MessageBubble role="assistant">
          <p>{msg.preface}</p>
          <MiniBarChart bars={msg.bars} />
        </MessageBubble>
        <ToolAttribution name={msg.via} />
      </div>
    );
  }

  if (msg.kind === "rich-chart") {
    return (
      <div>
        <MessageBubble role="assistant">
          {msg.preface && <p>{msg.preface}</p>}
          <Chart spec={msg.spec} />
        </MessageBubble>
        {msg.via && <ToolAttribution name={msg.via} />}
      </div>
    );
  }

  if (msg.kind === "parse") {
    return (
      <div className="flex w-full justify-start">
        <ParseCard
          preface={msg.preface}
          draft={msg.draft}
          cards={cards}
          committed={!!msg.committedTxId}
          committedState={msg.committedState}
          frozen={msg.frozen}
          pendingSync={msg.pendingSync}
          onConfirm={(draft) => onConfirmDraft(draft)}
          onFix={onFixDraft}
        />
      </div>
    );
  }

  if (msg.kind === "card-parse") {
    return (
      <div className="flex w-full justify-start">
        <CardParseCard
          preface={msg.preface}
          draft={msg.draft}
          committed={!!msg.committedCardId}
          committedState={msg.committedState}
          frozen={msg.frozen}
          pendingSync={msg.pendingSync}
          onConfirm={(draft) => chatStore.commitCardDraft(msg.id, draft)}
        />
      </div>
    );
  }

  if (msg.kind === "subscription-parse") {
    return (
      <div className="flex w-full justify-start">
        <SubscriptionParseCard
          preface={msg.preface}
          draft={msg.draft}
          cards={cards}
          committed={!!msg.committedSubscriptionId}
          committedState={msg.committedState}
          frozen={msg.frozen}
          pendingSync={msg.pendingSync}
          onConfirm={(draft) =>
            chatStore.commitSubscriptionDraft(msg.id, draft)
          }
        />
      </div>
    );
  }

  if (msg.kind === "candidates") {
    const lookup = new Map(transactions.map((t) => [t.id, t]));
    const candidates = msg.candidateIds
      .map((id) => lookup.get(id))
      .filter((t): t is Transaction => !!t);
    return (
      <div>
        <div className="flex w-full justify-start">
          <CandidateCards
            preface={msg.preface}
            candidates={candidates}
            cards={cards}
            onSelect={onSelectCandidate}
          />
        </div>
        <ToolAttribution name={msg.via} />
      </div>
    );
  }

  return null;
}

/* ─── Empty state ─────────────────────────────────────────────── */

function EmptyChat({
  onPrompt,
  onSimulateOutage,
}: {
  onPrompt: (text: string) => void;
  /** Null in production — gates the outage-banner dev affordance. */
  onSimulateOutage: (() => void) | null;
}) {
  const { t } = useTranslation();
  const exampleKeys = [
    "chat.examples.coffee",
    "chat.examples.lunch",
    "chat.examples.editDinner",
    "chat.examples.compare",
  ] as const;
  return (
    <div className="mt-10 flex flex-col items-center text-center">
      <h2 className="font-serif text-2xl text-ink lowercase-title">
        {t("chat.emptyState.heading")}
      </h2>
      <p className="mt-2 max-w-[28ch] text-[0.9rem] text-ink-secondary">
        {t("chat.emptyState.body")}
      </p>
      <div className="mt-5 flex flex-wrap justify-center gap-2">
        {exampleKeys.map((key) => {
          const ex = t(key);
          return (
            <button
              key={key}
              type="button"
              onClick={() => onPrompt(ex)}
              className="rounded-full border border-hairline bg-surface px-3 py-1 text-[0.78rem] text-ink-secondary hover:bg-elevated hover:text-ink"
            >
              {ex}
            </button>
          );
        })}
      </div>
      {onSimulateOutage && (
        <button
          type="button"
          onClick={onSimulateOutage}
          className="mt-8 text-[0.65rem] text-ink-quaternary hover:text-ink-tertiary"
        >
          {t("chat.emptyState.devSimulateOutage")}
        </button>
      )}
    </div>
  );
}

/* ─── Retry banner — Day 12 SSE failure surface ─────────────── */

function RetryBanner({
  message,
  busy,
  onRetry,
  onDismiss,
}: {
  message: string;
  busy: boolean;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="border-t border-hairline bg-canvas/95 px-3 pt-3 backdrop-blur">
      <div className="mx-auto flex max-w-md items-center justify-between gap-2 rounded-lg border border-hairline bg-sunken px-3 py-2 text-[0.8rem] text-ink-secondary">
        <span className="flex-1">{message}</span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={onRetry}
            disabled={busy}
            className="flex items-center gap-1 rounded-full bg-moss px-3 py-1 text-[0.75rem] text-surface hover:bg-moss-deep disabled:opacity-50"
          >
            <RefreshCw className="h-3 w-3" />
            {t("chat.retry")}
          </button>
          <button
            type="button"
            onClick={onDismiss}
            aria-label={t("chat.dismiss")}
            className="flex h-6 w-6 items-center justify-center rounded-full text-ink-tertiary hover:bg-elevated hover:text-ink"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}

/* ─── Input row ───────────────────────────────────────────────── */

function InputRow({
  value,
  onChange,
  onSend,
  onMic,
  onCapture,
  micSupported,
  offline,
  busy,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  onMic: () => void;
  onCapture: (file: File) => void;
  micSupported: boolean;
  offline: boolean;
  busy: boolean;
}) {
  const { t } = useTranslation();
  const cameraInputRef = useRef<HTMLInputElement>(null);
  const hasText = value.trim().length > 0;
  const canSend = hasText && !busy;
  // Requires a network round-trip to Gemini, so it's pointless offline; also
  // disabled while a turn/scan is in flight.
  const canCapture = !busy && !offline;
  return (
    <div className="border-t border-hairline bg-canvas/95 px-3 py-3 backdrop-blur">
      {offline && (
        <div className="mx-auto mb-2 flex max-w-md items-center gap-1.5 px-2 text-[0.7rem] text-ink-tertiary">
          <WifiOff className="h-3 w-3" />
          <span>{t("chat.offlineNotice")}</span>
        </div>
      )}
      {busy && (
        <div className="mx-auto mb-2 flex max-w-md items-center gap-1.5 px-2 text-[0.7rem] italic text-ink-tertiary">
          <span className="inline-block h-1.5 w-1.5 animate-ping-soft rounded-full bg-moss" />
          <span>{t("chat.thinking")}</span>
        </div>
      )}
      <div className="mx-auto flex max-w-md items-end gap-2">
        <div className="flex-1 rounded-2xl border border-hairline bg-surface px-3 py-2">
          <textarea
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (canSend) onSend();
              }
            }}
            rows={1}
            placeholder={t("chat.placeholder")}
            disabled={busy}
            className="block max-h-32 w-full resize-none bg-transparent text-[0.95rem] text-ink placeholder:text-ink-quaternary focus:outline-none disabled:opacity-60"
          />
        </div>
        <input
          ref={cameraInputRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            // Reset so picking the same file again still fires onChange.
            e.target.value = "";
            if (file) onCapture(file);
          }}
        />
        <button
          type="button"
          onClick={() => cameraInputRef.current?.click()}
          aria-label={t("chat.captureReceipt")}
          disabled={!canCapture}
          className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full border border-hairline bg-surface text-ink-secondary transition-colors hover:bg-elevated hover:text-ink disabled:opacity-50"
        >
          <Camera className="h-4 w-4" />
        </button>
        {micSupported && (
          <button
            type="button"
            onClick={onMic}
            aria-label={t("chat.recordVoice")}
            disabled={busy}
            className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full border border-hairline bg-surface text-ink-secondary transition-colors hover:bg-elevated hover:text-ink disabled:opacity-50"
          >
            <Mic className="h-4 w-4" />
          </button>
        )}
        <button
          type="button"
          onClick={onSend}
          aria-label={t("chat.send")}
          disabled={!canSend}
          className={cn(
            "flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full transition-all",
            canSend
              ? "bg-moss text-surface hover:bg-moss-deep scale-100 opacity-100"
              : "scale-90 opacity-0 pointer-events-none bg-moss text-surface"
          )}
        >
          <Send className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
