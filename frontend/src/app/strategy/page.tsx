"use client";

import { useEffect, useState } from "react";
import { Code2, GitMerge, Lightbulb, CheckCircle } from "lucide-react";
import { analysisApi, projectsApi, settingsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Project, PineAnalysis, MQL5Analysis } from "@/types";

export default function StrategyPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState("");
  const [pineAnalyses, setPineAnalyses] = useState<PineAnalysis[]>([]);
  const [mql5Analyses, setMql5Analyses] = useState<MQL5Analysis[]>([]);
  const [smcKnowledge, setSmcKnowledge] = useState<Record<string, { name: string; description: string }>>({});
  const [tab, setTab] = useState<"pine" | "mql5" | "diff" | "smc">("pine");

  useEffect(() => {
    projectsApi.list().then((r) => {
      setProjects(r.data);
      if (r.data.length > 0) setSelectedProject(r.data[0].id);
    });
    settingsApi.getSmcKnowledge().then((r) => setSmcKnowledge(r.data)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!selectedProject) return;
    Promise.all([
      analysisApi.getPineSources(selectedProject),
      analysisApi.getMql5Sources(selectedProject),
    ]).then(([pine, mql5]) => {
      setPineAnalyses(pine.data);
      setMql5Analyses(mql5.data);
    }).catch(() => {});
  }, [selectedProject]);

  const pine = pineAnalyses[0];
  const mql5 = mql5Analyses[0];

  const tabs = [
    { id: "pine", label: "Pine Script" },
    { id: "mql5", label: "MQL5 EA" },
    { id: "diff", label: "Pine vs EA Diff" },
    { id: "smc", label: "SMC Knowledge" },
  ] as const;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Strategy Logic Viewer</h1>
          <p className="text-sm text-gray-500 mt-0.5">Understand your Pine Script and MQL5 EA logic</p>
        </div>
        <select className="select w-48" value={selectedProject} onChange={(e) => setSelectedProject(e.target.value)}>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </div>

      <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-xl p-1 w-fit">
        {tabs.map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={cn("px-4 py-1.5 rounded-lg text-sm font-medium transition-all",
              tab === t.id ? "bg-brand-600 text-white" : "text-gray-400 hover:text-gray-200"
            )}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === "pine" && (
        <div className="space-y-4">
          {pine ? (
            <>
              <div className="card">
                <div className="section-title flex items-center gap-2">
                  <Code2 size={16} className="text-green-400" /> Pine Script Summary
                </div>
                <p className="text-sm text-gray-300">{pine.summary}</p>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="card">
                  <div className="section-title text-sm">SMC Concepts Detected</div>
                  <div className="flex flex-wrap gap-2">
                    {pine.detected_smc_concepts?.map((c) => (
                      <span key={c} className="badge bg-brand-900/30 text-brand-400 border border-brand-900/50">{c}</span>
                    ))}
                  </div>
                </div>
                <div className="card">
                  <div className="section-title text-sm">Session Filters</div>
                  <div className="space-y-1">
                    {pine.session_filters?.map((s) => (
                      <div key={s} className="flex items-center gap-2 text-sm text-gray-300">
                        <CheckCircle size={12} className="text-green-400" /> {s}
                      </div>
                    )) ?? <span className="text-gray-600 text-sm">None detected</span>}
                  </div>
                </div>
              </div>
              <div className="card">
                <div className="section-title text-sm">Entry Conditions</div>
                <div className="code-block text-xs">
                  {pine.entry_conditions?.slice(0, 20).join("\n") || "No conditions extracted"}
                </div>
              </div>
              {pine.ai_analysis && (
                <div className="card">
                  <div className="section-title flex items-center gap-2">
                    <Lightbulb size={16} className="text-yellow-400" /> AI Analysis
                  </div>
                  <div className="text-sm text-gray-300 whitespace-pre-wrap">{pine.ai_analysis}</div>
                </div>
              )}
            </>
          ) : (
            <div className="card text-center py-16 text-gray-600">
              No Pine Script analysis found. Upload and analyze your Pine Script first.
            </div>
          )}
        </div>
      )}

      {tab === "mql5" && (
        <div className="space-y-4">
          {mql5 ? (
            <>
              <div className="card">
                <div className="section-title flex items-center gap-2">
                  <Code2 size={16} className="text-blue-400" /> MQL5 EA Summary
                </div>
                <p className="text-sm text-gray-300">{mql5.summary}</p>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="card">
                  <div className="section-title text-sm">SMC Concepts in EA</div>
                  <div className="flex flex-wrap gap-2">
                    {mql5.detected_smc_concepts?.map((c) => (
                      <span key={c} className="badge bg-blue-900/30 text-blue-400 border border-blue-900/50">{c}</span>
                    ))}
                  </div>
                </div>
                <div className="card">
                  <div className="section-title text-sm">Input Parameters ({mql5.input_parameters?.length || 0})</div>
                  <div className="space-y-1 max-h-48 overflow-y-auto">
                    {mql5.input_parameters?.map((p) => (
                      <div key={p.name} className="flex justify-between text-xs">
                        <code className="text-blue-400 font-mono">{p.name}</code>
                        <span className="text-gray-500">{p.default}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
              {mql5.entry_logic && (
                <div className="card">
                  <div className="section-title text-sm">Entry Logic (Extracted)</div>
                  <pre className="code-block text-xs max-h-64 overflow-auto">{mql5.entry_logic}</pre>
                </div>
              )}
              {mql5.ai_analysis && (
                <div className="card">
                  <div className="section-title flex items-center gap-2">
                    <Lightbulb size={16} className="text-yellow-400" /> AI Analysis
                  </div>
                  <div className="text-sm text-gray-300 whitespace-pre-wrap">{mql5.ai_analysis}</div>
                </div>
              )}
            </>
          ) : (
            <div className="card text-center py-16 text-gray-600">
              No MQL5 analysis found. Upload and analyze your EA code first.
            </div>
          )}
        </div>
      )}

      {tab === "diff" && (
        <div className="space-y-4">
          {mql5?.pine_vs_ea_diff ? (
            <div className="card">
              <div className="section-title flex items-center gap-2">
                <GitMerge size={16} className="text-orange-400" /> Pine Script vs MQL5 EA Differences
              </div>
              <div className="text-sm text-gray-300 whitespace-pre-wrap">{mql5.pine_vs_ea_diff}</div>
            </div>
          ) : (
            <div className="card text-center py-16 text-gray-600">
              Upload both Pine Script and MQL5 EA to see differences.
            </div>
          )}
        </div>
      )}

      {tab === "smc" && (
        <div className="grid grid-cols-2 gap-4">
          {Object.entries(smcKnowledge).map(([key, data]) => (
            <div key={key} className="card-sm">
              <div className="font-semibold text-white mb-1">{data.name}</div>
              <p className="text-xs text-gray-400">{data.description.slice(0, 200)}…</p>
              <span className="badge bg-gray-800 text-gray-500 text-xs mt-2">{key}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
