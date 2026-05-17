/**
 * Chat domain: message types + a tiny scripted "AI" that interprets user
 * input as one of: parse-transaction / find-transactions / compare-categories
 * / generic chat. Pure mock — no network calls.
 */

import type {
  CardIssuer,
  CardNetwork,
  CardProgram,
} from "./cardsApi";
import { CATEGORIES, type Category } from "./categories";
import { FIXTURE_CARDS, type Transaction } from "./fixtures";

/* ─── Message model ──────────────────────────────────────────────── */

export type ToolName =
  | "parse_transaction"
  | "find_transactions"
  | "compare_categories"
  | "calculate_total";

export interface UserMessage {
  id: string;
  role: "user";
  text: string;
}

export interface AssistantTextMessage {
  id: string;
  role: "assistant";
  kind: "text";
  text: string;
  via?: ToolName;
}

export interface AssistantParseMessage {
  id: string;
  role: "assistant";
  kind: "parse";
  preface?: string;
  draft: ParseDraft;
  /** Set after the user commits this draft. */
  committedTxId?: string;
  /**
   * Lifecycle of the committed row, sourced from the rehydrate annotation
   * (`committed_state` on the wire's `tameru_proposal` block) — `'active'`
   * means the transaction still exists, `'deleted'` means it was soft-
   * deleted after confirm. Undefined for fresh in-session commits (assumed
   * `'active'`) and for never-confirmed drafts. Drives the badge text on
   * `ParseCard` (`logged.` vs `deleted.`).
   */
  committedState?: "active" | "deleted";
  /**
   * `true` for parse cards reconstructed from `chat_messages` history on a
   * page reload. Rehydrated cards are read-only — the draft is a historical
   * artifact and editing it would diverge from the row that was actually
   * committed. Fresh in-session cards are editable until the user taps
   * "looks right." Set by `_wireMessageToLocal`; absent / false on `_renderTurn`.
   */
  frozen?: boolean;
  /**
   * `true` between the user tapping "looks right" while offline (queued in
   * `offline_queue.ts`) and the drain landing on a terminal outcome. Hides
   * the action buttons so a second tap doesn't enqueue a duplicate;
   * cleared on drain 2xx (where `committedTxId` is also set) or on drain
   * permanent failure (which re-opens the card for the user to edit
   * + discard). Never persisted server-side — purely in-session UI state.
   */
  pendingSync?: boolean;
}

export interface AssistantCandidatesMessage {
  id: string;
  role: "assistant";
  kind: "candidates";
  preface: string;
  candidateIds: string[];
  intent: "edit" | "delete";
  via: ToolName;
}

export interface AssistantChartMessage {
  id: string;
  role: "assistant";
  kind: "chart";
  preface: string;
  bars: Array<{ label: string; valueCents: number }>;
  via: ToolName;
}

/**
 * Rich chart message — driven by the backend `render_chart` tool. Spec is
 * stored verbatim from the tool result so the renderer (Chart.tsx) can
 * dispatch on `type`. Distinct from `AssistantChartMessage` because that
 * one carries the legacy local-mock {label, valueCents} shape; merging
 * them would force the local mock to learn the richer spec for no v1
 * benefit. Both render via the same `MessageRow` switch.
 */
export interface ChartSeriesSpec {
  name: string;
  data: number[];
}

export interface ChartSpec {
  type: "line" | "bar" | "stacked_bar" | "donut";
  x: string[];
  series: ChartSeriesSpec[];
  y_label?: string;
  title: string;
}

export interface AssistantRichChartMessage {
  id: string;
  role: "assistant";
  kind: "rich-chart";
  preface?: string;
  spec: ChartSpec;
  via?: ToolName;
}

/**
 * Entry-moment insight bubble — Day 13. Rendered below a committed
 * parse card when the `insight` field on `POST /transactions/confirm`'s
 * response is non-null. Distinct kind (not reusing `text`) so the
 * renderer can give it a quieter visual treatment without bleeding
 * styles into ordinary assistant text.
 */
export interface AssistantInsightMessage {
  id: string;
  role: "assistant";
  kind: "insight";
  text: string;
}

/**
 * Card parse-card message — Day 14b. The chat-side analog of the
 * onboarding `AddCardStep` preview tile, rendered when the agent calls
 * `propose_card`. The proposal payload from the tool is wider than the
 * commit body (lookup metadata + issuer/network defaults), so this draft
 * carries the *editable* slice the user can tweak before tapping confirm.
 * `lastFour` is collected on the card itself (the agent never asks for it
 * up-front to avoid friction) and the confirm button stays disabled until
 * it parses as exactly 4 digits. `needsManual=true` from the backend
 * widens the visible-fields set so the user can fill issuer/network/etc.
 * when the web_search lookup couldn't determine them.
 */
