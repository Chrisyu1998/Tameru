import { cn } from "@/lib/utils";

type CardVariant = "surface" | "elevated" | "sunken";

interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: CardVariant;
}

const variantClasses: Record<CardVariant, string> = {
  surface: "bg-surface",
  elevated: "bg-elevated",
  sunken: "bg-sunken",
};

export function Card({ variant = "surface", className, ...props }: CardProps) {
  return (
    <div
      className={cn(
        "rounded-3xl border border-hairline p-5",
        variantClasses[variant],
        className
      )}
      {...props}
    />
  );
}
