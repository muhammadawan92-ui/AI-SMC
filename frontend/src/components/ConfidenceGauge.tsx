"use client";

import { cn, readinessLabel, readinessColor } from "@/lib/utils";
import type { ReadinessLevel } from "@/types";

interface ConfidenceGaugeProps {
  score: number;
  readiness?: ReadinessLevel;
  size?: "sm" | "md" | "lg";
  showLabel?: boolean;
  className?: string;
}

export function ConfidenceGauge({
  score,
  readiness,
  size = "md",
  showLabel = true,
  className,
}: ConfidenceGaugeProps) {
  const clampedScore = Math.max(0, Math.min(100, score));
  const strokeWidth = size === "sm" ? 6 : size === "lg" ? 10 : 8;
  const radius = size === "sm" ? 30 : size === "lg" ? 60 : 45;
  const svgSize = (radius + strokeWidth) * 2;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference - (clampedScore / 100) * circumference;

  const color =
    clampedScore >= 85
      ? "#22c55e"
      : clampedScore >= 70
      ? "#3b82f6"
      : clampedScore >= 50
      ? "#f59e0b"
      : "#ef4444";

  const fontSize = size === "sm" ? "text-lg" : size === "lg" ? "text-4xl" : "text-2xl";

  return (
    <div className={cn("flex flex-col items-center gap-2", className)}>
      <div className="relative" style={{ width: svgSize, height: svgSize }}>
        <svg
          width={svgSize}
          height={svgSize}
          style={{ transform: "rotate(-90deg)" }}
        >
          {/* Background circle */}
          <circle
            cx={svgSize / 2}
            cy={svgSize / 2}
            r={radius}
            fill="none"
            stroke="#1f2937"
            strokeWidth={strokeWidth}
          />
          {/* Progress circle */}
          <circle
            cx={svgSize / 2}
            cy={svgSize / 2}
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth={strokeWidth}
            strokeDasharray={circumference}
            strokeDashoffset={strokeDashoffset}
            strokeLinecap="round"
            style={{ transition: "stroke-dashoffset 0.6s ease" }}
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="text-center">
            <div className={cn("font-bold leading-none", fontSize)} style={{ color }}>
              {Math.round(clampedScore)}
            </div>
            <div className="text-xs text-gray-500">%</div>
          </div>
        </div>
      </div>
      {showLabel && readiness && (
        <span className={cn("badge text-xs", readinessColor(readiness))}>
          {readinessLabel(readiness)}
        </span>
      )}
    </div>
  );
}

interface ConfidenceBreakdownProps {
  breakdown?: Record<string, number>;
  className?: string;
}

export function ConfidenceBreakdown({ breakdown, className }: ConfidenceBreakdownProps) {
  if (!breakdown) return null;
  const labels: Record<string, string> = {
    improvement_over_baseline: "Improvement",
    drawdown_stability: "Drawdown Stability",
    profit_factor_stability: "Profit Factor",
    trade_count_score: "Trade Count",
    monthly_robustness: "Monthly Robustness",
    buy_sell_robustness: "Buy/Sell Balance",
    session_robustness: "Session Balance",
    parameter_sensitivity: "Param Sensitivity",
    overfit_penalty: "Overfit Check",
    smc_logic_consistency: "SMC Consistency",
    screenshot_validation: "Chart Validation",
  };

  return (
    <div className={cn("space-y-2", className)}>
      {Object.entries(breakdown).map(([key, score]) => (
        <div key={key}>
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>{labels[key] || key}</span>
            <span className={score >= 70 ? "text-green-400" : score >= 50 ? "text-yellow-400" : "text-red-400"}>
              {Math.round(score)}%
            </span>
          </div>
          <div className="w-full bg-gray-800 rounded-full h-1.5">
            <div
              className={cn(
                "h-1.5 rounded-full transition-all",
                score >= 70 ? "bg-green-500" : score >= 50 ? "bg-yellow-500" : "bg-red-500"
              )}
              style={{ width: `${score}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
