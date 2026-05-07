export interface Project {
  id: string;
  name: string;
  description?: string;
  symbol?: string;
  timeframe?: string;
  baseline?: BaselineMetrics | null;
  best_confidence_score?: number | null;
  readiness_level?: ReadinessLevel;
}

export interface BaselineMetrics {
  net_profit?: number;
  profit_factor?: number;
  win_rate?: number;
  total_trades?: number;
  max_drawdown_pct?: number;
}

export interface BacktestReport {
  id: string;
  label: string;
  is_baseline: boolean;
  symbol?: string;
  timeframe?: string;
  net_profit?: number;
  profit_factor?: number;
  win_rate?: number;
  total_trades?: number;
  winning_trades?: number;
  losing_trades?: number;
  avg_win?: number;
  avg_loss?: number;
  expectancy?: number;
  max_drawdown_pct?: number;
  sharpe_ratio?: number;
  recovery_factor?: number;
  long_win_rate?: number;
  short_win_rate?: number;
  monthly_breakdown?: MonthlyData[];
  session_breakdown?: Record<string, SessionData>;
  day_of_week_breakdown?: Record<string, DayData>;
  failure_zones?: FailureZone[];
  ai_summary?: string;
  ai_failure_analysis?: string;
  created_at?: string;
}

export interface MonthlyData {
  _month?: string;
  month?: string;
  profit: number;
  trades: number;
  win_rate: number;
}

export interface SessionData {
  trades: number;
  profit: number;
  win_rate: number;
}

export interface DayData {
  trades: number;
  profit: number;
  win_rate: number;
}

export interface FailureZone {
  type: string;
  name: string;
  win_rate: number;
  severity: "low" | "medium" | "high";
}

export interface UploadedFile {
  id: string;
  file_name: string;
  file_type: FileType;
  file_size_bytes: number;
  processing_status: ProcessingStatus;
  project_id?: string;
  parsed_summary?: string;
  created_at?: string;
}

export interface ImprovementIdea {
  id: string;
  name: string;
  category?: string;
  affected_component?: string;
  overfit_risk?: "low" | "medium" | "high";
  status: IdeaStatus;
  ai_generated: boolean;
  logic_explanation?: string;
  smc_reasoning?: string;
  expected_benefit?: string;
  expected_risk?: string;
  parameters_changed?: string[];
  pine_script_impact?: string;
  mql5_patch_suggestion?: string;
  user_notes?: string;
  created_at?: string;
}

export interface StrategyVersion {
  id: string;
  version_number: string;
  label?: string;
  description?: string;
  is_baseline: boolean;
  approval_status: ApprovalStatus;
  confidence_score?: number | null;
  readiness_level?: ReadinessLevel;
  created_at?: string;
}

export interface BacktestComparison {
  id: string;
  verdict: Verdict;
  profit_delta?: number;
  profit_factor_delta?: number;
  win_rate_delta?: number;
  drawdown_delta?: number;
  trade_count_delta?: number;
  overfit_detected?: boolean;
  overfit_reasons?: string[];
  is_statistically_significant?: boolean;
  ai_comparison_summary?: string;
}

export interface ConfidenceScore {
  id: string;
  overall_score: number;
  readiness_level: ReadinessLevel;
  breakdown?: Record<string, number>;
  ai_notes?: string;
  version_id?: string;
}

export interface BacktestKnowledgeLatest {
  strategy_version?: string;
  symbol?: string;
  backtest_date_range?: { start?: string; end?: string };
  total_trades?: number;
  win_rate?: number;
  profit_factor?: number;
  max_drawdown?: number;
  final_balance?: number;
  best_decision_type?: string;
  worst_decision_type?: string;
  best_ob_timeframe?: string;
  worst_ob_timeframe?: string;
  average_mfe_r?: number;
  average_mae_r?: number;
  recommendation_for_forward_demo_testing?: string;
  expected_by_group?: Record<
    string,
    {
      total_trades?: number;
      win_rate?: number;
      profit_factor?: number;
      average_r?: number;
      average_mfe_r?: number;
      average_mae_r?: number;
      wins?: number;
      losses?: number;
    }
  >;
  generated_at?: string;
}

export interface ScreenshotAnalysis {
  id: string;
  symbol?: string;
  timeframe?: string;
  detected_bias?: "bullish" | "bearish" | "neutral";
  ea_recommendation?: "trade" | "wait" | "avoid";
  confidence?: number;
  ai_structure_analysis?: string;
  detected_structures?: Record<string, boolean>;
  ai_vs_ea_comparison?: string;
  created_at?: string;
}

export interface MT5Position {
  ticket: number;
  symbol: string;
  type: string;
  volume: number;
  open_price: number;
  close_price?: number;
  sl: number;
  tp: number;
  profit: number;
  open_time: string;
}

export interface TradeDecision {
  id: string;
  decision_type: "trade" | "skip" | "wait" | "block_risk" | "kill_switch";
  symbol?: string;
  direction?: string;
  entry_price?: number;
  reason?: string;
  executed: boolean;
  requires_approval: boolean;
  approved?: boolean;
  risk_reward?: number;
  decision_time?: string;
}

export interface RiskSettings {
  project_id: string;
  enable_live_trading: boolean;
  max_daily_loss_usd: number;
  max_weekly_loss_usd: number;
  max_drawdown_percent: number;
  max_lot_size: number;
  max_trades_per_day: number;
  max_open_trades: number;
  max_consecutive_losses: number;
  spread_filter_pips: number;
  symbol_whitelist?: string[];
  session_whitelist?: string[];
  kill_switch_active: boolean;
  kill_switch_reason?: string;
}

export interface PineAnalysis {
  id: string;
  summary: string;
  detected_smc_concepts: string[];
  entry_conditions: string[];
  exit_conditions: string[];
  session_filters: string[];
  risk_logic: Record<string, unknown>;
  ai_analysis: string;
}

export interface MQL5Analysis {
  id: string;
  summary: string;
  detected_smc_concepts: string[];
  input_parameters: { name: string; default: string }[];
  entry_logic: string;
  exit_logic: string;
  sl_tp_logic: string;
  pine_vs_ea_diff: string;
  ai_analysis: string;
}

export type FileType =
  | "pine_script"
  | "mql5"
  | "backtest_report"
  | "mt5_log"
  | "screenshot"
  | "csv"
  | "trade_history"
  | "notes";

export type ProcessingStatus = "pending" | "processing" | "done" | "failed";

export type IdeaStatus = "pending" | "accepted" | "rejected" | "tested" | "deployed";

export type ApprovalStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "demo_testing"
  | "live_ready";

export type ReadinessLevel =
  | "research"
  | "demo_candidate"
  | "demo_testing"
  | "live_candidate"
  | "live_ready";

export type Verdict = "improvement" | "regression" | "neutral" | "overfit";
