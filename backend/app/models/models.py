from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# StrategyProject — top-level container for an EA project
# ---------------------------------------------------------------------------
class StrategyProject(TimestampMixin, Base):
    __tablename__ = "strategy_projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    symbol: Mapped[Optional[str]] = mapped_column(String(20))
    timeframe: Mapped[Optional[str]] = mapped_column(String(10))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    uploaded_files: Mapped[list[UploadedFile]] = relationship(back_populates="project")
    pine_sources: Mapped[list[PineScriptSource]] = relationship(back_populates="project")
    mql5_sources: Mapped[list[MQL5Source]] = relationship(back_populates="project")
    backtest_reports: Mapped[list[BacktestReport]] = relationship(back_populates="project")
    strategy_versions: Mapped[list[StrategyVersion]] = relationship(back_populates="project")
    improvement_ideas: Mapped[list[ImprovementIdea]] = relationship(back_populates="project")
    risk_settings: Mapped[Optional[RiskSettings]] = relationship(back_populates="project", uselist=False)


# ---------------------------------------------------------------------------
# UploadedFile
# ---------------------------------------------------------------------------
class UploadedFile(TimestampMixin, Base):
    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[Optional[str]] = mapped_column(ForeignKey("strategy_projects.id"))
    file_name: Mapped[str] = mapped_column(String(300), nullable=False)
    file_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # pine_script | mql5 | backtest_report | mt5_log | screenshot | csv | trade_history | notes
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    mime_type: Mapped[Optional[str]] = mapped_column(String(100))
    processing_status: Mapped[str] = mapped_column(
        String(30), default="pending"
    )  # pending | processing | done | failed
    processing_error: Mapped[Optional[str]] = mapped_column(Text)
    parsed_summary: Mapped[Optional[str]] = mapped_column(Text)
    meta: Mapped[Optional[dict]] = mapped_column(JSON)

    project: Mapped[Optional[StrategyProject]] = relationship(back_populates="uploaded_files")


# ---------------------------------------------------------------------------
# PineScriptSource
# ---------------------------------------------------------------------------
class PineScriptSource(TimestampMixin, Base):
    __tablename__ = "pine_script_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("strategy_projects.id"), nullable=False)
    file_id: Mapped[Optional[str]] = mapped_column(ForeignKey("uploaded_files.id"))
    version_label: Mapped[Optional[str]] = mapped_column(String(50))
    raw_code: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    detected_smc_concepts: Mapped[Optional[list]] = mapped_column(JSON)
    entry_conditions: Mapped[Optional[list]] = mapped_column(JSON)
    exit_conditions: Mapped[Optional[list]] = mapped_column(JSON)
    filter_conditions: Mapped[Optional[list]] = mapped_column(JSON)
    indicators_used: Mapped[Optional[list]] = mapped_column(JSON)
    session_filters: Mapped[Optional[list]] = mapped_column(JSON)
    risk_logic: Mapped[Optional[dict]] = mapped_column(JSON)
    ai_analysis: Mapped[Optional[str]] = mapped_column(Text)

    project: Mapped[StrategyProject] = relationship(back_populates="pine_sources")


# ---------------------------------------------------------------------------
# MQL5Source
# ---------------------------------------------------------------------------
class MQL5Source(TimestampMixin, Base):
    __tablename__ = "mql5_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("strategy_projects.id"), nullable=False)
    file_id: Mapped[Optional[str]] = mapped_column(ForeignKey("uploaded_files.id"))
    version_label: Mapped[Optional[str]] = mapped_column(String(50))
    raw_code: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    detected_smc_concepts: Mapped[Optional[list]] = mapped_column(JSON)
    input_parameters: Mapped[Optional[list]] = mapped_column(JSON)
    entry_logic: Mapped[Optional[str]] = mapped_column(Text)
    exit_logic: Mapped[Optional[str]] = mapped_column(Text)
    sl_tp_logic: Mapped[Optional[str]] = mapped_column(Text)
    filter_logic: Mapped[Optional[str]] = mapped_column(Text)
    pine_vs_ea_diff: Mapped[Optional[str]] = mapped_column(Text)
    ai_analysis: Mapped[Optional[str]] = mapped_column(Text)

    project: Mapped[StrategyProject] = relationship(back_populates="mql5_sources")


