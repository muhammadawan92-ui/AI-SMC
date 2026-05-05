"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp, Code, CheckCircle, XCircle, Clock } from "lucide-react";
import { cn, overfitColor } from "@/lib/utils";
import type { ImprovementIdea, IdeaStatus } from "@/types";
import { improvementsApi } from "@/lib/api";

interface ImprovementCardProps {
  idea: ImprovementIdea;
  onStatusChange?: (id: string, status: IdeaStatus) => void;
}

const STATUS_ICONS: Record<IdeaStatus, React.ReactNode> = {
  pending: <Clock size={14} className="text-yellow-400" />,
  accepted: <CheckCircle size={14} className="text-green-400" />,
  rejected: <XCircle size={14} className="text-red-400" />,
  tested: <CheckCircle size={14} className="text-blue-400" />,
  deployed: <CheckCircle size={14} className="text-emerald-400" />,
};

const STATUS_COLORS: Record<IdeaStatus, string> = {
  pending: "text-yellow-400 bg-yellow-900/30 border-yellow-900/50",
  accepted: "text-green-400 bg-green-900/30 border-green-900/50",
  rejected: "text-red-400 bg-red-900/30 border-red-900/50",
  tested: "text-blue-400 bg-blue-900/30 border-blue-900/50",
  deployed: "text-emerald-400 bg-emerald-900/30 border-emerald-900/50",
};

export function ImprovementCard({ idea, onStatusChange }: ImprovementCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);

  const updateStatus = async (status: IdeaStatus) => {
    setLoading(true);
    try {
      await improvementsApi.update(idea.id, { status });
      onStatusChange?.(idea.id, status);
    } catch {
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="card-sm border border-gray-800 rounded-lg overflow-hidden">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="font-semibold text-white text-sm">{idea.name}</h3>
            {idea.category && (
              <span className="badge bg-gray-800 text-gray-400 text-xs">{idea.category}</span>
            )}
            {idea.affected_component && (
              <span className="badge bg-brand-900/30 text-brand-400 border border-brand-900/50 text-xs">
                {idea.affected_component}
              </span>
            )}
            {idea.overfit_risk && (
              <span className={cn("badge border text-xs", overfitColor(idea.overfit_risk))}>
                overfit: {idea.overfit_risk}
              </span>
            )}
          </div>
          {idea.logic_explanation && (
            <p className="text-xs text-gray-400 mt-1 line-clamp-2">{idea.logic_explanation}</p>
          )}
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <span className={cn("badge border text-xs flex items-center gap-1", STATUS_COLORS[idea.status])}>
            {STATUS_ICONS[idea.status]}
            {idea.status}
          </span>
          <button onClick={() => setExpanded(!expanded)} className="text-gray-500 hover:text-gray-300 p-1">
            {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </button>
        </div>
      </div>

      {expanded && (
        <div className="mt-4 pt-4 border-t border-gray-800 space-y-4">
          {idea.smc_reasoning && (
            <div>
              <div className="label">SMC Reasoning</div>
              <p className="text-sm text-gray-300">{idea.smc_reasoning}</p>
            </div>
          )}
          <div className="grid grid-cols-2 gap-4">
            {idea.expected_benefit && (
              <div>
                <div className="label text-green-600">Expected Benefit</div>
                <p className="text-sm text-green-400">{idea.expected_benefit}</p>
              </div>
            )}
            {idea.expected_risk && (
              <div>
                <div className="label text-red-600">Expected Risk</div>
                <p className="text-sm text-red-400">{idea.expected_risk}</p>
              </div>
            )}
          </div>
          {idea.parameters_changed && idea.parameters_changed.length > 0 && (
            <div>
              <div className="label">Parameters Changed</div>
              <div className="flex flex-wrap gap-1">
                {idea.parameters_changed.map((p) => (
                  <code key={p} className="text-xs bg-gray-800 text-gray-300 px-2 py-0.5 rounded font-mono">{p}</code>
                ))}
              </div>
            </div>
          )}
          {idea.mql5_patch_suggestion && (
            <div>
              <div className="label flex items-center gap-1">
                <Code size={12} /> MQL5 Patch Suggestion
              </div>
              <pre className="code-block text-xs whitespace-pre-wrap max-h-48 overflow-auto">
                {idea.mql5_patch_suggestion}
              </pre>
            </div>
          )}
          {idea.status === "pending" && (
            <div className="flex gap-2 pt-2">
              <button
                onClick={() => updateStatus("accepted")}
                disabled={loading}
                className="btn-success text-xs py-1.5"
              >
                Accept
              </button>
              <button
                onClick={() => updateStatus("rejected")}
                disabled={loading}
                className="btn-danger text-xs py-1.5"
              >
                Reject
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
