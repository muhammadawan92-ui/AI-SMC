"use client";

import { useEffect, useState } from "react";
import { GitBranch, CheckCircle, XCircle, BarChart2, Loader2 } from "lucide-react";
import { ConfidenceGauge, ConfidenceBreakdown } from "@/components/ConfidenceGauge";
import { versionsApi, analysisApi, projectsApi } from "@/lib/api";
import { cn, readinessLabel, readinessColor, verdictColor } from "@/lib/utils";
import type { Project, StrategyVersion, BacktestReport, BacktestComparison, ConfidenceScore } from "@/types";

export default function VersionsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState("");
  const [versions, setVersions] = useState<StrategyVersion[]>([]);
  const [reports, setReports] = useState<BacktestReport[]>([]);
  const [scores, setScores] = useState<ConfidenceScore[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<StrategyVersion | null>(null);
  const [comparison, setComparison] = useState<BacktestComparison | null>(null);
  const [tab, setTab] = useState<"versions" | "compare" | "score">("versions");

  // Compare form
  const [baselineReport, setBaselineReport] = useState("");
  const [improvedReport, setImprovedReport] = useState("");
  const [comparing, setComparing] = useState(false);

  useEffect(() => {
    projectsApi.list().then((r) => {
      setProjects(r.data);
      if (r.data.length > 0) setSelectedProject(r.data[0].id);
    });
  }, []);

  useEffect(() => {
    if (!selectedProject) return;
    Promise.all([
      versionsApi.list(selectedProject),
      analysisApi.getBacktests(selectedProject),
      versionsApi.getScores(selectedProject),
    ]).then(([v, r, s]) => {
      setVersions(v.data);
      setReports(r.data);
      setScores(s.data);
      const baseline = r.data.find((x: BacktestReport) => x.is_baseline);
      if (baseline) setBaselineReport(baseline.id);
    });
  }, [selectedProject]);

  const approve = async (vId: string) => {
    await versionsApi.approve(vId);
    setVersions((prev) => prev.map((v) => v.id === vId ? { ...v, approval_status: "approved" } : v));
  };

  const reject = async (vId: string) => {
    await versionsApi.reject(vId);
    setVersions((prev) => prev.map((v) => v.id === vId ? { ...v, approval_status: "rejected" } : v));
  };

  const compare = async () => {
    if (!baselineReport || !improvedReport || !selectedProject) return;
    setComparing(true);
    try {
      const { data } = await versionsApi.compare({
        project_id: selectedProject,
        baseline_report_id: baselineReport,
        improved_report_id: improvedReport,
      });
      setComparison(data);
    } finally {
      setComparing(false);
    }
  };

  const statusColors: Record<string, string> = {
    pending: "text-yellow-400 bg-yellow-900/30 border-yellow-900/50",
    approved: "text-green-400 bg-green-900/30 border-green-900/50",
    rejected: "text-red-400 bg-red-900/30 border-red-900/50",
    demo_testing: "text-blue-400 bg-blue-900/30 border-blue-900/50",
    live_ready: "text-emerald-400 bg-emerald-900/30 border-emerald-900/50",
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Version Manager</h1>
          <p className="text-sm text-gray-500 mt-0.5">Track, compare, and manage EA strategy versions</p>
        </div>
        <select className="select w-48" value={selectedProject} onChange={(e) => setSelectedProject(e.target.value)}>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </div>

      <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-xl p-1 w-fit">
        {(["versions", "compare", "score"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={cn("px-4 py-1.5 rounded-lg text-sm font-medium transition-all capitalize",
              tab === t ? "bg-brand-600 text-white" : "text-gray-400 hover:text-gray-200"
            )}>
            {t}
          </button>
        ))}
      </div>

      {tab === "versions" && (
        <div className="space-y-3">
          {versions.length === 0 ? (
            <div className="card text-center py-16">
              <GitBranch size={48} className="text-gray-700 mx-auto mb-3" />
              <p className="text-gray-500">No versions yet. Create your first version in the Improvement Lab after accepting improvements.</p>
            </div>
          ) : versions.map((v) => {
            const vScore = scores.find((s) => s.version_id === v.id);
            return (
              <div key={v.id} className={cn("card-sm border cursor-pointer transition-all",
                selectedVersion?.id === v.id ? "border-brand-600/50 bg-brand-900/10" : "border-gray-800 hover:border-gray-700"
              )} onClick={() => setSelectedVersion(selectedVersion?.id === v.id ? null : v)}>
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <div className={cn("w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold",
                      v.is_baseline ? "bg-brand-900/40 text-brand-400 border border-brand-900/50" : "bg-gray-800 text-gray-400"
                    )}>
                      {v.is_baseline ? "B" : v.version_number.replace(/[^0-9.]/g, "").slice(-3) || "V"}
                    </div>
                    <div>
                      <div className="font-semibold text-white">{v.version_number}</div>
                      {v.label && <div className="text-xs text-gray-500">{v.label}</div>}
                    </div>
                    {v.is_baseline && <span className="badge bg-brand-900/30 text-brand-400 text-xs border border-brand-900/50">Baseline</span>}
                  </div>
                  <div className="flex items-center gap-3">
                    {vScore && <ConfidenceGauge score={vScore.overall_score} size="sm" showLabel={false} />}
                    <span className={cn("badge border text-xs", statusColors[v.approval_status] || "text-gray-400 bg-gray-800 border-gray-700")}>
                      {v.approval_status}
                    </span>
                    {v.approval_status === "pending" && (
                      <div className="flex gap-1">
                        <button onClick={(e) => { e.stopPropagation(); approve(v.id); }} className="text-green-400 hover:text-green-300 p-1">
                          <CheckCircle size={16} />
                        </button>
                        <button onClick={(e) => { e.stopPropagation(); reject(v.id); }} className="text-red-400 hover:text-red-300 p-1">
                          <XCircle size={16} />
                        </button>
                      </div>
                    )}
                  </div>
                </div>

                {selectedVersion?.id === v.id && vScore && (
                  <div className="mt-4 pt-4 border-t border-gray-800">
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <div className="text-xs text-gray-500 mb-3">Confidence Score Breakdown</div>
                        <ConfidenceBreakdown breakdown={vScore.breakdown} />
                      </div>
                      <div>
                        <div className="flex justify-center mb-3">
                          <ConfidenceGauge score={vScore.overall_score} readiness={vScore.readiness_level} size="lg" />
                        </div>
                        {vScore.ai_notes && (
                          <p className="text-xs text-gray-400 mt-2">{vScore.ai_notes}</p>
                        )}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {tab === "compare" && (
        <div className="space-y-4">
          <div className="card">
            <div className="section-title">Compare Two Backtests</div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="label">Baseline Report</label>
                <select className="select" value={baselineReport} onChange={(e) => setBaselineReport(e.target.value)}>
                  <option value="">— Select —</option>
                  {reports.map((r) => <option key={r.id} value={r.id}>{r.label} {r.is_baseline ? "(baseline)" : ""}</option>)}
                </select>
              </div>
              <div>
                <label className="label">Improved Report</label>
                <select className="select" value={improvedReport} onChange={(e) => setImprovedReport(e.target.value)}>
                  <option value="">— Select —</option>
                  {reports.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
                </select>
              </div>
            </div>
            <button onClick={compare} disabled={comparing || !baselineReport || !improvedReport}
              className="btn-primary flex items-center gap-2 mt-4">
              {comparing ? <Loader2 size={16} className="animate-spin" /> : <BarChart2 size={16} />}
              {comparing ? "Comparing…" : "Compare"}
            </button>
          </div>

          {comparison && (
            <div className="card">
              <div className={cn("text-lg font-bold mb-4", verdictColor(comparison.verdict))}>
                Verdict: {comparison.verdict?.toUpperCase()}
                {comparison.overfit_detected && <span className="text-orange-400 ml-2">⚠️ Overfit Risk</span>}
              </div>
              <div className="grid grid-cols-3 gap-4">
                {[
                  { label: "Profit Delta", value: comparison.profit_delta, prefix: "$" },
                  { label: "PF Delta", value: comparison.profit_factor_delta },
                  { label: "Win Rate Delta", value: comparison.win_rate_delta, suffix: "%" },
                  { label: "Drawdown Delta", value: comparison.drawdown_delta, suffix: "%" },
                  { label: "Trade Count Delta", value: comparison.trade_count_delta },
                  { label: "Expectancy Delta", value: comparison.expectancy_delta, prefix: "$" },
                ].map(({ label, value, prefix, suffix }) => (
                  <div key={label} className="card-sm">
                    <div className="text-xs text-gray-500 uppercase tracking-wider">{label}</div>
                    <div className={cn("text-xl font-bold mt-1",
                      value == null ? "text-gray-600" :
                      (label.includes("Drawdown") ? (value < 0 ? "text-green-400" : value > 0 ? "text-red-400" : "text-gray-400") :
                      (value > 0 ? "text-green-400" : value < 0 ? "text-red-400" : "text-gray-400"))
                    )}>
                      {value == null ? "N/A" : `${value > 0 ? "+" : ""}${prefix || ""}${value.toFixed(2)}${suffix || ""}`}
                    </div>
                  </div>
                ))}
              </div>
              {comparison.overfit_reasons && comparison.overfit_reasons.length > 0 && (
                <div className="mt-4 p-3 bg-orange-900/10 border border-orange-900/50 rounded-lg">
                  <div className="text-sm font-medium text-orange-400 mb-2">Overfitting Warnings</div>
                  {comparison.overfit_reasons.map((r, i) => (
                    <div key={i} className="text-xs text-orange-300">• {r}</div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {tab === "score" && (
        <div className="space-y-4">
          <div className="section-title">Confidence Score History</div>
          {scores.length === 0 ? (
            <div className="card text-center py-10 text-gray-600">No confidence scores yet. Run a comparison first.</div>
          ) : (
            <div className="grid grid-cols-2 gap-4">
              {scores.map((s) => (
                <div key={s.id} className="card">
                  <div className="flex items-center justify-between mb-4">
                    <span className={cn("badge", readinessColor(s.readiness_level))}>{readinessLabel(s.readiness_level)}</span>
                    <span className="text-xs text-gray-500">{s.created_at?.slice(0, 10)}</span>
                  </div>
                  <div className="flex items-center gap-6">
                    <ConfidenceGauge score={s.overall_score} size="md" showLabel={false} />
                    <div className="flex-1">
                      <ConfidenceBreakdown breakdown={s.breakdown} />
                    </div>
                  </div>
                  {s.ai_notes && <p className="text-xs text-gray-500 mt-3">{s.ai_notes}</p>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
