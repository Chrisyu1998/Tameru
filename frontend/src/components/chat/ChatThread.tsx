import { Chart } from "@/components/chat/Chart";
import { MessageBubble } from "@/components/chat/MessageBubble";
import type { ChatMessage } from "@/lib/chat";

interface ChatThreadProps {
  messages: ChatMessage[];
}

/**
 * Pure presentational chat thread. Renders user messages, assistant
 * text, and assistant rich-chart messages — the shapes the guided
 * tour needs and the most common shapes in live chat. The full chat
 * surface at `pages/chat.tsx` still owns the broader message-type
 * switch (parse cards, candidates, insight bubbles, etc.) because
 * those branches couple to commit / edit actions that don't belong
 * on a presentational component.
 *
 * Day 21 extracted this so the tour's Screen 3 can render the same
 * `MessageBubble` and `Chart` primitives that live chat uses
 * (DESIGN.md §5.4.2: "they look real because they are real").
 */
export function ChatThread({ messages }: ChatThreadProps) {
  return (
    <div className="flex flex-col gap-3">
      {messages.map((m) => {
        if (m.role === "user") {
          return (
            <MessageBubble key={m.id} role="user">
              {m.text}
            </MessageBubble>
          );
        }
        if (m.kind === "text") {
          return (
            <MessageBubble key={m.id} role="assistant">
              {m.text}
            </MessageBubble>
          );
        }
        if (m.kind === "rich-chart") {
          return (
            <MessageBubble key={m.id} role="assistant">
              {m.preface && <p className="mb-2">{m.preface}</p>}
              <Chart spec={m.spec} />
            </MessageBubble>
          );
        }
        // Other assistant shapes (parse, candidates, insights, etc.) belong
        // to the live chat surface — see pages/chat.tsx. Silently skip
        // here so a future caller that mixes types doesn't crash the tour.
        return null;
      })}
    </div>
  );
}