export interface CardParseDraft {
  name: string;
  issuer: CardIssuer | null;
  network: CardNetwork | null;
  program: CardProgram;
  multipliers: Record<string, number>;
  annualFee: string | null;
  sourceUrls: string[];
  lastFour: string;
  needsManual: boolean;
  /** Optional alias the user supplied via chat ("travel card"). */
  alias?: string | null;
  /**
   * Stable per-proposal join key from `propose_card`. Posted back at
   * `/cards/confirm`; persists on the row. Drives the chat-rehydrate
   * annotation's 1:1 join and the offline-queue drain's in-memory
   * match priority (crid → messageId → name). Always present for
   * fresh proposals after Day 15; optional for legacy rehydrated
   * drafts that predate the column.
   */
  clientRequestId?: string;
}

export interface AssistantCardParseMessage {
  id: string;
  role: "assistant";
  kind: "card-parse";
  preface?: string;
  draft: CardParseDraft;
  /** Set after the user successfully commits this draft. */
  committedCardId?: string;
  /**
   * Lifecycle of the committed card row, sourced from the rehydrate
   * annotation. `'active'` means the card is still in the wallet;
   * `'deleted'` means the user closed it after adding. Drives the badge
   * text on `CardParseCard` (`added.` vs `deleted.`). Same shape as
   * `AssistantParseMessage.committedState` for transactions.
   */
  committedState?: "active" | "deleted";
  /**
   * `true` for rehydrated cards (read-only history); `false`/absent for
   * fresh in-session cards. Mirrors `AssistantParseMessage.frozen`.
   */
  frozen?: boolean;
  /**
   * `true` between an offline "looks right" tap (queued in
   * `offline_queue.ts`) and the drain's terminal outcome. Mirrors
   * `AssistantParseMessage.pendingSync`.
   */
  pendingSync?: boolean;
}

export type ChatMessage =
  | UserMessage
  | AssistantTextMessage
  | AssistantParseMessage
  | AssistantCandidatesMessage
  | AssistantChartMessage
  | AssistantRichChartMessage
  | AssistantInsightMessage
  | AssistantCardParseMessage;

/* ─── Parse draft (the commit surface) ───────────────────────────── */

export interface ParseDraft {
  merchant: string;
  amountCents: number;
  date: string; // YYYY-MM-DD
  cardId: string;
  category: Category;
  /** Per-field confidence on 0..1 — drives "check this one" pencils. */
  confidence: {
    merchant: number;
    amount: number;
    date: number;
    card: number;
    category: number;
  };
  /*
   * Wire-payload fields, only set on drafts that came from the backend
   * propose_transaction tool. The local heuristic parser leaves these
   * undefined. They're carried through to POST /transactions/confirm so
   * client_request_id idempotency works and Gemini's category baseline
   * tracks correction-vs-accept on the server (DESIGN.md §8.4).
   */
  clientRequestId?: string;
  notes?: string | null;
  geminiSuggestion?: string | null;
}

/* ─── Heuristic parser ───────────────────────────────────────────── */

/** Pulls the first plausible "$12.34" / "12" / "$5.50" out of the text. */
function extractAmount(input: string): { cents: number; confidence: number } | null {
  const m = input.match(/\$?\s*(\d+(?:\.\d{1,2})?)/);
  if (!m) return null;
  const value = parseFloat(m[1]);
  if (!Number.isFinite(value) || value <= 0) return null;
  // Higher confidence when a $ is present or a decimal is included.
  const hasDollar = /\$/.test(input);
  const hasDecimal = m[1].includes(".");
  const confidence = hasDollar || hasDecimal ? 0.95 : 0.7;
  return { cents: Math.round(value * 100), confidence };
}

const STOP_WORDS = new Set([
  "i",
  "just",
  "spent",
  "bought",
  "got",
  "paid",
  "for",
  "at",
  "from",
  "on",
  "the",
  "a",
  "an",
  "with",
  "today",
  "yesterday",
  "this",
  "that",
  "lunch",
  "dinner",
  "breakfast",
  "coffee",
  "snack",
]);

