from __future__ import annotations

from typing import Any

# -----------------------------------------------------------------------
# SMC Knowledge Base — canonical definitions, rules, and validation logic
# -----------------------------------------------------------------------

SMC_CONCEPT_KEYWORDS = [
    "bos", "choch", "order_block", "fvg", "liquidity_sweep",
    "inducement", "displacement", "premium_discount", "mitigation",
    "session_filter", "trend_bias", "soft_reversal", "invalidation",
    "sl_placement", "tp_logic", "risk_reward",
]

SMC_KNOWLEDGE_BASE: dict[str, dict[str, Any]] = {
    "bos": {
        "name": "Break of Structure (BOS)",
        "description": (
            "A Break of Structure occurs when price closes beyond a significant "
            "swing high (bullish BOS) or swing low (bearish BOS), confirming continuation "
            "of the prevailing trend. BOS identifies institutional order flow direction."
        ),
        "bullish_condition": "Price closes above the most recent confirmed swing high",
        "bearish_condition": "Price closes below the most recent confirmed swing low",
        "trade_implication": "Trade in the direction of BOS — look for pullbacks to valid OBs",
        "invalidation": "Opposite BOS or CHOCH in the same structure",
        "common_mistakes": [
            "Trading BOS on low-timeframe without HTF confirmation",
            "Treating every new high/low as a BOS",
            "Ignoring spread/liquidity context",
        ],
        "quality_factors": ["size of break", "volume on break candle", "displacement after break"],
    },
    "choch": {
        "name": "Change of Character (CHOCH)",
        "description": (
            "A Change of Character signals a potential reversal of the current trend. "
            "Bullish CHOCH: price breaks above a swing high in a downtrend. "
            "Bearish CHOCH: price breaks below a swing low in an uptrend. "
            "CHOCH is the first signal of institutional interest changing direction."
        ),
        "bullish_condition": "Price breaks above a previous swing high while in a downtrend",
        "bearish_condition": "Price breaks below a previous swing low while in an uptrend",
        "trade_implication": "Potential reversal — wait for pullback to new OB formed by CHOCH move",
        "invalidation": "Return to previous trend without forming a new structure",
        "confirmation_required": "Displacement candle on CHOCH break is required for high-quality signals",
        "common_mistakes": [
            "Trading CHOCH without displacement confirmation",
            "Ignoring macro trend context",
        ],
    },
    "order_block": {
        "name": "Order Block (OB)",
        "description": (
            "An Order Block is the last opposite-direction candle before a strong "
            "displacement move. It represents institutional order accumulation/distribution. "
            "Bullish OB: last bearish candle before a bullish impulse. "
            "Bearish OB: last bullish candle before a bearish impulse."
        ),
        "bullish_condition": "Last bearish candle before a bullish BOS displacement",
        "bearish_condition": "Last bullish candle before a bearish BOS displacement",
        "entry_zone": "High to low of the OB candle (or just the body)",
        "invalidation": "Price closes through the OB without respecting it",
        "quality_factors": [
            "Formed after liquidity sweep",
            "Strong displacement after OB",
            "First touch (unmitigated)",
            "Sits in premium/discount zone",
            "FVG inside or after OB",
        ],
        "common_mistakes": [
            "Trading mitigated (already visited) OBs",
            "Trading OBs without displacement",
            "Ignoring premium/discount context",
        ],
    },
    "fvg": {
        "name": "Fair Value Gap (FVG) / Imbalance",
        "description": (
            "A Fair Value Gap is a three-candle pattern where candle 1's high/low "
            "does not overlap with candle 3's low/high, creating a price imbalance. "
            "FVGs represent one-sided institutional activity and tend to get filled."
        ),
        "bullish_condition": "Candle 3 low is above Candle 1 high (gap not touched by any wick)",
        "bearish_condition": "Candle 3 high is below Candle 1 low",
        "entry_zone": "50% of the FVG for high-probability entries",
        "invalidation": "Price completely fills the FVG (closes through it)",
        "use_cases": ["Entry confluence with OB", "Support/resistance levels", "TP target"],
    },
    "liquidity_sweep": {
        "name": "Liquidity Sweep / Stop Hunt",
        "description": (
            "A liquidity sweep occurs when price briefly moves to grab liquidity "
            "(stop losses sitting above/below equal highs/lows or swing points) "
            "before reversing. This is the engineered move before a true institutional trade."
        ),
        "buy_side_liquidity": "Equal highs, previous swing highs, resistance levels",
        "sell_side_liquidity": "Equal lows, previous swing lows, support levels",
        "confirmation": "Wick through liquidity level followed by close in opposite direction",
        "trade_implication": "After sweep, look for immediate OB or FVG for entry",
        "quality_factors": ["Volume spike on sweep", "Speed of reversal", "Displacement after sweep"],
    },
    "inducement": {
        "name": "Inducement",
        "description": (
            "Inducement is a small structure created to lure retail traders into a position "
            "before the true move. It looks like a valid setup but acts as bait. "
            "Usually a small swing high/low before a true BOS."
        ),
        "warning": "Trading inducement setups leads to stop-outs before the true move",
        "identification": "Small equal highs/lows before a larger structural move",
    },
    "displacement": {
        "name": "Displacement",
        "description": (
            "Displacement is a strong, fast move by price that creates imbalance. "
            "It represents institutional order execution. Displacement should close "
            "beyond structure (creating a BOS/CHOCH) and leave a FVG or OB."
        ),
        "minimum_threshold": "Typically > 1.5x ATR14 in pips for valid displacement",
        "quality_factors": ["Body size of displacement candle", "FVG created", "Volume"],
        "trade_implication": "Price will often return to the OB/FVG created by displacement",
    },
    "premium_discount": {
        "name": "Premium / Discount Zones",
        "description": (
            "Price is divided into Premium (above 50% of a swing range) and Discount (below 50%). "
            "Institutions buy in Discount and sell in Premium. "
            "Never buy in Premium, never sell in Discount — this filters poor entries."
        ),
        "calculation": "Fibonacci 50% of the most recent swing range",
        "buy_zone": "Below 50% (Discount) — valid long entries only",
        "sell_zone": "Above 50% (Premium) — valid short entries only",
        "optimal_entry_zone": "OTE: 62-79% retracement (Fibonacci 0.62 - 0.79)",
        "common_mistakes": ["Buying in premium", "Selling in discount"],
    },
    "mitigation": {
        "name": "Mitigation / Breaker Block",
        "description": (
            "Mitigation occurs when price returns to an OB and 'uses' it — "
            "either respecting it (bouncing) or breaking through it (invalidation). "
            "A Breaker Block is an OB that has been mitigated and becomes the opposite type."
        ),
        "post_mitigation": "After first touch, OB may still hold for a second touch but quality decreases",
        "breaker": "Mitigated OB that price breaks through — now acts as opposing OB",
    },
    "soft_reversal": {
        "name": "Soft Reversal",
        "description": (
            "A Soft Reversal is when the original trend bias shows signs of exhaustion "
            "and a counter-direction trade becomes valid. Triggered by a CHOCH after "
            "multiple consecutive BOS in the same direction without meaningful pullback. "
            "Not a full trend reversal — controlled counter-trades with smaller risk."
        ),
        "trigger_conditions": [
            "3+ consecutive BOS in same direction",
            "CHOCH forms at HTF premium/discount extreme",
            "Displacement in counter direction",
            "New OB formed in counter direction",
        ],
        "risk_management": "Use smaller lot size (50% of normal) for counter-trades",
        "invalidation": "Price breaks through the CHOCH level without forming new structure",
    },
    "session_filter": {
        "name": "Session Filter",
        "description": (
            "Trading sessions have different liquidity and volatility profiles. "
            "London and NY sessions offer the highest institutional participation. "
            "Asian session is generally lower quality for SMC setups."
        ),
        "sessions": {
            "asian": {"utc": "00:00-09:00", "quality": "low", "notes": "Range-building, avoid"},
            "london": {"utc": "07:00-16:00", "quality": "high", "notes": "Best for SMC"},
            "new_york": {"utc": "12:00-21:00", "quality": "high", "notes": "Best for SMC"},
            "overlap": {"utc": "12:00-16:00", "quality": "highest", "notes": "Most volatile"},
        },
        "recommendation": "Trade London and NY sessions only for highest-quality SMC setups",
    },
}


