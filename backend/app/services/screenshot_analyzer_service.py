from __future__ import annotations

import logging
import re
from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import ScreenshotAnalysis, UploadedFile
from app.services.knowledge_doc_service import get_reference_block_for_prompt
from app.services.llm_service import get_llm_service
from app.services.tradingview_context_service import (
    fetch_tradingview_chart_context,
    format_tradingview_context_for_prompt,
)
from app.services.tradingview_learning_service import get_latest_mql5_excerpt

logger = logging.getLogger(__name__)

# Hypothetical risk plan numbers embedded in the prompt (not live trading advice).
SCREENSHOT_PLAN_ACCOUNT_USD = 5000
SCREENSHOT_PLAN_RISK_PERCENT = 1.0

SMC_CHART_SYSTEM = """You are a world-class Smart Money Concepts (SMC) chart analyst with 15+ years experience.
You are given the actual chart image pixels — read labels, ticker, broker suffix, timeframe, and price scale from the image.
Never substitute a different currency pair or asset than what is visibly printed on the chart (e.g. if the title bar shows GBP/USD or GBPUSD, you must not describe EUR/USD). If the text fields in the user prompt disagree with the chart, trust the chart and state both clearly.
Identify SMC structures with approximate price levels from the chart axis. Be objective; say when labels are unreadable."""
SMC_ANALYSIS_PROMPT = """Analyze this TradingView chart screenshot for Smart Money Concepts structures.

CRITICAL — Chart vs form fields:
- Read the **instrument and timeframe from the screenshot** (top bar, symbol search box, watermark, interval button, axis).
- The lines below are user-supplied hints only and may be wrong:
  Symbol (hint): {symbol}
  Timeframe (hint): {timeframe}
- Start section "## 1. Market Structure" with two bullets: **Chart-identified symbol:** … and **Chart-identified timeframe:** … (from pixels). If the hint differs, write one sentence explaining that you follow the chart.

User Notes: {user_notes}
Current EA Decision: {ea_decision}

Hypothetical risk plan (for any numeric trade plan in this report — education only, not live sizing):
- Account: USD {account_usd:,.0f}
- Risk per trade: {risk_pct:.1f}% = USD {risk_usd:,.0f} maximum risk at stop
- Reward target: at least **1:3** risk:reward vs that stop (TP distance ≥ 3× stop distance in price)

Provide a detailed structured analysis:

## 1. Market Structure
- Chart-identified symbol and timeframe (from image; see rules above)
- Current trend direction (bullish/bearish/ranging)
- Recent swing highs and lows (approximate price levels from the chart)
- Most recent BOS — direction and approximate level (or "not clearly visible" if absent)
- Most recent CHOCH if visible — level and significance

## 2. Liquidity Analysis
- Buy-side liquidity zones (equal highs, resistance clusters)
- Sell-side liquidity zones (equal lows, support clusters)
- Any recent liquidity sweeps visible
- Inducement levels if visible

## 3. Order Blocks
- Valid bullish Order Blocks (numeric low–high, quality: high/medium/low)
- Valid bearish Order Blocks (numeric low–high, quality: high/medium/low)
- Mitigated OBs if visible; breaker blocks if present

## 4. Fair Value Gaps (FVG / Imbalances)
- Active bullish FVGs (price range)
- Active bearish FVGs (price range)
- Partially filled FVGs

## 5. Premium / Discount Analysis
- Current price vs recent swing range (premium / equilibrium / discount)
- Fibonacci 50% (equilibrium) if calculable from visible range

## 6. Session Context
- Session / time cues if visible on chart; otherwise state not visible
- High-volume session comment only if you can justify from chart tools shown

## 7. Overall Bias
- Bias: BULLISH / BEARISH / NEUTRAL
- Confidence: HIGH / MEDIUM / LOW
- Invalidation level (price)

## 8. Trade Recommendation and concrete plan
- EA action: **TRADE** / **WAIT** / **AVOID** (one choice) with one-sentence rationale
- If **TRADE** or a conditional setup is described, you MUST include all of:
  - **Direction:** buy or sell
  - **Entry OB:** which zone (bullish OB or bearish OB) with **numeric low–high** from the chart, and whether you expect a reaction for a buy or a sell from that OB
  - **Entry price:** single level or tight zone (e.g. OB edge / 50% of OB / mitigation)
  - **Stop loss:** exact price beyond invalidation
  - **Take profit:** price level(s) giving **minimum 1:3** R:R vs that stop — show the multiple (e.g. "SL 40 pips, TP 120 pips → 1:3")
  - **Risk check:** relate stop distance to **USD {risk_usd:,.0f}** risk on a **USD {account_usd:,.0f}** account ({risk_pct:.1f}%) — give approximate pip/point distance or conceptual lot sizing for FX if appropriate
- If **WAIT** or **AVOID**, still name the **best candidate OB** (with range) you would monitor next and what price event would flip you to TRADE

## 9. EA Decision Assessment
- Was the EA's decision correct based on the chart structure?
- What did the EA miss (if anything)?
- What would an ideal SMC trader do differently?

Be specific with price levels where visible. If the image is unclear, say so — do not invent pairs or levels. Rate setup quality: A/B/C/D/F."""


