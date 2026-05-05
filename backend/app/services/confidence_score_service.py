from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import BacktestComparison, BacktestReport, ConfidenceScore, StrategyVersion

logger = logging.getLogger(__name__)

READINESS_THRESHOLDS = {
    "research": 0,
    "demo_candidate": 65,
    "demo_testing": 75,
    "live_candidate": 85,
    "live_ready": 90,
}

WEIGHTS = {
    "improvement_over_baseline": 0.20,
    "drawdown_stability": 0.18,
    "profit_factor_stability": 0.15,
    "trade_count_score": 0.10,
    "monthly_robustness": 0.12,
    "buy_sell_robustness": 0.08,
    "session_robustness": 0.07,
    "parameter_sensitivity": 0.05,
    "overfit_penalty": 0.05,
    "smc_logic_consistency": 0.05,
    "screenshot_validation": 0.05,
}


def compute_confidence_score(
    baseline: BacktestReport,
    improved: BacktestReport,
    comparison: Optional[BacktestComparison],
    version: Optional[StrategyVersion],
    db: Session,
    screenshot_validation_score: float = 0.0,
    smc_consistency_score: float = 75.0,
) -> ConfidenceScore:
    scores: dict[str, float] = {}

    # 1. Improvement over baseline (0-100)
    scores["improvement_over_baseline"] = _score_improvement(baseline, improved)

    # 2. Drawdown stability (0-100)
    scores["drawdown_stability"] = _score_drawdown(baseline, improved)

    # 3. Profit factor stability (0-100)
    scores["profit_factor_stability"] = _score_profit_factor(baseline, improved)

    # 4. Trade count sufficiency (0-100)
    scores["trade_count_score"] = _score_trade_count(improved)

    # 5. Monthly robustness (0-100)
    scores["monthly_robustness"] = _score_monthly_robustness(improved)

    # 6. Buy/sell balance (0-100)
    scores["buy_sell_robustness"] = _score_direction_balance(improved)

    # 7. Session robustness (0-100)
    scores["session_robustness"] = _score_session_robustness(improved)

    # 8. Parameter sensitivity — placeholder (no walk-forward yet)
    scores["parameter_sensitivity"] = 60.0  # default until WF data available

    # 9. Overfit penalty (0-100, 100 = no overfitting detected)
    scores["overfit_penalty"] = _score_overfit(comparison)

    # 10. SMC logic consistency (passed in from analysis)
    scores["smc_logic_consistency"] = max(0, min(100, smc_consistency_score))

    # 11. Screenshot validation (passed in)
    scores["screenshot_validation"] = max(0, min(100, screenshot_validation_score))

    # Weighted overall score
    overall = sum(scores[k] * WEIGHTS[k] for k in WEIGHTS)
    overall = round(overall, 2)

    readiness = _determine_readiness(overall)

    cs = ConfidenceScore(
        project_id=baseline.project_id,
        version_id=version.id if version else None,
        comparison_id=comparison.id if comparison else None,
        overall_score=overall,
        improvement_over_baseline=scores["improvement_over_baseline"],
        drawdown_stability=scores["drawdown_stability"],
        profit_factor_stability=scores["profit_factor_stability"],
        trade_count_score=scores["trade_count_score"],
        monthly_robustness=scores["monthly_robustness"],
        buy_sell_robustness=scores["buy_sell_robustness"],
        session_robustness=scores["session_robustness"],
        parameter_sensitivity=scores["parameter_sensitivity"],
        overfit_penalty=scores["overfit_penalty"],
        smc_logic_consistency=scores["smc_logic_consistency"],
        screenshot_validation=scores["screenshot_validation"],
        readiness_level=readiness,
        breakdown=scores,
        ai_notes=_generate_notes(scores, overall, readiness),
    )
    db.add(cs)
    db.commit()
    db.refresh(cs)
    return cs


def _score_improvement(baseline: BacktestReport, improved: BacktestReport) -> float:
    score = 50.0  # neutral start

    # Net profit improvement
    if baseline.net_profit and improved.net_profit:
        pct_change = (improved.net_profit - baseline.net_profit) / abs(baseline.net_profit + 1e-9) * 100
        if pct_change > 20:
            score += 30
        elif pct_change > 10:
            score += 20
        elif pct_change > 5:
            score += 10
        elif pct_change < -10:
            score -= 30

    # Expectancy improvement
    if baseline.expectancy and improved.expectancy:
        if improved.expectancy > baseline.expectancy * 1.1:
            score += 10
        elif improved.expectancy < baseline.expectancy * 0.9:
            score -= 10

    return max(0, min(100, score))