def get_concept_explanation(concept: str) -> dict:
    return SMC_KNOWLEDGE_BASE.get(concept, {"error": f"Unknown concept: {concept}"})


def validate_trade_against_smc(trade_context: dict) -> dict[str, Any]:
    """
    Validate a trade setup against SMC principles.
    Returns a score and list of violations/confirmations.
    """
    score = 100
    confirmations: list[str] = []
    violations: list[str] = []
    warnings: list[str] = []

    direction = trade_context.get("direction", "buy")
    in_premium = trade_context.get("in_premium_zone", None)
    in_discount = trade_context.get("in_discount_zone", None)
    has_ob = trade_context.get("has_order_block", False)
    ob_mitigated = trade_context.get("ob_mitigated", False)
    has_displacement = trade_context.get("has_displacement", False)
    has_liquidity_sweep = trade_context.get("has_liquidity_sweep", False)
    has_fvg = trade_context.get("has_fvg", False)
    bos_confirmed = trade_context.get("bos_confirmed", False)
    choch_confirmed = trade_context.get("choch_confirmed", False)
    session = trade_context.get("session", "unknown")

    # Premium/Discount check
    if direction == "buy" and in_premium:
        violations.append("Buying in Premium zone — avoid")
        score -= 30
    elif direction == "sell" and in_discount:
        violations.append("Selling in Discount zone — avoid")
        score -= 30
    elif direction == "buy" and in_discount:
        confirmations.append("Buying in Discount zone — valid")
    elif direction == "sell" and in_premium:
        confirmations.append("Selling in Premium zone — valid")

    # Order Block
    if not has_ob:
        violations.append("No Order Block identified for entry")
        score -= 25
    elif ob_mitigated:
        warnings.append("OB has been previously mitigated — reduced quality")
        score -= 10
    else:
        confirmations.append("Valid, unmitigated Order Block")

    # Displacement
    if not has_displacement:
        warnings.append("No displacement detected — OB quality may be low")
        score -= 10
    else:
        confirmations.append("Displacement confirmed — high-quality OB")

    # Liquidity sweep
    if has_liquidity_sweep:
        confirmations.append("Liquidity sweep detected before OB — institutional signal")
        score += 5
    else:
        warnings.append("No liquidity sweep — setup may be retail-level")

    # FVG
    if has_fvg:
        confirmations.append("FVG present — additional confluence")
        score += 5

    # Structure confirmation
    if bos_confirmed:
        confirmations.append("BOS confirmed in entry direction")
    elif choch_confirmed:
        confirmations.append("CHOCH confirmed — potential reversal trade")
    else:
        violations.append("No BOS or CHOCH — no structural confirmation")
        score -= 20

    # Session
    if session in ["london", "new_york", "overlap"]:
        confirmations.append(f"Valid session: {session}")
    elif session == "asian":
        warnings.append("Asian session — lower quality, consider skipping")
        score -= 15

    score = max(0, min(100, score))

    return {
        "score": score,
        "grade": _score_to_grade(score),
        "confirmations": confirmations,
        "violations": violations,
        "warnings": warnings,
        "recommendation": "TRADE" if score >= 70 else ("CAUTION" if score >= 50 else "SKIP"),
    }