# ---------------------------------------------------------------------------
# BacktestReport
# ---------------------------------------------------------------------------
class BacktestReport(TimestampMixin, Base):
    __tablename__ = "backtest_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("strategy_projects.id"), nullable=False)
    file_id: Mapped[Optional[str]] = mapped_column(ForeignKey("uploaded_files.id"))
    label: Mapped[str] = mapped_column(String(100), default="baseline")
    symbol: Mapped[Optional[str]] = mapped_column(String(20))
    timeframe: Mapped[Optional[str]] = mapped_column(String(10))
    period_start: Mapped[Optional[datetime]] = mapped_column(DateTime)
    period_end: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Core metrics
    initial_deposit: Mapped[Optional[float]] = mapped_column(Float)
    net_profit: Mapped[Optional[float]] = mapped_column(Float)
    gross_profit: Mapped[Optional[float]] = mapped_column(Float)
    gross_loss: Mapped[Optional[float]] = mapped_column(Float)
    profit_factor: Mapped[Optional[float]] = mapped_column(Float)
    win_rate: Mapped[Optional[float]] = mapped_column(Float)
    total_trades: Mapped[Optional[int]] = mapped_column(Integer)
    winning_trades: Mapped[Optional[int]] = mapped_column(Integer)
    losing_trades: Mapped[Optional[int]] = mapped_column(Integer)
    avg_win: Mapped[Optional[float]] = mapped_column(Float)
    avg_loss: Mapped[Optional[float]] = mapped_column(Float)
    expectancy: Mapped[Optional[float]] = mapped_column(Float)
    max_drawdown_usd: Mapped[Optional[float]] = mapped_column(Float)
    max_drawdown_pct: Mapped[Optional[float]] = mapped_column(Float)
    relative_drawdown_pct: Mapped[Optional[float]] = mapped_column(Float)
    sharpe_ratio: Mapped[Optional[float]] = mapped_column(Float)
    recovery_factor: Mapped[Optional[float]] = mapped_column(Float)
    max_consecutive_wins: Mapped[Optional[int]] = mapped_column(Integer)
    max_consecutive_losses: Mapped[Optional[int]] = mapped_column(Integer)
    long_trades: Mapped[Optional[int]] = mapped_column(Integer)
    short_trades: Mapped[Optional[int]] = mapped_column(Integer)
    long_win_rate: Mapped[Optional[float]] = mapped_column(Float)
    short_win_rate: Mapped[Optional[float]] = mapped_column(Float)

    # Breakdown data (JSON)
    monthly_breakdown: Mapped[Optional[dict]] = mapped_column(JSON)
    session_breakdown: Mapped[Optional[dict]] = mapped_column(JSON)
    day_of_week_breakdown: Mapped[Optional[dict]] = mapped_column(JSON)
    hour_breakdown: Mapped[Optional[dict]] = mapped_column(JSON)
    failure_zones: Mapped[Optional[list]] = mapped_column(JSON)
    missed_opportunities: Mapped[Optional[list]] = mapped_column(JSON)
    ai_summary: Mapped[Optional[str]] = mapped_column(Text)
    ai_failure_analysis: Mapped[Optional[str]] = mapped_column(Text)

    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False)

    trades: Mapped[list[Trade]] = relationship(back_populates="backtest_report")
    project: Mapped[StrategyProject] = relationship(back_populates="backtest_reports")


# ---------------------------------------------------------------------------
# Trade
# ---------------------------------------------------------------------------
class Trade(TimestampMixin, Base):
    __tablename__ = "trades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    backtest_report_id: Mapped[Optional[str]] = mapped_column(ForeignKey("backtest_reports.id"))
    project_id: Mapped[Optional[str]] = mapped_column(ForeignKey("strategy_projects.id"))
    ticket: Mapped[Optional[str]] = mapped_column(String(50))
    symbol: Mapped[Optional[str]] = mapped_column(String(20))
    direction: Mapped[Optional[str]] = mapped_column(String(10))  # buy | sell
    open_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    close_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    open_price: Mapped[Optional[float]] = mapped_column(Float)
    close_price: Mapped[Optional[float]] = mapped_column(Float)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float)
    take_profit: Mapped[Optional[float]] = mapped_column(Float)
    lot_size: Mapped[Optional[float]] = mapped_column(Float)
    profit: Mapped[Optional[float]] = mapped_column(Float)
    commission: Mapped[Optional[float]] = mapped_column(Float)
    swap: Mapped[Optional[float]] = mapped_column(Float)
    net_profit: Mapped[Optional[float]] = mapped_column(Float)
    pips: Mapped[Optional[float]] = mapped_column(Float)
    risk_reward: Mapped[Optional[float]] = mapped_column(Float)
    session: Mapped[Optional[str]] = mapped_column(String(50))
    day_of_week: Mapped[Optional[str]] = mapped_column(String(15))
    hour_of_day: Mapped[Optional[int]] = mapped_column(Integer)
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    smc_context: Mapped[Optional[dict]] = mapped_column(JSON)
    trade_source: Mapped[str] = mapped_column(String(20), default="backtest")

    backtest_report: Mapped[Optional[BacktestReport]] = relationship(back_populates="trades")


