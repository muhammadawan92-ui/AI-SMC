import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { ReadinessLevel, Verdict } from "@/types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmt(val: number | null | undefined, decimals = 2): string {
  if (val == null) return "N/A";
  return val.toFixed(decimals);
}

export function fmtUsd(val: number | null | undefined, decimals = 2): string {
  if (val == null) return "N/A";
  const sign = val >= 0 ? "+" : "";
  return `${sign}$${Math.abs(val).toFixed(decimals)}`;
}

export function fmtPct(val: number | null | undefined, decimals = 1): string {
  if (val == null) return "N/A";
  return `${val.toFixed(decimals)}%`;
}

export function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function readinessLabel(level?: ReadinessLevel | null): string {
  const map: Record<ReadinessLevel, string> = {
    research: "Research",
    demo_candidate: "Demo Candidate",
    demo_testing: "Demo Testing",
    live_candidate: "Live Candidate",
    live_ready: "Live Ready",
  };
  return level ? (map[level] ?? level) : "N/A";
}

export function readinessColor(level?: ReadinessLevel | null): string {
  const map: Record<ReadinessLevel, string> = {
    research: "text-gray-400 bg-gray-800",
    demo_candidate: "text-yellow-400 bg-yellow-900/30",
    demo_testing: "text-blue-400 bg-blue-900/30",
    live_candidate: "text-emerald-400 bg-emerald-900/30",
    live_ready: "text-green-400 bg-green-900/30",
  };
  return level ? (map[level] ?? "text-gray-400 bg-gray-800") : "text-gray-400 bg-gray-800";
}

export function verdictColor(verdict?: Verdict | null): string {
  if (!verdict) return "text-gray-400";
  const map: Record<Verdict, string> = {
    improvement: "text-green-400",
    regression: "text-red-400",
    neutral: "text-yellow-400",
    overfit: "text-orange-400",
  };
  return map[verdict];
}

export function overfitColor(risk?: string): string {
  if (risk === "low") return "text-green-400 bg-green-900/30";
  if (risk === "high") return "text-red-400 bg-red-900/30";
  return "text-yellow-400 bg-yellow-900/30";
}

export function profitColor(val?: number | null): string {
  if (val == null) return "text-gray-400";
  return val >= 0 ? "text-green-400" : "text-red-400";
}

export function confidenceColor(score: number): string {
  if (score >= 85) return "text-green-400";
  if (score >= 70) return "text-blue-400";
  if (score >= 50) return "text-yellow-400";
  return "text-red-400";
}

export function truncate(str: string, maxLen = 100): string {
  if (str.length <= maxLen) return str;
  return str.slice(0, maxLen) + "…";
}
