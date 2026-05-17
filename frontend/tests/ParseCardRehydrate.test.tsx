/**
 * ParseCard rehydrate — Day 15 `committed_payload` precedence.
 *
 * The persisted `tameru_proposal` block on `chat_messages` carries the
 * agent's *original* proposal (`input` + `result`). When the user edits
 * the parse card before tapping "looks right" — or edits the row later
 * via the edit sheet — the agent's suggestion no longer matches the
 * row's actual values. `_annotate_committed_proposals` (backend) stitches
 * a `committed_payload` field onto the block carrying the row's live
 * field values; `_proposalToDraft` / `_proposalToCardDraft` (frontend)
 * prefer it over `result` when present.
 *
 * Tests drive the private `_wireMessageToLocal` mapper directly via the
 * `_testing` export so we don't have to mock `getChatMessages` or
 * stand up a conversation id — the mapper is the load-bearing surface
 * here.
 */

import { describe, expect, test } from "vitest";
import { _testing } from "@/lib/chatStore";
import type { ChatMessageWire } from "@/lib/chatApi";
import type {
  AssistantParseMessage,
  AssistantCardParseMessage,
} from "@/lib/chat";

const CRID = "00000000-0000-0000-0000-000000000777";

function assistantMessage(
  blocks: ChatMessageWire["content_blocks"],
): ChatMessageWire {
  return {
    role: "assistant",
    content_blocks: blocks,
    created_at: "2026-05-13T00:00:00Z",
  };
}

describe("ParseCard rehydrate — transactions", () => {
  test("draft amount comes from committed_payload, not result", () => {
    // Agent proposed $40; user edited to $42 at confirm time; the row
    // exists at $42. The block's `result.amount` is still "40.00", and
    // `committed_payload.amount` is "42.00". The rehydrated card must
    // display $42 — without committed_payload precedence the user would
    // see "logged. $40" forever, drifting from the ledger row.
    const out = _testing.wireMessageToLocal(
      assistantMessage([
        {
          type: "tameru_proposal",
          tool_name: "propose_transaction",
          input: { merchant: "Lupa", amount: 40, date: "2026-05-13" },
          result: {
            merchant: "Lupa",
            amount: "40.00",
            date: "2026-05-13",
            category: "Dining",
            client_request_id: CRID,
            card_id: null,
            notes: null,
          },
          committed_id: "tx-row-1",
          committed_state: "active",
          committed_payload: {
            client_request_id: CRID,
            merchant: "Lupa",
            amount: "42.00",
            date: "2026-05-13",
            category: "Dining",
            card_id: null,
            notes: null,
          },
        },
      ]),
    );

    expect(out).toHaveLength(1);
    const parse = out[0] as AssistantParseMessage;
    expect(parse.kind).toBe("parse");
    expect(parse.draft.amountCents).toBe(4200); // committed, not 4000
    expect(parse.committedTxId).toBe("tx-row-1");
    expect(parse.committedState).toBe("active");
    expect(parse.frozen).toBe(true);
  });

  test("falls back to result when committed_payload is absent", () => {
    // Pre-Day-15 row (or uncommitted proposal): no `committed_payload`.
    // The rehydrated card displays the agent's original suggestion. The
    // "not saved." badge will appear because `committed_id` is also
    // missing.
    const out = _testing.wireMessageToLocal(
      assistantMessage([
        {
          type: "tameru_proposal",
          tool_name: "propose_transaction",
          input: { merchant: "Lupa", amount: 40 },
          result: {
            merchant: "Lupa",
            amount: "40.00",
            date: "2026-05-13",
            category: "Dining",
            client_request_id: CRID,
            card_id: null,
            notes: null,
          },
        },
      ]),
    );

    expect(out).toHaveLength(1);
    const parse = out[0] as AssistantParseMessage;
    expect(parse.kind).toBe("parse");
    expect(parse.draft.amountCents).toBe(4000); // proposal value
    expect(parse.committedTxId).toBeUndefined();
    expect(parse.frozen).toBe(true);
  });

  test("committed_payload merges OVER result — only overridden fields change", () => {
    // The merge isn't a wholesale replacement — `gemini_suggestion`
    // (proposal-only field, never written to the row) survives via the
    // spread fallback. This matters because the rehydrated draft needs
    // both the committed values (truth) AND the proposal-time
    // annotations the row doesn't carry.
    const out = _testing.wireMessageToLocal(
      assistantMessage([
        {
          type: "tameru_proposal",
          tool_name: "propose_transaction",
          input: {},
          result: {
            merchant: "Lupa",
            amount: "40.00",
            date: "2026-05-13",
            category: "Dining",
            client_request_id: CRID,
            card_id: null,
            notes: null,
            gemini_suggestion: "Dining",
          },
          committed_id: "tx-row-2",
          committed_state: "active",
          committed_payload: {
            client_request_id: CRID,
            merchant: "Lupa Trattoria", // edited merchant name
            amount: "42.50", // edited amount
            date: "2026-05-13",
            category: "Dining",
            card_id: null,
            notes: null,
            // no gemini_suggestion in committed_payload — comes from result
          },
        },
      ]),
    );

    const parse = out[0] as AssistantParseMessage;
    expect(parse.draft.merchant).toBe("Lupa Trattoria");
    expect(parse.draft.amountCents).toBe(4250);
    expect(parse.draft.geminiSuggestion).toBe("Dining");
  });
});

