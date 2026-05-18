import { apiJson } from "./api";

/*
 * Typed client for /goals/* endpoints.
 *
 * Mirrors app/routes/goals.py exactly. The closed `GoalPeriod` enum below
 * matches the Postgres CHECK constraint in
 * supabase/migrations/[goals migration].sql; changing one requires
 * changing the other plus the `set_goal` tool's enum in app/agent/tools.py
 * in the same commit.
 *
 * Goal *creation* lives in chat via the `set_goal` agent tool (CLAUDE.md
 * invariant #8 carve-out). This client exposes only read / edit / delete.
 */

export type GoalPeriod = "week" | "month" | "year";

export const GOAL_PERIOD_LABELS: Record<GoalPeriod, string> = {
  week: "weekly",
  month: "monthly",
  year: "yearly",
};

export const GOAL_OVERALL_LABEL = "all spending";

export interface Goal {
  id: string;
  user_id: string;
  category: string | null;
  amount: string;
  period: GoalPeriod;
  created_at: string;
  updated_at: string;
}

export interface GoalWithSpend {
  goal: Goal;
  spent_period_to_date: string;
  window_start: string;
  window_end: string;
  progress_ratio: number;
}

export interface GoalsListResponse {
  items: GoalWithSpend[];
}

export interface GoalPatch {
  amount?: string;
  period?: GoalPeriod;
}

export async function listGoals(): Promise<GoalsListResponse> {
  return apiJson<GoalsListResponse>("/goals");
}

export async function patchGoal(id: string, patch: GoalPatch): Promise<Goal> {
  return apiJson<Goal>(`/goals/${id}`, {
    method: "PATCH",
    body: patch,
  });
}

export async function deleteGoal(id: string): Promise<void> {
  await apiJson<null>(`/goals/${id}`, { method: "DELETE" });
}
