from __future__ import annotations

import logging
import re
from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import MQL5Source, UploadedFile
from app.services.llm_service import get_llm_service

logger = logging.getLogger(__name__)

INPUT_PATTERN = re.compile(r'input\s+\w+\s+(\w+)\s*=\s*([^;]+);', re.IGNORECASE)
SINPUT_PATTERN = re.compile(r'sinput\s+\w+\s+(\w+)\s*=\s*([^;]+);', re.IGNORECASE)
FUNC_PATTERN = re.compile(r'(?:void|int|double|bool|string)\s+(\w+)\s*\(([^)]*)\)', re.IGNORECASE)
COMMENT_LINE = re.compile(r'^\s*//')
BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.DOTALL)

SMC_MQL5_PATTERNS: dict[str, list[str]] = {
    "bos": ["BOS", "BreakOfStructure", "break_of_structure", "SwingHigh", "SwingLow"],
    "choch": ["CHOCH", "ChangeOfCharacter", "change_of_character"],
    "order_block": ["OrderBlock", "OB_", "_OB", "BullishOB", "BearishOB", "order_block"],
    "fvg": ["FVG", "FairValueGap", "Imbalance", "GapUp", "GapDown"],
    "liquidity": ["Liquidity", "SweepHigh", "SweepLow", "EqualHigh", "EqualLow"],
    "premium_discount": ["Premium", "Discount", "Equilibrium", "Fibonacci"],
    "displacement": ["Displacement", "Impulse", "StrongMove"],
    "session": ["Session", "London", "NewYork", "Tokyo", "Asian"],
    "sl_tp": ["StopLoss", "TakeProfit", "SL_", "TP_", "sl_price", "tp_price"],
    "trailing": ["TrailingStop", "Trailing", "trailing"],
    "breakeven": ["BreakEven", "break_even", "MoveToBreakEven"],
}


def parse_mql5_ea(
    code: str,
    file_record: Optional[UploadedFile],
    db: Session,
    project_id: str,
    pine_source_code: Optional[str] = None,
    run_llm: bool = True,
) -> MQL5Source:
    clean_code = _strip_comments(code)
    detected_smc = _detect_smc_concepts(code)
    input_params = _extract_inputs(code)
    entry_logic = _extract_section(clean_code, ["entry", "open_trade", "opentrade", "place_order", "OrderSend"])
    exit_logic = _extract_section(clean_code, ["close", "exit", "ClosePosition", "OrderClose"])
    sl_tp_logic = _extract_section(clean_code, ["StopLoss", "TakeProfit", "sl_price", "tp_price"])
    filter_logic = _extract_section(clean_code, ["filter", "session", "spread", "IsAllowed"])

    ai_analysis = ""
    pine_diff = ""
    if run_llm:
        ai_analysis = _llm_analyze_mql5(code, detected_smc, input_params)
        if pine_source_code:
            pine_diff = _llm_compare_pine_ea(pine_source_code, code)

    source = MQL5Source(
        project_id=project_id,
        file_id=file_record.id if file_record else None,
        raw_code=code,
        summary=_generate_summary(detected_smc, input_params),
        detected_smc_concepts=detected_smc,
        input_parameters=input_params,
        entry_logic=entry_logic,
        exit_logic=exit_logic,
        sl_tp_logic=sl_tp_logic,
        filter_logic=filter_logic,
        pine_vs_ea_diff=pine_diff,
        ai_analysis=ai_analysis,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def _strip_comments(code: str) -> str:
    code = BLOCK_COMMENT.sub("", code)
    lines = [l for l in code.splitlines() if not COMMENT_LINE.match(l)]
    return "\n".join(lines)


def _detect_smc_concepts(code: str) -> list[str]:
    found: list[str] = []
    for concept, patterns in SMC_MQL5_PATTERNS.items():
        if any(p in code for p in patterns):
            found.append(concept)
    return found


def _extract_inputs(code: str) -> list[dict]:
    params: list[dict] = []
    for m in INPUT_PATTERN.finditer(code):
        params.append({"name": m.group(1), "default": m.group(2).strip(), "type": "input"})
    for m in SINPUT_PATTERN.finditer(code):
        params.append({"name": m.group(1), "default": m.group(2).strip(), "type": "sinput"})
    return params


def _extract_section(code: str, keywords: list[str]) -> str:
    lines = code.splitlines()
    relevant: list[str] = []
    for i, line in enumerate(lines):
        lower = line.lower()
        if any(kw.lower() in lower for kw in keywords):
            start = max(0, i - 2)
            end = min(len(lines), i + 5)
            relevant.extend(lines[start:end])
            relevant.append("---")
    return "\n".join(relevant[:100])  # max 100 lines


def _generate_summary(detected_smc: list, input_params: list) -> str:
    concepts = ", ".join(detected_smc) if detected_smc else "None detected"
    n_params = len(input_params)
    return f"MQL5 EA implements SMC: {concepts}. Has {n_params} input parameters."


def _llm_analyze_mql5(code: str, detected_smc: list, input_params: list) -> str:
    llm = get_llm_service()
    system = (
        "You are a senior MQL5 developer and SMC trading strategist. "
        "Analyze MQL5 Expert Advisor code thoroughly."
    )
    param_list = ", ".join(p["name"] for p in input_params[:20])
    prompt = f"""Analyze this MQL5 Expert Advisor code:

```cpp
{code[:6000]}
```

Detected SMC concepts: {detected_smc}
Input parameters: {param_list}

Provide:
1. EA structure overview (event handlers used)
2. Entry logic — exact conditions
3. Exit logic — SL/TP placement method
4. Filter logic (session, spread, etc.)
5. Order management (lot sizing, risk)
6. SMC implementation quality (1-10)
7. Potential bugs or logic issues
8. Execution timing risks (tick vs bar logic)

Be specific and reference the actual code."""
    try:
        return llm.complete(prompt, system=system)
    except Exception as e:
        logger.error("LLM MQL5 analysis failed: %s", e)
        return f"LLM analysis failed: {e}"


def _llm_compare_pine_ea(pine_code: str, mql5_code: str) -> str:
    llm = get_llm_service()
    system = "You are a senior quant developer who specializes in translating Pine Script to MQL5."
    prompt = f"""Compare this Pine Script strategy with its MQL5 EA implementation.

Pine Script:
```pinescript
{pine_code[:3000]}
```

MQL5 EA:
```cpp
{mql5_code[:3000]}
```

Identify:
1. Logic differences (what's implemented differently)
2. Missing features (in EA but not Pine, or vice versa)
3. Implementation errors (things that should match but don't)
4. Execution timing differences
5. Parameter differences
6. SMC concept implementation differences

Rate the fidelity of the translation (1-10) with explanation."""
    try:
        return llm.complete(prompt, system=system)
    except Exception as e:
        logger.error("LLM comparison failed: %s", e)
        return f"Comparison failed: {e}"
