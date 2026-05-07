"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, CheckCircle2, AlertTriangle, TrendingUp, Scale } from "lucide-react";
import axios from "axios";

import { forwardValidationApi } from "@/lib/api";
import type { BacktestKnowledgeLatest } from "@/types";
import { cn, fmt, fmtPct } from "@/lib/utils";

type Verdict = "matching" | "weaker" | "stronger" | "unknown";

function detectVerdict(reportText: string): Verdict {
  const line = reportText
    .split("\n")
    .find((x) => x.toLowerCase().startsWith("verdict:"));
  if (!line) return "unknown";
  const value = line.split(":")[1]?.trim().toLowerCase() || "";
  if (value.includes("matching")) return "matching";
  if (value.includes("weaker")) return "weaker";
  if (value.includes("stronger")) return "stronger";
  return "unknown";
}

function parseGroupKey(groupKey: string): { decision: string; tradeMode: string; obTimeframe: string } {
  const parts = groupKey.split("|");
  let decision = "";
  let tradeMode = "";
  let obTimeframe = "";
  parts.forEach((p) => {
    if (p.startsWith("decision=")) decision = p.replace("decision=", "");
    if (p.startsWith("trade_mode=")) tradeMode = p.replace("trade_mode=", "");
    if (p.startsWith("ob_timeframe=")) obTimeframe = p.replace("ob_timeframe=", "");
  });
  return { decision, tradeMode, obTimeframe };
}

