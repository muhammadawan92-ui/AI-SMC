from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import (
    BacktestComparison,
    BacktestReport,
    ConfidenceScore,
    ImprovementIdea,
    MQL5Source,
    StrategyVersion,
)
from app.services.confidence_score_service import compute_confidence_score

logger = logging.getLogger(__name__)


def create_version(
    project_id: str,
    version_number: str,
    db: Session,
    label: str = "",
    description: str = "",
    mql5_code: str = "",
    input_parameters: Optional[dict] = None,
    improvement_ids: Optional[list] = None,
    ai_explanation: str = "",
    is_baseline: bool = False,
) -> StrategyVersion:
    version = StrategyVersion(
        project_id=project_id,
        version_number=version_number,
        label=label,
        description=description,
        is_baseline=is_baseline,
        mql5_code_snapshot=mql5_code,
        input_parameters=input_parameters or {},
        improvement_ids=improvement_ids or [],
        ai_explanation=ai_explanation,
        approval_status="pending",
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def approve_version(version_id: str, db: Session, approved_by: str = "user") -> StrategyVersion:
    version = db.get(StrategyVersion, version_id)
    if not version:
        raise ValueError(f"Version {version_id} not found")
    version.approval_status = "approved"
    version.approved_by = approved_by
    version.approved_at = datetime.utcnow()
    db.commit()
    db.refresh(version)
    return version


def reject_version(version_id: str, db: Session, reason: str = "") -> StrategyVersion:
    version = db.get(StrategyVersion, version_id)
    if not version:
        raise ValueError(f"Version {version_id} not found")
    version.approval_status = "rejected"
    version.notes = (version.notes or "") + f"\nRejected: {reason}"
    db.commit()
    db.refresh(version)
    return version


def compare_backtests(
    baseline_id: str,
    improved_id: str,
    db: Session,
    project_id: str,
    version_id: Optional[str] = None,
) -> BacktestComparison:
    baseline: BacktestReport = db.get(BacktestReport, baseline_id)
    improved: BacktestReport = db.get(BacktestReport, improved_id)
    if not baseline or not improved:
        raise ValueError("Backtest reports not found")

    profit_delta = _delta(improved.net_profit, baseline.net_profit)
    pf_delta = _delta(improved.profit_factor, baseline.profit_factor)
    wr_delta = _delta(improved.win_rate, baseline.win_rate)
    dd_delta = _delta(improved.max_drawdown_pct, baseline.max_drawdown_pct)
    tc_delta = (improved.total_trades or 0) - (baseline.total_trades or 0)
    exp_delta = _delta(improved.expectancy, baseline.expectancy)
    sharpe_delta = _delta(improved.sharpe_ratio, baseline.sharpe_ratio)

    # Overfit detection
    overfit_detected, overfit_reasons = _detect_overfit(baseline, improved)

    # Verdict
    verdict = _determine_verdict(
        profit_delta, pf_delta, dd_delta, tc_delta, overfit_detected
    )

    comparison = BacktestComparison(
        project_id=project_id,
        baseline_report_id=baseline_id,
        improved_report_id=improved_id,
        improved_version_id=version_id,
        profit_delta=profit_delta,
        profit_factor_delta=pf_delta,
        win_rate_delta=wr_delta,
        drawdown_delta=dd_delta,
        trade_count_delta=tc_delta,
        expectancy_delta=exp_delta,
        sharpe_delta=sharpe_delta,
        is_statistically_significant=_is_significant(tc_delta, pf_delta),
        overfit_detected=overfit_detected,
        overfit_reasons=overfit_reasons,
        verdict=verdict,
    )
    db.add(comparison)
    db.commit()
    db.refresh(comparison)
    return comparison


def get_version_history(project_id: str, db: Session) -> list[dict]:
    versions = (
        db.query(StrategyVersion)
        .filter(StrategyVersion.project_id == project_id)
        .order_by(StrategyVersion.created_at.desc())
        .all()
    )
    result = []
    for v in versions:
        cs = (
            db.query(ConfidenceScore)
            .filter(ConfidenceScore.version_id == v.id)
            .order_by(ConfidenceScore.created_at.desc())
            .first()
        )
        result.append({
            "id": v.id,
            "version_number": v.version_number,
            "label": v.label,
            "description": v.description,
            "is_baseline": v.is_baseline,
            "approval_status": v.approval_status,
            "confidence_score": cs.overall_score if cs else None,
            "readiness_level": cs.readiness_level if cs else "research",
            "created_at": v.created_at.isoformat() if v.created_at else None,
        })
    return result


def _delta(new_val: Optional[float], base_val: Optional[float]) -> Optional[float]:
    if new_val is None or base_val is None:
        return None
    return round(new_val - base_val, 4)


def _detect_overfit(baseline: BacktestReport, improved: BacktestReport) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    # 1. Trade count dropped significantly but profit increased — suspicious
    if (
        improved.total_trades is not None
        and baseline.total_trades is not None
        and improved.total_trades < baseline.total_trades * 0.6
        and (improved.profit_factor or 0) > (baseline.profit_factor or 0) * 1.3
    ):
        reasons.append("Trade count dropped >40% while profit factor increased >30% — curve fitting risk")

    # 2. Win rate too high
    if improved.win_rate and improved.win_rate > 85:
        reasons.append("Win rate > 85% — likely overfit to historical data")

    # 3. Perfect months — every month profitable with high PF
    monthly = improved.monthly_breakdown
    if monthly:
        months_data = monthly if isinstance(monthly, list) else list(monthly.values())
        if months_data:
            all_profitable = all(float(m.get("profit", 0)) > 0 for m in months_data)
            if all_profitable and len(months_data) >= 6:
                reasons.append("Every month profitable — may indicate overfitting to in-sample data")

    # 4. Profit factor unrealistically high
    if improved.profit_factor and improved.profit_factor > 4.0:
        reasons.append("Profit factor > 4.0 — unrealistically high for live trading")

    return len(reasons) > 0, reasons


def _determine_verdict(
    profit_delta: Optional[float],
    pf_delta: Optional[float],
    dd_delta: Optional[float],
    tc_delta: int,
    overfit: bool,
) -> str:
    if overfit:
        return "overfit"
    improvements = 0
    regressions = 0
    if profit_delta is not None:
        if profit_delta > 0:
            improvements += 1
        elif profit_delta < -50:
            regressions += 1
    if pf_delta is not None:
        if pf_delta > 0.05:
            improvements += 1
        elif pf_delta < -0.1:
            regressions += 1
    if dd_delta is not None:
        if dd_delta < -1:
            improvements += 1  # drawdown improved
        elif dd_delta > 3:
            regressions += 1
    if improvements > regressions:
        return "improvement"
    if regressions > improvements:
        return "regression"
    return "neutral"


def _is_significant(tc_delta: int, pf_delta: Optional[float]) -> bool:
    if pf_delta is None:
        return False
    return abs(pf_delta) > 0.15 and tc_delta > -20
