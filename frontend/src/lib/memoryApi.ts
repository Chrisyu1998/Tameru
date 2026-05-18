import { apiJson } from "./api";

/*
 * Day 16 — typed client for /memory/* endpoints.
 *
 * Mirrors app/routes/memory.py exactly. The categories below match the
 * `user_memory_category_check` constraint in
 * supabase/migrations/20260421120500_user_memory.sql; changing one
 * requires changing the other (and the distillation system prompt in
 * app/agent/memory.py) in the same commit.
 */

export type MemoryCategory =
  | "spending_pattern"
  | "preference"
  | "active_context"
  | "card_preference"
  | "goal";

// Friendly labels for chips/pills. Keep the wire enum snake_case (matches
// DB + Pydantic); the label is what the user actually reads.
export const MEMORY_CATEGORY_LABELS: Record<MemoryCategory, string> = {
  spending_pattern: "spending pattern",
  preference: "preference",
  active_context: "active context",
  card_preference: "card preference",
  goal: "goal",
};

export interface MemoryFactRow {
  id: string;
  fact: string;
  category: MemoryCategory;
  relevance_score: number;
  reinforced_at: string;
  created_at: string;
}

export interface MemoryListResponse {
  facts: MemoryFactRow[];
  capacity: number;
}

export async function listMemory(): Promise<MemoryListResponse> {
  return apiJson<MemoryListResponse>("/memory");
}

export async function patchMemory(
  id: string,
  patch: { fact?: string; relevance_score?: number },
): Promise<MemoryFactRow> {
  return apiJson<MemoryFactRow>(`/memory/${id}`, {
    method: "PATCH",
    body: patch,
  });
}

export async function deleteMemory(id: string): Promise<void> {
  await apiJson<null>(`/memory/${id}`, { method: "DELETE" });
}