export default function ForwardValidationPage() {
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [knowledge, setKnowledge] = useState<BacktestKnowledgeLatest | null>(null);
  const [reportText, setReportText] = useState("");
  const [error, setError] = useState("");

  const toErrorMessage = (e: unknown, fallback: string): string => {
    if (axios.isAxiosError(e)) {
      const detail = (e.response?.data as { detail?: string } | undefined)?.detail;
      return detail || e.message || fallback;
    }
    if (e instanceof Error) {
      return e.message;
    }
    return fallback;
  };

  const loadLatest = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await forwardValidationApi.latest();
      setKnowledge((res.data?.knowledge || null) as BacktestKnowledgeLatest | null);
      setReportText(String(res.data?.latest_report_text || ""));
    } catch (e: unknown) {
      setError(toErrorMessage(e, "Failed to load forward validation data."));
    } finally {
      setLoading(false);
    }
  }, []);

  const runCompare = async () => {
    setRunning(true);
    setError("");
    try {
      await forwardValidationApi.runCompare({});
      await loadLatest();
    } catch (e: unknown) {
      setError(toErrorMessage(e, "Failed to run compare."));
    } finally {
      setRunning(false);
    }
  };

  useEffect(() => {
    loadLatest();
  }, [loadLatest]);

  const verdict = detectVerdict(reportText);

  const verdictStyle =
    verdict === "stronger"
      ? "bg-green-900/20 border-green-700/40 text-green-300"
      : verdict === "matching"
      ? "bg-blue-900/20 border-blue-700/40 text-blue-300"
      : verdict === "weaker"
      ? "bg-red-900/20 border-red-700/40 text-red-300"
      : "bg-gray-900 border-gray-800 text-gray-300";

  const groupRows = useMemo(() => {
    const groups = knowledge?.expected_by_group || {};
    return Object.entries(groups)
      .map(([key, val]) => ({ key, ...val }))
      .sort((a, b) => (b.total_trades || 0) - (a.total_trades || 0));
  }, [knowledge]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Forward Validation</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Compare backtest expectations with forward demo outcomes.
          </p>
        </div>
        <div className="flex gap-2">
          <button className="btn-secondary inline-flex items-center gap-2" onClick={loadLatest} disabled={loading}>
            <RefreshCw size={15} className={cn(loading && "animate-spin")} />
            Refresh
          </button>
          <button className="btn-primary inline-flex items-center gap-2" onClick={runCompare} disabled={running}>
            <Scale size={15} />
            {running ? "Running..." : "Run Comparison"}
          </button>
        </div>
      </div>

      <div className={cn("rounded-xl border px-4 py-3 text-sm", verdictStyle)}>
        <div className="flex items-center gap-2 font-medium">
          {verdict === "weaker" ? <AlertTriangle size={15} /> : <CheckCircle2 size={15} />}
          Current verdict: {verdict.toUpperCase()}
        </div>
      </div>

      {error && (
        <div className="rounded-xl border border-red-900/40 bg-red-900/10 px-4 py-3 text-sm text-red-300">{error}</div>
      )}

      {!knowledge ? (
        <div className="card text-center py-14 text-gray-500">
          No backtest knowledge found yet. Run one backtest, then click &quot;Run Comparison&quot;.
        </div>
      ) : (
        <>
          <div className="grid grid-cols-4 gap-4">
            <div className="card-sm">
              <div className="text-xs text-gray-500">Strategy Version</div>
              <div className="text-sm font-semibold text-white mt-1">{knowledge.strategy_version || "N/A"}</div>
            </div>
            <div className="card-sm">
              <div className="text-xs text-gray-500">Symbol</div>
              <div className="text-sm font-semibold text-white mt-1">{knowledge.symbol || "N/A"}</div>
            </div>
            <div className="card-sm">
              <div className="text-xs text-gray-500">Backtest Win Rate</div>
              <div className="text-sm font-semibold text-white mt-1">{fmtPct(knowledge.win_rate)}</div>
            </div>
            <div className="card-sm">
              <div className="text-xs text-gray-500">Backtest Profit Factor</div>
              <div className="text-sm font-semibold text-white mt-1">{fmt(knowledge.profit_factor)}</div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-6">
            <div className="card">
              <div className="section-title">Knowledge Summary</div>
              <div className="space-y-2 text-sm">
                <div className="text-gray-400">
                  Date range:{" "}
                  <span className="text-gray-200">
                    {knowledge.backtest_date_range?.start || "?"} → {knowledge.backtest_date_range?.end || "?"}
                  </span>
                </div>
                <div className="text-gray-400">
                  Total trades: <span className="text-gray-200">{knowledge.total_trades ?? 0}</span>
                </div>
                <div className="text-gray-400">
                  Max drawdown: <span className="text-gray-200">{knowledge.max_drawdown ?? 0}</span>
                </div>
                <div className="text-gray-400">
                  Final balance: <span className="text-gray-200">{knowledge.final_balance ?? 0}</span>
                </div>
                <div className="text-gray-400">
                  Best decision / OB TF:{" "}
                  <span className="text-green-300">
                    {knowledge.best_decision_type || "N/A"} / {knowledge.best_ob_timeframe || "N/A"}
                  </span>
                </div>
                <div className="text-gray-400">
                  Worst decision / OB TF:{" "}
                  <span className="text-red-300">
                    {knowledge.worst_decision_type || "N/A"} / {knowledge.worst_ob_timeframe || "N/A"}
                  </span>
                </div>
                <div className="text-gray-400">
                  Avg MFE R / MAE R:{" "}
                  <span className="text-gray-200">
                    {knowledge.average_mfe_r ?? 0} / {knowledge.average_mae_r ?? 0}
                  </span>
                </div>
              </div>
            </div>

            <div className="card">
              <div className="section-title">Forward Testing Recommendation</div>
              <div className="text-sm text-gray-300 leading-relaxed">
                {knowledge.recommendation_for_forward_demo_testing || "No recommendation available."}
              </div>
            </div>
          </div>

          <div className="card">
            <div className="section-title flex items-center gap-2">
              <TrendingUp size={16} />
              Expected Performance by Group
            </div>
            {groupRows.length === 0 ? (
              <div className="text-sm text-gray-500">No grouped expected performance available yet.</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-800">
                      <th className="py-2 px-2 text-left text-gray-500">Decision</th>
                      <th className="py-2 px-2 text-left text-gray-500">Trade Mode</th>
                      <th className="py-2 px-2 text-left text-gray-500">OB TF</th>
                      <th className="py-2 px-2 text-left text-gray-500">Trades</th>
                      <th className="py-2 px-2 text-left text-gray-500">Win Rate</th>
                      <th className="py-2 px-2 text-left text-gray-500">Profit Factor</th>
                      <th className="py-2 px-2 text-left text-gray-500">Avg R</th>
                    </tr>
                  </thead>
                  <tbody>
                    {groupRows.slice(0, 20).map((r) => {
                      const parsed = parseGroupKey(r.key);
                      return (
                        <tr key={r.key} className="border-b border-gray-800/60">
                          <td className="py-2 px-2 text-gray-200">{parsed.decision || "N/A"}</td>
                          <td className="py-2 px-2 text-gray-300">{parsed.tradeMode || "N/A"}</td>
                          <td className="py-2 px-2 text-gray-300">{parsed.obTimeframe || "N/A"}</td>
                          <td className="py-2 px-2 text-gray-300">{r.total_trades ?? 0}</td>
                          <td className="py-2 px-2 text-gray-300">{fmtPct(r.win_rate)}</td>
                          <td className="py-2 px-2 text-gray-300">{fmt(r.profit_factor)}</td>
                          <td className="py-2 px-2 text-gray-300">{fmt(r.average_r)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

