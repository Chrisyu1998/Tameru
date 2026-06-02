import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";
import { DeltaTile } from "@/components/DeltaTile";
import type {
  CategoryTileWire,
  DashboardSummaryWire,
} from "@/lib/dashboardApi";
import { formatMoney, formatMonth, formatPercent } from "@/lib/format";
import { useCategoryLabel } from "@/lib/categories";

interface DashboardProps {
  data: DashboardSummaryWire;
  /**
   * When true, suppress in-app navigation (used by the guided tour, which
   * renders this component on a non-app route and shouldn't expose a
   * breakdown link that drops the user into the real product). Defaults
   * to false — the live `/` route renders the breakdown link.
   */
  inert?: boolean;
}

/**
 * Pure presentational dashboard. No data fetching, no hooks, no app
 * state — everything flows in via `data`. The live route at `/`
 * resolves data through `useDashboardSummary()` in `pages/home.tsx`;
 * the guided tour passes `tourFixtures.dashboard` from `fixtures/tour.ts`.
 *
 * Day 21 extracted this from `pages/home.tsx` so the tour can render the
 * real component with fixture data rather than maintain a parallel mock.
 */
export function Dashboard({ data, inert = false }: DashboardProps) {
  const monthCents = Math.round(Number(data.this_month) * 100);
  const deltaPct = data.delta_pct ?? 0;
  const tiles = data.categories.slice(0, 4);

  return (
    <>
      <header className="flex items-center justify-between">
        <h1 className="font-serif text-3xl text-ink lowercase-title">home</h1>
        {inert ? (
          <span className="inline-flex items-center gap-1 text-sm text-moss">
            <ArrowUpRight className="h-3.5 w-3.5" />
            <span>Breakdown</span>
          </span>
        ) : (
          <Link
            to="/breakdown"
            className="inline-flex items-center gap-1 text-sm text-moss hover:text-moss-deep transition-colors"
          >
            <ArrowUpRight className="h-3.5 w-3.5" />
            <span>Breakdown</span>
          </Link>
        )}
      </header>

      <section className="mt-12">
        <p className="text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
          {formatMonth().toLowerCase()}
        </p>
        <p className="mt-3 font-serif text-[4rem] leading-none text-ink tabular">
          {formatMoney(monthCents)}
        </p>

        {data.baseline_ready && data.delta_pct !== null && (
          <div className="mt-5 inline-flex items-center gap-2 rounded-full bg-warn-wash px-3 py-1 text-xs text-warn">
            <span className="tabular font-medium">
              {formatPercent(deltaPct)} vs your avg
            </span>
          </div>
        )}

        {data.observation && (
          <p className="mt-6 max-w-[28ch] font-serif italic text-[0.95rem] leading-relaxed text-ink-secondary">
            {data.observation}
          </p>
        )}
      </section>

      <section className="mt-12 grid grid-cols-2 gap-3">
        {tiles.map((tile) => (
          <DashboardTile key={tile.name} tile={tile} />
        ))}
      </section>
    </>
  );
}

/** Per-category tile. Falls back to the "still learning" empty state when
 * the user has < 3 months of history for the category. */
function DashboardTile({ tile }: { tile: CategoryTileWire }) {
  // `tile.name` is the canonical English enum; localize the *display* label
  // (DESIGN.md §6.6 Tier 2). DeltaTile lowercases via CSS, so a localized
  // CJK label is unaffected and English stays lowercased as before.
  const catLabel = useCategoryLabel();
  if (!tile.baseline_ready || tile.delta_abs === null) {
    return (
      <DeltaTile
        layout="stacked"
        tone="neutral"
        direction="neutral"
        category={catLabel(tile.name).toLowerCase()}
        delta={null}
        band="still learning"
      />
    );
  }
  const deltaDollars = Math.round(Number(tile.delta_abs));
  return (
    <DeltaTile
      layout="stacked"
      tone="neutral"
      category={catLabel(tile.name).toLowerCase()}
      delta={deltaDollars}
    />
  );
}