def _score_to_grade(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def get_improvement_categories() -> list[dict]:
    return [
        {"id": "soft_reversal", "name": "Soft Reversal Logic", "risk": "medium"},
        {"id": "ob_quality", "name": "Order Block Quality Filter", "risk": "low"},
        {"id": "session_filter", "name": "Session Filter", "risk": "low"},
        {"id": "displacement_filter", "name": "Displacement Threshold", "risk": "low"},
        {"id": "liquidity_sweep_confirm", "name": "Liquidity Sweep Confirmation", "risk": "low"},
        {"id": "premium_discount_filter", "name": "Premium/Discount Entry Filter", "risk": "low"},
        {"id": "dynamic_sl", "name": "Dynamic Stop Loss (ATR-based)", "risk": "medium"},
        {"id": "break_even", "name": "Break-Even Logic", "risk": "low"},
        {"id": "partial_close", "name": "Partial Close at 1R", "risk": "low"},
        {"id": "trailing_stop", "name": "Trailing Stop Logic", "risk": "medium"},
        {"id": "consecutive_loss_stop", "name": "Consecutive Loss Protection", "risk": "low"},
        {"id": "spread_filter", "name": "Dynamic Spread Filter", "risk": "low"},
        {"id": "volatility_filter", "name": "Volatility Filter (ATR)", "risk": "low"},
        {"id": "ob_invalidation", "name": "Dynamic OB Invalidation", "risk": "medium"},
        {"id": "counter_trade", "name": "Counter-Direction After Exhaustion", "risk": "high"},
        {"id": "fvg_confirmation", "name": "FVG Entry Confirmation", "risk": "low"},
        {"id": "time_filter", "name": "Time-of-Day Filter", "risk": "low"},
        {"id": "trade_frequency", "name": "Trade Frequency Limiter", "risk": "low"},
    ]