describe("CardParseCard rehydrate — cards", () => {
  test("draft uses committed_payload when present (filled-in last_four wins)", () => {
    const CARD_CRID = "00000000-0000-0000-0000-000000000aaa";
    const out = _testing.wireMessageToLocal(
      assistantMessage([
        {
          type: "tameru_proposal",
          tool_name: "propose_card",
          input: { name: "Amex Gold" },
          result: {
            name: "Amex Gold",
            issuer: "amex",
            network: "amex",
            program: "MR",
            multipliers: { Dining: 3 }, // proposal said 3x
            annual_fee: "250",
            source_urls: [],
            last_four: null, // proposal didn't have it
            needs_manual: false,
            alias: null,
            client_request_id: CARD_CRID,
          },
          committed_id: "card-row-1",
          committed_state: "active",
          committed_payload: {
            client_request_id: CARD_CRID,
            network: "amex",
            last_four: "1234", // user filled this in at confirm time
            name: "Amex Gold",
            issuer: "amex",
            program: "MR",
            multipliers: { Dining: 4 }, // adjusted post-confirm
            annual_fee: "250",
            source_urls: [],
            alias: null,
          },
        },
      ]),
    );

    expect(out).toHaveLength(1);
    const cardMsg = out[0] as AssistantCardParseMessage;
    expect(cardMsg.kind).toBe("card-parse");
    expect(cardMsg.draft.lastFour).toBe("1234"); // committed, not null
    expect(cardMsg.draft.multipliers.Dining).toBe(4); // committed, not 3
    expect(cardMsg.draft.clientRequestId).toBe(CARD_CRID); // rehydrate-stable join key
    expect(cardMsg.committedCardId).toBe("card-row-1");
    expect(cardMsg.frozen).toBe(true);
  });

  test("card falls back to result when committed_payload absent", () => {
    const CARD_CRID = "00000000-0000-0000-0000-000000000bbb";
    const out = _testing.wireMessageToLocal(
      assistantMessage([
        {
          type: "tameru_proposal",
          tool_name: "propose_card",
          input: {},
          result: {
            name: "Amex Gold",
            issuer: "amex",
            network: "amex",
            program: "MR",
            multipliers: { Dining: 3 },
            annual_fee: "250",
            source_urls: [],
            last_four: "9999",
            needs_manual: false,
            alias: null,
            client_request_id: CARD_CRID,
          },
        },
      ]),
    );

    const cardMsg = out[0] as AssistantCardParseMessage;
    expect(cardMsg.draft.lastFour).toBe("9999");
    expect(cardMsg.draft.multipliers.Dining).toBe(3);
    expect(cardMsg.draft.clientRequestId).toBe(CARD_CRID);
    expect(cardMsg.committedCardId).toBeUndefined();
  });
});