# ---------------------------------------------------------------------------
# TradeLog (live MT5 logs)
# ---------------------------------------------------------------------------
class TradeLog(TimestampMixin, Base):
    __tablename__ = "trade_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[Optional[str]] = mapped_column(ForeignKey("strategy_projects.id"))
    log_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    log_level: Mapped[Optional[str]] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(50))
    raw_line: Mapped[Optional[str]] = mapped_column(Text)
    parsed_data: Mapped[Optional[dict]] = mapped_column(JSON)
    is_decision: Mapped[bool] = mapped_column(Boolean, default=False)
    decision_type: Mapped[Optional[str]] = mapped_column(String(50))
    decision_reason: Mapped[Optional[str]] = mapped_column(Text)


# ---------------------------------------------------------------------------
# ScreenshotAnalysis
# ---------------------------------------------------------------------------
class ScreenshotAnalysis(TimestampMixin, Base):
    __tablename__ = "screenshot_analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[Optional[str]] = mapped_column(ForeignKey("strategy_projects.id"))
    file_id: Mapped[Optional[str]] = mapped_column(ForeignKey("uploaded_files.id"))
    symbol: Mapped[Optional[str]] = mapped_column(String(20))
    timeframe: Mapped[Optional[str]] = mapped_column(String(10))
    user_notes: Mapped[Optional[str]] = mapped_column(Text)
    ea_decision_log: Mapped[Optional[str]] = mapped_column(Text)
    ai_structure_analysis: Mapped[Optional[str]] = mapped_column(Text)
    detected_structures: Mapped[Optional[dict]] = mapped_column(JSON)
    detected_bias: Mapped[Optional[str]] = mapped_column(String(20))
    ea_recommendation: Mapped[Optional[str]] = mapped_column(String(50))
    ai_vs_ea_comparison: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[Optional[float]] = mapped_column(Float)


# ---------------------------------------------------------------------------
# ImprovementIdea
# ---------------------------------------------------------------------------
class ImprovementIdea(TimestampMixin, Base):
    __tablename__ = "improvement_ideas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("strategy_projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100))
    logic_explanation: Mapped[Optional[str]] = mapped_column(Text)
    affected_component: Mapped[Optional[str]] = mapped_column(
        String(50)
    )  # entry | exit | sl | tp | filter | bias | session | reversal | trade_management
    smc_reasoning: Mapped[Optional[str]] = mapped_column(Text)
    expected_benefit: Mapped[Optional[str]] = mapped_column(Text)
    expected_risk: Mapped[Optional[str]] = mapped_column(Text)
    parameters_changed: Mapped[Optional[list]] = mapped_column(JSON)
    overfit_risk: Mapped[Optional[str]] = mapped_column(String(20))  # low | medium | high
    pine_script_impact: Mapped[Optional[str]] = mapped_column(Text)
    mql5_patch_suggestion: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(30), default="pending"
    )  # pending | accepted | rejected | tested | deployed
    user_notes: Mapped[Optional[str]] = mapped_column(Text)
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=True)
    source_version_id: Mapped[Optional[str]] = mapped_column(ForeignKey("strategy_versions.id"))

    project: Mapped[StrategyProject] = relationship(back_populates="improvement_ideas")


