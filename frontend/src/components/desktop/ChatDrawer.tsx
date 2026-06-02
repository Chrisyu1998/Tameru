import { useEffect, useState } from "react";
import { ChevronsLeft, ChevronsRight, SquarePen, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { CandidateCards } from "@/components/chat/CandidateCards";
import { CardParseCard } from "@/components/chat/CardParseCard";
import { Chart } from "@/components/chat/Chart";
import { EntryInsightBubble } from "@/components/chat/EntryInsightBubble";
import { MessageBubble, ToolAttribution } from "@/components/chat/MessageBubble";
import { MiniBarChart } from "@/components/chat/MiniBarChart";
import { ParseCard } from "@/components/chat/ParseCard";
import { SubscriptionParseCard } from "@/components/chat/SubscriptionParseCard";
import { EditTransactionSheet } from "@/components/EditTransactionSheet";
import { chatStore, useChatStore } from "@/lib/chatStore";
import { useLedger } from "@/lib/ledger";
import type { Card, Transaction } from "@/lib/fixtures";
import type { ChatMessage } from "@/lib/chat";
import { cn } from "@/lib/utils";

/**
 * Desktop right-side chat drawer. Slides in from the right edge of the main
 * pane when the user submits via the persistent composer. No scrim — main
 * pane stays interactive. Closes only on X / Esc / ⌘\.
 */
export function ChatDrawer() {
  const { t } = useTranslation();
  const {
    drawerOpen,
    drawerExpanded,
    messages,
    busy,
    streamingText,
    lastError,
  } = useChatStore();
  const { transactions, cards } = useLedger();
  const [editingTx, setEditingTx] = useState<Transaction | null>(null);

  // Esc + ⌘\
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!drawerOpen) return;
      if (e.key === "Escape") {
        chatStore.closeDrawer();
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "\\") {
        e.preventDefault();
        if (drawerExpanded) chatStore.toggleExpanded();
        else chatStore.closeDrawer();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen, drawerExpanded]);

  if (!drawerOpen) return null;

  return (
    <aside
      role="complementary"
      aria-label={t("chat.drawer.label")}
      className={cn(
        "fixed top-0 right-0 z-30 hidden h-screen md:flex flex-col border-l border-hairline bg-canvas",
        "animate-slide-in-right",
        drawerExpanded
          ? "w-[calc(100vw-15rem)]"
          : "w-[33%] min-w-[400px]"
      )}
    >
      {/* Header */}
      <header className="flex items-center justify-between border-b border-hairline px-4 py-3">
        <span className="font-serif text-[1.05rem] text-ink lowercase-title">
          tameru
        </span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => chatStore.newChat()}
            aria-label={t("chat.drawer.newChat")}
            className="flex h-8 w-8 items-center justify-center rounded-full text-ink-tertiary hover:bg-sunken/60 hover:text-ink"
          >
            <SquarePen className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => chatStore.toggleExpanded()}
            aria-label={drawerExpanded ? t("chat.drawer.collapse") : t("chat.drawer.expand")}
            title={drawerExpanded ? "collapse (⌘\\)" : "expand"}
            className="flex h-8 w-8 items-center justify-center rounded-full text-ink-tertiary hover:bg-sunken/60 hover:text-ink"
          >
            {drawerExpanded ? (
              <ChevronsRight className="h-4 w-4" />
            ) : (
              <ChevronsLeft className="h-4 w-4" />
            )}
          </button>
          <button
            type="button"
            onClick={() => chatStore.closeDrawer()}
            aria-label={t("chat.drawer.close")}
            className="flex h-8 w-8 items-center justify-center rounded-full text-ink-tertiary hover:bg-sunken/60 hover:text-ink"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </header>

      {/* Conversation — leaves room at bottom for the morphed composer */}
      <div className="flex-1 overflow-y-auto px-4 py-5 pb-28">
        <div className="mx-auto flex max-w-2xl flex-col gap-4">
          {messages.length === 0 && (
            <div className="mt-10 text-center">
              <h2 className="font-serif text-xl text-ink lowercase-title">
                {t("chat.drawer.emptyHeading")}
              </h2>
              <p className="mt-2 text-[0.85rem] text-ink-secondary">
                {t("chat.drawer.emptyBody")}
              </p>
            </div>
          )}
          {messages.map((m) => (
            <MessageRow
              key={m.id}
              msg={m}
              transactions={transactions}
              cards={cards}
              onSelectCandidate={(tx) => setEditingTx(tx)}
            />
          ))}

          {/* Live SSE stream (Day 12) — mirrors the mobile /chat surface. */}
          {busy && streamingText && (
            <MessageBubble role="assistant" bubble={false}>
              {streamingText}
            </MessageBubble>
          )}

          {/* Retry affordance when the stream dropped. The desktop
              composer floats outside this drawer, so we surface the
              retry inline here instead of next to the input. */}
          {lastError && !busy && (
            <div className="mx-auto mt-2 flex w-fit items-center gap-2 rounded-full border border-hairline bg-sunken px-3 py-1.5 text-[0.78rem] text-ink-secondary">
              <span>{lastError.message}</span>
              <button
                type="button"
                onClick={() => void chatStore.retry()}
                className="rounded-full bg-moss px-2.5 py-0.5 text-[0.7rem] text-surface hover:bg-moss-deep"
              >
                {t("chat.drawer.retry")}
              </button>
              <button
                type="button"
                onClick={() => chatStore.dismissError()}
                aria-label={t("chat.drawer.dismiss")}
                className="text-ink-tertiary hover:text-ink"
              >
                ✕
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Edit sheet (renders as side panel on desktop via BottomSheet override) */}
      <EditTransactionSheet
        open={editingTx !== null}
        transaction={editingTx}
        cards={cards}
        onClose={() => setEditingTx(null)}
        onRequestDelete={(tx) => {
          // delegate to ledger directly — same pattern as /chat
          import("@/lib/ledger").then(({ ledger }) => {
            ledger.deleteTransaction(tx.id);
            setEditingTx(null);
          });
        }}
      />
    </aside>
  );
}

/* Same dispatcher as /chat, but inline so we don't export-cycle. */
function MessageRow({
  msg,
  transactions,
  cards,
  onSelectCandidate,
}: {
  msg: ChatMessage;
  transactions: Transaction[];
  cards: Card[];
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
          onConfirm={(draft) => chatStore.commitDraft(msg.id, draft)}
          onFix={() => {
            /* desktop drawer skips the inline "fix" sheet flow */
          }}
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
