from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.models import BacktestComparison, BacktestReport, ConfidenceScore, StrategyProject, StrategyVersion
from app.services.llm_service import get_llm_service

logger = logging.getLogger(__name__)
settings = get_settings()


def generate_baseline_summary_report(project: StrategyProject, report: BacktestReport, db: Session) -> str:
    llm = get_llm_service()
    content = f"""# Baseline EA Summary Report
Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
Project: {project.name}

## Performance Metrics

| Metric | Value |
|--------|-------|
| Net Profit | ${_fmt(report.net_profit)} |
| Profit Factor | {_fmt(report.profit_factor)} |
| Win Rate | {_fmt(report.win_rate)}% |
| Total Trades | {report.total_trades or 'N/A'} |
| Avg Win | ${_fmt(report.avg_win)} |
| Avg Loss | ${_fmt(report.avg_loss)} |
| Expectancy | ${_fmt(report.expectancy)} |
| Max Drawdown | {_fmt(report.max_drawdown_pct)}% |
| Sharpe Ratio | {_fmt(report.sharpe_ratio)} |
| Recovery Factor | {_fmt(report.recovery_factor)} |
| Long Win Rate | {_fmt(report.long_win_rate)}% |
| Short Win Rate | {_fmt(report.short_win_rate)}% |

## Monthly Breakdown
{_format_monthly(report.monthly_breakdown)}

## Session Breakdown
{_format_session(report.session_breakdown)}

## Failure Zones
{_format_failure_zones(report.failure_zones)}

## AI Analysis
{report.ai_summary or 'Not yet generated'}

## Failure Analysis
{report.ai_failure_analysis or 'Not yet generated'}
"""
    _save_report(content, project.id, "baseline_summary")
    return content


def generate_improvement_candidate_report(
    project: StrategyProject,
    baseline: BacktestReport,
    improved: BacktestReport,
    comparison: BacktestComparison,
    confidence: ConfidenceScore,
    version: Optional[StrategyVersion],
    db: Session,
) -> str:
    verdict_emoji = {"improvement": "✅", "regression": "❌", "overfit": "⚠️", "neutral": "➡️"}.get(comparison.verdict or "neutral", "")
    content = f"""# Improvement Candidate Report {verdict_emoji}
Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
Project: {project.name}
Version: {version.version_number if version else 'Unknown'}

## Verdict: {(comparison.verdict or 'unknown').upper()}

## Confidence Score: {confidence.overall_score:.1f}% — {confidence.readiness_level.replace('_', ' ').title()}

## Side-by-Side Comparison

| Metric | Baseline | Improved | Delta |
|--------|----------|----------|-------|
| Net Profit | ${_fmt(baseline.net_profit)} | ${_fmt(improved.net_profit)} | {_delta_str(comparison.profit_delta)} |
| Profit Factor | {_fmt(baseline.profit_factor)} | {_fmt(improved.profit_factor)} | {_delta_str(comparison.profit_factor_delta)} |
| Win Rate | {_fmt(baseline.win_rate)}% | {_fmt(improved.win_rate)}% | {_delta_str(comparison.win_rate_delta)}% |
| Total Trades | {baseline.total_trades or 'N/A'} | {improved.total_trades or 'N/A'} | {comparison.trade_count_delta or 'N/A'} |
| Max Drawdown | {_fmt(baseline.max_drawdown_pct)}% | {_fmt(improved.max_drawdown_pct)}% | {_delta_str(comparison.drawdown_delta)}% |
| Expectancy | ${_fmt(baseline.expectancy)} | ${_fmt(improved.expectancy)} | {_delta_str(comparison.expectancy_delta)} |
| Sharpe Ratio | {_fmt(baseline.sharpe_ratio)} | {_fmt(improved.sharpe_ratio)} | {_delta_str(comparison.sharpe_delta)} |

## Overfitting Assessment
Overfit Detected: {'YES ⚠️' if comparison.overfit_detected else 'No ✅'}
{chr(10).join('- ' + r for r in (comparison.overfit_reasons or []))}

## Confidence Score Breakdown
{_format_confidence_breakdown(confidence)}

## AI Comparison Notes
{comparison.ai_comparison_summary or 'Not yet generated'}

## Recommendation
{confidence.ai_notes or 'No notes'}
"""
    _save_report(content, project.id, f"improvement_{version.version_number if version else 'v0'}")
    return content


