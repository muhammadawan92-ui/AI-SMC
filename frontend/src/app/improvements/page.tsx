"use client";

import { useEffect, useState } from "react";
import { Lightbulb, Sparkles, Filter, Loader2 } from "lucide-react";
import { ImprovementCard } from "@/components/ImprovementCard";
import { improvementsApi, analysisApi, projectsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ImprovementIdea, IdeaStatus, BacktestReport, Project } from "@/types";

export default function ImprovementsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState("");
  const [ideas, setIdeas] = useState<ImprovementIdea[]>([]);
  const [reports, setReports] = useState<BacktestReport[]>([]);
  const [selectedReport, setSelectedReport] = useState("");
  const [filterStatus, setFilterStatus] = useState<IdeaStatus | "">("");
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [nIdeas, setNIdeas] = useState(10);

  useEffect(() => {
    projectsApi.list().then((r) => {
      setProjects(r.data);
      if (r.data.length > 0) setSelectedProject(r.data[0].id);
    });
  }, []);

  useEffect(() => {
    if (!selectedProject) return;
    setLoading(true);
    Promise.all([
      improvementsApi.list(selectedProject, filterStatus || undefined),
      analysisApi.getBacktests(selectedProject),
    ]).then(([ideas, reports]) => {
      setIdeas(ideas.data);
      setReports(reports.data);
      const baseline = reports.data.find((r: BacktestReport) => r.is_baseline);
      if (baseline) setSelectedReport(baseline.id);
    }).finally(() => setLoading(false));
  }, [selectedProject, filterStatus]);

  const generate = async () => {
    if (!selectedProject || !selectedReport) return;
    setGenerating(true);
    try {
      const { data } = await improvementsApi.generate({
        project_id: selectedProject,
        backtest_report_id: selectedReport,
        n_ideas: nIdeas,
      });
      setIdeas((prev) => [...data, ...prev]);
    } catch (e) {
      console.error(e);
    } finally {
      setGenerating(false);
    }
  };

  const handleStatusChange = (id: string, status: IdeaStatus) => {
    setIdeas((prev) => prev.map((i) => i.id === id ? { ...i, status } : i));
  };

  const grouped = {
    pending: ideas.filter((i) => i.status === "pending"),
    accepted: ideas.filter((i) => i.status === "accepted"),
    rejected: ideas.filter((i) => i.status === "rejected"),
    tested: ideas.filter((i) => i.status === "tested"),
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Improvement Lab</h1>
          <p className="text-sm text-gray-500 mt-0.5">AI-generated, SMC-based EA improvement hypotheses</p>
        </div>
        <div className="flex items-center gap-3">
          <select className="select w-40" value={selectedProject} onChange={(e) => setSelectedProject(e.target.value)}>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
      </div>

      {/* Generate Panel */}
      <div className="card">
        <div className="section-title flex items-center gap-2">
          <Sparkles size={16} className="text-yellow-400" /> Generate Improvement Ideas
        </div>
        <div className="flex items-end gap-4 flex-wrap">
          <div>
            <label className="label">Based on backtest report</label>
            <select className="select w-48" value={selectedReport} onChange={(e) => setSelectedReport(e.target.value)}>
              <option value="">— Select report —</option>
              {reports.map((r) => <option key={r.id} value={r.id}>{r.label} {r.is_baseline ? "(baseline)" : ""}</option>)}
            </select>
          </div>
          <div>
            <label className="label">Number of ideas</label>
            <select className="select w-24" value={nIdeas} onChange={(e) => setNIdeas(Number(e.target.value))}>
              {[5, 10, 15, 20].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
          <button
            onClick={generate}
            disabled={!selectedProject || !selectedReport || generating}
            className="btn-primary flex items-center gap-2"
          >
            {generating ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
            {generating ? "Generating…" : "Generate Ideas"}
          </button>
        </div>
        <p className="text-xs text-gray-600 mt-3">
          AI will analyze the backtest failure zones and generate hypothesis-driven improvement ideas using SMC logic.
          No random optimization — every suggestion has a reason.
        </p>
      </div>

      {/* Filter */}
      <div className="flex items-center gap-2">
        <Filter size={14} className="text-gray-500" />
        <span className="text-sm text-gray-500">Filter:</span>
        {(["", "pending", "accepted", "rejected", "tested"] as const).map((s) => (
          <button key={s}
            onClick={() => setFilterStatus(s as IdeaStatus | "")}
            className={cn("px-3 py-1 rounded-lg text-xs font-medium transition-all",
              filterStatus === s ? "bg-brand-600/20 text-brand-400" : "bg-gray-800 text-gray-400 hover:text-gray-200"
            )}>
            {s || "All"} {s && `(${grouped[s as keyof typeof grouped]?.length ?? 0})`}
          </button>
        ))}
      </div>

      {/* Ideas */}
      {loading ? (
        <div className="text-center py-12 text-gray-600">Loading…</div>
      ) : ideas.length === 0 ? (
        <div className="card text-center py-16">
          <Lightbulb size={48} className="text-gray-700 mx-auto mb-3" />
          <p className="text-gray-500">No improvement ideas yet. Generate ideas above or upload your backtest report first.</p>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="text-sm text-gray-500">{ideas.length} ideas total</div>
          {ideas.map((idea) => (
            <ImprovementCard key={idea.id} idea={idea} onStatusChange={handleStatusChange} />
          ))}
        </div>
      )}
    </div>
  );
}
