/**
 * In-memory AI fact store. Mock — no backend yet.
 * Capacity is fixed at 60 facts; UI surfaces an amber warning >80% full.
 */

export type MemoryCategory =
  | "card preference"
  | "goal"
  | "spending pattern"
  | "person"
  | "place"
  | "rule";

export interface MemoryFact {
  id: string;
  category: MemoryCategory;
  text: string;
  /** Where this fact came from, e.g. "saved apr 12 from chat". */
  provenance: string;
}

export const MEMORY_CAPACITY = 60;

// v1 has no /memory backend yet; the AI memory page renders an empty state
// until a memory feed lands.
export const seedMemoryFacts: MemoryFact[] = [];

export const initialMemoryFacts: MemoryFact[] = seedMemoryFacts;