def analyze_screenshot(
    file_record: UploadedFile,
    db: Session,
    project_id: Optional[str],
    symbol: str = "",
    timeframe: str = "",
    user_notes: str = "",
    ea_decision: str = "",
    chart_url: str = "",
) -> ScreenshotAnalysis:
    llm = get_llm_service()

    ref_block = get_reference_block_for_prompt(db=db, project_id=project_id)
    mq_excerpt = get_latest_mql5_excerpt(db, project_id, max_chars=6000)
    mq_block = ""
    if mq_excerpt:
        mq_block = (
            "--- MQL5 EA CODE EXCERPT (latest uploaded for project) ---\n"
            f"{mq_excerpt}\n"
            "--- END MQL5 EA CODE EXCERPT ---\n"
        )
    tv_block = ""
    if chart_url and chart_url.strip():
        tv_block = format_tradingview_context_for_prompt(fetch_tradingview_chart_context(chart_url.strip()))

    risk_usd = int(round(SCREENSHOT_PLAN_ACCOUNT_USD * SCREENSHOT_PLAN_RISK_PERCENT / 100.0))
    prompt = SMC_ANALYSIS_PROMPT.format(
        symbol=symbol or "Unknown — read from chart image",
        timeframe=timeframe or "Unknown — read from chart image",
        user_notes=user_notes or "None",
        ea_decision=ea_decision or "None provided",
        account_usd=SCREENSHOT_PLAN_ACCOUNT_USD,
        risk_pct=SCREENSHOT_PLAN_RISK_PERCENT,
        risk_usd=risk_usd,
    )
    if tv_block:
        prompt = tv_block + "\n\n" + prompt
    if ref_block:
        prompt = ref_block + "\n" + prompt
    if mq_block:
        prompt = mq_block + "\n" + prompt

    try:
        analysis_text = llm.analyze_image(
            image_path=file_record.file_path,
            prompt=prompt,
            system=SMC_CHART_SYSTEM,
        )
    except Exception as e:
        logger.error("Screenshot analysis failed: %s", e)
        analysis_text = f"Analysis failed: {e}"

    # Extract structured data from analysis text (avoid TOC / quoted knowledge false positives)
    tag_source = _text_for_tag_extraction(analysis_text)
    structures = _extract_structures(tag_source)
    bias = _extract_bias(tag_source)
    recommendation = _extract_recommendation(tag_source)
    ea_cmp = ""
    if "MOCK_MODE / MOCK_LLM" not in analysis_text:
        ea_cmp = _extract_ea_comparison(analysis_text)

    record = ScreenshotAnalysis(
        project_id=project_id,
        file_id=file_record.id,
        symbol=symbol,
        timeframe=timeframe,
        user_notes=user_notes,
        ea_decision_log=ea_decision,
        ai_structure_analysis=analysis_text,
        detected_structures=structures,
        detected_bias=bias,
        ea_recommendation=recommendation,
        ai_vs_ea_comparison=ea_cmp,
        confidence=_extract_confidence(tag_source),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def _text_for_tag_extraction(text: str) -> str:
    """Use only the analyst-written tail so quoted .docx TOC does not flip every tag on."""
    if "## Preliminary checklist" in text:
        return text.split("## Preliminary checklist", 1)[1]
    if "## 1. Market Structure" in text:
        return text.split("## 1. Market Structure", 1)[1]
    return text


def _extract_structures(text: str) -> dict:
    structures = {}
    text_lower = text.lower()

    if "bos" in text_lower or "break of structure" in text_lower:
        structures["bos_detected"] = True
    if "choch" in text_lower or "change of character" in text_lower:
        structures["choch_detected"] = True
    if "order block" in text_lower or re.search(r"\bob\b", text_lower):
        structures["order_block_detected"] = True
    if (
        "fair value gap" in text_lower
        or re.search(r"\bfvg\b", text_lower)
        or ("imbalance" in text_lower and "fvg" in text_lower)
    ):
        structures["fvg_detected"] = True
    if "liquidity sweep" in text_lower or re.search(
        r"\bliquidity\b.*\bsweep\b|\bsweep\b.*\bliquidity\b", text_lower
    ):
        structures["liquidity_sweep_detected"] = True
    # Avoid "premium/discount" educational phrases counting as both zones
    if re.search(r"\b(in|at|within)\s+premium\b", text_lower):
        structures["in_premium"] = True
    if re.search(r"\b(in|at|within)\s+discount\b", text_lower):
        structures["in_discount"] = True
    if "inducement" in text_lower:
        structures["inducement_detected"] = True

    return structures


def _extract_bias(text: str) -> str:
    text_lower = text.lower()
    if "bias: bullish" in text_lower or "bullish bias" in text_lower:
        return "bullish"
    if "bias: bearish" in text_lower or "bearish bias" in text_lower:
        return "bearish"
    if "neutral" in text_lower and "bias" in text_lower:
        return "neutral"
    # Fallback: count mentions
    bull_count = text_lower.count("bullish")
    bear_count = text_lower.count("bearish")
    if bull_count > bear_count:
        return "bullish"
    if bear_count > bull_count:
        return "bearish"
    return "neutral"


def _extract_recommendation(text: str) -> str:
    text_upper = text.upper()
    if "SHOULD TRADE" in text_upper or "EA TRADE" in text_upper or "RECOMMENDATION: TRADE" in text_upper:
        return "trade"
    if "WAIT" in text_upper and ("RECOMMENDATION" in text_upper or "SHOULD" in text_upper):
        return "wait"
    if "AVOID" in text_upper and ("RECOMMENDATION" in text_upper or "SHOULD" in text_upper):
        return "avoid"
    return "wait"


def _extract_ea_comparison(text: str) -> str:
    lines = text.splitlines()
    capture = False
    comparison_lines = []
    for line in lines:
        if "ea decision assessment" in line.lower() or "## 9" in line:
            capture = True
        if capture:
            comparison_lines.append(line)
    return "\n".join(comparison_lines[:20]) if comparison_lines else ""


def _extract_confidence(text: str) -> float:
    text_lower = text.lower()
    if "confidence: high" in text_lower or "high confidence" in text_lower:
        return 85.0
    if "confidence: medium" in text_lower or "medium confidence" in text_lower:
        return 60.0
    if "confidence: low" in text_lower or "low confidence" in text_lower:
        return 35.0
    # Grade-based
    if "grade: a" in text_lower or "quality: a" in text_lower:
        return 90.0
    if "grade: b" in text_lower or "quality: b" in text_lower:
        return 75.0
    if "grade: c" in text_lower or "quality: c" in text_lower:
        return 55.0
    return 60.0
