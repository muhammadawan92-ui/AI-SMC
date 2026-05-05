"use client";

import { useState, useEffect } from "react";
import { CheckCircle, Loader2, Play, AlertCircle } from "lucide-react";
import { UploadZone } from "@/components/UploadZone";
import { projectsApi, analysisApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Project } from "@/types";

interface UploadedFiles {
  pine_script?: { id: string; name: string };
  mql5?: { id: string; name: string };
  backtest_report?: { id: string; name: string };
  mt5_log?: { id: string; name: string };
  screenshot?: { id: string; name: string };
  csv?: { id: string; name: string };
  notes?: { id: string; name: string };
}

export default function UploadPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState("");
  const [newProjectName, setNewProjectName] = useState("");
  const [symbol, setSymbol] = useState("XAUUSD");
  const [timeframe, setTimeframe] = useState("H1");
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFiles>({});
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisResults, setAnalysisResults] = useState<Record<string, string>>({});
  const [runLLM, setRunLLM] = useState(true);

  useEffect(() => {
    projectsApi.list().then((r) => setProjects(r.data)).catch(() => {});
  }, []);

  const createProject = async () => {
    if (!newProjectName.trim()) return;
    const { data } = await projectsApi.create({ name: newProjectName, symbol, timeframe });
    setProjects((prev) => [...prev, data]);
    setSelectedProject(data.id);
    setNewProjectName("");
  };

  const handleUploadSuccess = (type: keyof UploadedFiles, id: string, name: string) => {
    setUploadedFiles((prev) => ({ ...prev, [type]: { id, name } }));
  };

  const runAnalysis = async () => {
    if (!selectedProject) return;
    setAnalyzing(true);
    setAnalysisResults({});
    const results: Record<string, string> = {};

    if (uploadedFiles.pine_script) {
      try {
        await analysisApi.analyzePine({ file_id: uploadedFiles.pine_script.id, project_id: selectedProject, run_llm: runLLM });
        results.pine = "done";
      } catch { results.pine = "failed"; }
    }
    if (uploadedFiles.mql5) {
      try {
        await analysisApi.analyzeMql5({ file_id: uploadedFiles.mql5.id, project_id: selectedProject, run_llm: runLLM });
        results.mql5 = "done";
      } catch { results.mql5 = "failed"; }
    }
    if (uploadedFiles.backtest_report) {
      try {
        await analysisApi.analyzeBacktest({
          file_id: uploadedFiles.backtest_report.id,
          project_id: selectedProject,
          label: "baseline",
          is_baseline: true,
          run_llm: runLLM,
        });
        results.backtest = "done";
      } catch { results.backtest = "failed"; }
    }
    setAnalysisResults(results);
    setAnalyzing(false);
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Upload Center</h1>
        <p className="text-sm text-gray-500 mt-0.5">Upload your EA files to begin analysis</p>
      </div>

      {/* Project Selection */}
      <div className="card">
        <div className="section-title">Project</div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="label">Select existing project</label>
            <select
              className="select"
              value={selectedProject}
              onChange={(e) => setSelectedProject(e.target.value)}
            >
              <option value="">— Choose project —</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">Or create new project</label>
            <div className="flex gap-2">
              <input
                className="input"
                placeholder="Project name (e.g. XAUUSD SMC EA)"
                value={newProjectName}
                onChange={(e) => setNewProjectName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && createProject()}
              />
              <button onClick={createProject} className="btn-primary whitespace-nowrap">Create</button>
            </div>
          </div>
          <div>
            <label className="label">Symbol</label>
            <input className="input" value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="XAUUSD" />
          </div>
          <div>
            <label className="label">Timeframe</label>
            <select className="select" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
              {["M1","M5","M15","M30","H1","H4","D1"].map((tf) => <option key={tf}>{tf}</option>)}
            </select>
          </div>
        </div>
      </div>

      {/* Upload Zones */}
      <div className="grid grid-cols-2 gap-4">
        {[
          { type: "pine_script" as const, label: "Pine Script Source", desc: ".pine or .txt — your TradingView strategy" },
          { type: "mql5" as const, label: "MQL5 Expert Advisor", desc: ".mq5 / .mq4 — your MT5 EA source code" },
          { type: "backtest_report" as const, label: "MT5 Backtest Report", desc: ".htm / .html — from MT5 Strategy Tester" },
          { type: "mt5_log" as const, label: "MT5 Expert Log", desc: ".log / .txt — EA journal from MT5" },
          { type: "screenshot" as const, label: "TradingView Screenshot", desc: ".png / .jpg — chart screenshot for analysis" },
          { type: "csv" as const, label: "OHLC / Trade CSV", desc: ".csv — price data or trade history export" },
          { type: "notes" as const, label: "SMC Knowledge Doc", desc: ".docx / .txt / .md — reference knowledge for model learning" },
        ].map(({ type, label, desc }) => (
          <div key={type} className="card">
            <div className="flex items-center justify-between mb-3">
              <div className="text-sm font-semibold text-white">{label}</div>
              {uploadedFiles[type] && (
                <span className="badge bg-green-900/30 text-green-400 text-xs flex items-center gap-1">
                  <CheckCircle size={10} /> Done
                </span>
              )}
              {analysisResults[type] && (
                <span className={cn("badge text-xs flex items-center gap-1",
                  analysisResults[type] === "done"
                    ? "bg-green-900/30 text-green-400"
                    : "bg-red-900/30 text-red-400"
                )}>
                  {analysisResults[type] === "done" ? <CheckCircle size={10} /> : <AlertCircle size={10} />}
                  Analysis: {analysisResults[type]}
                </span>
              )}
            </div>
            <UploadZone
              fileType={type}
              projectId={selectedProject || undefined}
              onSuccess={(id, name) => handleUploadSuccess(type, id, name)}
              label={label}
              description={desc}
            />
            {uploadedFiles[type] && (
              <div className="mt-2 text-xs text-gray-500 truncate">{uploadedFiles[type]!.name}</div>
            )}
          </div>
        ))}
      </div>

      {/* Run Analysis */}
      <div className="card">
        <div className="section-title">Run Analysis Pipeline</div>
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            <p className="text-sm text-gray-400">
              Analyzes all uploaded files: Pine Script parsing, MQL5 parsing, backtest analysis, and generates improvement ideas.
            </p>
            <div className="flex items-center gap-2 mt-2">
              <input
                type="checkbox"
                id="runllm"
                checked={runLLM}
                onChange={(e) => setRunLLM(e.target.checked)}
                className="w-4 h-4 accent-brand-500"
              />
              <label htmlFor="runllm" className="text-sm text-gray-400">
                Run AI analysis (requires LLM API key or mock mode)
              </label>
            </div>
          </div>
          <button
            onClick={runAnalysis}
            disabled={!selectedProject || analyzing || Object.keys(uploadedFiles).length === 0}
            className="btn-primary flex items-center gap-2 ml-6"
          >
            {analyzing ? (
              <><Loader2 size={16} className="animate-spin" /> Analyzing…</>
            ) : (
              <><Play size={16} /> Run Analysis</>
            )}
          </button>
        </div>

        {Object.keys(analysisResults).length > 0 && (
          <div className="mt-4 grid grid-cols-3 gap-3">
            {Object.entries(analysisResults).map(([key, status]) => (
              <div key={key} className={cn("card-sm flex items-center gap-2",
                status === "done" ? "border-green-900/50" : "border-red-900/50"
              )}>
                {status === "done"
                  ? <CheckCircle size={14} className="text-green-400" />
                  : <AlertCircle size={14} className="text-red-400" />}
                <span className="text-sm capitalize">{key}: {status}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
