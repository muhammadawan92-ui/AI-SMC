"use client";

import { useEffect, useState } from "react";
import { RefreshCw, Upload, Loader2, Shield, Zap } from "lucide-react";
import { mt5Api, projectsApi } from "@/lib/api";
import { cn, fmtUsd, profitColor } from "@/lib/utils";
import type { Project, MT5Position, TradeDecision } from "@/types";

interface AccountInfo {
  balance?: number;
  equity?: number;
  profit?: number;
  currency?: string;
  server?: string;
  mock?: boolean;
  mock_source?: string;
  mock_symbol?: string;
}

export default function MT5Page() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState("");
  const [connected, setConnected] = useState(false);
  const [isMockMode, setIsMockMode] = useState(false);
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [positions, setPositions] = useState<MT5Position[]>([]);
  const [history, setHistory] = useState<MT5Position[]>([]);
  const [logs, setLogs] = useState<{ id: string; level: string; message: string; source: string }[]>([]);
  const [decisions, setDecisions] = useState<TradeDecision[]>([]);
  const [tab, setTab] = useState<"positions" | "history" | "logs" | "decisions">("positions");
  const [loading, setLoading] = useState(false);
  const [killSwitchLoading, setKillSwitchLoading] = useState(false);

  useEffect(() => {
    projectsApi.list().then((r) => {
      setProjects(r.data);
      if (r.data.length > 0) setSelectedProject(r.data[0].id);
    });
    loadMT5Status();
  }, []);

  const loadMT5Status = async () => {
    setLoading(true);
    try {
      const [status, pos, hist] = await Promise.all([
        mt5Api.status(),
        mt5Api.positions(),
        mt5Api.history(30),
      ]);
      setConnected(status.data.connected);
      setIsMockMode(Boolean(status.data.mock_mode));
      setAccount(status.data.account);
      setPositions(pos.data);
      setHistory(hist.data);
    } catch {} finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!selectedProject) return;
    Promise.all([
      mt5Api.logs(selectedProject, 100),
      mt5Api.decisions(selectedProject),
    ]).then(([l, d]) => {
      setLogs(l.data);
      setDecisions(d.data);
    }).catch(() => {});
  }, [selectedProject]);

  const connect = async () => {
    setLoading(true);
    try {
      const { data } = await mt5Api.connect();
      setConnected(data.success);
      if (data.success) await loadMT5Status();
    } catch {} finally {
      setLoading(false);
    }
  };

  const triggerKillSwitch = async () => {
    if (!selectedProject || !confirm("Activate kill switch? This will block all trading for this project.")) return;
    setKillSwitchLoading(true);
    try {
      await mt5Api.killSwitch(selectedProject, "User-activated kill switch");
    } catch {} finally {
      setKillSwitchLoading(false);
    }
  };

  const uploadLog = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    if (selectedProject) fd.append("project_id", selectedProject);
    await mt5Api.uploadLog(fd);
    if (selectedProject) {
      const { data } = await mt5Api.logs(selectedProject, 100);
      setLogs(data);
    }
  };

  const tabs = [
    { id: "positions", label: `Positions (${positions.length})` },
    { id: "history", label: `History (${history.length})` },
    { id: "logs", label: `Logs (${logs.length})` },
    { id: "decisions", label: `Decisions (${decisions.length})` },
  ] as const;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">MT5 Monitor</h1>
          <p className="text-sm text-gray-500 mt-0.5">Monitor MetaTrader 5 positions, history and EA decisions</p>
        </div>
        <div className="flex items-center gap-3">
          <select className="select w-40" value={selectedProject} onChange={(e) => setSelectedProject(e.target.value)}>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <button onClick={loadMT5Status} className="btn-secondary flex items-center gap-2">
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh
          </button>
        </div>
      </div>

      {isMockMode && (
        <div className="card-sm border border-yellow-800/50 bg-yellow-900/10">
          <div className="text-sm font-medium text-yellow-400">Mock Data Mode Active</div>
          <div className="text-xs text-yellow-300 mt-1">
            Values shown here are synthetic placeholders from backend mock mode, not your real MT5 account.
            {account?.mock_symbol ? ` Current mock symbol: ${account.mock_symbol}.` : ""}
          </div>
          {account?.mock_source && (
            <div className="text-xs text-yellow-500 mt-1">{account.mock_source}</div>
          )}
        </div>
      )}

      {/* Account Status */}
      <div className="grid grid-cols-4 gap-4">
        <div className={cn("card-sm border", connected ? "border-green-800/50 bg-green-900/10" : "border-gray-700")}>
          <div className="metric-label">Connection</div>
          <div className={cn("metric-value text-xl mt-1", connected ? "text-green-400" : "text-gray-500")}>
            {connected ? "Connected" : "Disconnected"}
          </div>
          {isMockMode && <div className="text-xs text-yellow-500 mt-1">Mock Mode</div>}
          {!connected && (
            <button onClick={connect} disabled={loading} className="btn-primary text-xs mt-2 w-full">
              {loading ? "Connecting…" : "Connect MT5"}
            </button>
          )}
        </div>
        <div className="card-sm">
          <div className="metric-label">Balance</div>
          <div className="metric-value text-xl mt-1">{account?.balance != null ? `$${account.balance.toFixed(2)}` : "—"}</div>
        </div>
        <div className="card-sm">
          <div className="metric-label">Equity</div>
          <div className="metric-value text-xl mt-1">{account?.equity != null ? `$${account.equity.toFixed(2)}` : "—"}</div>
        </div>
        <div className="card-sm">
          <div className="metric-label">Float P&L</div>
          <div className={cn("metric-value text-xl mt-1", profitColor(account?.profit))}>
            {account?.profit != null ? fmtUsd(account.profit) : "—"}
          </div>
        </div>
      </div>

      {/* Safety Controls */}
      <div className="card border-red-900/30 bg-red-950/10">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Shield size={20} className="text-red-400" />
            <div>
              <div className="font-semibold text-white">Safety Controls</div>
              <div className="text-xs text-gray-500">Live trading: DISABLED by default. Requires ENABLE_LIVE_TRADING=true in .env</div>
            </div>
          </div>
          <button
            onClick={triggerKillSwitch}
            disabled={killSwitchLoading}
            className="btn-danger flex items-center gap-2"
          >
            {killSwitchLoading ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
            Kill Switch
          </button>
        </div>
      </div>

      {/* Log Upload */}
      <div className="card-sm flex items-center gap-4">
        <Upload size={16} className="text-gray-500" />
        <div className="flex-1">
          <div className="text-sm text-gray-300">Upload MT5 Expert Log file</div>
          <div className="text-xs text-gray-600">Parses log entries into structured format</div>
        </div>
        <label className="btn-secondary text-xs cursor-pointer">
          Choose .log file
          <input type="file" accept=".log,.txt" className="hidden" onChange={uploadLog} />
        </label>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-xl p-1 w-fit">
        {tabs.map((t) => (
          <button key={t.id} onClick={() => setTab(t.id as typeof tab)}
            className={cn("px-4 py-1.5 rounded-lg text-sm font-medium transition-all",
              tab === t.id ? "bg-brand-600 text-white" : "text-gray-400 hover:text-gray-200"
            )}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === "positions" && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800">
                {["Ticket", "Symbol", "Type", "Volume", "Open Price", "SL", "TP", "Profit", "Open Time"].map((h) => (
                  <th key={h} className="text-left py-2 px-3 text-gray-500 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {positions.length === 0 ? (
                <tr><td colSpan={9} className="py-8 text-center text-gray-600">No open positions</td></tr>
              ) : positions.map((p, i) => (
                <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                  <td className="py-2 px-3 text-gray-400 font-mono text-xs">{p.ticket}</td>
                  <td className="py-2 px-3 font-medium text-white">{p.symbol}</td>
                  <td className="py-2 px-3"><span className={cn("badge text-xs", p.type === "buy" ? "bg-green-900/30 text-green-400" : "bg-red-900/30 text-red-400")}>{p.type}</span></td>
                  <td className="py-2 px-3 text-gray-400">{p.volume}</td>
                  <td className="py-2 px-3 text-gray-300">{p.open_price}</td>
                  <td className="py-2 px-3 text-red-400">{p.sl}</td>
                  <td className="py-2 px-3 text-green-400">{p.tp}</td>
                  <td className={cn("py-2 px-3 font-medium", profitColor(p.profit))}>{fmtUsd(p.profit)}</td>
                  <td className="py-2 px-3 text-gray-500 text-xs">{p.open_time}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "history" && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800">
                {["Ticket", "Symbol", "Type", "Volume", "Open", "Close", "Profit", "Open Time"].map((h) => (
                  <th key={h} className="text-left py-2 px-3 text-gray-500 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {history.length === 0 ? (
                <tr><td colSpan={8} className="py-8 text-center text-gray-600">No closed positions (last 30 days)</td></tr>
              ) : history.slice(0, 100).map((p, i) => (
                <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                  <td className="py-2 px-3 text-gray-400 font-mono text-xs">{p.ticket}</td>
                  <td className="py-2 px-3 font-medium text-white">{p.symbol}</td>
                  <td className="py-2 px-3"><span className={cn("badge text-xs", p.type === "buy" ? "bg-green-900/30 text-green-400" : "bg-red-900/30 text-red-400")}>{p.type}</span></td>
                  <td className="py-2 px-3 text-gray-400">{p.volume}</td>
                  <td className="py-2 px-3 text-gray-300">{p.open_price ?? "—"}</td>
                  <td className="py-2 px-3 text-gray-300">{p.close_price ?? "—"}</td>
                  <td className={cn("py-2 px-3 font-medium", profitColor(p.profit))}>{fmtUsd(p.profit)}</td>
                  <td className="py-2 px-3 text-gray-500 text-xs">{p.open_time}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "logs" && (
        <div className="card">
          <div className="space-y-1 max-h-[600px] overflow-y-auto font-mono text-xs">
            {logs.length === 0 ? (
              <div className="text-center py-8 text-gray-600">No logs. Upload an MT5 expert log file.</div>
            ) : logs.map((l) => (
              <div key={l.id} className={cn("flex gap-3 py-1 px-2 rounded",
                l.level?.toLowerCase().includes("error") ? "bg-red-900/10 text-red-300" : "text-gray-400 hover:bg-gray-800/30"
              )}>
                <span className={cn("flex-shrink-0 w-16 text-xs", l.level === "error" ? "text-red-400" : l.level === "warning" ? "text-yellow-400" : "text-gray-600")}>
                  {l.level}
                </span>
                <span className="flex-1 break-all">{l.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "decisions" && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800">
                {["Time", "Decision", "Symbol", "Direction", "Entry", "R:R", "Reason", "Executed"].map((h) => (
                  <th key={h} className="text-left py-2 px-3 text-gray-500 font-medium text-xs">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {decisions.length === 0 ? (
                <tr><td colSpan={8} className="py-8 text-center text-gray-600">No trade decisions recorded</td></tr>
              ) : decisions.map((d) => (
                <tr key={d.id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                  <td className="py-2 px-3 text-gray-500 text-xs whitespace-nowrap">{d.decision_time?.slice(0, 16) ?? "—"}</td>
                  <td className="py-2 px-3">
                    <span className={cn("badge text-xs",
                      d.decision_type === "trade" ? "bg-green-900/30 text-green-400" :
                      d.decision_type === "block_risk" ? "bg-red-900/30 text-red-400" :
                      "bg-gray-800 text-gray-400"
                    )}>{d.decision_type}</span>
                  </td>
                  <td className="py-2 px-3 text-white font-medium">{d.symbol ?? "—"}</td>
                  <td className="py-2 px-3">{d.direction ? <span className={cn("badge text-xs", d.direction === "buy" ? "bg-green-900/30 text-green-400" : "bg-red-900/30 text-red-400")}>{d.direction}</span> : "—"}</td>
                  <td className="py-2 px-3 text-gray-300">{d.entry_price ?? "—"}</td>
                  <td className="py-2 px-3 text-gray-400">{d.risk_reward ? `${d.risk_reward.toFixed(2)}R` : "—"}</td>
                  <td className="py-2 px-3 text-gray-500 text-xs max-w-xs truncate">{d.reason}</td>
                  <td className="py-2 px-3">{d.executed ? <span className="text-green-400">✓</span> : <span className="text-gray-600">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
