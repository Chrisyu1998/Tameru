/**
 * Rich chart renderer for the `render_chart` agent tool (Day 10b §6).
 *
 * The backend tool echoes a `ChartSpec` verbatim — data extraction lives
 * in `calculate_total` / `get_spending_summary`, so this component is
 * pure presentation. Dispatches on `spec.type`:
 *
 *   - line         → trends over time (one or more series)
 *   - bar          → category comparison (one or more series, grouped)
 *   - stacked_bar  → contribution to total (two or more series, stacked)
 *   - donut        → share-of-total (exactly one series; uses `x` as slice labels)
 *
 * Renders inside `<ResponsiveContainer>` so the SVG reflows for 375px
 * (mobile) up through the desktop drawer width without manual breakpoints.
 * Title sits above the chart in the same lowercase-serif voice the rest
 * of the app uses; legend appears only for multi-series charts.
 */

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { ChartSpec } from "@/lib/chat";

interface ChartProps {
  spec: ChartSpec;
}

// Palette pulled from the existing CSS tokens so the chart visually
// belongs to the app rather than recharts' default rainbow. Cycled per
// series index; donut slices use the same cycle on the single series.
const SERIES_COLORS = [
  "var(--moss)",
  "var(--over)",
  "var(--warn)",
  "var(--moss-deep)",
  "var(--ink-tertiary)",
];

const CHART_HEIGHT_PX = 220;

/**
 * Build the row-shape recharts expects: one object per x-axis label
 * carrying every series' value for that label. Recharts groups by key
 * across series, which is why we have to invert the {series:[{name,data}]}
 * shape the agent tool produced.
 */
function toRows(spec: ChartSpec): Array<Record<string, number | string>> {
  return spec.x.map((label, i) => {
    const row: Record<string, number | string> = { label };
    for (const series of spec.series) {
      row[series.name] = series.data[i] ?? 0;
    }
    return row;
  });
}

export function Chart({ spec }: ChartProps) {
  const multiSeries = spec.series.length > 1;
  return (
    <figure
      className="mt-3 w-full"
      data-testid="chart"
      data-chart-type={spec.type}
    >
      <figcaption className="mb-2 font-serif text-[0.95rem] text-ink lowercase-title">
        {spec.title}
      </figcaption>
      <div className="w-full" style={{ height: CHART_HEIGHT_PX }}>
        <ResponsiveContainer width="100%" height="100%">
          {renderChart(spec, multiSeries)}
        </ResponsiveContainer>
      </div>
    </figure>
  );
}

function renderChart(spec: ChartSpec, multiSeries: boolean) {
  if (spec.type === "donut") {
    // Donut: single series, slice per x label. Use `x` as the dimension
    // and the lone series' data as the values. Inner radius gives the
    // hole; outer radius leaves padding for labels.
    const series = spec.series[0];
    const data = spec.x.map((label, i) => ({
      name: label,
      value: series.data[i] ?? 0,
    }));
    return (
      <PieChart>
        <Pie
          data={data}
          dataKey="value"
          nameKey="name"
          innerRadius="55%"
          outerRadius="80%"
          stroke="var(--canvas)"
          strokeWidth={2}
        >
          {data.map((_, i) => (
            <Cell
              key={i}
              fill={SERIES_COLORS[i % SERIES_COLORS.length]}
            />
          ))}
        </Pie>
        <Tooltip />
        <Legend verticalAlign="bottom" height={24} />
      </PieChart>
    );
  }

  const rows = toRows(spec);

  if (spec.type === "line") {
    return (
      <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
        <CartesianGrid stroke="var(--hairline)" strokeDasharray="3 3" />
        <XAxis dataKey="label" stroke="var(--ink-tertiary)" fontSize={11} />
        <YAxis
          stroke="var(--ink-tertiary)"
          fontSize={11}
          label={
            spec.y_label
              ? {
                  value: spec.y_label,
                  angle: -90,
                  position: "insideLeft",
                  style: { fill: "var(--ink-tertiary)", fontSize: 11 },
                }
              : undefined
          }
        />
        <Tooltip />
        {multiSeries && <Legend verticalAlign="bottom" height={24} />}
        {spec.series.map((s, i) => (
          <Line
            key={s.name}
            type="monotone"
            dataKey={s.name}
            stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
            strokeWidth={2}
            dot={false}
          />
        ))}
      </LineChart>
    );
  }

  // bar / stacked_bar — same primitive, only stackId differs.
  const stackId = spec.type === "stacked_bar" ? "stack" : undefined;
  return (
    <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
      <CartesianGrid stroke="var(--hairline)" strokeDasharray="3 3" />
      <XAxis dataKey="label" stroke="var(--ink-tertiary)" fontSize={11} />
      <YAxis
        stroke="var(--ink-tertiary)"
        fontSize={11}
        label={
          spec.y_label
            ? {
                value: spec.y_label,
                angle: -90,
                position: "insideLeft",
                style: { fill: "var(--ink-tertiary)", fontSize: 11 },
              }
            : undefined
        }
      />
      <Tooltip />
      {multiSeries && <Legend verticalAlign="bottom" height={24} />}
      {spec.series.map((s, i) => (
        <Bar
          key={s.name}
          dataKey={s.name}
          stackId={stackId}
          fill={SERIES_COLORS[i % SERIES_COLORS.length]}
          radius={[4, 4, 0, 0]}
        />
      ))}
    </BarChart>
  );
}
