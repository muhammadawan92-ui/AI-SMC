from __future__ import annotations

import logging
import re
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.models import PineScriptSource, UploadedFile
from app.services.llm_service import get_llm_service
from app.services.smc_logic_service import SMC_CONCEPT_KEYWORDS

logger = logging.getLogger(__name__)

SMC_PINE_PATTERNS: dict[str, list[str]] = {
    "bos": ["bos", "break_of_structure", "breakofstructure", "break of structure"],
    "choch": ["choch", "change_of_character", "changeofcharacter", "change of character"],
    "order_block": ["order_block", "orderblock", "ob_", "_ob", "bullish_ob", "bearish_ob"],
    "fvg": ["fvg", "fair_value_gap", "imbalance", "gap_up", "gap_down"],
    "liquidity_sweep": ["liquidity_sweep", "sweep", "stop_hunt", "equal_highs", "equal_lows"],
    "premium_discount": ["premium", "discount", "equilibrium", "fibonacci", "fib_50"],
    "displacement": ["displacement", "impulse", "strong_move"],
    "inducement": ["inducement", "false_break"],
    "mitigation": ["mitigation", "mitigated", "breaker"],
    "session": ["session", "london", "new_york", "tokyo", "asian"],
    "trend_bias": ["trend", "bias", "bullish", "bearish", "direction"],
    "sl_tp": ["stoploss", "stop_loss", "takeprofit", "take_profit", "sl_", "tp_"],
}

INPUT_PATTERN = re.compile(
    r"(\w+)\s*=\s*input(?:\.\w+)?\s*\(([^)]*)\)",
    re.IGNORECASE,
)
VAR_ASSIGN_PATTERN = re.compile(r"^(?:var\s+)?(\w+)\s*:=\s*(.+)$", re.MULTILINE)
FUNCTION_DEF_PATTERN = re.compile(r"^(\w+)\s*\(([^)]*)\)\s*=>", re.MULTILINE)
CONDITION_PATTERN = re.compile(r"if\s+(.+?)(?:\n|$)", re.IGNORECASE)


def parse_pine_script(
    code: str,
    file_record: Optional[UploadedFile],
    db: Session,
    project_id: str,
    run_llm: bool = True,
) -> PineScriptSource:
    detected_smc = _detect_smc_concepts(code)
    inputs = _extract_inputs(code)
    entry_conds = _extract_conditions(code, keywords=["entry", "long", "short", "buy", "sell"])
    exit_conds = _extract_conditions(code, keywords=["close", "exit", "tp", "sl", "profit", "loss"])
    filters = _extract_conditions(code, keywords=["filter", "session", "spread", "atr"])
    sessions = _extract_sessions(code)
    risk = _extract_risk_logic(code)

    ai_analysis = ""
    if run_llm:
        ai_analysis = _llm_analyze_pine(code, detected_smc)

    source = PineScriptSource(
        project_id=project_id,
        file_id=file_record.id if file_record else None,
        raw_code=code,
        summary=_generate_summary(detected_smc, inputs, entry_conds),
        detected_smc_concepts=detected_smc,
        entry_conditions=entry_conds,
        exit_conditions=exit_conds,
        filter_conditions=filters,
        indicators_used=inputs,
        session_filters=sessions,
        risk_logic=risk,
        ai_analysis=ai_analysis,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def _detect_smc_concepts(code: str) -> list[str]:
    code_lower = code.lower()
    found: list[str] = []
    for concept, keywords in SMC_PINE_PATTERNS.items():
        if any(kw in code_lower for kw in keywords):
            found.append(concept)
    return found


def _extract_inputs(code: str) -> list[dict]:
    inputs = []
    for match in INPUT_PATTERN.finditer(code):
        name = match.group(1)
        args = match.group(2).strip()
        # First positional arg is the default value or title
        parts = [p.strip() for p in args.split(",")]
        inputs.append({"name": name, "args": parts[:3]})
    return inputs


def _extract_conditions(code: str, keywords: list[str]) -> list[str]:
    conditions: list[str] = []
    lines = code.splitlines()
    for i, line in enumerate(lines):
        lower = line.lower()
        if any(kw in lower for kw in keywords):
            stripped = line.strip()
            if stripped and not stripped.startswith("//"):
                conditions.append(stripped[:200])  # cap length
    return list(dict.fromkeys(conditions))[:30]  # deduplicate, max 30


def _extract_sessions(code: str) -> list[str]:
    sessions: list[str] = []
    code_lower = code.lower()
    session_keywords = {
        "london": "London (07:00-16:00 UTC)",
        "new_york": "New York (12:00-21:00 UTC)",
        "tokyo": "Tokyo (00:00-09:00 UTC)",
        "asian": "Asian (00:00-09:00 UTC)",
        "ny_open": "NY Open (13:30-15:00 UTC)",
    }
    for key, label in session_keywords.items():
        if key in code_lower:
            sessions.append(label)
    return sessions


def _extract_risk_logic(code: str) -> dict:
    risk: dict[str, Any] = {}
    code_lower = code.lower()

    pct_match = re.search(r"risk[_\s]*(?:percent|pct|per[_\s]*trade)?\s*=\s*input[^(]*\(([0-9.]+)", code_lower)
    if pct_match:
        risk["risk_percent"] = float(pct_match.group(1))

    rr_match = re.search(r"(?:rr|risk_reward|reward)[_\s]*=\s*input[^(]*\(([0-9.]+)", code_lower)
    if rr_match:
        risk["risk_reward"] = float(rr_match.group(1))

    if "atr" in code_lower:
        risk["sl_type"] = "ATR-based"
    elif "swing" in code_lower or "high" in code_lower:
        risk["sl_type"] = "Swing-based"

    if "trailingStop" in code or "trailing" in code_lower:
        risk["trailing_stop"] = True
    if "breakeven" in code_lower or "break_even" in code_lower:
        risk["break_even"] = True
    if "partial" in code_lower:
        risk["partial_close"] = True

    return risk


def _generate_summary(detected_smc: list, inputs: list, entry_conds: list) -> str:
    concepts = ", ".join(detected_smc) if detected_smc else "None detected"
    n_inputs = len(inputs)
    n_entry = len(entry_conds)
    return (
        f"Pine Script uses SMC concepts: {concepts}. "
        f"Contains {n_inputs} configurable inputs and {n_entry} entry condition lines."
    )


def _llm_analyze_pine(code: str, detected_smc: list) -> str:
    llm = get_llm_service()
    system = (
        "You are a senior quant developer and SMC trading strategist. "
        "Analyze Pine Script code and provide a clear, structured analysis."
    )
    prompt = f"""Analyze this Pine Script trading strategy code:

```pinescript
{code[:6000]}
```

Detected SMC concepts: {detected_smc}

Provide:
1. Strategy type and overall logic
2. Entry conditions (step by step)
3. Exit conditions (SL and TP)
4. Session and time filters
5. Risk management logic
6. SMC concepts used and how
7. Any weaknesses or gaps in the logic
8. How well it implements SMC principles (1-10 score with explanation)

Be specific and reference the actual code."""
    try:
        return llm.complete(prompt, system=system)
    except Exception as e:
        logger.error("LLM pine analysis failed: %s", e)
        return f"LLM analysis failed: {e}"
