from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import BacktestReport, ImprovementIdea, PineScriptSource, MQL5Source
from app.services.llm_service import get_llm_service
from app.services.smc_logic_service import get_improvement_categories

logger = logging.getLogger(__name__)


def generate_improvement_ideas(
    project_id: str,
    db: Session,
    backtest_report: BacktestReport,
    pine_source: PineScriptSource | None = None,
    mql5_source: MQL5Source | None = None,
    n_ideas: int = 10,
) -> list[ImprovementIdea]:
    llm = get_llm_service()

    # Build context
    report_summary = _format_report_summary(backtest_report)
    pine_context = f"Pine Script SMC concepts: {pine_source.detected_smc_concepts}" if pine_source else "No Pine Script uploaded"
    ea_context = f"MQL5 SMC concepts: {mql5_source.detected_smc_concepts}\nEntry logic: {mql5_source.entry_logic or 'N/A'}" if mql5_source else "No MQL5 uploaded"
    failure_zones = json.dumps(backtest_report.failure_zones or [], indent=2)
    session_data = json.dumps(backtest_report.session_breakdown or {}, indent=2)
    categories = get_improvement_categories()

    system = """You are a senior quantitative trading researcher specializing in Smart Money Concepts (SMC).
Your task is to generate hypothesis-driven improvement ideas for an EA — NOT random optimizations.
Every idea must have a clear SMC-based reasoning. Never suggest curve-fitting. Always consider drawdown impact."""

    prompt = f"""Generate {n_ideas} specific improvement ideas for this SMC Expert Advisor.

BACKTEST PERFORMANCE:
{report_summary}

FAILURE ZONES:
{failure_zones}

SESSION BREAKDOWN:
{session_data}

STRATEGY CONTEXT:
{pine_context}
{ea_context}

AVAILABLE IMPROVEMENT CATEGORIES:
{json.dumps([c['id'] for c in categories], indent=2)}

For each improvement, respond with a JSON array. Each item must have:
- name: short descriptive name
- category: one of the available categories
- logic_explanation: clear explanation of the change
- affected_component: one of [entry, exit, sl, tp, filter, bias, session, reversal, trade_management]
- smc_reasoning: why this makes sense from an SMC perspective
- expected_benefit: specific expected improvement with estimated impact
- expected_risk: what could go wrong or regress
- parameters_changed: list of parameter names that would change
- overfit_risk: low | medium | high
- pine_script_impact: how Pine Script code would need to change
- mql5_patch_suggestion: brief MQL5 code snippet or pseudocode for the change

Rules:
1. Each idea must solve a specific problem from the failure analysis
2. Prioritize low-overfit improvements first
3. Include at least 2 session-related improvements
4. Include at least 1 reversal/counter-trade improvement
5. Do NOT suggest improvements that would destroy trade count without major PF improvement
6. Do NOT suggest parameter optimization without structural logic change

Respond with ONLY the JSON array, no other text."""

    ideas: list[ImprovementIdea] = []
    try:
        raw = llm.complete(prompt, system=system, temperature=0.4)
        parsed = _extract_json_array(raw)
        for item in parsed[:n_ideas]:
            idea = ImprovementIdea(
                project_id=project_id,
                name=item.get("name", "Unnamed Improvement"),
                category=item.get("category", "general"),
                logic_explanation=item.get("logic_explanation", ""),
                affected_component=item.get("affected_component", "entry"),
                smc_reasoning=item.get("smc_reasoning", ""),
                expected_benefit=item.get("expected_benefit", ""),
                expected_risk=item.get("expected_risk", ""),
                parameters_changed=item.get("parameters_changed", []),
                overfit_risk=item.get("overfit_risk", "medium"),
                pine_script_impact=item.get("pine_script_impact", ""),
                mql5_patch_suggestion=item.get("mql5_patch_suggestion", ""),
                ai_generated=True,
                status="pending",
            )
            db.add(idea)
            ideas.append(idea)
        db.commit()
        logger.info("Generated %d improvement ideas for project %s", len(ideas), project_id)
    except Exception as e:
        logger.error("Improvement generation failed: %s", e)
        # Fallback: generate static ideas from known patterns
        ideas = _generate_fallback_ideas(project_id, backtest_report, db)

    return ideas


