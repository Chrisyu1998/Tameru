import { CATEGORY_TINT, type Category } from "@/lib/categories";

interface DonutSlice {
  category: Category;
  cents: number;
}

interface DonutProps {
  slices: DonutSlice[];
  /** Inner content (typically the total). */
  children?: React.ReactNode;
  size?: number;
  thickness?: number;
}

export function Donut({
  slices,
  children,
  size = 220,
  thickness = 14,
}: DonutProps) {
  const total = slices.reduce((s, x) => s + x.cents, 0);
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;

  let cumulative = 0;

  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg
        viewBox={`0 0 ${size} ${size}`}
        width={size}
        height={size}
        className="-rotate-90"
        aria-hidden="true"
      >
        {/* Track */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--color-hairline)"
          strokeWidth={thickness}
        />

        {total > 0 &&
          slices.map((slice) => {
            const fraction = slice.cents / total;
            const dash = fraction * circumference;
            const gap = circumference - dash;
            const offset = -((cumulative / total) * circumference);
            cumulative += slice.cents;

            return (
              <circle
                key={slice.category}
                cx={size / 2}
                cy={size / 2}
                r={radius}
                fill="none"
                stroke={CATEGORY_TINT[slice.category]}
                strokeWidth={thickness}
                strokeDasharray={`${dash} ${gap}`}
                strokeDashoffset={offset}
                strokeLinecap="butt"
              />
            );
          })}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
        {children}
      </div>
    </div>
  );
}
