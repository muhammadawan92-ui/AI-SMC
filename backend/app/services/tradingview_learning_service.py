from __future__ import annotations

import json
import re
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.models import MQL5Source
from app.services.knowledge_doc_service import (
    get_external_knowledge_excerpt,
    get_reference_block_for_prompt,
    get_uploaded_knowledge_excerpt,
)
from app.services.llm_service import get_llm_service
from app.services.tradingview_context_service import (
    fetch_tradingview_chart_context,
    format_tradingview_context_for_prompt,
    parse_tradingview_symbol_from_url,
)

settings = get_settings()


def get_latest_mql5_excerpt(db: Optional[Session], project_id: Optional[str], max_chars: int = 6000) -> str:
    if db is None or not project_id:
        return ""
    row = (
        db.query(MQL5Source)
        .filter(MQL5Source.project_id == project_id)
        .order_by(MQL5Source.created_at.desc())
        .first()
    )
    if not row or not row.raw_code:
        return ""
    code = row.raw_code.strip()
    if len(code) <= max_chars:
        return code
    return code[: max_chars - 20] + "\n… [truncated]"


def _mql5_block(excerpt: str) -> str:
    if not excerpt:
        return ""
    return (
        "--- MQL5 EA CODE EXCERPT (latest uploaded for project) ---\n"
        f"{excerpt}\n"
        "--- END MQL5 EA CODE EXCERPT ---\n"
    )


def _mock_compare_response(
    symbol: str,
    timeframe: str,
    chart_url: str,
    ea_decision: str,
    ea_reasoning: str,
    notes: str,
    knowledge_excerpt: str,
    mql5_excerpt: str,
    tv_ctx: dict[str, Any],
) -> dict[str, Any]:
    sym = (tv_ctx.get("normalized_symbol") or symbol or "").strip() or "UNKNOWN"
    tf = (timeframe or "").strip() or "UNKNOWN"
    k = knowledge_excerpt or ""
    m = mql5_excerpt or ""

    # Lightweight heuristic: align EA wording with SMC-style caution from reference
    ea_l = (ea_decision or "").lower()
    model_action = "wait"
    if not k and not m:
        reason = (
            "Mock mode: configure SMC_KNOWLEDGE_DOCX_PATH in backend .env and upload MQL5 to the project "
            "so the model can mirror your reference + EA logic."
        )
    else:
        concepts: list[str] = []
        kl = k.lower()
        for label, needle in [
            ("BOS / structure", "bos"),
            ("CHOCH / character change", "choch"),
            ("order blocks", "order block"),
            ("FVG / imbalance", "fair value"),
            ("liquidity", "liquidity"),
            ("premium/discount", "premium"),
        ]:
            if needle in kl:
                concepts.append(label)
        concept_line = ", ".join(concepts[:4]) if concepts else "general SMC discipline from your reference"

        if any(x in ea_l for x in ("buy", "long", "bull")):
            model_action = "trade"
        elif any(x in ea_l for x in ("sell", "short", "bear")):
            model_action = "trade"
        elif "wait" in ea_l or "flat" in ea_l or "no trade" in ea_l:
            model_action = "wait"

        if model_action == "trade":
            reason = (
                f"Mock model (uses your Word reference + MQL5 excerpt, not live chart pixels): "
                f"On {sym} {tf}, the EA narrative suggests participation. "
                f"Against the reference ({concept_line}), the model would still want displacement + "
                f"clear structure (BOS/CHOCH) and a defended OB/FVG before committing — "
                f"treat this as a learning stub until MOCK_MODE/MOCK_LLM are off and vision/LLM sees the chart."
            )
        else:
            reason = (
                f"Mock model (reference-driven): For {sym} {tf}, default stance is WAIT until "
                f"structure + liquidity story in the reference ({concept_line}) lines up with the EA's rules "
                f"in the uploaded MQ5. EA stated: «{ea_decision or 'n/a'}». "
                f"TradingView page context: {tv_ctx.get('og_title') or 'not fetched'}."
            )

    agreement = _decision_agreement(ea_decision, model_action)
    hint = (
        "Set MOCK_MODE=false and MOCK_LLM=false in backend/.env, add OPENAI_API_KEY (or Anthropic), "
        "restart the backend, and re-run compare for a chart-aware JSON opinion."
    )
    if k:
        hint = (
            "Reference doc is loaded — with real LLM enabled, prompts include this knowledge + MQ5 + TradingView URL metadata."
        )

    return {
        "symbol": sym,
        "timeframe": tf,
        "chart_url": chart_url,
        "ea_decision": ea_decision,
        "model_decision": model_action,
        "agreement": agreement,
        "model_reasoning": reason,
        "improvement_hint": hint,
        "confidence": 52.0 if (k or m) else 40.0,
        "source": "mock_knowledge",
        "tradingview_context": {
            "fetch_ok": tv_ctx.get("fetch_ok"),
            "normalized_symbol": tv_ctx.get("normalized_symbol"),
            "og_title": tv_ctx.get("og_title"),
            "note": tv_ctx.get("note"),
        },
        "knowledge_loaded": bool(k),
        "mql5_excerpt_chars": len(m),
    }


