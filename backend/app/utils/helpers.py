from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional


def parse_mt5_datetime(s: str) -> Optional[datetime]:
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y.%m.%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            pass
    return None


def extract_float(s: Any) -> Optional[float]:
    if isinstance(s, (int, float)):
        return float(s)
    if not isinstance(s, str):
        return None
    cleaned = re.sub(r"[^\d.\-]", "", s.replace(",", ""))
    try:
        return float(cleaned)
    except ValueError:
        return None


def classify_session(hour_utc: int) -> str:
    if 0 <= hour_utc < 7:
        return "asian"
    if 7 <= hour_utc < 12:
        return "london"
    if 12 <= hour_utc < 16:
        return "overlap"
    if 16 <= hour_utc < 21:
        return "new_york"
    return "off_hours"


def calculate_risk_reward(entry: float, sl: float, tp: float) -> Optional[float]:
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk == 0:
        return None
    return round(reward / risk, 2)


def format_pct(val: Optional[float], decimals: int = 1) -> str:
    if val is None:
        return "N/A"
    return f"{val:.{decimals}f}%"


def format_usd(val: Optional[float], decimals: int = 2) -> str:
    if val is None:
        return "N/A"
    sign = "" if val >= 0 else "-"
    return f"{sign}${abs(val):.{decimals}f}"


def clamp(val: float, low: float, high: float) -> float:
    return max(low, min(high, val))


def chunk_text(text: str, max_chars: int = 6000) -> list[str]:
    chunks: list[str] = []
    while text:
        chunks.append(text[:max_chars])
        text = text[max_chars:]
    return chunks
