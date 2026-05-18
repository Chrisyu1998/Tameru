/**
 * AI memory page — shared types + capacity constant.
 *
 * Real data is fetched via `lib/memoryApi.ts` (Day 16). This module
 * re-exports the wire-shape types from there and adds a UI-friendly
 * MemoryFact shape so the page can render with provenance copy
 * without referencing the API shape everywhere.
 *
 * Capacity is fixed at 60 facts (DESIGN.md §7.6). The UI surfaces an
 * amber warning >80% full; the backend's prune cron (Day 17) is what
 * actually enforces the cap.
 */

export type {
  MemoryCategory,
  MemoryFactRow,
  MemoryListResponse,
} from "./memoryApi";
export {
  MEMORY_CATEGORY_LABELS,
  listMemory,
  patchMemory,
  deleteMemory,
} from "./memoryApi";

import type { MemoryCategory } from "./memoryApi";

export interface MemoryFact {
  id: string;
  category: MemoryCategory;
  text: string;
  /** Human-readable provenance, e.g. "reinforced 3 days ago". */
  provenance: string;
}

export const MEMORY_CAPACITY = 60;