def _score_drawdown(baseline: BacktestReport, improved: BacktestReport) -> float:
    if not improved.max_drawdown_pct:
        return 50.0
    score = 80.0
    if baseline.max_drawdown_pct:
        dd_change = improved.max_drawdown_pct - baseline.max_drawdown_pct
        if dd_change > 5:
            score -= 40  # DD increased significantly — bad
        elif dd_change > 2:
            score -= 20
        elif dd_change < -2:
            score += 15  # DD improved
    # Absolute DD penalty
    if improved.max_drawdown_pct > 20:
        score -= 30
    elif improved.max_drawdown_pct > 15:
        score -= 15
    elif improved.max_drawdown_pct < 8:
        score += 10
    return max(0, min(100, score))


def _score_profit_factor(baseline: BacktestReport, improved: BacktestReport) -> float:
    if not improved.profit_factor:
        return 40.0
    score = 60.0
    if improved.profit_factor >= 2.0:
        score = 100
    elif improved.profit_factor >= 1.8:
        score = 90
    elif improved.profit_factor >= 1.5:
        score = 75
    elif improved.profit_factor >= 1.3:
        score = 60
    elif improved.profit_factor >= 1.1:
        score = 40
    else:
        score = 10
    # Consistency with baseline
    if baseline.profit_factor:
        if improved.profit_factor < baseline.profit_factor * 0.9:
            score -= 20
    return max(0, min(100, score))


def _score_trade_count(improved: BacktestReport) -> float:
    n = improved.total_trades or 0
    if n >= 100:
        return 100.0
    if n >= 60:
        return 85.0
    if n >= 40:
        return 70.0
    if n >= 20:
        return 50.0
    if n >= 10:
        return 30.0
    return 10.0


def _score_monthly_robustness(improved: BacktestReport) -> float:
    monthly = improved.monthly_breakdown
    if not monthly:
        return 50.0
    months_data = monthly if isinstance(monthly, list) else list(monthly.values())
    if not months_data:
        return 50.0
    profitable_months = sum(1 for m in months_data if float(m.get("profit", 0)) > 0)
    total_months = len(months_data)
    if total_months == 0:
        return 50.0
    pct = profitable_months / total_months
    if pct >= 0.80:
        return 95.0
    if pct >= 0.70:
        return 80.0
    if pct >= 0.60:
        return 65.0
    if pct >= 0.50:
        return 50.0
    return 25.0


def _score_direction_balance(improved: BacktestReport) -> float:
    lwr = improved.long_win_rate
    swr = improved.short_win_rate
    if lwr is None or swr is None:
        return 60.0
    min_wr = min(lwr, swr)
    balance = 1 - abs(lwr - swr) / 100
    score = (min_wr / 100 * 60) + (balance * 40)
    return max(0, min(100, score))


def _score_session_robustness(improved: BacktestReport) -> float:
    session_bd = improved.session_breakdown
    if not session_bd:
        return 60.0
    high_q_sessions = ["london", "new_york", "overlap"]
    profitable = sum(1 for s in high_q_sessions if s in session_bd and session_bd[s].get("profit", 0) > 0)
    if profitable == 3:
        return 95.0
    if profitable == 2:
        return 75.0
    if profitable == 1:
        return 50.0
    return 30.0


def _score_overfit(comparison: Optional[BacktestComparison]) -> float:
    if not comparison:
        return 60.0
    if comparison.overfit_detected:
        return 10.0
    if comparison.verdict == "overfit":
        return 15.0
    if comparison.verdict == "improvement":
        return 90.0
    if comparison.verdict == "neutral":
        return 60.0
    return 70.0


def _determine_readiness(score: float) -> str:
    if score >= READINESS_THRESHOLDS["live_ready"]:
        return "live_ready"
    if score >= READINESS_THRESHOLDS["live_candidate"]:
        return "live_candidate"
    if score >= READINESS_THRESHOLDS["demo_testing"]:
        return "demo_testing"
    if score >= READINESS_THRESHOLDS["demo_candidate"]:
        return "demo_candidate"
    return "research"


def _generate_notes(scores: dict, overall: float, readiness: str) -> str:
    weak = [k for k, v in scores.items() if v < 50]
    strong = [k for k, v in scores.items() if v >= 80]
    notes = f"Overall confidence: {overall:.1f}% — Readiness: {readiness.replace('_', ' ').title()}\n"
    if strong:
        notes += f"Strong areas: {', '.join(strong)}\n"
    if weak:
        notes += f"Weak areas needing improvement: {', '.join(weak)}\n"
    if readiness in ("demo_candidate", "demo_testing"):
        notes += "Recommendation: Proceed to demo testing with reduced position sizing.\n"
    elif readiness == "research":
        notes += "Recommendation: More backtest analysis and structural improvements needed before demo.\n"
    return notes
