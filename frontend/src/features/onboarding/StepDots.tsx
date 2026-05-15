interface StepDotsProps {
  current: number;
  total: number;
  label?: string;
}

export function StepDots({ current, total, label }: StepDotsProps) {
  return (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-1.5">
        {Array.from({ length: total }, (_, i) => {
          const isActive = i + 1 === current;
          const isPast = i + 1 < current;
          return (
            <span
              key={i}
              className={
                isActive
                  ? "h-1.5 w-6 rounded-full bg-moss"
                  : isPast
                  ? "h-1.5 w-1.5 rounded-full bg-moss-soft"
                  : "h-1.5 w-1.5 rounded-full bg-ink-quaternary/40"
              }
            />
          );
        })}
      </div>
      {label && (
        <span className="text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
          {label}
        </span>
      )}
    </div>
  );
}
