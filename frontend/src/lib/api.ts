import axios from "axios";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const api = axios.create({
  baseURL: `${API_BASE}/api/v1`,
  timeout: 60000,
});

// ── Projects ──────────────────────────────────────────────────────────────
export const projectsApi = {
  list: () => api.get("/projects/"),
  create: (data: { name: string; symbol?: string; timeframe?: string; description?: string }) =>
    api.post("/projects/", data),
  get: (id: string) => api.get(`/projects/${id}`),
};

// ── Uploads ───────────────────────────────────────────────────────────────
export const uploadsApi = {
  upload: (formData: FormData) =>
    api.post("/uploads/", formData, { headers: { "Content-Type": "multipart/form-data" } }),
  list: (projectId?: string) =>
    api.get("/uploads/", { params: projectId ? { project_id: projectId } : {} }),
  get: (id: string) => api.get(`/uploads/${id}`),
  delete: (id: string) => api.delete(`/uploads/${id}`),
};

// ── Analysis ──────────────────────────────────────────────────────────────
export const analysisApi = {
  analyzePine: (data: { file_id: string; project_id: string; run_llm?: boolean }) =>
    api.post("/analysis/pine", data),
  analyzeMql5: (data: { file_id: string; project_id: string; run_llm?: boolean }) =>
    api.post("/analysis/mql5", data),
  analyzeBacktest: (data: {
    file_id: string;
    project_id: string;
    label?: string;
    is_baseline?: boolean;
    run_llm?: boolean;
  }) => api.post("/analysis/backtest", data),
  analyzeScreenshot: (data: {
    file_id: string;
    project_id?: string;
    symbol?: string;
    timeframe?: string;
    user_notes?: string;
    ea_decision?: string;
    chart_url?: string;
  }) => api.post("/analysis/screenshot", data),
  getBacktests: (projectId: string) => api.get(`/analysis/backtest/${projectId}`),
  getBacktestDetail: (reportId: string) => api.get(`/analysis/backtest/detail/${reportId}`),
  getPineSources: (projectId: string) => api.get(`/analysis/pine/${projectId}`),
  getMql5Sources: (projectId: string) => api.get(`/analysis/mql5/${projectId}`),
  getScreenshots: (projectId: string) => api.get(`/analysis/screenshots/${projectId}`),
  generateBaselineReport: (projectId: string) =>
    api.post(`/analysis/report/baseline/${projectId}`),
};

// ── Improvements ──────────────────────────────────────────────────────────
export const improvementsApi = {
  generate: (data: { project_id: string; backtest_report_id: string; n_ideas?: number }) =>
    api.post("/improvements/generate", data),
  list: (projectId: string, status?: string) =>
    api.get(`/improvements/${projectId}`, { params: status ? { status } : {} }),
  update: (ideaId: string, data: { status?: string; user_notes?: string }) =>
    api.patch(`/improvements/${ideaId}`, data),
  getDetail: (ideaId: string) => api.get(`/improvements/detail/${ideaId}`),
  generatePatch: (ideaId: string) => api.post("/improvements/patch", { idea_id: ideaId }),
};

// ── Versions ──────────────────────────────────────────────────────────────
export const versionsApi = {
  create: (data: {
    project_id: string;
    version_number: string;
    label?: string;
    description?: string;
    mql5_code?: string;
    input_parameters?: Record<string, unknown>;
    improvement_ids?: string[];
    is_baseline?: boolean;
  }) => api.post("/versions/", data),
  list: (projectId: string) => api.get(`/versions/${projectId}`),
  approve: (versionId: string, approvedBy?: string) =>
    api.post(`/versions/${versionId}/approve`, null, { params: { approved_by: approvedBy || "user" } }),
  reject: (versionId: string, reason?: string) =>
    api.post(`/versions/${versionId}/reject`, null, { params: { reason: reason || "" } }),
  compare: (data: {
    project_id: string;
    baseline_report_id: string;
    improved_report_id: string;
    version_id?: string;
  }) => api.post("/versions/compare", data),
  score: (data: {
    project_id: string;
    baseline_report_id: string;
    improved_report_id: string;
    comparison_id?: string;
    version_id?: string;
    screenshot_validation_score?: number;
    smc_consistency_score?: number;
  }) => api.post("/versions/score", data),
  getScores: (projectId: string) => api.get(`/versions/scores/${projectId}`),
};

// ── MT5 ───────────────────────────────────────────────────────────────────
export const mt5Api = {
  status: () => api.get("/mt5/status"),
  connect: () => api.post("/mt5/connect"),
  positions: () => api.get("/mt5/positions"),
  history: (days?: number) => api.get("/mt5/history", { params: { days: days || 30 } }),
  logs: (projectId: string, limit?: number) =>
    api.get(`/mt5/logs/${projectId}`, { params: { limit: limit || 100 } }),
  decisions: (projectId: string) => api.get(`/mt5/decisions/${projectId}`),
  killSwitch: (projectId: string, reason?: string) =>
    api.post(`/mt5/kill-switch/${projectId}`, null, { params: { reason } }),
  uploadLog: (formData: FormData) =>
    api.post("/mt5/upload-log", formData, { headers: { "Content-Type": "multipart/form-data" } }),
};

// ── Settings ──────────────────────────────────────────────────────────────
export const settingsApi = {
  get: () => api.get("/settings/"),
  getRisk: (projectId: string) => api.get(`/settings/risk/${projectId}`),
  updateRisk: (projectId: string, data: Partial<import("@/types").RiskSettings>) =>
    api.put(`/settings/risk/${projectId}`, data),
  getSmcKnowledge: () => api.get("/settings/smc-knowledge"),
  getConcept: (concept: string) => api.get(`/settings/smc-knowledge/${concept}`),
  getCategories: () => api.get("/settings/improvement-categories"),
};

// ── TradingView Learning ───────────────────────────────────────────────────
export const tradingviewApi = {
  mockCompare: (data: {
    symbol?: string;
    timeframe?: string;
    chart_url?: string;
    ea_decision: string;
    ea_reasoning?: string;
    notes?: string;
    project_id?: string;
  }) => api.post("/tradingview/mock-compare", data),
};

// ── Health ────────────────────────────────────────────────────────────────
export const healthApi = {
  check: () => axios.get(`${API_BASE}/health`),
};

// ── Forward Validation ─────────────────────────────────────────────────────
export const forwardValidationApi = {
  latest: () => api.get("/forward-validation/latest"),
  runCompare: (data?: { symbol?: string; strategy_version?: string }) =>
    api.post("/forward-validation/run-compare", data || {}),
};
