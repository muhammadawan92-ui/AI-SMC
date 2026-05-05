"use client";

import { useEffect, useState } from "react";
import { Settings, Shield, Zap, BookOpen, Save, AlertTriangle } from "lucide-react";
import { settingsApi, projectsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Project, RiskSettings } from "@/types";

export default function SettingsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState("");
  const [globalSettings, setGlobalSettings] = useState<Record<string, unknown>>({});
  const [riskSettings, setRiskSettings] = useState<RiskSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [tab, setTab] = useState<"global" | "risk" | "smc">("global");
  const [smcKnowledge, setSmcKnowledge] = useState<Record<string, { name: string; description: string }>>({});
  const [selectedConcept, setSelectedConcept] = useState("");
  const [conceptDetail, setConceptDetail] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    Promise.all([
      projectsApi.list(),
      settingsApi.get(),
      settingsApi.getSmcKnowledge(),
    ]).then(([p, g, smc]) => {
      setProjects(p.data);
      setGlobalSettings(g.data);
      setSmcKnowledge(smc.data);
      if (p.data.length > 0) setSelectedProject(p.data[0].id);
    });
  }, []);

  useEffect(() => {
    if (!selectedProject) return;
    settingsApi.getRisk(selectedProject).then((r) => setRiskSettings(r.data)).catch(() => {});
  }, [selectedProject]);

  const saveRiskSettings = async () => {
    if (!selectedProject || !riskSettings) return;
    setSaving(true);
    try {
      await settingsApi.updateRisk(selectedProject, riskSettings);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      alert(msg || "Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  const loadConcept = async (concept: string) => {
    setSelectedConcept(concept);
    const { data } = await settingsApi.getConcept(concept);
    setConceptDetail(data);
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Settings</h1>
        <p className="text-sm text-gray-500 mt-0.5">Configure LLM, risk controls, and platform settings</p>
      </div>

      <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-xl p-1 w-fit">
        {(["global", "risk", "smc"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={cn("px-4 py-1.5 rounded-lg text-sm font-medium transition-all capitalize",
              tab === t ? "bg-brand-600 text-white" : "text-gray-400 hover:text-gray-200"
            )}>
            {t === "global" ? "Global" : t === "risk" ? "Risk Controls" : "SMC Knowledge"}
          </button>
        ))}
      </div>

      {tab === "global" && (
        <div className="space-y-4">
          <div className="card">
            <div className="section-title flex items-center gap-2"><Zap size={16} className="text-brand-400" /> LLM Configuration</div>
            <div className="grid grid-cols-2 gap-4">
              {[
                { label: "LLM Provider", key: "llm_provider" },
                { label: "Model", key: "llm_model" },
              ].map(({ label, key }) => (
                <div key={key}>
                  <label className="label">{label}</label>
                  <div className="input bg-gray-800 text-gray-400 cursor-default">
                    {String(globalSettings[key] ?? "—")}
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-3 p-3 bg-blue-900/10 border border-blue-900/30 rounded-lg">
              <p className="text-xs text-blue-300">
                Configure LLM in <code className="bg-gray-800 px-1 rounded">backend/.env</code> or OS environment variables{" "}
                (<code className="bg-gray-800 px-1 rounded">LLM_PROVIDER</code>: openai, anthropic, openai_compatible, ollama, or gemini; Gemini requires{" "}
                <code className="bg-gray-800 px-1 rounded">GEMINI_API_KEY</code> and <code className="bg-gray-800 px-1 rounded">GEMINI_MODEL</code> on the server only — never in the frontend).
                Restart the backend after changes.
              </p>
            </div>
          </div>

          <div className="card">
            <div className="section-title flex items-center gap-2"><Settings size={16} className="text-gray-400" /> System Status</div>
            <div className="grid grid-cols-3 gap-4">
              {[
                { label: "Mock Mode", value: String(globalSettings.mock_mode ?? false), ok: !globalSettings.mock_mode },
                { label: "Live Trading", value: globalSettings.live_trading_enabled ? "ENABLED" : "Locked", ok: !globalSettings.live_trading_enabled },
                { label: "Max Daily Loss", value: `$${globalSettings.max_daily_loss_usd ?? "—"}` },
              ].map(({ label, value, ok }) => (
                <div key={label} className="card-sm">
                  <div className="metric-label">{label}</div>
                  <div className={cn("font-semibold mt-1", ok === false ? "text-red-400" : ok === true ? "text-green-400" : "text-white")}>
                    {value}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {tab === "risk" && riskSettings && (
        <div className="space-y-4">
          <div className="p-4 bg-red-950/30 border border-red-900/40 rounded-xl flex items-start gap-3">
            <AlertTriangle size={20} className="text-red-400 flex-shrink-0 mt-0.5" />
            <div className="text-sm text-red-300">
              <strong>Live trading is permanently locked by default.</strong> To enable it, you must first set{" "}
              <code className="bg-gray-800 px-1 rounded text-xs">ENABLE_LIVE_TRADING=true</code> in your <code className="bg-gray-800 px-1 rounded text-xs">.env</code>{" "}
              file AND complete a full demo validation phase. Never enable in production without extensive testing.
            </div>
          </div>

          <div className="card">
            <div className="section-title">Project Risk Settings</div>
            <div className="mb-4">
              <label className="label">Project</label>
              <select className="select w-56" value={selectedProject} onChange={(e) => setSelectedProject(e.target.value)}>
                {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-4">
              {[
                { label: "Max Daily Loss (USD)", key: "max_daily_loss_usd", type: "number" },
                { label: "Max Weekly Loss (USD)", key: "max_weekly_loss_usd", type: "number" },
                { label: "Max Drawdown (%)", key: "max_drawdown_percent", type: "number" },
                { label: "Max Lot Size", key: "max_lot_size", type: "number" },
                { label: "Max Trades / Day", key: "max_trades_per_day", type: "number" },
                { label: "Max Open Trades", key: "max_open_trades", type: "number" },
                { label: "Max Consecutive Losses", key: "max_consecutive_losses", type: "number" },
                { label: "Spread Filter (Pips)", key: "spread_filter_pips", type: "number" },
              ].map(({ label, key, type }) => (
                <div key={key}>
                  <label className="label">{label}</label>
                  <input
                    className="input"
                    type={type}
                    step="any"
                    value={String(riskSettings[key as keyof RiskSettings] ?? "")}
                    onChange={(e) => setRiskSettings((prev) => prev ? { ...prev, [key]: Number(e.target.value) } : prev)}
                  />
                </div>
              ))}
            </div>
            {riskSettings.kill_switch_active && (
              <div className="mt-4 p-3 bg-red-900/20 border border-red-900/50 rounded-lg">
                <div className="text-sm font-medium text-red-400 flex items-center gap-2">
                  <Shield size={14} /> Kill Switch Active
                </div>
                <div className="text-xs text-red-300 mt-1">{riskSettings.kill_switch_reason}</div>
              </div>
            )}
            <div className="flex items-center justify-end mt-6">
              <button onClick={saveRiskSettings} disabled={saving} className="btn-primary flex items-center gap-2">
                <Save size={14} />
                {saving ? "Saving…" : saved ? "Saved!" : "Save Risk Settings"}
              </button>
            </div>
          </div>
        </div>
      )}

      {tab === "smc" && (
        <div className="grid grid-cols-3 gap-4">
          <div className="card-sm">
            <div className="section-title text-sm">SMC Concepts</div>
            <div className="space-y-1">
              {Object.entries(smcKnowledge).map(([key, data]) => (
                <button key={key} onClick={() => loadConcept(key)}
                  className={cn("w-full text-left px-2 py-1.5 rounded text-sm transition-all",
                    selectedConcept === key ? "bg-brand-900/30 text-brand-400" : "text-gray-400 hover:text-gray-200 hover:bg-gray-800"
                  )}>
                  {data.name}
                </button>
              ))}
            </div>
          </div>
          <div className="col-span-2">
            {conceptDetail ? (
              <div className="card space-y-4">
                <h2 className="text-xl font-bold text-white">{String(conceptDetail.name)}</h2>
                <p className="text-sm text-gray-300">{String(conceptDetail.description)}</p>
                {Object.entries(conceptDetail).filter(([k]) => !["name", "description"].includes(k)).map(([k, v]) => (
                  <div key={k}>
                    <div className="text-xs text-gray-500 uppercase tracking-wider font-medium mb-1">{k.replace(/_/g, " ")}</div>
                    {typeof v === "string" ? (
                      <p className="text-sm text-gray-300">{v}</p>
                    ) : Array.isArray(v) ? (
                      <ul className="space-y-1">
                        {v.map((item, i) => <li key={i} className="text-sm text-gray-400">• {item}</li>)}
                      </ul>
                    ) : typeof v === "object" && v !== null ? (
                      <div className="code-block text-xs">{JSON.stringify(v, null, 2)}</div>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : (
              <div className="card text-center py-16 text-gray-600">
                <BookOpen size={32} className="mx-auto mb-3" />
                Select a concept to view details
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
