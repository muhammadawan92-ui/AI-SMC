"use client";

import { useEffect, useState } from "react";
import { Camera, Loader2, ArrowUpCircle } from "lucide-react";
import { UploadZone } from "@/components/UploadZone";
import { analysisApi, projectsApi, tradingviewApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Project, ScreenshotAnalysis } from "@/types";

export default function ScreenshotsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState("");
  const [screenshots, setScreenshots] = useState<ScreenshotAnalysis[]>([]);
  const [uploadedFileId, setUploadedFileId] = useState("");
  const [symbol, setSymbol] = useState("XAUUSD");
  const [timeframe, setTimeframe] = useState("H1");
  const [userNotes, setUserNotes] = useState("");
  const [eaDecision, setEaDecision] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<ScreenshotAnalysis | null>(null);
  const [selected, setSelected] = useState<ScreenshotAnalysis | null>(null);
  const [chartUrl, setChartUrl] = useState("");
  const [tvCompare, setTvCompare] = useState<{
    model_decision: string;
    agreement: string;
    model_reasoning: string;
    improvement_hint: string;
    confidence: number;
    source: string;
    knowledge_loaded?: boolean;
    mql5_excerpt_chars?: number;
    tradingview_context?: { fetch_ok?: boolean; normalized_symbol?: string; note?: string; og_title?: string };
  } | null>(null);

  useEffect(() => {
    projectsApi.list().then((r) => {
      setProjects(r.data);
      if (r.data.length > 0) setSelectedProject(r.data[0].id);
    });
  }, []);

  useEffect(() => {
    if (!selectedProject) return;
    analysisApi.getScreenshots(selectedProject).then((r) => setScreenshots(r.data)).catch(() => {});
  }, [selectedProject]);

  const analyze = async () => {
    if (!uploadedFileId) return;
    setAnalyzing(true);
    try {
      const { data } = await analysisApi.analyzeScreenshot({
        file_id: uploadedFileId,
        project_id: selectedProject || undefined,
        symbol, timeframe,
        user_notes: userNotes,
        ea_decision: eaDecision,
        chart_url: chartUrl || undefined,
      });
      setResult(data);
      setScreenshots((prev) => [data, ...prev]);
    } catch (e) {
      console.error(e);
    } finally {
      setAnalyzing(false);
    }
  };

  const compareTradingView = async () => {
    if (!eaDecision.trim()) return;
    setAnalyzing(true);
    try {
      const { data } = await tradingviewApi.mockCompare({
        symbol,
        timeframe,
        chart_url: chartUrl,
        ea_decision: eaDecision,
        ea_reasoning: "",
        notes: userNotes,
        project_id: selectedProject || undefined,
      });
      setTvCompare(data);
    } catch (e) {
      console.error(e);
    } finally {
      setAnalyzing(false);
    }
  };

  const biasColor = (bias?: string) => {
    if (bias === "bullish") return "text-green-400 bg-green-900/30";
    if (bias === "bearish") return "text-red-400 bg-red-900/30";
    return "text-yellow-400 bg-yellow-900/30";
  };

  const recColor = (rec?: string) => {
    if (rec === "trade") return "text-green-400 bg-green-900/30 border-green-900/50";
    if (rec === "avoid") return "text-red-400 bg-red-900/30 border-red-900/50";
    return "text-yellow-400 bg-yellow-900/30 border-yellow-900/50";
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Screenshot Analyzer</h1>
        <p className="text-sm text-gray-500 mt-0.5">Upload TradingView chart screenshots for AI SMC analysis</p>
      </div>

      <div className="grid grid-cols-3 gap-6">
        {/* Upload + Analysis Form */}
        <div className="col-span-2 space-y-4">
          <div className="card">
            <div className="section-title flex items-center gap-2">
              <Camera size={16} /> Upload Chart Screenshot
            </div>
            <div className="grid grid-cols-2 gap-4 mb-4">
              <div>
                <label className="label">Symbol</label>
                <input className="input" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
              </div>
              <div>
                <label className="label">Timeframe</label>
                <select className="select" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
                  {["M1","M5","M15","M30","H1","H4","D1"].map((tf) => <option key={tf}>{tf}</option>)}
                </select>
              </div>
              <div>
                <label className="label">Project</label>
                <select className="select" value={selectedProject} onChange={(e) => setSelectedProject(e.target.value)}>
                  <option value="">— No project —</option>
                  {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </div>
              <div>
                <label className="label">EA Decision (optional)</label>
                <input className="input" value={eaDecision} onChange={(e) => setEaDecision(e.target.value)}
                  placeholder="e.g. EA took buy trade at 2340" />
              </div>
              <div className="col-span-2">
                <label className="label">TradingView Chart URL (optional)</label>
                <input
                  className="input"
                  value={chartUrl}
                  onChange={(e) => setChartUrl(e.target.value)}
                  placeholder="https://www.tradingview.com/chart/..."
                />
              </div>
              <div className="col-span-2">
                <label className="label">Your Notes (optional)</label>
                <textarea className="input h-16 resize-none" value={userNotes}
                  onChange={(e) => setUserNotes(e.target.value)}
                  placeholder="Describe what you see, market context, etc." />
              </div>
            </div>
            <UploadZone
              fileType="screenshot"
              projectId={selectedProject || undefined}
              onSuccess={(id) => setUploadedFileId(id)}
              label="Drop TradingView chart screenshot"
              description="PNG, JPG or WebP — ideally 1920x1080 or higher"
            />
            <button
              onClick={analyze}
              disabled={!uploadedFileId || analyzing}
              className="btn-primary w-full mt-4 flex items-center justify-center gap-2"
            >
              {analyzing ? (
                <><Loader2 size={16} className="animate-spin" /> Analyzing with AI…</>
              ) : (
                <><ArrowUpCircle size={16} /> Analyze Chart</>
              )}
            </button>
            <button
              onClick={compareTradingView}
              disabled={!eaDecision.trim() || analyzing}
              className="btn-secondary w-full mt-2 flex items-center justify-center gap-2"
            >
              {analyzing ? <Loader2 size={16} className="animate-spin" /> : <ArrowUpCircle size={16} />}
              Compare EA vs Model (TradingView Context)
            </button>
          </div>

          {/* Analysis Result */}
          {result && (
            <div className="card">
              <div className="flex flex-wrap items-center gap-2 sm:gap-3 mb-4">
                <div className="font-semibold text-white w-full sm:w-auto shrink-0">
                  {result.symbol} {result.timeframe}
                </div>
                {result.detected_bias && (
                  <span className={cn("badge text-xs font-medium capitalize shrink-0", biasColor(result.detected_bias))}>
                    {result.detected_bias} bias
                  </span>
                )}
                {result.ea_recommendation && (
                  <span
                    className={cn(
                      "badge border text-xs font-medium uppercase shrink-0",
                      recColor(result.ea_recommendation)
                    )}
                  >
                    {result.ea_recommendation}
                  </span>
                )}
                {typeof result.confidence === "number" && (
                  <span className="badge bg-gray-800 text-gray-400 text-xs shrink-0">
                    {result.confidence.toFixed(0)}% confidence
                  </span>
                )}
              </div>

              {result.detected_structures && (
                <div className="flex flex-wrap gap-2 mb-4">
                  {Object.entries(result.detected_structures).filter(([, v]) => v).map(([key]) => (
                    <span key={key} className="badge bg-brand-900/30 text-brand-400 text-xs">
                      {key.replace(/_/g, " ")}
                    </span>
                  ))}
                </div>
              )}

              <div className="prose prose-invert prose-sm max-w-none text-sm text-gray-300 whitespace-pre-wrap leading-relaxed">
                {result.ai_structure_analysis}
              </div>

              {result.ai_vs_ea_comparison && (
                <div className="mt-4 pt-4 border-t border-gray-800">
                  <div className="text-sm font-semibold text-yellow-400 mb-2">EA Decision Assessment</div>
                  <div className="text-sm text-gray-300 whitespace-pre-wrap">{result.ai_vs_ea_comparison}</div>
                </div>
              )}
            </div>
          )}

          {tvCompare && (
            <div className="card">
              <div className="section-title">EA vs Model (TradingView-first Learning)</div>
              <div className="flex items-center gap-2 mb-3">
                <span className="badge bg-gray-800 text-gray-300">EA: {eaDecision || "N/A"}</span>
                <span className="badge bg-brand-900/30 text-brand-300">Model: {tvCompare.model_decision}</span>
                <span className="badge bg-yellow-900/30 text-yellow-300">Agreement: {tvCompare.agreement}</span>
                <span className="badge bg-blue-900/30 text-blue-300">{tvCompare.confidence?.toFixed?.(0) ?? 0}%</span>
              </div>
              <div className="text-sm text-gray-300 whitespace-pre-wrap">{tvCompare.model_reasoning}</div>
              {tvCompare.improvement_hint && (
                <div className="mt-3 text-sm text-green-300">
                  Improvement hint: {tvCompare.improvement_hint}
                </div>
              )}
              <div className="mt-2 text-xs text-gray-500 space-y-0.5">
                <div>Source: {tvCompare.source}</div>
                {typeof tvCompare.knowledge_loaded === "boolean" && (
                  <div>Word knowledge loaded: {tvCompare.knowledge_loaded ? "yes" : "no"}</div>
                )}
                {typeof tvCompare.mql5_excerpt_chars === "number" && tvCompare.mql5_excerpt_chars > 0 && (
                  <div>MQ5 excerpt: {tvCompare.mql5_excerpt_chars} chars</div>
                )}
                {tvCompare.tradingview_context?.normalized_symbol && (
                  <div>Symbol from URL: {tvCompare.tradingview_context.normalized_symbol}</div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* History */}
        <div>
          <div className="card">
            <div className="section-title text-sm">Analysis History</div>
            {screenshots.length === 0 ? (
              <div className="text-center py-8 text-gray-600 text-sm">No screenshots analyzed yet</div>
            ) : (
              <div className="space-y-2">
                {screenshots.slice(0, 20).map((s) => (
                  <button key={s.id} onClick={() => setSelected(s)}
                    className={cn("w-full text-left p-2 rounded-lg border transition-all",
                      selected?.id === s.id ? "border-brand-600/50 bg-brand-900/20" : "border-gray-800 hover:border-gray-700"
                    )}>
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-medium text-white">{s.symbol} {s.timeframe}</span>
                      {s.detected_bias && (
                        <span className={cn("text-xs", biasColor(s.detected_bias))}>{s.detected_bias}</span>
                      )}
                    </div>
                    <div className="flex items-center gap-2 mt-1">
                      {s.ea_recommendation && (
                        <span className={cn("badge text-xs", recColor(s.ea_recommendation))}>{s.ea_recommendation}</span>
                      )}
                      {s.confidence && <span className="text-xs text-gray-600">{s.confidence.toFixed(0)}%</span>}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