# ---------------------------------------------------------------------------
# StrategyVersion
# ---------------------------------------------------------------------------
class StrategyVersion(TimestampMixin, Base):
    __tablename__ = "strategy_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("strategy_projects.id"), nullable=False)
    version_number: Mapped[str] = mapped_column(String(20), nullable=False)  # e.g. v1.0.0
    label: Mapped[Optional[str]] = mapped_column(String(100))
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False)
    mql5_code_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    input_parameters: Mapped[Optional[dict]] = mapped_column(JSON)
    changelog: Mapped[Optional[str]] = mapped_column(Text)
    ai_explanation: Mapped[Optional[str]] = mapped_column(Text)
    improvement_ids: Mapped[Optional[list]] = mapped_column(JSON)
    approval_status: Mapped[str] = mapped_column(
        String(30), default="pending"
    )  # pending | approved | rejected | demo_testing | live_ready
    approved_by: Mapped[Optional[str]] = mapped_column(String(100))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    confidence_score: Mapped[Optional[float]] = mapped_column(Float)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    project: Mapped[StrategyProject] = relationship(back_populates="strategy_versions")
    comparisons_as_improved: Mapped[list["BacktestComparison"]] = relationship(
        foreign_keys="[BacktestComparison.improved_version_id]",
        back_populates="improved_version",
    )


# ---------------------------------------------------------------------------
# BacktestComparison
# ---------------------------------------------------------------------------
class BacktestComparison(TimestampMixin, Base):
    __tablename__ = "backtest_comparisons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("strategy_projects.id"), nullable=False)
    baseline_report_id: Mapped[str] = mapped_column(ForeignKey("backtest_reports.id"), nullable=False)
    improved_report_id: Mapped[str] = mapped_column(ForeignKey("backtest_reports.id"), nullable=False)
    improved_version_id: Mapped[Optional[str]] = mapped_column(ForeignKey("strategy_versions.id"))

    profit_delta: Mapped[Optional[float]] = mapped_column(Float)
    profit_factor_delta: Mapped[Optional[float]] = mapped_column(Float)
    win_rate_delta: Mapped[Optional[float]] = mapped_column(Float)
    drawdown_delta: Mapped[Optional[float]] = mapped_column(Float)
    trade_count_delta: Mapped[Optional[int]] = mapped_column(Integer)
    expectancy_delta: Mapped[Optional[float]] = mapped_column(Float)
    sharpe_delta: Mapped[Optional[float]] = mapped_column(Float)

    is_statistically_significant: Mapped[Optional[bool]] = mapped_column(Boolean)
    overfit_detected: Mapped[Optional[bool]] = mapped_column(Boolean)
    overfit_reasons: Mapped[Optional[list]] = mapped_column(JSON)
    verdict: Mapped[Optional[str]] = mapped_column(
        String(30)
    )  # improvement | regression | neutral | overfit
    ai_comparison_summary: Mapped[Optional[str]] = mapped_column(Text)

    improved_version: Mapped[Optional[StrategyVersion]] = relationship(
        foreign_keys=[improved_version_id],
        back_populates="comparisons_as_improved",
    )


# ---------------------------------------------------------------------------
# ConfidenceScore
# ---------------------------------------------------------------------------
class ConfidenceScore(TimestampMixin, Base):
    __tablename__ = "confidence_scores"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("strategy_projects.id"), nullable=False)
    version_id: Mapped[Optional[str]] = mapped_column(ForeignKey("strategy_versions.id"))
    comparison_id: Mapped[Optional[str]] = mapped_column(ForeignKey("backtest_comparisons.id"))

    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    improvement_over_baseline: Mapped[Optional[float]] = mapped_column(Float)
    drawdown_stability: Mapped[Optional[float]] = mapped_column(Float)
    profit_factor_stability: Mapped[Optional[float]] = mapped_column(Float)
    trade_count_score: Mapped[Optional[float]] = mapped_column(Float)
    monthly_robustness: Mapped[Optional[float]] = mapped_column(Float)
    buy_sell_robustness: Mapped[Optional[float]] = mapped_column(Float)
    session_robustness: Mapped[Optional[float]] = mapped_column(Float)
    parameter_sensitivity: Mapped[Optional[float]] = mapped_column(Float)
    overfit_penalty: Mapped[Optional[float]] = mapped_column(Float)
    smc_logic_consistency: Mapped[Optional[float]] = mapped_column(Float)
    screenshot_validation: Mapped[Optional[float]] = mapped_column(Float)

    readiness_level: Mapped[str] = mapped_column(
        String(30), default="research"
    )  # research | demo_candidate | demo_testing | live_candidate | live_ready
    breakdown: Mapped[Optional[dict]] = mapped_column(JSON)
    ai_notes: Mapped[Optional[str]] = mapped_column(Text)


