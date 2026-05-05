"use client";

import { useEffect, useState } from "react";
import {
  TrendingUp, TrendingDown, Activity, Target, Shield,
  AlertTriangle, CheckCircle, BarChart3, Zap, ChevronRight,
} from "lucide-react";
import { MetricCard, MetricGrid } from "@/components/MetricCard";
import { ConfidenceGauge } from "@/components/ConfidenceGauge";
import { MonthlyProfitChart, SessionChart } from "@/components/MonthlyChart";
import { projectsApi, healthApi, analysisApi, versionsApi } from "@/lib/api";
import { fmt, fmtUsd, fmtPct, profitColor, readinessLabel, readinessColor } from "@/lib/utils";
import { cn } from "@/lib/utils";
import type { Project, BacktestReport, ConfidenceScore } from "@/types";
import Link from "next/link";

export default function DashboardPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProject, setActiveProject] = useState<Project | null>(null);
  const [baseline, setBaseline] = useState<BacktestReport | null>(null);
  const [bestScore, setBestScore] = useState<ConfidenceScore | null>(null);
  const [systemStatus, setSystemStatus] = useState<{ live_trading_enabled: boolean; mock_mode: boolean } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const [projRes, healthRes] = await Promise.all([
          projectsApi.list(),
          healthApi.check(),
        ]);
        setProjects(projRes.data);
        setSystemStatus(healthRes.data);

        if (projRes.data.length > 0) {
          const proj = projRes.data[0];
          const projDetail = await projectsApi.get(proj.id);
          setActiveProject(projDetail.data);

          const [btRes, scoresRes] = await Promise.all([
            analysisApi.getBacktests(proj.id),
            versionsApi.getScores(proj.id),
          ]);
          const baselineReport = btRes.data.find((r: BacktestReport) => r.is_baseline);
          if (baselineReport) {
            const detail = await analysisApi.getBacktestDetail(baselineReport.id);
            setBaseline(detail.data);
          }
          if (scoresRes.data.length > 0) {
            setBestScore(scoresRes.data[0]);
          }
        }
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-500 text-sm">Loading dashboard…</div>
      </div>
    );
  }

  const noData = !baseline;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Dashboard</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            {activeProject ? activeProject.name : "No project — upload your EA to get started"}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className={cn("badge", systemStatus?.mock_mode ? "bg-yellow-900/30 text-yellow-400" : "bg-green-900/30 text-green-400")}>
            {systemStatus?.mock_mode ? "Mock Mode" : "Live Mode"}
          </div>
          <div className={cn("badge", systemStatus?.live_trading_enabled ? "bg-red-900/30 text-red-400 border border-red-900/50" : "bg-gray-800 text-gray-500")}>
            <Shield size={12} className="mr-1" />
            Trading: {systemStatus?.live_trading_enabled ? "LIVE" : "Locked"}
          </div>
        </div>
      </div>

      {/* Trading mode banner */}
      <div className={cn(
        "flex items-center gap-3 px-4 py-3 rounded-xl border text-sm",
        bestScore?.readiness_level === "live_ready"
          ? "bg-green-900/20 border-green-800/50 text-green-300"
          : bestScore?.readiness_level === "demo_testing" || bestScore?.readiness_level === "demo_candidate"
          ? "bg-blue-900/20 border-blue-800/50 text-blue-300"
          : "bg-gray-900 border-gray-800 text-gray-400"
      )}>
        <Zap size={16} />
        <span className="font-medium">Mode:</span>
        <span>{bestScore ? readinessLabel(bestScore.readiness_level) : "Research"}</span>
        <span className="text-gray-500 ml-1">—</span>
        <span className="text-gray-400">
          {bestScore
            ? `Confidence score: ${bestScore.overall_score.toFixed(1)}%`
            : "Upload your EA and backtest report to begin analysis"}
        </span>
        {noData && (
          <Link href="/upload" className="ml-auto flex items-center gap-1 text-brand-400 hover:text-brand-300 font-medium">
            Get started <ChevronRight size={14} />
          </Link>
        )}
      </div>

      {/* Baseline Performance Metrics */}
      <div>
        <div className="section-title">Baseline Performance</div>
        <MetricGrid cols={4}>
          <MetricCard
            label="Net Profit"
            value={baseline ? fmtUsd(baseline.net_profit) : "—"}
            trend={baseline?.net_profit ? (baseline.net_profit > 0 ? "up" : "down") : "neutral"}
            icon={<TrendingUp size={20} />}
          />
          <MetricCard
            label="Profit Factor"
            value={baseline ? fmt(baseline.profit_factor) : "—"}
            trend={baseline?.profit_factor ? (baseline.profit_factor > 1.5 ? "up" : baseline.profit_factor < 1.2 ? "down" : "neutral") : "neutral"}
            icon={<BarChart3 size={20} />}
          />
          <MetricCard
            label="Win Rate"
            value={baseline ? fmtPct(baseline.win_rate) : "—"}
            trend={baseline?.win_rate ? (baseline.win_rate > 55 ? "up" : baseline.win_rate < 45 ? "down" : "neutral") : "neutral"}
            icon={<Target size={20} />}
          />
          <MetricCard
            label="Total Trades"
            value={baseline?.total_trades ?? "—"}
            subValue={baseline ? `${baseline.winning_trades ?? "?"} W / ${baseline.losing_trades ?? "?"} L` : ""}
            icon={<Activity size={20} />}
          />
          <MetricCard
            label="Max Drawdown"
            value={baseline ? fmtPct(baseline.max_drawdown_pct) : "—"}
            trend={baseline?.max_drawdown_pct ? (baseline.max_drawdown_pct < 10 ? "up" : baseline.max_drawdown_pct > 20 ? "down" : "neutral") : "neutral"}
            icon={<TrendingDown size={20} />}
          />
          <MetricCard
            label="Sharpe Ratio"
            value={baseline ? fmt(baseline.sharpe_ratio) : "—"}
            trend={baseline?.sharpe_ratio ? (baseline.sharpe_ratio > 1 ? "up" : "neutral") : "neutral"}
          />
          <MetricCard
            label="Recovery Factor"
            value={baseline ? fmt(baseline.recovery_factor) : "—"}
          />
          <MetricCard
            label="Expectancy"
            value={baseline ? fmtUsd(baseline.expectancy) : "—"}
            trend={baseline?.expectancy ? (baseline.expectancy > 0 ? "up" : "down") : "neutral"}
          />
        </MetricGrid>
      </div>

      <div className="grid grid-cols-3 gap-6">
        {/* Monthly chart */}
        <div className="col-span-2 card">
          <div className="section-title">Monthly P&L</div>
          <MonthlyProfitChart data={baseline?.monthly_breakdown} />
        </div>

        {/* Confidence + Session */}
        <div className="space-y-4">
          <div className="card">
            <div className="section-title text-center">Confidence Score</div>
            {bestScore ? (
              <div className="flex flex-col items-center gap-3">
                <ConfidenceGauge
                  score={bestScore.overall_score}
                  readiness={bestScore.readiness_level}
                  size="lg"
                />
                <p className="text-xs text-gray-500 text-center">{bestScore.ai_notes?.split("\n")[0]}</p>
              </div>
            ) : (
              <div className="text-center text-gray-600 text-sm py-6">
                No versions tested yet
              </div>
            )}
          </div>

          {/* Long vs Short */}
          {baseline && (
            <div className="card-sm">
              <div className="text-xs text-gray-500 uppercase tracking-wider mb-3">Direction Balance</div>
              <div className="space-y-2">
                <div>
                  <div className="flex justify-between text-xs mb-1">
                    <span className="text-gray-400">Long</span>
                    <span className={profitColor(baseline.long_win_rate)}>{fmtPct(baseline.long_win_rate)} WR</span>
                  </div>
                  <div className="w-full bg-gray-800 rounded-full h-1.5">
                    <div className="bg-green-500 h-1.5 rounded-full" style={{ width: `${baseline.long_win_rate ?? 0}%` }} />
                  </div>
                </div>
                <div>
                  <div className="flex justify-between text-xs mb-1">
                    <span className="text-gray-400">Short</span>
                    <span className={profitColor(baseline.short_win_rate)}>{fmtPct(baseline.short_win_rate)} WR</span>
                  </div>
                  <div className="w-full bg-gray-800 rounded-full h-1.5">
                    <div className="bg-red-500 h-1.5 rounded-full" style={{ width: `${baseline.short_win_rate ?? 0}%` }} />
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Session breakdown */}
      {baseline?.session_breakdown && (
        <div className="card">
          <div className="section-title">Session Win Rate</div>
          <SessionChart data={baseline.session_breakdown} />
        </div>
      )}

      {/* Failure Zones */}
      {baseline?.failure_zones && baseline.failure_zones.length > 0 && (
        <div className="card">
          <div className="section-title text-red-400 flex items-center gap-2">
            <AlertTriangle size={16} />
            Failure Zones
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
            {baseline.failure_zones.map((z, i) => (
              <div key={i} className={cn(
                "card-sm border",
                z.severity === "high" ? "border-red-900/50 bg-red-900/10" : "border-yellow-900/50 bg-yellow-900/10"
              )}>
                <div className="text-xs font-medium text-gray-400 uppercase">{z.type}</div>
                <div className="font-semibold text-white mt-1">{z.name}</div>
                <div className="text-sm text-red-400">Win rate: {z.win_rate?.toFixed(1)}%</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* AI Summary */}
      {baseline?.ai_summary && (
        <div className="card">
          <div className="section-title flex items-center gap-2">
            <CheckCircle size={16} className="text-brand-400" />
            AI Strategy Analysis
          </div>
          <div className="prose prose-invert prose-sm max-w-none text-gray-300 whitespace-pre-wrap text-sm leading-relaxed">
            {baseline.ai_summary}
          </div>
        </div>
      )}

      {/* Empty state */}
      {noData && (
        <div className="card text-center py-16">
          <BarChart3 size={48} className="text-gray-700 mx-auto mb-4" />
          <h2 className="text-xl font-semibold text-gray-400 mb-2">No data yet</h2>
          <p className="text-gray-600 mb-6 max-w-md mx-auto">
            Upload your Pine Script, MQL5 EA, and MT5 backtest report to start the analysis pipeline.
          </p>
          <Link href="/upload" className="btn-primary inline-flex items-center gap-2">
            <Zap size={16} /> Start Uploading
          </Link>
        </div>
      )}
    </div>
  );
}
