"use client";

import {
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  LineChart,
  Line,
} from "recharts";
import type { MonthlyData } from "@/types";

interface MonthlyChartProps {
  data?: MonthlyData[] | Record<string, MonthlyData>;
  height?: number;
}

export function MonthlyProfitChart({ data, height = 260 }: MonthlyChartProps) {
  const normalizedData = Array.isArray(data)
    ? data
    : data && typeof data === "object"
    ? Object.values(data)
    : [];

  if (normalizedData.length === 0) {
    return <EmptyChart message="No monthly data" height={height} />;
  }

  const formatted = normalizedData.map((d) => ({
    month: d._month || d.month || "",
    profit: d.profit,
    trades: d.trades,
    win_rate: d.win_rate,
  }));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={formatted} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
        <XAxis
          dataKey="month"
          tick={{ fill: "#6b7280", fontSize: 11 }}
          tickFormatter={(v) => v.replace(/^\d{4}-/, "")}
        />
        <YAxis tick={{ fill: "#6b7280", fontSize: 11 }} tickFormatter={(v) => `$${v}`} />
        <Tooltip
          contentStyle={{ backgroundColor: "#111827", border: "1px solid #374151", borderRadius: 8 }}
          labelStyle={{ color: "#9ca3af", fontSize: 12 }}
          formatter={(value: number) => [`$${value.toFixed(2)}`, "Profit"]}
        />
        <ReferenceLine y={0} stroke="#374151" />
        <Bar dataKey="profit" radius={[3, 3, 0, 0]} label={false}>
          {formatted.map((entry, idx) => (
            <Cell key={idx} fill={entry.profit >= 0 ? "#22c55e" : "#ef4444"} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

interface SessionChartProps {
  data?: Record<string, { profit: number; win_rate: number; trades: number }>;
  height?: number;
}

export function SessionChart({ data, height = 200 }: SessionChartProps) {
  if (!data || Object.keys(data).length === 0) {
    return <EmptyChart message="No session data" height={height} />;
  }
  const formatted = Object.entries(data).map(([session, d]) => ({
    session: session.charAt(0).toUpperCase() + session.slice(1),
    profit: d.profit,
    win_rate: d.win_rate,
    trades: d.trades,
  }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={formatted} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
        <XAxis dataKey="session" tick={{ fill: "#6b7280", fontSize: 11 }} />
        <YAxis tick={{ fill: "#6b7280", fontSize: 11 }} tickFormatter={(v) => `${v}%`} />
        <Tooltip
          contentStyle={{ backgroundColor: "#111827", border: "1px solid #374151", borderRadius: 8 }}
          labelStyle={{ color: "#9ca3af", fontSize: 12 }}
          formatter={(v: number, n: string) => [n === "win_rate" ? `${v.toFixed(1)}%` : `$${v.toFixed(2)}`, n]}
        />
        <Bar dataKey="win_rate" name="Win Rate %" fill="#3b82f6" radius={[3, 3, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

interface EquityCurveProps {
  data?: { date: string; equity: number }[];
  height?: number;
}

export function EquityCurve({ data, height = 240 }: EquityCurveProps) {
  if (!data || data.length === 0) {
    return <EmptyChart message="No equity data" height={height} />;
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
        <XAxis dataKey="date" tick={{ fill: "#6b7280", fontSize: 10 }} />
        <YAxis tick={{ fill: "#6b7280", fontSize: 11 }} tickFormatter={(v) => `$${v}`} />
        <Tooltip
          contentStyle={{ backgroundColor: "#111827", border: "1px solid #374151", borderRadius: 8 }}
          formatter={(v: number) => [`$${v.toFixed(2)}`, "Equity"]}
        />
        <Line type="monotone" dataKey="equity" stroke="#4f63d2" strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

function EmptyChart({ message, height }: { message: string; height: number }) {
  return (
    <div
      className="flex items-center justify-center bg-gray-900/50 rounded-lg border border-gray-800"
      style={{ height }}
    >
      <span className="text-sm text-gray-600">{message}</span>
    </div>
  );
}