# ---------------------------------------------------------------------------
# MT5Session
# ---------------------------------------------------------------------------
class MT5Session(TimestampMixin, Base):
    __tablename__ = "mt5_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[Optional[str]] = mapped_column(ForeignKey("strategy_projects.id"))
    session_type: Mapped[str] = mapped_column(String(20), default="demo")  # demo | live
    account_id: Mapped[Optional[str]] = mapped_column(String(50))
    server: Mapped[Optional[str]] = mapped_column(String(100))
    connected_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    disconnected_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    balance: Mapped[Optional[float]] = mapped_column(Float)
    equity: Mapped[Optional[float]] = mapped_column(Float)
    daily_pnl: Mapped[Optional[float]] = mapped_column(Float)
    session_meta: Mapped[Optional[dict]] = mapped_column(JSON)


# ---------------------------------------------------------------------------
# LiveTradeDecision
# ---------------------------------------------------------------------------
class LiveTradeDecision(TimestampMixin, Base):
    __tablename__ = "live_trade_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[Optional[str]] = mapped_column(ForeignKey("strategy_projects.id"))
    mt5_session_id: Mapped[Optional[str]] = mapped_column(ForeignKey("mt5_sessions.id"))
    decision_time: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    symbol: Mapped[Optional[str]] = mapped_column(String(20))
    timeframe: Mapped[Optional[str]] = mapped_column(String(10))
    decision_type: Mapped[str] = mapped_column(
        String(30)
    )  # trade | skip | wait | block_risk | kill_switch
    direction: Mapped[Optional[str]] = mapped_column(String(10))
    entry_price: Mapped[Optional[float]] = mapped_column(Float)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float)
    take_profit: Mapped[Optional[float]] = mapped_column(Float)
    lot_size: Mapped[Optional[float]] = mapped_column(Float)
    risk_reward: Mapped[Optional[float]] = mapped_column(Float)
    session: Mapped[Optional[str]] = mapped_column(String(50))
    spread_at_entry: Mapped[Optional[float]] = mapped_column(Float)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    smc_context: Mapped[Optional[dict]] = mapped_column(JSON)
    executed: Mapped[bool] = mapped_column(Boolean, default=False)
    execution_ticket: Mapped[Optional[str]] = mapped_column(String(50))
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    approved: Mapped[Optional[bool]] = mapped_column(Boolean)
    approved_by: Mapped[Optional[str]] = mapped_column(String(100))


# ---------------------------------------------------------------------------
# RiskSettings
# ---------------------------------------------------------------------------
class RiskSettings(TimestampMixin, Base):
    __tablename__ = "risk_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_projects.id"), nullable=False, unique=True
    )
    enable_live_trading: Mapped[bool] = mapped_column(Boolean, default=False)
    max_daily_loss_usd: Mapped[float] = mapped_column(Float, default=100.0)
    max_weekly_loss_usd: Mapped[float] = mapped_column(Float, default=300.0)
    max_drawdown_percent: Mapped[float] = mapped_column(Float, default=10.0)
    max_lot_size: Mapped[float] = mapped_column(Float, default=0.10)
    max_trades_per_day: Mapped[int] = mapped_column(Integer, default=5)
    max_open_trades: Mapped[int] = mapped_column(Integer, default=2)
    max_consecutive_losses: Mapped[int] = mapped_column(Integer, default=3)
    spread_filter_pips: Mapped[float] = mapped_column(Float, default=3.0)
    slippage_filter_pips: Mapped[float] = mapped_column(Float, default=2.0)
    symbol_whitelist: Mapped[Optional[list]] = mapped_column(JSON)
    session_whitelist: Mapped[Optional[list]] = mapped_column(JSON)
    kill_switch_active: Mapped[bool] = mapped_column(Boolean, default=False)
    kill_switch_reason: Mapped[Optional[str]] = mapped_column(Text)

    project: Mapped[StrategyProject] = relationship(back_populates="risk_settings")
