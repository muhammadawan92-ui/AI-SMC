import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

interface MetricCardProps {
  label: string;
  value: string | number | ReactNode;
  subValue?: string;
  trend?: "up" | "down" | "neutral";
  className?: string;
  valueClassName?: string;
  icon?: ReactNode;
}

export function MetricCard({
  label,
  value,
  subValue,
  trend,
  className,
  valueClassName,
  icon,
}: MetricCardProps) {
  const trendColor =
    trend === "up" ? "text-green-400" : trend === "down" ? "text-red-400" : "text-gray-400";

  return (
    <div className={cn("card-sm", className)}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="metric-label">{label}</div>
          <div className={cn("metric-value mt-1", trendColor, valueClassName)}>
            {value}
          </div>
          {subValue && <div className="text-xs text-gray-500 mt-0.5">{subValue}</div>}
        </div>
        {icon && (
          <div className="text-gray-600 flex-shrink-0 mt-0.5">{icon}</div>
        )}
      </div>
    </div>
  );
}

export function MetricGrid({ children, cols = 4 }: { children: ReactNode; cols?: number }) {
  return (
    <div
      className={cn(
        "grid gap-4",
        cols === 2 && "grid-cols-2",
        cols === 3 && "grid-cols-3",
        cols === 4 && "grid-cols-2 lg:grid-cols-4",
        cols === 6 && "grid-cols-2 lg:grid-cols-3 xl:grid-cols-6"
      )}
    >
      {children}
    </div>
  );
}