def generate_mql5_patch(idea: ImprovementIdea, mql5_source: MQL5Source | None) -> str:
    llm = get_llm_service()
    system = "You are a senior MQL5 developer. Generate clean, production-ready MQL5 code patches."

    existing_code = ""
    if mql5_source:
        existing_code = f"""
Existing entry logic:
{mql5_source.entry_logic or ''}

Existing SL/TP logic:
{mql5_source.sl_tp_logic or ''}

Input parameters:
{json.dumps(mql5_source.input_parameters or [], indent=2)}
"""

    prompt = f"""Generate an MQL5 code patch for this improvement:

IMPROVEMENT: {idea.name}
LOGIC: {idea.logic_explanation}
AFFECTED COMPONENT: {idea.affected_component}
SMC REASONING: {idea.smc_reasoning}
PARAMETERS TO ADD/CHANGE: {idea.parameters_changed}

{existing_code}

Generate:
1. New input parameters (with defaults and descriptions)
2. Helper function(s) if needed
3. Modified entry/exit/filter logic
4. Clear comments explaining the SMC logic

Output clean MQL5 code only."""

    try:
        return llm.complete(prompt, system=system, temperature=0.2)
    except Exception as e:
        return f"Patch generation failed: {e}"


def _format_report_summary(report: BacktestReport) -> str:
    return f"""Net Profit: ${report.net_profit or 'N/A'}
Profit Factor: {report.profit_factor or 'N/A'}
Win Rate: {report.win_rate or 'N/A'}%
Total Trades: {report.total_trades or 'N/A'}
Max Drawdown: {report.max_drawdown_pct or 'N/A'}%
Avg Win: ${report.avg_win or 'N/A'}
Avg Loss: ${report.avg_loss or 'N/A'}
Expectancy: ${report.expectancy or 'N/A'}
Sharpe: {report.sharpe_ratio or 'N/A'}
Recovery Factor: {report.recovery_factor or 'N/A'}
Long Win Rate: {report.long_win_rate or 'N/A'}%
Short Win Rate: {report.short_win_rate or 'N/A'}%"""


def _extract_json_array(raw: str) -> list[dict]:
    try:
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start != -1 and end > start:
            return json.loads(raw[start:end])
    except json.JSONDecodeError:
        pass
    return []


def _generate_fallback_ideas(project_id: str, report: BacktestReport, db: Session) -> list[ImprovementIdea]:
    fallback_templates = [
        {
            "name": "Asian Session Exclusion",
            "category": "session_filter",
            "logic_explanation": "Disable new trade entries during 22:00-07:00 UTC to avoid low-liquidity false signals",
            "affected_component": "filter",
            "smc_reasoning": "Asian session lacks institutional participation, leading to weaker OBs and noise-driven BOS signals",
            "expected_benefit": "Eliminate worst-performing session. Expected +15-25% improvement in profit factor",
            "expected_risk": "Reduced trade count by ~20%",
            "parameters_changed": ["SessionStartHour", "SessionEndHour"],
            "overfit_risk": "low",
            "pine_script_impact": "Add session filter: in_session = (hour >= 7 and hour < 22)",
            "mql5_patch_suggestion": "bool InSession() { int h = TimeHour(TimeCurrent()); return (h >= 7 && h < 22); }",
        },
        {
            "name": "OB Displacement Quality Filter",
            "category": "ob_quality",
            "logic_explanation": "Only accept Order Blocks where the displacement candle size > 1.5 * ATR(14)",
            "affected_component": "entry",
            "smc_reasoning": "Strong displacement indicates genuine institutional order execution. Weak displacement OBs are retail patterns",
            "expected_benefit": "Filter 30-40% of low-quality entries, improve win rate by 8-15%",
            "expected_risk": "Trade count reduction of 25-35%",
            "parameters_changed": ["OBDisplacementFactor", "ATRPeriod"],
            "overfit_risk": "low",
            "pine_script_impact": "disp_valid = displacement_size > atr(14) * OBDisplacementFactor",
            "mql5_patch_suggestion": "bool ValidDisplacement() { double atr = iATR(NULL,0,14,1); return (disp_size > atr * OBDisplacementFactor); }",
        },
        {
            "name": "Soft Reversal Counter-Trade",
            "category": "soft_reversal",
            "logic_explanation": "After 3+ consecutive BOS in same direction AND a CHOCH forms, allow counter-direction trade from new OB with 50% normal lot size",
            "affected_component": "entry",
            "smc_reasoning": "After displacement exhaustion, institutions begin distributing/accumulating in opposite direction. CHOCH is the first signal",
            "expected_benefit": "Capture 15-20% additional valid setups during trend exhaustion",
            "expected_risk": "False reversals in strong trends. Mitigated by smaller lot size",
            "parameters_changed": ["SoftReversalBOSCount", "SoftReversalLotMultiplier", "EnableSoftReversal"],
            "overfit_risk": "medium",
            "pine_script_impact": "soft_reversal = consecutive_bos >= SoftReversalBOSCount and choch_detected",
            "mql5_patch_suggestion": "if(consecutive_bos >= SoftReversalBOSCount && CHOCHDetected()) { lot_size *= SoftReversalLotMultiplier; AllowCounterTrade(); }",
        },
    ]
    ideas = []
    for t in fallback_templates:
        idea = ImprovementIdea(project_id=project_id, ai_generated=True, status="pending", **t)
        db.add(idea)
        ideas.append(idea)
    db.commit()
    return ideas