function extractMerchant(input: string): { value: string; confidence: number } {
  // Strip the amount fragment first.
  const stripped = input.replace(/\$?\s*\d+(?:\.\d{1,2})?/g, " ");
  // Prefer a sequence of TitleCase or quoted words.
  const titleCase = stripped.match(/[A-Z][a-zA-Z'’&]+(?:\s+[A-Z][a-zA-Z'’&]+){0,3}/);
  if (titleCase) {
    return { value: titleCase[0].trim(), confidence: 0.9 };
  }
  // Fall back to first non-stopword token.
  const tokens = stripped
    .toLowerCase()
    .split(/[^a-z0-9'’&]+/)
    .filter((t) => t && !STOP_WORDS.has(t));
  if (tokens.length === 0) {
    return { value: "Unknown merchant", confidence: 0.4 };
  }
  // Capitalize for display.
  const guess = tokens
    .slice(0, 2)
    .map((t) => t.charAt(0).toUpperCase() + t.slice(1))
    .join(" ");
  return { value: guess, confidence: 0.55 };
}

function inferCategory(input: string, merchant: string): { value: Category; confidence: number } {
  const hay = `${input} ${merchant}`.toLowerCase();
  const rules: Array<[Category, RegExp, number]> = [
    ["Coffee Shops", /\b(coffee|cafe|café|starbucks|blue bottle|roji)\b/, 0.92],
    ["Dining", /\b(restaurant|lunch|dinner|breakfast|deli|pizza|sushi|ramen|bar|brunch|lupa|misi|wayan|le crocodile)\b/, 0.9],
    ["Groceries", /\b(grocery|groceries|whole foods|trader joe|market|sahadi|greenmarket|supermarket)\b/, 0.92],
    ["Gas", /\b(shell|chevron|bp|exxon|mobil|gas station|gas bill)\b/, 0.92],
    ["Transit", /\b(uber|lyft|taxi|cab|mta|omny|subway|bus|revel|toll|parking)\b/, 0.92],
    ["Travel", /\b(amtrak|delta|united|jetblue|airbnb|hotel|airline|flight)\b/, 0.9],
    ["Streaming", /\b(netflix|spotify|hulu|apple music|youtube premium|disney\+)\b/, 0.95],
    ["Subscriptions", /\b(nyt|icloud|patreon|substack|gym|class pass)\b/, 0.9],
    ["Entertainment", /\b(metrograph|movie|cinema|concert|brooklyn steel|theater|theatre)\b/, 0.85],
    ["Shopping", /\b(uniqlo|amazon|etsy|store|shop|mcnally)\b/, 0.7],
    ["Drugstores", /\b(cvs|walgreens|rite aid|drugstore|pharmacy)\b/, 0.92],
    ["Home", /\b(home depot|ikea|lowes|furniture)\b/, 0.88],
    ["Utilities", /\b(con edison|verizon|electric|internet|water bill)\b/, 0.9],
    ["Health", /\b(doctor|dentist|vet|clinic|therapy|copay)\b/, 0.85],
  ];
  for (const [cat, re, conf] of rules) {
    if (re.test(hay)) return { value: cat, confidence: conf };
  }
  return { value: "Other", confidence: 0.45 };
}

function inferDate(input: string): { value: string; confidence: number } {
  const today = new Date();
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  if (/\byesterday\b/i.test(input)) {
    const d = new Date(today);
    d.setDate(d.getDate() - 1);
    return { value: iso(d), confidence: 0.95 };
  }
  if (/\btoday\b/i.test(input)) {
    return { value: iso(today), confidence: 0.95 };
  }
  // Default: today, lower confidence.
  return { value: iso(today), confidence: 0.7 };
}

function pickCard(category: Category): { id: string; confidence: number } {
  // When no real cards exist (v1 default), every proposal defaults to "Other".
  // The category affinity below is kept for the post-v1 case when FIXTURE_CARDS
  // (or a future /cards feed) is populated; non-UUID slugs still get sanitized
  // to null at the wire boundary (transactionsApi.ts:72), so the affinity is
  // purely a UI nicety.
  if (FIXTURE_CARDS.length === 0) {
    return { id: "", confidence: 0.6 };
  }
  if (category === "Dining" || category === "Coffee Shops" || category === "Health") {
    return { id: "card-amex", confidence: 0.6 };
  }
  if (
    category === "Groceries" ||
    category === "Subscriptions" ||
    category === "Streaming" ||
    category === "Utilities" ||
    category === "Drugstores"
  ) {
    return { id: "card-citi", confidence: 0.6 };
  }
  return { id: "card-csp", confidence: 0.55 };
}

/** True if the input clearly looks like "log this transaction". */
export function looksLikeTransaction(input: string): boolean {
  const hasAmount = /\$?\s*\d+(?:\.\d{1,2})?/.test(input);
  const isQuestion = /\?$/.test(input.trim()) || /^(how|what|where|when|why|did|do|does|show|find|edit|delete|change|fix)\b/i.test(input.trim());
  return hasAmount && !isQuestion;
}

export function parseTransaction(input: string): ParseDraft | null {
  const amt = extractAmount(input);
  if (!amt) return null;
  const merchant = extractMerchant(input);
  const category = inferCategory(input, merchant.value);
  const date = inferDate(input);
  const card = pickCard(category.value);
  return {
    merchant: merchant.value,
    amountCents: amt.cents,
    date: date.value,
    cardId: card.id,
    category: category.value,
    confidence: {
      merchant: merchant.confidence,
      amount: amt.confidence,
      date: date.confidence,
      card: card.confidence,
      category: category.confidence,
    },
  };
}

/* ─── Candidate finder ───────────────────────────────────────────── */

const FIND_INTENT_RE = /\b(edit|change|fix|update|delete|remove|find|show|that)\b/i;

export interface FindIntent {
  intent: "edit" | "delete";
  /** Free-text needle, e.g. "lupa" or "coffee" */
  query: string | null;
}

export function detectFindIntent(input: string): FindIntent | null {
  if (!FIND_INTENT_RE.test(input)) return null;
  const intent = /\b(delete|remove)\b/i.test(input) ? "delete" : "edit";
  // Crude needle: longest non-stopword token after stripping intent words.
  const cleaned = input
    .toLowerCase()
    .replace(/\b(edit|change|fix|update|delete|remove|find|show|that|the|a|an|my|last|recent|please)\b/g, " ");
  const tokens = cleaned.split(/[^a-z0-9'’]+/).filter((t) => t.length > 2);
  return {
    intent,
    query: tokens.length > 0 ? tokens.sort((a, b) => b.length - a.length)[0] : null,
  };
}

export function findCandidates(
  transactions: Transaction[],
  query: string | null,
  limit = 8
): Transaction[] {
  const sorted = [...transactions].sort((a, b) => b.date.localeCompare(a.date));
  if (!query) return sorted.slice(0, limit);
  const q = query.toLowerCase();
  const matches = sorted.filter((t) => {
    if (t.merchant.toLowerCase().includes(q)) return true;
    if (t.category.toLowerCase().includes(q)) return true;
    return false;
  });
  return (matches.length > 0 ? matches : sorted).slice(0, limit);
}

/* ─── Comparison detector ────────────────────────────────────────── */

export interface CompareIntent {
  a: Category;
  b: Category;
}

export function detectCompareIntent(input: string): CompareIntent | null {
  const lower = input.toLowerCase();
  if (!/\b(vs|versus|compare|compared to|against)\b/.test(lower)) return null;
  const found: Category[] = [];
  for (const cat of CATEGORIES) {
    if (lower.includes(cat.toLowerCase())) found.push(cat);
    if (found.length === 2) break;
  }
  if (found.length === 2) {
    return { a: found[0], b: found[1] };
  }
  return null;
}

export function compareCategories(
  transactions: Transaction[],
  intent: CompareIntent
): { a: { label: Category; cents: number }; b: { label: Category; cents: number } } {
  const sum = (cat: Category) =>
    transactions
      .filter((t) => t.category === cat)
      .reduce((s, t) => s + t.amountCents, 0);
  return {
    a: { label: intent.a, cents: sum(intent.a) },
    b: { label: intent.b, cents: sum(intent.b) },
  };
}

/* ─── Card name lookup helper ────────────────────────────────────── */

export function cardLabel(cardId: string): { name: string; last4: string } {
  const card = FIXTURE_CARDS.find((c) => c.id === cardId);
  if (!card) return { name: "Other", last4: "—" };
  return { name: card.name, last4: card.last4 };
}

/* ─── Daily-cap dev toggle (sessionStorage) ──────────────────────── */

const CAP_KEY = "tameru-chat-daily-cap";

export function isDailyCapEngaged(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.sessionStorage.getItem(CAP_KEY) === "1";
  } catch {
    return false;
  }
}

export function setDailyCapEngaged(engaged: boolean) {
  if (typeof window === "undefined") return;
  try {
    if (engaged) window.sessionStorage.setItem(CAP_KEY, "1");
    else window.sessionStorage.removeItem(CAP_KEY);
  } catch {
    // ignore
  }
}

/* ─── ID helper ──────────────────────────────────────────────────── */

export function newId(prefix = "msg"): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}