def compare_ea_vs_model_on_tradingview(
    symbol: str,
    timeframe: str,
    chart_url: str,
    ea_decision: str,
    ea_reasoning: str = "",
    notes: str = "",
    db: Optional[Session] = None,
    project_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    TradingView-first learning: EA vs model under SMC + optional Word reference + MQ5.
    """
    settings = get_settings()
    tv_ctx = fetch_tradingview_chart_context(chart_url)
    url_sym = tv_ctx.get("normalized_symbol") or parse_tradingview_symbol_from_url(chart_url)
    eff_symbol = (symbol or "").strip() or (url_sym or "")
    knowledge_excerpt = get_uploaded_knowledge_excerpt(db, project_id, settings.smc_knowledge_max_chars)
    if not knowledge_excerpt:
        knowledge_excerpt = get_external_knowledge_excerpt(settings.smc_knowledge_max_chars)
    mql5_excerpt = get_latest_mql5_excerpt(db, project_id, max_chars=6000)
    tv_prompt_block = format_tradingview_context_for_prompt(tv_ctx)

    if settings.mock_mode or settings.mock_llm:
        return _mock_compare_response(
            eff_symbol,
            timeframe,
            chart_url,
            ea_decision,
            ea_reasoning,
            notes,
            knowledge_excerpt,
            mql5_excerpt,
            tv_ctx,
        )

    llm = get_llm_service()
    ref_block = get_reference_block_for_prompt(db=db, project_id=project_id)
    mq_block = _mql5_block(mql5_excerpt)

    system = (
        "You are a senior SMC strategist and MQL5 EA reviewer. "
        "Use ONLY the provided reference knowledge, MQ5 excerpt, and TradingView URL context. "
        "If the chart was not visually supplied, say what is missing and stay conservative. "
        "Return strict JSON only."
    )
    prompt = f"""Compare EA decision vs your model decision for this TradingView scenario.

{tv_prompt_block}

Resolved symbol: {eff_symbol}
Timeframe: {timeframe}
EA decision: {ea_decision}
EA reasoning: {ea_reasoning}
User notes: {notes}

{ref_block}
{mq_block}

Return a JSON object with keys:
- model_decision: "trade" | "wait" | "avoid"
- agreement: "agree" | "partial" | "disagree"
- model_reasoning: concise SMC + EA-logic rationale citing reference concepts where relevant
- ea_gap: what the EA logic may miss vs the reference (or empty string)
- improvement_hint: one concrete, non-curve-fit improvement
- confidence: number 0-100
"""
    raw = llm.complete(prompt, system=system, temperature=0.2)
    data = _extract_json(raw)
    return {
        "symbol": eff_symbol,
        "timeframe": timeframe,
        "chart_url": chart_url,
        "ea_decision": ea_decision,
        "model_decision": data.get("model_decision", "wait"),
        "agreement": data.get("agreement", "partial"),
        "model_reasoning": data.get("model_reasoning", raw[:800]),
        "ea_gap": data.get("ea_gap", ""),
        "improvement_hint": data.get("improvement_hint", ""),
        "confidence": float(data.get("confidence", 60.0)),
        "source": "llm",
        "tradingview_context": {
            "fetch_ok": tv_ctx.get("fetch_ok"),
            "normalized_symbol": tv_ctx.get("normalized_symbol"),
            "og_title": tv_ctx.get("og_title"),
            "note": tv_ctx.get("note"),
        },
        "knowledge_loaded": bool(knowledge_excerpt),
        "mql5_excerpt_chars": len(mql5_excerpt),
    }


def _decision_agreement(ea_decision: str, model_decision: str) -> str:
    e = re.sub(r"\s+", " ", (ea_decision or "").lower()).strip()
    m = (model_decision or "").lower()
    if m in e or (m == "trade" and ("buy" in e or "sell" in e or "long" in e or "short" in e)):
        return "agree"
    if m == "wait" and ("wait" in e or "no trade" in e or "flat" in e):
        return "agree"
    if ("wait" in e and m == "avoid") or ("avoid" in e and m == "wait"):
        return "partial"
    return "disagree"


def _extract_json(raw: str) -> dict[str, Any]:
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(raw[start:end])
    except Exception:
        pass
    return {}
