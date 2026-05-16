/**
 * Dashboard summary fetch + hook — Day 13.
 *
 * `GET /dashboard/summary` returns the headline + tile data for UX
 * frame 8. The Lovable scaffold used hardcoded fixture baselines
 * (`CATEGORY_BASELINES`, `TOTAL_BASELINE`) and a TS-side observation
 * generator; Day 13 retires both in favor of this single round trip
 * (see day-13-dashboard-entry-insight.md).
 *
 * Numerics arrive as JSON strings from Pydantic Decimals. Translation
 * to whole-cents integers happens in the consumer (home.tsx) so this
 * module can stay agnostic to the cents-vs-dollars debate.
 */

import { useEffect, useMemo, useState } from "react";
import { apiJson } from "./api";
import { useAppStore } from "../store";
import { useLedger } from "./ledger";

export type TileColor = "green" | "neutral" | "amber" | "red";

export interface CategoryTileWire {
  name: string;
  this_month: string;
  baseline: string | null;
  delta_abs: string | null;
  delta_pct: number | null;
  color: TileColor;
  baseline_ready: boolean;
}

export interface DashboardSummaryWire {
  this_month: string;
  baseline: string | null;
  delta_pct: number | null;
  baseline_ready: boolean;
  observation: string | null;
  categories: CategoryTileWire[];
}

export async function fetchDashboardSummary(): Promise<DashboardSummaryWire> {
  return apiJson<DashboardSummaryWire>("/dashboard/summary");
}

export interface UseDashboardSummary {
  summary: DashboardSummaryWire | null;
  loading: boolean;
  error: Error | null;
}

/**
 * Subscribe to GET /dashboard/summary, refetching whenever the ledger's
 * dashboard-relevant fields change. Returns `{summary, loading, error}`
 * — null `summary` means "not fetched yet."
 *
 * Refetch trigger is a signature over the fields that move tile values:
 * amount, date, and category (plus id so deletes/adds are caught). An
 * earlier version keyed on `transactions.length`, which missed inline
 * edits — the edit sheet PATCHes a row without changing the count, so
 * the dashboard stayed stale until the next add/delete. Building the
 * signature is O(N) per render; at v1 transaction volumes (~100–500
 * rows/user) that's a few microseconds, well below any threshold worth
 * optimizing.
 */
export function useDashboardSummary(): UseDashboardSummary {
  const jwt = useAppStore((s) => s.jwt);
  const { transactions } = useLedger();
  // Stable across renders unless a dashboard-relevant field actually
  // changes. `useSyncExternalStore` returns the same `transactions`
  // reference until ledger state mutates, so the memo only recomputes
  // when there's real work to do.
  const ledgerSignature = useMemo(
    () =>
      transactions
        .map((t) => `${t.id}:${t.amountCents}:${t.date}:${t.category}`)
        .join("|"),
    [transactions],
  );
  const [summary, setSummary] = useState<DashboardSummaryWire | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!jwt) {
      setSummary(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    fetchDashboardSummary()
      .then((data) => {
        if (cancelled) return;
        setSummary(data);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err : new Error(String(err)));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [jwt, ledgerSignature]);

  return { summary, loading, error };
}
