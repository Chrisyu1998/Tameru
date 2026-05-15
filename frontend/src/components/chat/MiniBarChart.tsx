import { formatMoney } from "@/lib/format";

interface MiniBarChartProps {
  bars: Array<{ label: string; valueCents: number }>;
}

/** Two-bar comparison chart rendered inside an AI bubble. */
export function MiniBarChart({ bars }: MiniBarChartProps) {
  const max = Math.max(...bars.map((b) => b.valueCents), 1);

  return (
    <div className="mt-3 flex items-end gap-4">
      {bars.map((b, i) => {
        const heightPct = Math.max((b.valueCents / max) * 100, 6);
        const isLeading = b.valueCents === max;
        return (
          <div key={i} className="flex flex-1 flex-col items-center gap-2">
            <span className="font-serif text-[0.85rem] tabular text-ink">
              {formatMoney(b.valueCents)}
            </span>
            <div className="relative flex h-24 w-full items-end justify-center">
              <div
                className="w-10 rounded-t-md transition-all"
                style={{
                  height: `${heightPct}%`,
                  backgroundColor: isLeading
                    ? "var(--moss)"
                    : "var(--moss-soft)",
                }}
              />
            </div>
            <span className="text-[0.72rem] lowercase tracking-wide text-ink-tertiary">
              {b.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}