def generate_live_readiness_report(
    project: StrategyProject,
    confidence: ConfidenceScore,
    db: Session,
) -> str:
    ready = confidence.overall_score >= 85
    content = f"""# Live Readiness Report
Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
Project: {project.name}

## Live Trading Readiness: {'✅ READY' if ready else '❌ NOT READY'}

Confidence Score: {confidence.overall_score:.1f}%
Readiness Level: {confidence.readiness_level.replace('_', ' ').title()}

## Requirements Checklist

| Requirement | Status |
|-------------|--------|
| Confidence >= 85% | {'✅' if confidence.overall_score >= 85 else '❌'} |
| Demo Phase Completed | {'✅' if confidence.readiness_level in ('live_candidate', 'live_ready') else '❌'} |
| ENABLE_LIVE_TRADING flag | ❌ Must be set manually in .env |
| Risk settings configured | Must verify in dashboard |
| Kill switch ready | Must verify in dashboard |

## Risk Controls Summary
- Max Daily Loss: ${settings.max_daily_loss_usd}
- Max Drawdown: {settings.max_drawdown_percent}%
- Max Lot Size: {settings.max_lot_size}
- Max Trades/Day: {settings.max_trades_per_day}
- Max Consecutive Losses: {settings.max_consecutive_losses}

## Notes
{confidence.ai_notes or 'No notes available'}

---
⚠️ Live trading must NEVER be enabled without explicit user action and completed demo validation.
"""
    _save_report(content, project.id, "live_readiness")
    return content


def _save_report(content: str, project_id: str, report_type: str) -> Path:
    folder = Path(settings.reports_dir) / project_id
    folder.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    path = folder / f"{report_type}_{ts}.md"
    path.write_text(content, encoding="utf-8")
    logger.info("Report saved: %s", path)
    return path


def _fmt(val) -> str:
    if val is None:
        return "N/A"
    if isinstance(val, float):
        return f"{val:.2f}"
    return str(val)


def _delta_str(val: Optional[float]) -> str:
    if val is None:
        return "N/A"
    sign = "+" if val > 0 else ""
    return f"{sign}{val:.2f}"


def _format_monthly(monthly: Optional[dict | list]) -> str:
    if not monthly:
        return "No monthly data available"
    rows = monthly if isinstance(monthly, list) else list(monthly.values())
    lines = ["| Month | Profit | Trades | Win Rate |", "|-------|--------|--------|----------|"]
    for m in rows[:12]:
        lines.append(f"| {m.get('_month', m.get('month', 'N/A'))} | ${_fmt(m.get('profit'))} | {m.get('trades', 'N/A')} | {_fmt(m.get('win_rate'))}% |")
    return "\n".join(lines)


def _format_session(session_bd: Optional[dict]) -> str:
    if not session_bd:
        return "No session data"
    lines = ["| Session | Profit | Trades | Win Rate |", "|---------|--------|--------|----------|"]
    for s, data in session_bd.items():
        lines.append(f"| {s} | ${_fmt(data.get('profit'))} | {data.get('trades', 'N/A')} | {_fmt(data.get('win_rate'))}% |")
    return "\n".join(lines)


def _format_failure_zones(zones: Optional[list]) -> str:
    if not zones:
        return "No significant failure zones detected"
    lines = []
    for z in zones:
        lines.append(f"- [{z.get('severity', '').upper()}] {z.get('type', '')}: {z.get('name', '')} — Win Rate: {z.get('win_rate', 'N/A')}%")
    return "\n".join(lines)


def _format_confidence_breakdown(cs: ConfidenceScore) -> str:
    breakdown = cs.breakdown or {}
    lines = ["| Factor | Score | Weight |", "|--------|-------|--------|"]
    from app.services.confidence_score_service import WEIGHTS
    for k, w in WEIGHTS.items():
        v = breakdown.get(k, cs.__dict__.get(k, "N/A"))
        lines.append(f"| {k.replace('_', ' ').title()} | {_fmt(v)} | {w:.0%} |")
    return "\n".join(lines)
