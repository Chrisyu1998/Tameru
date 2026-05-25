/**
 * Single source of truth for the 4-screen guided tour (Day 21).
 *
 * The tour renders real Tameru components with these fixtures so the
 * preview looks identical to the live product. Per DESIGN.md §5.4.2:
 * "they look real because they are real."
 *
 * Numerics in the dashboard fixture are JSON strings to match the wire
 * shape from `GET /dashboard/summary` (Pydantic Decimal serialization).
 * The presentational `<Dashboard>` component cannot tell this fixture
 * apart from a live response.
 */

import type { DashboardSummaryWire } from "@/lib/dashboardApi";
import type { ChatMessage } from "@/lib/chat";

/** Mirrors `DashboardSummaryWire`. Numerics are decimal strings. */
export const tourDashboard: DashboardSummaryWire = {
  this_month: "1842.40",
  baseline: "1640.00",
  delta_pct: 12,
  baseline_ready: true,
  observation: "Dining is running a touch high this week. Groceries are steady.",
  categories: [
    {
      name: "Dining",
      this_month: "287.00",
      baseline: "240.00",
      delta_abs: "47.00",
      delta_pct: 19,
      color: "amber",
      baseline_ready: true,
    },
    {
      name: "Groceries",
      this_month: "318.00",
      baseline: "340.00",
      delta_abs: "-22.00",
      delta_pct: -6,
      color: "green",
      baseline_ready: true,
    },
    {
      name: "Transit",
      this_month: "108.00",
      baseline: "100.00",
      delta_abs: "8.00",
      delta_pct: 8,
      color: "neutral",
      baseline_ready: true,
    },
    {
      name: "Shopping",
      this_month: "164.00",
      baseline: "180.00",
      delta_abs: "-16.00",
      delta_pct: -9,
      color: "green",
      baseline_ready: true,
    },
  ],
};

/**
 * Script for the Screen 2 entry-moment animation. Four beats render in
 * sequence; the EntryNudgeAnimation component drives the timing via
 * CSS keyframes.
 */
export const tourEntryNudge = {
  userMessage: "spent $47 at Trader Joe's",
  parseCard: {
    merchant: "Trader Joe's",
    amount: "$47.00",
    category: "Groceries",
    card: "Chase Sapphire",
    date: "today",
  },
  confirmedLine: {
    merchant: "Trader Joe's",
    amount: "$47.00",
    category: "Groceries",
  },
  insight: "4th grocery run this week — you usually have 2.",
};

/**
 * Static chat transcript for Screen 3. Three messages: the user's
 * question, an assistant text reply, and an assistant rich-chart that
 * visualizes the 4-month dining trend. Shapes match the live
 * `ChatMessage` union so `<ChatThread>` renders this identically to a
 * real turn driven by `render_chart`.
 */
export const tourChatMessages: ChatMessage[] = [
  {
    id: "tour-user-1",
    role: "user",
    text: "How much did I spend on dining last month?",
  },
  {
    id: "tour-asst-1",
    role: "assistant",
    kind: "text",
    text:
      "$284 — about $54 below your 3-month average. Two restaurant visits and a takeout streak the week of the 10th drove most of it.",
  },
  {
    id: "tour-asst-2",
    role: "assistant",
    kind: "rich-chart",
    spec: {
      type: "bar",
      title: "dining, last 4 months",
      y_label: "spend ($)",
      x: ["Feb", "Mar", "Apr", "May"],
      series: [{ name: "dining", data: [332, 351, 338, 284] }],
    },
    via: "render_chart",
  },
];

/** Static email shape for Screen 4. Subject + from + body bullets. */
export const tourDigest = {
  subject: "Your week, in brief",
  from: "Tameru <weekly@tameru.xyz>",
  preheader: "A quiet week.",
  bullets: [
    {
      tone: "good" as const,
      text: "You spent $284, below your usual $340.",
    },
    {
      tone: "good" as const,
      text: "Dining is trending down for the second week.",
    },
    {
      tone: "warn" as const,
      text: "One subscription renews Tuesday.",
    },
  ],
};

export const tourFixtures = {
  dashboard: tourDashboard,
  entryNudge: tourEntryNudge,
  chat: tourChatMessages,
  digest: tourDigest,
};
