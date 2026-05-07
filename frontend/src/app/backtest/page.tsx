"use client";

import { useEffect, useState } from "react";
import { BarChart3, AlertTriangle, RefreshCw } from "lucide-react";
import { MetricCard, MetricGrid } from "@/components/MetricCard";
import { MonthlyProfitChart, SessionChart } from "@/components/MonthlyChart";
import { analysisApi, projectsApi } from "@/lib/api";
import { fmt, fmtUsd, fmtPct, profitColor, cn } from "@/lib/utils";
import type { BacktestReport, Project } from "@/types";

export default function BacktestPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState("");
  const [reports, setReports] = useState<BacktestReport[]>([]);
  const [selected, setSelected] = useState<BacktestReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<"overview" | "monthly" | "sessions" | "failures" | "ai">("overview");

  useEffect(() => {
    projectsApi.list().then((r) => {
      setProjects(r.data);
      if (r.data.length > 0) setSelectedProject(r.data[0].id);
    });
  }, []);

  useEffect(() => {
    if (!selectedProject) return;
    setLoading(true);
    analysisApi.getBacktests(selectedProject)
      .then(async (r) => {
        setReports(r.data);
        const baseline = r.data.find((x: BacktestReport) => x.is_baseline);
        if (baseline) {
          const detail = await analysisApi.getBacktestDetail(baseline.id);
          setSelected(detail.data);
        }
      })
      .finally(() => setLoading(false));
  }, [selectedProject]);

  const selectReport = async (id: string) => {
    const detail = await analysisApi.getBacktestDetail(id);
    setSelected(detail.data);
  };

  const tabs = [
    { id: "overview", label: "Overview" },
    { id: "monthly", label: "Monthly" },
    { id: "sessions", label: "Sessions" },
    { id: "failures", label: "Failures" },
    { id: "ai", label: "AI Analysis" },
  ] as const;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Backtest Analyzer</h1>
          <p className="text-sm text-gray-500 mt-0.5">Analyze and compare backtest results</p>
        </div>
        <div className="flex items-center gap-3">
          <select className="select w-48" value={selectedProject} onChange={(e) => setSelectedProject(e.target.value)}>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          {loading && <RefreshCw size={16} className="text-gray-500 animate-spin" />}
        </div>
      </div>

      {/* Report selector */}
      {reports.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {reports.map((r) => (
            <button
              key={r.id}
              onClick={() => selectReport(r.id)}
              className={cn("px-3 py-1.5 rounded-lg text-sm font-medium transition-all border",
                selected?.id === r.id
                  ? "bg-brand-600/20 text-brand-400 border-brand-600/30"
                  : "bg-gray-900 text-gray-400 border-gray-700 hover:border-gray-600"
              )}
            >
              {r.label} {r.is_baseline && <span className="text-xs ml-1 text-green-500">(baseline)</span>}
            </button>
          ))}
        </div>
      )}

      {!selected && !loading && (
        <div className="card text-center py-16">
          <BarChart3 size={48} className="text-gray-700 mx-auto mb-3" />
          <p className="text-gray-500">No backtest reports found. Upload a backtest report in the Upload Center.</p>
        </div>
      )}

      {selected && (
        <>
          {/* Tabs */}
          <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-xl p-1 w-fit">
            {tabs.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={cn("px-4 py-1.5 rounded-lg text-sm font-medium transition-all",
                  tab === t.id ? "bg-brand-600 text-white" : "text-gray-400 hover:text-gray-200"
                )}
              >
                {t.label}
              </button>
            ))}
          </div>

          {tab === "overview" && (
            <div className="space-y-4">
              <MetricGrid cols={4}>
                <MetricCard label="Net Profit" value={fmtUsd(selected.net_profit)} trend={selected.net_profit! > 0 ? "up" : "down"} />
                <MetricCard label="Profit Factor" value={fmt(selected.profit_factor)} trend={selected.profit_factor! > 1.5 ? "up" : "neutral"} />
                <MetricCard label="Win Rate" value={fmtPct(selected.win_rate)} trend={selected.win_rate! > 55 ? "up" : "neutral"} />
                <MetricCard label="Total Trades" value={selected.total_trades ?? "N/A"} subValue={`${selected.winning_trades ?? "?"}W / ${selected.losing_trades ?? "?"}L`} />
                <MetricCard label="Avg Win" value={fmtUsd(selected.avg_win)} trend="up" />
                <MetricCard label="Avg Loss" value={fmtUsd(selected.avg_loss)} trend="down" />
                <MetricCard label="Expectancy" value={fmtUsd(selected.expectancy)} trend={(selected.expectancy ?? 0) > 0 ? "up" : "down"} />
                <MetricCard label="Max Drawdown" value={fmtPct(selected.max_drawdown_pct)} trend={(selected.max_drawdown_pct ?? 0) < 10 ? "up" : "down"} />
                <MetricCard label="Sharpe Ratio" value={fmt(selected.sharpe_ratio)} />
                <MetricCard label="Recovery Factor" value={fmt(selected.recovery_factor)} />
                <MetricCard label="Long Win Rate" value={fmtPct(selected.long_win_rate)} valueClassName={profitColor(selected.long_win_rate)} />
                <MetricCard label="Short Win Rate" value={fmtPct(selected.short_win_rate)} valueClassName={profitColor(selected.short_win_rate)} />
              </MetricGrid>
            </div>
          )}

          {tab === "monthly" && (
            <div className="card">
              <div className="section-title">Monthly P&L Breakdown</div>
              <MonthlyProfitChart data={selected.monthly_breakdown} height={320} />
              {selected.monthly_breakdown && (
                <div className="mt-4 overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-gray-800">
                        {["Month", "Profit", "Trades", "Win Rate"].map((h) => (
                          <th key={h} className="text-left py-2 px-3 text-gray-500 font-medium">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {(Array.isArray(selected.monthly_breakdown) ? selected.monthly_breakdown : Object.values(selected.monthly_breakdown)).map((m, i) => (
                        <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                          <td className="py-2 px-3 text-gray-300">{m._month || m.month}</td>
                          <td className={cn("py-2 px-3 font-medium", profitColor(m.profit))}>{fmtUsd(m.profit)}</td>
                          <td className="py-2 px-3 text-gray-400">{m.trades}</td>
                          <td className="py-2 px-3 text-gray-400">{fmtPct(m.win_rate)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {tab === "sessions" && (
            <div className="grid grid-cols-2 gap-6">
              <div className="card">
                <div className="section-title">Session Win Rate</div>
                <SessionChart data={selected.session_breakdown} height={250} />
              </div>
              <div className="card">
                <div className="section-title">Session Performance</div>
                <div className="space-y-3">
                  {selected.session_breakdown && Object.entries(selected.session_breakdown).map(([session, data]) => (
                    <div key={session} className="flex items-center gap-3 p-3 bg-gray-800/50 rounded-lg">
                      <div className="flex-1">
                        <div className="font-medium text-white capitalize">{session}</div>
                        <div className="text-xs text-gray-500">{data.trades} trades</div>
                      </div>
                      <div className="text-right">
                        <div className={cn("font-medium", profitColor(data.profit))}>{fmtUsd(data.profit)}</div>
                        <div className="text-xs text-gray-400">{fmtPct(data.win_rate)} WR</div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {tab === "failures" && (
            <div className="space-y-4">
              {selected.failure_zones && selected.failure_zones.length > 0 ? (
                <>
                  <div className="section-title flex items-center gap-2 text-red-400">
                    <AlertTriangle size={16} /> Failure Zones
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    {selected.failure_zones.map((z, i) => (
                      <div key={i} className={cn("card-sm border",
                        z.severity === "high" ? "border-red-900/50 bg-red-900/10" : "border-yellow-900/50 bg-yellow-900/10"
                      )}>
                        <div className="text-xs text-gray-400 uppercase">{z.type}</div>
                        <div className="font-semibold text-white">{z.name}</div>
                        <div className="text-sm text-red-400">Win Rate: {fmtPct(z.win_rate)}</div>
                        <div className={cn("badge mt-2 text-xs",
                          z.severity === "high" ? "bg-red-900/40 text-red-400" : "bg-yellow-900/40 text-yellow-400"
                        )}>{z.severity} severity</div>
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <div className="card text-center py-10 text-gray-600">No significant failure zones detected</div>
              )}
              {selected.ai_failure_analysis && (
                <div className="card mt-4">
                  <div className="section-title text-red-400">AI Failure Analysis</div>
                  <div className="text-sm text-gray-300 whitespace-pre-wrap">{selected.ai_failure_analysis}</div>
                </div>
              )}
            </div>
          )}

          {tab === "ai" && (
            <div className="space-y-4">
              {selected.ai_summary ? (
                <div className="card">
                  <div className="section-title">AI Strategy Summary</div>
                  <div className="text-sm text-gray-300 leading-relaxed whitespace-pre-wrap">{selected.ai_summary}</div>
                </div>
              ) : (
                <div className="card text-center py-10 text-gray-600">
                  AI analysis not yet generated. Re-run analysis with LLM enabled.
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
