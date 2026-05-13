"""
Zone Refinement V4 (opus, research-only)

This experimental backtest is built around one explicit hypothesis from the V2 /
V2_max2 evidence:

  > Direction (bias) is usually correct. What fails is zone quality, OB
  > refinement, entry placement, and SL placement.

V4 therefore does NOT add direction filters and is NOT buy-only or sell-only.
It builds a zone-quality score and converts that score into execution.

  - score >= 6 : full-risk OB mitigation entry (limit), normal risk_percent
  - score 4..5 : reduced-risk (0.5%) AI-zone market entry after pending miss
  - score <  4 : skipped as weak zone (reason recorded)

It also enforces:
  - skip AT_BUY_ENTRY / AT_SELL_ENTRY (they were ~0% WR in V2_max2)
  - prefer M15 OB inside H1 supply/demand context, M5 only as trigger
  - true premium/discount thresholds (0.382/0.618/0.705/0.886/0.295/0.114)
  - liquidity-sweep + CHoCH/BOS qualifier for retracements
  - opposing-H1-zone-in-the-way penalty (so we don't sell into demand or buy
    into supply that sits between entry and TP)
  - SL protection: M5 inside H1 ctx -> use M15/H1 midpoint, never bare-M5

Outputs are research-only. The script never imports demo executor / monitor.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

from smc_core import (
    BEARISH,
    BULLISH,
    choose_h1_swing_range,
    choose_internal_event,
    decide_trade_context,
    detect_structure,
    fib_prices,
    get_entry_status,
    is_ob_invalidated,
    last_valid_ob,
    most_recent_ob,
    pd_range_detail,
    price_inside_ob,
    price_location,
)

try:
    from smc_core import find_rejection_order_block
except Exception:  # pragma: no cover - fallback only used if smc_core is older
    def find_rejection_order_block(*args, **kwargs):
        return None


# ============================================================================
# Constants and CSV mapping (mirror V2 max2 to keep data identical)
# ============================================================================
MANUAL_BARS_COLUMN_MAP: dict[str, str] = {}
MANUAL_TICKS_COLUMN_MAP: dict[str, str] = {}

BARS_CSV = r"C:\Users\osama\OneDrive\New folder\trading strateges\AI GENRATED\GBPUSD OHLC DATA\GBPUSD_mt5_bars.csv"
TICKS_CSV = r"C:\Users\osama\OneDrive\New folder\trading strateges\AI GENRATED\GBPUSD OHLC DATA\GBPUSD_mt5_ticks.csv"
POINT_SIZE = 0.00001
PIP_SIZE = 0.0001
VALID_DECISIONS = {
    "BUY_CONTINUATION",
    "SELL_CONTINUATION",
    "BUY_RETRACEMENT",
    "SELL_RETRACEMENT",
}

STRATEGY_VERSION = "zone_refinement_v4_opus"


# Runtime-resolved PD thresholds (set once in main, used in scoring hot path).
_PD_THRESHOLDS: dict[str, float] = {
    "premium_start": 0.618,
    "deep_premium": 0.705,
    "extreme_premium": 0.886,
    "discount_start": 0.382,
    "deep_discount": 0.295,
    "extreme_discount": 0.114,
}


def _resolve_pd_thresholds_from_env() -> None:
    """Cache PD env values once so per-bar scoring does not pay 6x os.getenv per signal."""
    _PD_THRESHOLDS["premium_start"] = env_float("SMC_PD_PREMIUM_START", 0.618)
    _PD_THRESHOLDS["deep_premium"] = env_float("SMC_PD_DEEP_PREMIUM", 0.705)
    _PD_THRESHOLDS["extreme_premium"] = env_float("SMC_PD_EXTREME_PREMIUM", 0.886)
    _PD_THRESHOLDS["discount_start"] = env_float("SMC_PD_DISCOUNT_START", 0.382)
    _PD_THRESHOLDS["deep_discount"] = env_float("SMC_PD_DEEP_DISCOUNT", 0.295)
    _PD_THRESHOLDS["extreme_discount"] = env_float("SMC_PD_EXTREME_DISCOUNT", 0.114)


def _pd_label_fast(price: float, swing_low: float, swing_high: float) -> tuple[str, float]:
    rng = float(swing_high) - float(swing_low)
    if rng <= 0:
        return "equilibrium", 0.5
    pos = (float(price) - float(swing_low)) / rng
    t = _PD_THRESHOLDS
    if pos >= t["extreme_premium"]:
        label = "extreme_premium"
    elif pos >= t["deep_premium"]:
        label = "deep_premium"
    elif pos >= t["premium_start"]:
        label = "true_premium"
    elif pos > 0.5:
        label = "above_EQ_not_true_premium"
    elif pos <= t["extreme_discount"]:
        label = "extreme_discount"
    elif pos <= t["deep_discount"]:
        label = "deep_discount"
    elif pos <= t["discount_start"]:
        label = "true_discount"
    elif pos < 0.5:
        label = "below_EQ_not_true_discount"
    else:
        label = "equilibrium"
    return label, float(pos)


# ============================================================================
# Settings + env helpers
# ============================================================================
@dataclass
class BacktestSettings:
    initial_balance: float
    risk_percent: float
    reduced_risk_percent: float
    rr: float
    ob_buffer_pips: float
    order_expiry_hours: int
    max_open_trades: int
    spread_points: float
    slippage_points: float
    commission_per_lot: float
    save_skipped: bool
    use_ticks: bool
    score_full_entry: int
    score_ai_zone_entry: int
    target_min_trades: int
    target_max_trades: int
    target_profit_factor: float


def env_float(name: str, default: float) -> float:
    v = os.getenv(name, "").strip()
    if not v:
        return default
    try:
        return float(v)
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    v = os.getenv(name, "").strip()
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def str_to_bool(v: str) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name, "").strip()
    if not v:
        return default
    return str_to_bool(v)


# ============================================================================
# CSV loading (identical to V2 max2 - we want the SAME bars/ticks)
# ============================================================================
def detect_column(columns: list[str], aliases: list[str]) -> str | None:
    lowered = {c.lower().strip(): c for c in columns}
    for alias in aliases:
        if alias in lowered:
            return lowered[alias]
    for c in columns:
        cl = c.lower()
        if any(alias in cl for alias in aliases):
            return c
    return None


def looks_like_data_header(columns: list[str]) -> bool:
    if not columns:
        return False
    score = 0
    for c in columns[:6]:
        s = str(c).strip()
        if s.isdigit() and len(s) >= 6:
            score += 1
            continue
        try:
            float(s)
            score += 1
            continue
        except ValueError:
            pass
        if ":" in s:
            score += 1
    return score >= 3


def read_csv_smart(path: str, expected_kind: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    cols = [str(c) for c in df.columns]
    if not looks_like_data_header(cols):
        return df
    raw = pd.read_csv(path, header=None)
    col_count = raw.shape[1]
    if expected_kind == "bars":
        defaults = ["time_date", "time_clock", "open", "high", "low", "close", "tick_volume", "volume", "spread"]
    else:
        defaults = ["time_date", "time_clock", "bid", "ask", "last", "volume", "flags"]
    names = defaults[:col_count] + [f"col_{i}" for i in range(len(defaults), col_count)]
    raw.columns = names[:col_count]
    print(f"Detected headerless {expected_kind} CSV; applied fallback columns: {list(raw.columns)}")
    return raw


def map_columns(df: pd.DataFrame, is_ticks: bool = False) -> dict[str, str]:
    columns = list(df.columns)
    print(f"{'Ticks' if is_ticks else 'Bars'} CSV columns detected: {columns}")
    mapping = dict(MANUAL_TICKS_COLUMN_MAP if is_ticks else MANUAL_BARS_COLUMN_MAP)
    if is_ticks:
        req = {
            "time": ["time", "date", "datetime", "timestamp", "time_date", "time_clock"],
            "bid": ["bid"],
            "ask": ["ask"],
        }
        for k, aliases in req.items():
            if k not in mapping:
                f = detect_column(columns, aliases)
                if f:
                    mapping[k] = f
        missing = [k for k in req if k not in mapping]
        if missing:
            raise ValueError(f"Could not detect required ticks columns: {missing}.")
    else:
        req = {
            "time": ["time", "date", "datetime", "timestamp", "time_date", "time_clock"],
            "open": ["open", "o"],
            "high": ["high", "h"],
            "low": ["low", "l"],
            "close": ["close", "c"],
        }
        opt = {"volume": ["volume", "tick_volume", "real_volume"]}
        for k, aliases in req.items():
            if k not in mapping:
                f = detect_column(columns, aliases)
                if f:
                    mapping[k] = f
        if "volume" not in mapping:
            f = detect_column(columns, opt["volume"])
            if f:
                mapping["volume"] = f
        missing = [k for k in req if k not in mapping]
        if missing:
            raise ValueError(f"Could not detect required bars columns: {missing}.")
    print(f"{'Ticks' if is_ticks else 'Bars'} column mapping: {mapping}")
    return mapping


def normalize_bars(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "time": pd.to_datetime(df[mapping["time"]], errors="coerce"),
            "open": pd.to_numeric(df[mapping["open"]], errors="coerce"),
            "high": pd.to_numeric(df[mapping["high"]], errors="coerce"),
            "low": pd.to_numeric(df[mapping["low"]], errors="coerce"),
            "close": pd.to_numeric(df[mapping["close"]], errors="coerce"),
            "volume": pd.to_numeric(df[mapping["volume"]], errors="coerce") if "volume" in mapping else 0.0,
        }
    )
    out = out.dropna(subset=["time", "open", "high", "low", "close"]).copy()
    out = out.sort_values("time").drop_duplicates(subset=["time"], keep="last").reset_index(drop=True)
    out[["open", "high", "low", "close", "volume"]] = out[
        ["open", "high", "low", "close", "volume"]
    ].astype(float)
    return out


def normalize_ticks(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "time": pd.to_datetime(df[mapping["time"]], errors="coerce"),
            "bid": pd.to_numeric(df[mapping["bid"]], errors="coerce"),
            "ask": pd.to_numeric(df[mapping["ask"]], errors="coerce"),
        }
    )
    out = out.dropna(subset=["time", "bid", "ask"]).copy()
    out = out.sort_values("time").drop_duplicates(subset=["time"], keep="last").reset_index(drop=True)
    out[["bid", "ask"]] = out[["bid", "ask"]].astype(float)
    return out


def ensure_single_time_column(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    if mapping.get("time") == "time_date" and "time_clock" in df.columns:
        combined = df["time_date"].astype(str).str.strip() + " " + df["time_clock"].astype(str).str.strip()
        df = df.copy()
        df["time_date"] = combined
    return df


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    x = df.set_index("time")
    agg = x.resample(rule, label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    agg = agg.dropna(subset=["open", "high", "low", "close"]).reset_index()
    return agg


# ============================================================================
# V4 ZONE-QUALITY HELPERS
# ============================================================================
def _ob_width_pips(ob: dict | None) -> float:
    if not ob:
        return 999999.0
    return abs(float(ob["high"]) - float(ob["low"])) / PIP_SIZE


def _ob_passes_width_filter(ob: dict | None, timeframe: str) -> bool:
    if not ob:
        return False
    tf = str(timeframe or ob.get("timeframe", "")).upper()
    default = 50.0 if tf == "M15" else 25.0
    max_width = env_float(f"SMC_{tf}_MAX_OB_WIDTH_PIPS", default)
    if max_width <= 0:
        return True
    return _ob_width_pips(ob) <= max_width


def _recently_touched_h1_ob(m5_now: pd.DataFrame, ob: dict | None, direction: int) -> bool:
    """H1 OB context stays valid for a short memory window after the first rejection."""
    if ob is None or m5_now is None or m5_now.empty:
        return False
    lookback = max(1, env_int("SMC_H1_OB_REJECTION_MEMORY_M5_BARS", 24))
    tail = m5_now.tail(lookback)
    if tail.empty:
        return False
    if direction == BEARISH:
        touched = bool((tail["high"] >= float(ob["low"])).any())
        invalidated = bool((tail["close"] > float(ob["high"])).any())
        return touched and not invalidated
    if direction == BULLISH:
        touched = bool((tail["low"] <= float(ob["high"])).any())
        invalidated = bool((tail["close"] < float(ob["low"])).any())
        return touched and not invalidated
    return False


def _get_h1_rejection_context_ob(
    direction: int,
    trade_mode: str,
    current_price: float,
    m5_now: pd.DataFrame,
    h1_supply_ob: dict | None,
    h1_demand_ob: dict | None,
):
    if not env_bool("SMC_H1_OB_REFINEMENT_ENABLED", True):
        return None, "none"
    if direction == BEARISH and h1_supply_ob:
        if price_inside_ob(current_price, h1_supply_ob):
            return h1_supply_ob, "inside_h1_supply"
        if env_bool("SMC_H1_OB_KEEP_CONTEXT_AFTER_REJECTION", True) and _recently_touched_h1_ob(
            m5_now, h1_supply_ob, BEARISH
        ):
            return h1_supply_ob, "recent_h1_supply_rejection"
    if direction == BULLISH and h1_demand_ob:
        if price_inside_ob(current_price, h1_demand_ob):
            return h1_demand_ob, "inside_h1_demand"
        if env_bool("SMC_H1_OB_KEEP_CONTEXT_AFTER_REJECTION", True) and _recently_touched_h1_ob(
            m5_now, h1_demand_ob, BULLISH
        ):
            return h1_demand_ob, "recent_h1_demand_rejection"
    return None, "none"


_PIVOT_SCAN_LIMIT_DEFAULT = 8


def annotate_pivots_swept(structure_result: dict, closes_np, recent_limit: int = 16) -> None:
    """Annotate ONLY the last `recent_limit` pivots with a 'swept' boolean.

    Older pivots are ignored to keep this O(recent_limit). Find functions only
    look at the most recent pivots anyway, so older annotations are unused.

    For pivot_highs: swept = any close after pivot index is greater than pivot price.
    For pivot_lows : swept = any close after pivot index is less than pivot price.
    """
    if not structure_result or closes_np is None:
        return
    n = int(len(closes_np))
    if n <= 0:
        return
    for kind, op in (("pivot_highs", ">"), ("pivot_lows", "<")):
        pivots = structure_result.get(kind, []) or []
        if not pivots:
            continue
        # Annotate the last recent_limit pivots only.
        tail = pivots[-recent_limit:]
        for piv in tail:
            idx = int(piv.get("index", 0))
            start = idx + 1
            if start >= n:
                piv["swept"] = False
                continue
            price = float(piv.get("price", 0.0))
            slice_view = closes_np[start:n]
            if op == ">":
                piv["swept"] = bool((slice_view > price).any())
            else:
                piv["swept"] = bool((slice_view < price).any())


def _find_unswept_pivot_high(
    structure_result: dict, current_price: float, scan_limit: int = _PIVOT_SCAN_LIMIT_DEFAULT
) -> dict | None:
    """Most recent annotated unswept pivot-high above current_price."""
    pivots = (structure_result or {}).get("pivot_highs", []) or []
    if not pivots:
        return None
    checked = 0
    for piv in reversed(pivots):
        if checked >= scan_limit:
            break
        checked += 1
        price = float(piv.get("price", 0.0))
        if price <= float(current_price):
            continue
        # If swept annotation is missing, conservatively assume swept (skip).
        if not bool(piv.get("swept", True if "swept" not in piv else piv.get("swept"))):
            return piv
    return None


def _find_unswept_pivot_low(
    structure_result: dict, current_price: float, scan_limit: int = _PIVOT_SCAN_LIMIT_DEFAULT
) -> dict | None:
    pivots = (structure_result or {}).get("pivot_lows", []) or []
    if not pivots:
        return None
    checked = 0
    for piv in reversed(pivots):
        if checked >= scan_limit:
            break
        checked += 1
        price = float(piv.get("price", 0.0))
        if price >= float(current_price):
            continue
        if not bool(piv.get("swept", True if "swept" not in piv else piv.get("swept"))):
            return piv
    return None


def _liquidity_sweep_before_internal_break(
    direction: int,
    m5_now: pd.DataFrame,
    m5_result: dict,
    m15_result: dict,
    lookback: int = 12,
) -> bool:
    """Did the most recent CHoCH/BOS happen right after a sweep of a weak pivot?"""
    events = (m5_result or {}).get("events") or []
    if not events:
        return False
    last_event = events[-1]
    if last_event.get("bias") != direction:
        return False
    break_index = int(last_event.get("break_index", -1))
    if break_index <= 0 or break_index >= len(m5_now):
        return False
    start = max(0, break_index - lookback)
    end = break_index + 1
    highs = m5_now["high"].to_numpy()[start:end]
    lows = m5_now["low"].to_numpy()[start:end]
    closes = m5_now["close"].to_numpy()[start:end]
    if highs.size == 0:
        return False
    if direction == BEARISH:
        target = _find_unswept_pivot_high(m15_result, float(highs.max()))
        if not target:
            return False
        target_price = float(target.get("price", 0.0))
        wicked = bool((highs >= target_price).any())
        closed_back = bool((closes < target_price).all())
        return wicked and closed_back
    if direction == BULLISH:
        target = _find_unswept_pivot_low(m15_result, float(lows.min()))
        if not target:
            return False
        target_price = float(target.get("price", 0.0))
        wicked = bool((lows <= target_price).any())
        closed_back = bool((closes > target_price).all())
        return wicked and closed_back
    return False


def _displacement_strength_score(direction: int, m5_now: pd.DataFrame, lookback: int = 6, last_n: int = 3) -> int:
    """+1 if the last few M5 bars show strong displacement in the trade direction."""
    if len(m5_now) < lookback:
        return 0
    closes = m5_now["close"].to_numpy()[-lookback:]
    opens = m5_now["open"].to_numpy()[-lookback:]
    highs = m5_now["high"].to_numpy()[-lookback:]
    lows = m5_now["low"].to_numpy()[-lookback:]
    bodies = (closes - opens)
    body_abs = abs(bodies)
    ranges = (highs - lows)
    avg_body = float(body_abs.mean())
    avg_range = float(ranges.mean())
    if avg_range <= 0:
        return 0
    for j in range(1, last_n + 1):
        idx = -j
        body = float(body_abs[idx])
        candle_range = float(ranges[idx])
        if candle_range <= 0:
            continue
        if direction == BULLISH and bodies[idx] <= 0:
            continue
        if direction == BEARISH and bodies[idx] >= 0:
            continue
        body_strong = avg_body > 0 and body >= avg_body * 1.4
        range_strong = avg_range > 0 and candle_range >= avg_range * 1.2
        body_clean = candle_range > 0 and body / candle_range >= 0.5
        if body_clean and (body_strong or range_strong):
            return 1
    return 0


def _opposing_h1_zone_nearby(
    direction: str,
    entry: float,
    take_profit: float,
    h1_supply_ob: dict | None,
    h1_demand_ob: dict | None,
) -> bool:
    """Returns True if an opposing H1 OB sits between entry and TP (blocks the move)."""
    if direction == "buy" and h1_supply_ob:
        ob_low = float(h1_supply_ob["low"])
        if min(entry, take_profit) < ob_low < max(entry, take_profit):
            return True
    if direction == "sell" and h1_demand_ob:
        ob_high = float(h1_demand_ob["high"])
        if min(entry, take_profit) < ob_high < max(entry, take_profit):
            return True
    return False


_HTF_LIQUIDITY_AGAINST_MIN_PIPS = 50.0


def _unresolved_htf_liquidity_against_direction(
    direction: int,
    current_price: float,
    h1_result: dict,
) -> bool:
    """If unswept H1 liquidity sits against us by >= threshold, penalize."""
    if not h1_result:
        return False
    if direction == BEARISH:
        target = _find_unswept_pivot_low(h1_result, float(current_price))
        if not target:
            return False
        distance_pips = (float(current_price) - float(target.get("price", current_price))) / PIP_SIZE
        return distance_pips >= _HTF_LIQUIDITY_AGAINST_MIN_PIPS
    if direction == BULLISH:
        target = _find_unswept_pivot_high(h1_result, float(current_price))
        if not target:
            return False
        distance_pips = (float(target.get("price", current_price)) - float(current_price)) / PIP_SIZE
        return distance_pips >= _HTF_LIQUIDITY_AGAINST_MIN_PIPS
    return False


def _pd_true_zone_bounds(direction: int, swing_low: float, swing_high: float) -> tuple[float, float, str]:
    rng = float(swing_high) - float(swing_low)
    if rng <= 0:
        return float(swing_low), float(swing_high), "invalid_pd_range"
    premium_start = env_float("SMC_PD_PREMIUM_START", 0.618)
    discount_start = env_float("SMC_PD_DISCOUNT_START", 0.382)
    if direction == BEARISH:
        return float(swing_low) + rng * premium_start, float(swing_high), "true_premium_retracement_zone"
    if direction == BULLISH:
        return float(swing_low), float(swing_low) + rng * discount_start, "true_discount_retracement_zone"
    return float(swing_low), float(swing_high), "full_range"


# ============================================================================
# V4 ZONE SCORING + SELECTION
# ============================================================================
def score_zone_v4(
    *,
    direction: int,
    trade_mode: str,
    decision: str,
    selected_ob: dict | None,
    m15_refined_ob: dict | None,
    m5_refined_ob: dict | None,
    h1_context_ob: dict | None,
    h1_context_label: str,
    h1_supply_ob: dict | None,
    h1_demand_ob: dict | None,
    swing_low: float,
    swing_high: float,
    current_price: float,
    m5_now: pd.DataFrame,
    m5_result: dict,
    m15_now: pd.DataFrame,
    m15_result: dict,
    h1_now: pd.DataFrame,
    h1_result: dict,
) -> dict[str, Any]:
    score = 0
    reasons: list[str] = []

    # +3 inside active H1 supply/demand
    if h1_context_label.startswith("inside_"):
        score += 3
        reasons.append("+3 inside_h1_ob")
    # +2 recent H1 OB rejection memory
    elif h1_context_label.startswith("recent_"):
        score += 2
        reasons.append("+2 h1_rejection_memory")

    # +2 M15 refined OB exists and aligns with H1 / valid zone
    m15_ok = _ob_passes_width_filter(m15_refined_ob, "M15")
    if m15_refined_ob and m15_ok:
        score += 2
        reasons.append("+2 m15_refined_ob_available")

    # +1 M5 confirms with internal CHoCH/BOS in trade direction
    m5_trigger_confirmed = False
    last_m5 = (m5_result or {}).get("events") or []
    if last_m5 and last_m5[-1].get("bias") == direction:
        m5_trigger_confirmed = True
        score += 1
        reasons.append("+1 m5_trigger_choch_or_bos")

    # True premium/discount zone alignment (uses cached env thresholds)
    pd_label, pd_pos = _pd_label_fast(current_price, swing_low, swing_high)
    premium_labels = {"true_premium", "deep_premium", "extreme_premium"}
    deep_premium_labels = {"deep_premium", "extreme_premium"}
    discount_labels = {"true_discount", "deep_discount", "extreme_discount"}
    deep_discount_labels = {"deep_discount", "extreme_discount"}
    if direction == BEARISH and pd_label in premium_labels:
        score += 2
        reasons.append(f"+2 true_premium({pd_label})")
        if pd_label in deep_premium_labels:
            score += 1
            reasons.append("+1 deep_or_extreme_premium")
    elif direction == BULLISH and pd_label in discount_labels:
        score += 2
        reasons.append(f"+2 true_discount({pd_label})")
        if pd_label in deep_discount_labels:
            score += 1
            reasons.append("+1 deep_or_extreme_discount")

    # +1 weak high / weak low target supports direction
    weak_target = None
    if direction == BEARISH:
        weak_target = _find_unswept_pivot_low(m15_result, current_price)
        if weak_target:
            score += 1
            reasons.append("+1 weak_low_target_below")
    else:
        weak_target = _find_unswept_pivot_high(m15_result, current_price)
        if weak_target:
            score += 1
            reasons.append("+1 weak_high_target_above")

    # +1 strong displacement away from the zone (recent M5 impulse)
    if _displacement_strength_score(direction, m5_now):
        score += 1
        reasons.append("+1 strong_displacement")

    # liquidity sweep before internal break
    liq_sweep = _liquidity_sweep_before_internal_break(direction, m5_now, m5_result, m15_result)
    if liq_sweep:
        score += 1
        reasons.append("+1 liquidity_sweep_before_choch")

    # Penalties
    selected_tf = (selected_ob or {}).get("timeframe", "")
    if selected_tf == "M5" and h1_context_label == "none":
        score -= 2
        reasons.append("-2 m5_only_no_h1_context")

    if selected_ob and not _ob_passes_width_filter(selected_ob, selected_tf):
        score -= 2
        reasons.append("-2 zone_too_wide")

    # Plain premium/discount (NOT true) penalty when used as retracement zone
    if direction == BEARISH and pd_label == "above_EQ_not_true_premium":
        score -= 1
        reasons.append("-1 sell_from_generic_premium")
    if direction == BULLISH and pd_label == "below_EQ_not_true_discount":
        score -= 1
        reasons.append("-1 buy_from_generic_discount")

    # Trading into opposing H1 OB (will be re-checked once entry/tp known)
    opposing_h1 = False
    if direction == BULLISH and h1_supply_ob and not h1_context_label.startswith("inside_h1_demand"):
        # supply between current price and likely target
        if float(h1_supply_ob["low"]) > float(current_price):
            opposing_h1 = True
    if direction == BEARISH and h1_demand_ob and not h1_context_label.startswith("inside_h1_supply"):
        if float(h1_demand_ob["high"]) < float(current_price):
            opposing_h1 = True
    if opposing_h1:
        score -= 2
        reasons.append("-2 opposing_h1_zone_nearby")

    # Direction against unresolved HTF liquidity
    if _unresolved_htf_liquidity_against_direction(direction, current_price, h1_result):
        score -= 2
        reasons.append("-2 unresolved_htf_liquidity_against")

    # Grade
    if score >= 8:
        grade = "A"
    elif score >= 6:
        grade = "B"
    elif score >= 4:
        grade = "C"
    elif score >= 2:
        grade = "D"
    else:
        grade = "F"

    return {
        "zone_score": int(score),
        "zone_grade": grade,
        "zone_reason": " | ".join(reasons) if reasons else "no_factors",
        "true_pd_location": pd_label,
        "pd_position": round(float(pd_pos), 4),
        "liquidity_sweep_confirmed": bool(liq_sweep),
        "opposing_h1_zone_nearby": bool(opposing_h1),
        "m5_trigger_confirmed": bool(m5_trigger_confirmed),
        "m15_refined_ob_available": bool(m15_refined_ob and m15_ok),
        "weak_target_present": bool(weak_target),
    }


def select_zone_v4(
    *,
    direction: int,
    trade_mode: str,
    m15_result: dict,
    m5_result: dict,
    m15_now: pd.DataFrame,
    m5_now: pd.DataFrame,
    swing_low: float,
    swing_high: float,
    fibs: dict[float, float],
    h1_supply_ob: dict | None,
    h1_demand_ob: dict | None,
    current_price: float,
):
    """
    Returns: selected_ob, m15_ob, m5_ob, zone_name, selected_source, h1_context_ob, h1_context_label
    """
    if direction is None:
        return None, None, None, "none", "no_direction", None, "none"

    h1_context_ob, h1_context_label = _get_h1_rejection_context_ob(
        direction, trade_mode, float(current_price), m5_now, h1_supply_ob, h1_demand_ob
    )

    if h1_context_ob:
        zone_low = float(h1_context_ob["low"])
        zone_high = float(h1_context_ob["high"])
        zone_name = "h1_supply_refined_sell_zone" if direction == BEARISH else "h1_demand_refined_buy_zone"
    elif trade_mode == "retracement":
        zone_low, zone_high, zone_name = _pd_true_zone_bounds(direction, swing_low, swing_high)
    else:
        poi_top = max(fibs[0.618], fibs[0.886])
        poi_bottom = min(fibs[0.618], fibs[0.886])
        zone_low = poi_bottom
        zone_high = poi_top
        zone_name = "external_fib_poi"

    m15_ob = last_valid_ob(m15_result["events"], m15_now, direction, "M15", zone_low=zone_low, zone_high=zone_high)
    m5_ob = last_valid_ob(m5_result["events"], m5_now, direction, "M5", zone_low=zone_low, zone_high=zone_high)

    selected_ob = None
    selected_source = "none"

    # V4 rule: when an H1 OB context exists, M15 OB is preferred and M5 is just trigger.
    if h1_context_ob:
        if _ob_passes_width_filter(m15_ob, "M15"):
            selected_ob = m15_ob
            selected_source = "h1_context_m15_refined"
        elif _ob_passes_width_filter(m5_ob, "M5"):
            selected_ob = m5_ob
            selected_source = "h1_context_m5_fallback"
    else:
        # No H1 ctx: prefer M15 for retracements, otherwise the recent M5.
        if trade_mode == "retracement" and _ob_passes_width_filter(m15_ob, "M15"):
            selected_ob = m15_ob
            selected_source = "no_h1_retracement_m15_preferred"
        elif _ob_passes_width_filter(m5_ob, "M5") and trade_mode == "continuation":
            selected_ob = m5_ob
            selected_source = "no_h1_continuation_m5"
        elif _ob_passes_width_filter(m15_ob, "M15"):
            selected_ob = m15_ob
            selected_source = "no_h1_m15_fallback"
        elif _ob_passes_width_filter(m5_ob, "M5"):
            selected_ob = m5_ob
            selected_source = "no_h1_m5_only"
        else:
            selected_ob = most_recent_ob(m15_ob, m5_ob)
            selected_source = "fallback_most_recent_width_unchecked"

    if selected_ob:
        selected_ob = dict(selected_ob)
        if h1_context_ob:
            selected_ob["h1_context_ob"] = h1_context_ob
            selected_ob["inside_h1_ob"] = h1_context_label.startswith("inside_")
        selected_ob["h1_context_label"] = h1_context_label
        selected_ob["selected_source"] = selected_source

    return selected_ob, m15_ob, m5_ob, zone_name, selected_source, h1_context_ob, h1_context_label


# ============================================================================
# V4 STOP-LOSS + TAKE-PROFIT BUILDER
# ============================================================================
def _h1_midpoint(ob: dict | None) -> float | None:
    if not ob:
        return None
    return (float(ob["high"]) + float(ob["low"])) / 2.0


def _m15_swing_protected_stop(direction: str, m15_now: pd.DataFrame, fallback_stop: float, buffer_price: float) -> tuple[float, str]:
    """When the LTF OB is bare M5 but we still have a recent M15 swing, use the M15 swing."""
    if m15_now is None or len(m15_now) < 5:
        return fallback_stop, "ltf_only"
    tail = m15_now.tail(env_int("SMC_V4_M15_SWING_LOOKBACK_BARS", 20))
    if tail.empty:
        return fallback_stop, "ltf_only"
    if direction == "buy":
        swing_low = float(tail["low"].min())
        protected = swing_low - buffer_price
        return min(fallback_stop, protected), "m15_swing_protected"
    swing_high = float(tail["high"].max())
    protected = swing_high + buffer_price
    return max(fallback_stop, protected), "m15_swing_protected"


def build_v4_candidate(
    *,
    decision: str,
    trade_mode: str,
    selected_ob: dict | None,
    current_close: float,
    settings: BacktestSettings,
    scoring: dict[str, Any],
    h1_context_ob: dict | None,
    h1_supply_ob: dict | None,
    h1_demand_ob: dict | None,
    m15_now: pd.DataFrame,
    now: pd.Timestamp,
):
    """
    Converts a scored zone into an executable trade idea, or returns (None, reason).
    """
    if decision not in VALID_DECISIONS or not selected_ob:
        return None, "no_valid_decision_or_ob"

    direction = "buy" if decision.startswith("BUY") else "sell"
    ob_high = float(selected_ob["high"])
    ob_low = float(selected_ob["low"])
    buffer_price = settings.ob_buffer_pips * PIP_SIZE
    selected_tf = str(selected_ob.get("timeframe", "")).upper()

    # Base limit-entry and base SL anchored on the chosen OB
    if direction == "buy":
        limit_entry = ob_high
        base_stop = ob_low - buffer_price
    else:
        limit_entry = ob_low
        base_stop = ob_high + buffer_price

    # Stop refinement
    stop = base_stop
    stop_source = "ltf_ob"
    if selected_tf == "M15":
        stop_source = "m15_ob_buffer"
    elif selected_tf == "M5" and h1_context_ob:
        h1_mid = _h1_midpoint(h1_context_ob)
        if h1_mid is not None:
            if direction == "buy":
                if h1_mid < limit_entry:
                    stop = min(stop, h1_mid)
                    stop_source = "h1_context_midpoint_protected"
            else:
                if h1_mid > limit_entry:
                    stop = max(stop, h1_mid)
                    stop_source = "h1_context_midpoint_protected"
    elif selected_tf == "M5" and not h1_context_ob:
        # Bare M5 - use M15 swing as a stronger stop anchor if available
        stop, stop_source = _m15_swing_protected_stop(direction, m15_now, base_stop, buffer_price)

    # Risk depending on score (already decided one level up, but we recompute here for safety)
    risk_percent = float(settings.risk_percent)
    execution_style = "pending_limit"
    entry_model = "OB_MITIGATION_LIMIT_ENTRY"
    score = int(scoring.get("zone_score", 0))
    if score < settings.score_full_entry:
        risk_percent = float(settings.reduced_risk_percent)
        execution_style = "ai_zone_market"
        entry_model = "OB_NOT_MITIGATED_ZONE_ENTRY"

    if direction == "buy":
        risk = limit_entry - stop
        if risk <= 0:
            return None, "invalid_risk_distance"
        tp = limit_entry + risk * settings.rr
        if limit_entry >= current_close:
            execution_style = "ai_zone_market"
            entry_model = "OB_NOT_MITIGATED_ZONE_ENTRY"
            risk_percent = float(settings.reduced_risk_percent)
            entry = float(current_close)
            risk = entry - stop
            if risk <= 0:
                return None, "invalid_market_risk_distance"
            tp = entry + risk * settings.rr
        else:
            entry = float(limit_entry)
    else:
        risk = stop - limit_entry
        if risk <= 0:
            return None, "invalid_risk_distance"
        tp = limit_entry - risk * settings.rr
        if limit_entry <= current_close:
            execution_style = "ai_zone_market"
            entry_model = "OB_NOT_MITIGATED_ZONE_ENTRY"
            risk_percent = float(settings.reduced_risk_percent)
            entry = float(current_close)
            risk = stop - entry
            if risk <= 0:
                return None, "invalid_market_risk_distance"
            tp = entry - risk * settings.rr
        else:
            entry = float(limit_entry)

    # Check opposing H1 zone now that we know entry & tp
    opposing_now = _opposing_h1_zone_nearby(direction, entry, tp, h1_supply_ob, h1_demand_ob)
    if opposing_now:
        scoring = dict(scoring)
        scoring["opposing_h1_zone_nearby"] = True
        # Only flag - the score itself already penalized this, so no double-deduct.
        scoring["zone_reason"] = scoring.get("zone_reason", "") + " | flagged_opposing_h1_in_path"

    return {
        "decision": decision,
        "trade_mode": trade_mode,
        "direction": direction,
        "ob_timeframe": selected_ob.get("timeframe"),
        "ob_type": selected_ob.get("type"),
        "selected_ob_high": ob_high,
        "selected_ob_low": ob_low,
        "selected_ob_time": selected_ob.get("time"),
        "selected_ob_source": selected_ob.get("selected_source", "none"),
        "stop_source": stop_source,
        "inside_h1_ob": bool(selected_ob.get("inside_h1_ob") or h1_context_ob),
        "h1_context_ob_high": float(h1_context_ob.get("high", 0.0)) if h1_context_ob else 0.0,
        "h1_context_ob_low": float(h1_context_ob.get("low", 0.0)) if h1_context_ob else 0.0,
        "entry": float(entry),
        "stop_loss": float(stop),
        "take_profit": float(tp),
        "rr": float(settings.rr),
        "risk_percent": float(risk_percent),
        "execution_style": execution_style,
        "entry_model": entry_model,
        "missed_limit_entry": float(limit_entry) if execution_style == "ai_zone_market" else None,
        "distance_from_ob_pips": float(abs(entry - limit_entry) / PIP_SIZE) if execution_style == "ai_zone_market" else 0.0,
        "moved_r_from_ob": float(abs(entry - limit_entry) / risk) if risk > 0 else 0.0,
    }, None


# ============================================================================
# REPLAY / EXIT helpers (mirror V2 max2)
# ============================================================================
def classify_candle_exit(direction: str, low: float, high: float, sl: float, tp: float) -> str | None:
    if direction == "buy":
        if low <= sl and high >= tp:
            return "SL"  # conservative
        if low <= sl:
            return "SL"
        if high >= tp:
            return "TP"
    else:
        if high >= sl and low <= tp:
            return "SL"
        if high >= sl:
            return "SL"
        if low <= tp:
            return "TP"
    return None


def classify_tick_exit_np(direction: str, bids: Any, asks: Any, sl: float, tp: float) -> str | None:
    """Vectorized tick exit classification using pre-sliced numpy arrays."""
    n = int(len(bids)) if bids is not None else 0
    if n == 0:
        return None
    if direction == "buy":
        sl_hits = bids <= sl
        tp_hits = bids >= tp
    else:
        sl_hits = asks >= sl
        tp_hits = asks <= tp
    sl_idx = int(sl_hits.argmax()) if sl_hits.any() else -1
    tp_idx = int(tp_hits.argmax()) if tp_hits.any() else -1
    if sl_idx < 0 and tp_idx < 0:
        return None
    if sl_idx < 0:
        return "TP"
    if tp_idx < 0:
        return "SL"
    return "SL" if sl_idx <= tp_idx else "TP"


def monthly_performance(rows: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for r in rows:
        if r.get("result") not in {"WIN", "LOSS"}:
            continue
        m = pd.Timestamp(r["close_time"]).strftime("%Y-%m")
        out[m] += float(r["profit"])
    return dict(sorted(out.items()))


def _safe_pf(gp: float, gl: float) -> float:
    if gl > 0:
        return round(gp / gl, 4)
    return round(999.99, 4) if gp > 0 else 0.0


def _group_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [r for r in rows if r.get("result") in {"WIN", "LOSS"}]
    expired = [r for r in rows if r.get("result") == "EXPIRED"]
    wins = [r for r in closed if r["result"] == "WIN"]
    losses = [r for r in closed if r["result"] == "LOSS"]
    gp = sum(float(r.get("profit", 0) or 0) for r in wins)
    gl = abs(sum(float(r.get("profit", 0) or 0) for r in losses))
    net = sum(float(r.get("profit", 0) or 0) for r in closed)
    wr = round(100.0 * len(wins) / (len(wins) + len(losses)), 2) if (len(wins) + len(losses)) else 0.0
    return {
        "total_filled": len(closed),
        "expired": len(expired),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": wr,
        "gross_profit": round(gp, 4),
        "gross_loss": round(gl, 4),
        "profit_factor": _safe_pf(gp, gl),
        "net_profit": round(net, 4),
    }


def _group_by_key(trades: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        v = t.get(key)
        if v is None or v == "":
            v = "unknown"
        buckets[str(v)].append(t)
    return {k: _group_metrics(v) for k, v in sorted(buckets.items())}


def _zone_score_bucket(score: int) -> str:
    if score >= 8:
        return "A_8+"
    if score >= 6:
        return "B_6-7"
    if score >= 4:
        return "C_4-5"
    if score >= 2:
        return "D_2-3"
    return "F_lt_2"


# ============================================================================
# MAIN
# ============================================================================
def main() -> None:
    backend_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Zone Refinement V4 (opus) backtest.")
    parser.add_argument("--env-file", type=str, default=".env.entry_refinement_v2")
    parser.add_argument("--start", type=str, default="")
    parser.add_argument("--end", type=str, default="")
    parser.add_argument("--bars-csv", type=str, default=BARS_CSV)
    parser.add_argument("--ticks-csv", type=str, default=TICKS_CSV)
    parser.add_argument("--initial-balance", type=float, default=None)
    parser.add_argument("--risk-percent", type=float, default=None)
    parser.add_argument("--rr", type=float, default=None)
    parser.add_argument("--ob-buffer-pips", type=float, default=None)
    parser.add_argument("--save-skipped", action="store_true")
    parser.add_argument("--use-ticks", type=str, default="true")
    parser.add_argument("--structure-lookback", type=int, default=env_int("BACKTEST_STRUCTURE_LOOKBACK", 2000))
    parser.add_argument("--m5-lookback", type=int, default=0)
    parser.add_argument("--m15-lookback", type=int, default=0)
    parser.add_argument("--h1-lookback", type=int, default=0)
    parser.add_argument(
        "--max-trades-per-day",
        type=int,
        default=None,
        help="Daily cap on executable trades by signal_time date. Default: MAX_TRADES_PER_DAY env or unlimited.",
    )
    parser.add_argument("--target-min-trades", type=int, default=170, help="Research target only (not used as filter).")
    parser.add_argument("--target-max-trades", type=int, default=240, help="Research target only (not used as filter).")
    parser.add_argument("--target-profit-factor", type=float, default=2.5, help="Research target only (not used as filter).")
    parser.add_argument("--score-full-entry", type=int, default=env_int("V4_SCORE_FULL_ENTRY", 6))
    parser.add_argument("--score-ai-zone-entry", type=int, default=env_int("V4_SCORE_AI_ZONE_ENTRY", 4))
    parser.add_argument("--reduced-risk-percent", type=float, default=env_float("V4_REDUCED_RISK_PERCENT", 0.5))
    args = parser.parse_args()

    # Env load
    base_env = backend_dir / ".env"
    if base_env.exists():
        load_dotenv(base_env, override=False)
        print(f"Loaded base env: {base_env}")
    env_file_path = Path(args.env_file)
    if not env_file_path.is_absolute():
        env_file_path = backend_dir / env_file_path
    if env_file_path.exists():
        load_dotenv(env_file_path, override=True)
        print(f"Loaded override env: {env_file_path}")
    else:
        print(f"Override env file not found, continuing without it: {env_file_path}")

    # Resolve numerics
    env_initial = os.getenv("BACKTEST_INITIAL_BALANCE", "").strip()
    default_initial = float(env_initial) if env_initial else 5000.0
    initial_balance = args.initial_balance if args.initial_balance is not None else default_initial
    if initial_balance <= 0:
        initial_balance = 5000.0
    risk_percent = args.risk_percent if args.risk_percent is not None else env_float("RISK_PERCENT", 1.0)
    rr = args.rr if args.rr is not None else env_float("SMC_RR", 4.0)
    ob_buffer_pips = args.ob_buffer_pips if args.ob_buffer_pips is not None else env_float("OB_BUFFER_PIPS", 3.0)
    max_trades_per_day = (
        int(args.max_trades_per_day)
        if args.max_trades_per_day is not None
        else env_int("MAX_TRADES_PER_DAY", 0)  # V4 default: unlimited; daily cap proved ineffective in V2_max2
    )

    settings = BacktestSettings(
        initial_balance=float(initial_balance),
        risk_percent=float(risk_percent),
        reduced_risk_percent=float(args.reduced_risk_percent),
        rr=float(rr),
        ob_buffer_pips=float(ob_buffer_pips),
        order_expiry_hours=env_int("ORDER_EXPIRY_HOURS", 24),
        max_open_trades=env_int("MAX_OPEN_TRADES", 1),
        spread_points=env_float("BACKTEST_SPREAD_POINTS", 20.0),
        slippage_points=env_float("BACKTEST_SLIPPAGE_POINTS", 5.0),
        commission_per_lot=env_float("BACKTEST_COMMISSION_PER_LOT", 0.0),
        save_skipped=args.save_skipped or env_bool("SAVE_SKIPPED_SIGNALS", False),
        use_ticks=str_to_bool(args.use_ticks),
        score_full_entry=int(args.score_full_entry),
        score_ai_zone_entry=int(args.score_ai_zone_entry),
        target_min_trades=int(args.target_min_trades),
        target_max_trades=int(args.target_max_trades),
        target_profit_factor=float(args.target_profit_factor),
    )

    # Data load
    bars_df = read_csv_smart(args.bars_csv, expected_kind="bars")
    bars_map = map_columns(bars_df, is_ticks=False)
    bars_df = ensure_single_time_column(bars_df, bars_map)
    bars = normalize_bars(bars_df, bars_map)

    ticks = pd.DataFrame(columns=["time", "bid", "ask"])
    ticks_enabled = False
    tick_times_np = None
    tick_bids_np = None
    tick_asks_np = None
    if settings.use_ticks and args.ticks_csv and Path(args.ticks_csv).exists():
        try:
            ticks_df = read_csv_smart(args.ticks_csv, expected_kind="ticks")
            ticks_map = map_columns(ticks_df, is_ticks=True)
            ticks_df = ensure_single_time_column(ticks_df, ticks_map)
            ticks = normalize_ticks(ticks_df, ticks_map)
            ticks_enabled = settings.use_ticks and not ticks.empty
            if ticks_enabled:
                tick_times_np = ticks["time"].to_numpy(dtype="datetime64[ns]")
                tick_bids_np = ticks["bid"].to_numpy(dtype=float)
                tick_asks_np = ticks["ask"].to_numpy(dtype=float)
                print(f"Tick numpy arrays cached: {len(tick_times_np)} ticks indexed for searchsorted lookup")
                sys.stdout.flush()
        except Exception as exc:
            print(f"Ticks CSV ignored due to parsing error: {exc}")

    if args.start:
        bars = bars[bars["time"] >= pd.Timestamp(args.start)]
        ticks = ticks[ticks["time"] >= pd.Timestamp(args.start)]
    if args.end:
        end_ts = pd.Timestamp(args.end) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        bars = bars[bars["time"] <= end_ts]
        ticks = ticks[ticks["time"] <= end_ts]
    bars = bars.reset_index(drop=True)
    ticks = ticks.reset_index(drop=True)
    if ticks_enabled and not ticks.empty:
        # Re-derive numpy arrays after start/end filtering so searchsorted works.
        tick_times_np = ticks["time"].to_numpy(dtype="datetime64[ns]")
        tick_bids_np = ticks["bid"].to_numpy(dtype=float)
        tick_asks_np = ticks["ask"].to_numpy(dtype=float)
    if bars.empty:
        raise RuntimeError("No bars after date filtering.")

    m5 = resample_ohlc(bars, "5min")
    m15 = resample_ohlc(bars, "15min")
    h1 = resample_ohlc(bars, "1h")
    print(
        f"Bars loaded: raw={len(bars)}, M5={len(m5)}, M15={len(m15)}, H1={len(h1)} | "
        f"time range {bars['time'].min()} -> {bars['time'].max()}"
    )
    _resolve_pd_thresholds_from_env()
    global _HTF_LIQUIDITY_AGAINST_MIN_PIPS
    _HTF_LIQUIDITY_AGAINST_MIN_PIPS = env_float("SMC_V4_HTF_LIQUIDITY_AGAINST_MIN_PIPS", 50.0)
    print(
        f"V4 zone-refinement thresholds: score_full_entry={settings.score_full_entry}, "
        f"score_ai_zone_entry={settings.score_ai_zone_entry}, reduced_risk={settings.reduced_risk_percent}%"
    )
    print(
        f"V4 research targets: trades={settings.target_min_trades}..{settings.target_max_trades}, "
        f"profit_factor>={settings.target_profit_factor}, daily_cap={max_trades_per_day if max_trades_per_day>0 else 'unlimited'}"
    )
    sys.stdout.flush()

    structure_lookback = max(0, int(args.structure_lookback))
    if structure_lookback and structure_lookback < 300:
        structure_lookback = 300

    lookback_m5 = max(0, int(args.m5_lookback or structure_lookback))
    lookback_m15 = max(0, int(args.m15_lookback or structure_lookback))
    lookback_h1 = max(0, int(args.h1_lookback or structure_lookback))
    if lookback_m5 and lookback_m5 < 300:
        lookback_m5 = 300
    if lookback_m15 and lookback_m15 < 300:
        lookback_m15 = 300
    if lookback_h1 and lookback_h1 < 300:
        lookback_h1 = 300

    swing_length = int(os.getenv("SMC_SWING_LENGTH", "20"))
    internal_length = int(os.getenv("SMC_INTERNAL_LENGTH", "3"))

    # State
    balance = float(settings.initial_balance)
    peak_balance = balance
    max_drawdown = 0.0
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = [{"time": m5.iloc[0]["time"], "balance": balance}]
    wins = losses = expired = cancelled = total_signals = 0
    consec_wins = consec_losses = 0
    max_consec_wins = max_consec_losses = 0
    active: dict[str, Any] | None = None
    h1_end = 0
    m15_end = 0
    h1_result_cache: dict[str, Any] = {"events": []}
    m15_result_cache: dict[str, Any] = {"events": []}
    h1_cache_time = None
    m15_cache_time = None
    # V4 perf caches: H1 OBs change only when a new H1 candle is added.
    h1_obs_cache_time = None
    h1_supply_ob_cache: dict | None = None
    h1_demand_ob_cache: dict | None = None
    daily_trade_attempts: dict[str, int] = defaultdict(int)
    skipped_due_daily_trade_limit_count = 0
    skipped_due_low_score = 0
    skipped_due_at_entry = 0
    skipped_due_no_decision = 0

    skip_at_entry = env_bool("V4_SKIP_AT_ENTRY", True)

    progress_interval = max(1000, env_int("V4_PROGRESS_INTERVAL", 2000))
    for i in range(len(m5)):
        now = pd.Timestamp(m5.iloc[i]["time"])
        if i and i % progress_interval == 0:
            print(
                f"Replay progress: {i}/{len(m5)} M5 candles | trades={len(trades)} | "
                f"signals={total_signals} | skipped_low_score={skipped_due_low_score} | "
                f"skipped_at_entry={skipped_due_at_entry}",
                flush=True,
            )
        while h1_end < len(h1) and pd.Timestamp(h1.iloc[h1_end]["time"]) <= now:
            h1_end += 1
        while m15_end < len(m15) and pd.Timestamp(m15.iloc[m15_end]["time"]) <= now:
            m15_end += 1

        h1_start = max(0, h1_end - lookback_h1) if lookback_h1 > 0 else 0
        m15_start = max(0, m15_end - lookback_m15) if lookback_m15 > 0 else 0
        m5_start = max(0, (i + 1) - lookback_m5) if lookback_m5 > 0 else 0
        h1_now = h1.iloc[h1_start:h1_end].reset_index(drop=True)
        m15_now = m15.iloc[m15_start:m15_end].reset_index(drop=True)
        m5_now = m5.iloc[m5_start : (i + 1)].reset_index(drop=True)
        if len(h1_now) < 200 or len(m15_now) < 300 or len(m5_now) < 300:
            equity_curve.append({"time": now, "balance": balance})
            continue

        candle = m5_now.iloc[-1]
        c_low = float(candle["low"])
        c_high = float(candle["high"])
        c_close = float(candle["close"])
        tick_bids_slice = None
        tick_asks_slice = None
        if ticks_enabled and i > 0 and tick_times_np is not None:
            prev_t_np = pd.Timestamp(m5.iloc[i - 1]["time"]).to_datetime64()
            now_np = pd.Timestamp(now).to_datetime64()
            lo = int(tick_times_np.searchsorted(prev_t_np, side="right"))
            hi = int(tick_times_np.searchsorted(now_np, side="right"))
            if hi > lo:
                tick_bids_slice = tick_bids_np[lo:hi]
                tick_asks_slice = tick_asks_np[lo:hi]

        # Manage active trade
        if active:
            if active["state"] == "pending":
                if now > active["expiry_time"]:
                    active["result"] = "EXPIRED"
                    active["close_time"] = now
                    trades.append(active)
                    expired += 1
                    active = None
                else:
                    if active["direction"] == "buy":
                        filled = c_low <= active["entry"]
                    else:
                        filled = c_high >= active["entry"]
                    if filled:
                        active["state"] = "open"
                        active["fill_time"] = now
            if active and active["state"] == "open":
                exit_hit = None
                if tick_bids_slice is not None:
                    exit_hit = classify_tick_exit_np(
                        active["direction"], tick_bids_slice, tick_asks_slice, active["stop_loss"], active["take_profit"]
                    )
                if exit_hit is None:
                    exit_hit = classify_candle_exit(
                        active["direction"], c_low, c_high, active["stop_loss"], active["take_profit"]
                    )
                if exit_hit:
                    rp = float(active.get("risk_percent", settings.risk_percent) or settings.risk_percent)
                    risk_amount = balance * (rp / 100.0)
                    risk_price = abs(active["entry"] - active["stop_loss"])
                    cost_price = (settings.spread_points + settings.slippage_points) * POINT_SIZE
                    r_cost = (cost_price / risk_price) if risk_price > 0 else 0.0
                    r_mult = float(active.get("rr", settings.rr) or settings.rr) - r_cost if exit_hit == "TP" else -1.0 - r_cost
                    profit = risk_amount * r_mult
                    balance += profit
                    peak_balance = max(peak_balance, balance)
                    dd = peak_balance - balance
                    max_drawdown = max(max_drawdown, dd)
                    active["result"] = "WIN" if exit_hit == "TP" else "LOSS"
                    active["profit"] = profit
                    active["r_multiple"] = r_mult
                    active["balance_after"] = balance
                    active["max_drawdown_at_trade"] = max_drawdown
                    active["close_time"] = now
                    trades.append(active)
                    if active["result"] == "WIN":
                        wins += 1
                        consec_wins += 1
                        consec_losses = 0
                    else:
                        losses += 1
                        consec_losses += 1
                        consec_wins = 0
                    max_consec_wins = max(max_consec_wins, consec_wins)
                    max_consec_losses = max(max_consec_losses, consec_losses)
                    active = None

        # Look for a new idea only when nothing active
        if not active:
            if h1_cache_time != h1_now.iloc[-1]["time"]:
                h1_result_cache = detect_structure(h1_now, swing_length) or {"events": []}
                h1_cache_time = h1_now.iloc[-1]["time"]
                # Annotate H1 pivots' swept status once per H1 change.
                annotate_pivots_swept(h1_result_cache, h1_now["close"].to_numpy())
            if m15_cache_time != m15_now.iloc[-1]["time"]:
                m15_result_cache = detect_structure(m15_now, internal_length) or {"events": []}
                m15_cache_time = m15_now.iloc[-1]["time"]
                # Annotate M15 pivots' swept status once per M15 change.
                annotate_pivots_swept(m15_result_cache, m15_now["close"].to_numpy())
            h1_result = h1_result_cache
            m15_result = m15_result_cache
            m5_result = detect_structure(m5_now, internal_length) or {"events": []}

            h1_last_event = h1_result["events"][-1] if h1_result["events"] else None
            external_bias = h1_last_event["bias"] if h1_last_event else (BULLISH if c_close >= h1_now.iloc[-1]["close"] else BEARISH)
            swing = choose_h1_swing_range(h1_now, h1_result, h1_last_event)
            swing_high = float(swing["swing_high"])
            swing_low = float(swing["swing_low"])
            equilibrium = (swing_high + swing_low) / 2.0
            fibs = fib_prices(swing_low, swing_high, external_bias)
            current_location = price_location(c_close, swing_low, swing_high)
            internal_event_pack = choose_internal_event(m15_result, m5_result, m15_now, m5_now)
            decision, trade_bias, trade_mode = decide_trade_context(
                external_bias, current_location, internal_event_pack, swing_low, swing_high
            )
            current_h1_time = h1_now.iloc[-1]["time"]
            if h1_obs_cache_time != current_h1_time:
                h1_supply_ob_cache = (
                    find_rejection_order_block(h1_now, h1_result, BEARISH, "H1")
                    or last_valid_ob(h1_result["events"], h1_now, BEARISH, "H1")
                )
                h1_demand_ob_cache = (
                    find_rejection_order_block(h1_now, h1_result, BULLISH, "H1")
                    or last_valid_ob(h1_result["events"], h1_now, BULLISH, "H1")
                )
                h1_obs_cache_time = current_h1_time
            h1_supply_ob = h1_supply_ob_cache
            h1_demand_ob = h1_demand_ob_cache

            if decision not in VALID_DECISIONS or trade_bias is None:
                skipped_due_no_decision += 1
                equity_curve.append({"time": now, "balance": balance})
                continue

            selected_ob, m15_refined_ob, m5_refined_ob, active_zone_name, selected_ob_source, h1_context_ob, h1_context_label = (
                select_zone_v4(
                    direction=trade_bias,
                    trade_mode=trade_mode,
                    m15_result=m15_result,
                    m5_result=m5_result,
                    m15_now=m15_now,
                    m5_now=m5_now,
                    swing_low=swing_low,
                    swing_high=swing_high,
                    fibs=fibs,
                    h1_supply_ob=h1_supply_ob,
                    h1_demand_ob=h1_demand_ob,
                    current_price=c_close,
                )
            )

            if selected_ob and is_ob_invalidated(m5_now, selected_ob, use_close=True):
                replacement = last_valid_ob(m5_result["events"], m5_now, trade_bias, "M5") or most_recent_ob(selected_ob)
                selected_ob = dict(replacement) if replacement else None
                if selected_ob:
                    if h1_context_ob:
                        selected_ob["h1_context_ob"] = h1_context_ob
                        selected_ob["inside_h1_ob"] = h1_context_label.startswith("inside_")
                    selected_ob["h1_context_label"] = h1_context_label
                    selected_ob["selected_source"] = (selected_ob.get("selected_source") or selected_ob_source) + "+ob_invalidated_replacement"

            scoring = score_zone_v4(
                direction=trade_bias,
                trade_mode=trade_mode,
                decision=decision,
                selected_ob=selected_ob,
                m15_refined_ob=m15_refined_ob,
                m5_refined_ob=m5_refined_ob,
                h1_context_ob=h1_context_ob,
                h1_context_label=h1_context_label,
                h1_supply_ob=h1_supply_ob,
                h1_demand_ob=h1_demand_ob,
                swing_low=swing_low,
                swing_high=swing_high,
                current_price=c_close,
                m5_now=m5_now,
                m5_result=m5_result,
                m15_now=m15_now,
                m15_result=m15_result,
                h1_now=h1_now,
                h1_result=h1_result,
            )

            # Decide execution by score
            zone_score = int(scoring["zone_score"])
            if zone_score < settings.score_ai_zone_entry:
                skipped_due_low_score += 1
                if settings.save_skipped:
                    trades.append(
                        {
                            "trade_id": f"S{len(trades)+1:06d}",
                            "signal_time": now,
                            "fill_time": None,
                            "close_time": now,
                            "decision": decision,
                            "trade_mode": trade_mode,
                            "direction": "buy" if trade_bias == BULLISH else "sell",
                            "ob_timeframe": selected_ob.get("timeframe") if selected_ob else "",
                            "entry": None,
                            "stop_loss": None,
                            "take_profit": None,
                            "rr": settings.rr,
                            "result": "SKIPPED_LOW_SCORE",
                            "profit": 0.0,
                            "r_multiple": 0.0,
                            "balance_after": balance,
                            "max_drawdown_at_trade": max_drawdown,
                            "h1_bias": "bullish" if external_bias == BULLISH else "bearish",
                            "current_location": current_location,
                            "active_zone_name": active_zone_name,
                            "selected_ob_source": selected_ob_source,
                            "h1_ob_context": h1_context_label,
                            "h1_rejection_memory": h1_context_label.startswith("recent_"),
                            "m15_refined_ob_available": scoring["m15_refined_ob_available"],
                            "m5_trigger_confirmed": scoring["m5_trigger_confirmed"],
                            "true_pd_location": scoring["true_pd_location"],
                            "liquidity_sweep_confirmed": scoring["liquidity_sweep_confirmed"],
                            "opposing_h1_zone_nearby": scoring["opposing_h1_zone_nearby"],
                            "zone_score": zone_score,
                            "zone_grade": scoring["zone_grade"],
                            "zone_reason": scoring["zone_reason"],
                            "entry_status": "SKIPPED",
                            "stop_source": None,
                            "reason": "skipped_low_zone_score",
                        }
                    )
                equity_curve.append({"time": now, "balance": balance})
                continue

            candidate, reason = build_v4_candidate(
                decision=decision,
                trade_mode=trade_mode,
                selected_ob=selected_ob,
                current_close=c_close,
                settings=settings,
                scoring=scoring,
                h1_context_ob=h1_context_ob,
                h1_supply_ob=h1_supply_ob,
                h1_demand_ob=h1_demand_ob,
                m15_now=m15_now,
                now=now,
            )
            if not candidate:
                equity_curve.append({"time": now, "balance": balance})
                continue

            entry_status = get_entry_status(c_close, candidate["entry"], trade_bias, POINT_SIZE)

            # V4 hard rule: AT_BUY_ENTRY / AT_SELL_ENTRY were ~0% WR in V2_max2, skip unless score very strong
            if skip_at_entry and entry_status in {"AT_BUY_ENTRY", "AT_SELL_ENTRY"} and zone_score < 8:
                skipped_due_at_entry += 1
                if settings.save_skipped:
                    trades.append(
                        {
                            "trade_id": f"X{len(trades)+1:06d}",
                            "signal_time": now,
                            "fill_time": None,
                            "close_time": now,
                            "decision": decision,
                            "trade_mode": trade_mode,
                            "direction": candidate["direction"],
                            "ob_timeframe": candidate["ob_timeframe"],
                            "entry": candidate["entry"],
                            "stop_loss": candidate["stop_loss"],
                            "take_profit": candidate["take_profit"],
                            "rr": candidate["rr"],
                            "result": "SKIPPED_AT_ENTRY",
                            "profit": 0.0,
                            "r_multiple": 0.0,
                            "balance_after": balance,
                            "max_drawdown_at_trade": max_drawdown,
                            "h1_bias": "bullish" if external_bias == BULLISH else "bearish",
                            "current_location": current_location,
                            "active_zone_name": active_zone_name,
                            "selected_ob_source": selected_ob_source,
                            "h1_ob_context": h1_context_label,
                            "h1_rejection_memory": h1_context_label.startswith("recent_"),
                            "m15_refined_ob_available": scoring["m15_refined_ob_available"],
                            "m5_trigger_confirmed": scoring["m5_trigger_confirmed"],
                            "true_pd_location": scoring["true_pd_location"],
                            "liquidity_sweep_confirmed": scoring["liquidity_sweep_confirmed"],
                            "opposing_h1_zone_nearby": scoring["opposing_h1_zone_nearby"],
                            "zone_score": zone_score,
                            "zone_grade": scoring["zone_grade"],
                            "zone_reason": scoring["zone_reason"],
                            "entry_status": entry_status,
                            "stop_source": candidate["stop_source"],
                            "reason": "skipped_at_entry_low_score",
                        }
                    )
                equity_curve.append({"time": now, "balance": balance})
                continue

            # Daily cap (V4 default is unlimited; keep optional for parity with v2_max2)
            sig_day = pd.Timestamp(now).date().isoformat()
            if max_trades_per_day > 0 and daily_trade_attempts[sig_day] >= max_trades_per_day:
                skipped_due_daily_trade_limit_count += 1
                equity_curve.append({"time": now, "balance": balance})
                continue

            daily_trade_attempts[sig_day] += 1
            total_signals += 1
            execution_style = candidate["execution_style"]
            active = {
                "trade_id": f"T{len(trades)+1:06d}",
                "signal_time": now,
                "fill_time": now if execution_style == "ai_zone_market" else None,
                "close_time": None,
                "state": "open" if execution_style == "ai_zone_market" else "pending",
                "expiry_time": now + timedelta(hours=settings.order_expiry_hours),
                "entry_status": entry_status,
                "result": "",
                "profit": 0.0,
                "r_multiple": 0.0,
                "balance_after": balance,
                "max_drawdown_at_trade": max_drawdown,
                "h1_bias": "bullish" if external_bias == BULLISH else "bearish",
                "current_location": current_location,
                "active_zone_name": active_zone_name,
                "selected_ob_source": candidate.get("selected_ob_source", selected_ob_source),
                "h1_ob_context": h1_context_label,
                "h1_rejection_memory": h1_context_label.startswith("recent_"),
                "h1_context_label": h1_context_label,
                "m15_refined_ob_available": scoring["m15_refined_ob_available"],
                "m5_trigger_confirmed": scoring["m5_trigger_confirmed"],
                "selected_zone_timeframe": candidate["ob_timeframe"],
                "true_pd_location": scoring["true_pd_location"],
                "pd_position": scoring["pd_position"],
                "liquidity_sweep_confirmed": scoring["liquidity_sweep_confirmed"],
                "opposing_h1_zone_nearby": scoring["opposing_h1_zone_nearby"],
                "zone_score": zone_score,
                "zone_grade": scoring["zone_grade"],
                "zone_reason": scoring["zone_reason"],
                "m15_refined_ob_high": float(m15_refined_ob["high"]) if m15_refined_ob else 0.0,
                "m15_refined_ob_low": float(m15_refined_ob["low"]) if m15_refined_ob else 0.0,
                "m5_refined_ob_high": float(m5_refined_ob["high"]) if m5_refined_ob else 0.0,
                "m5_refined_ob_low": float(m5_refined_ob["low"]) if m5_refined_ob else 0.0,
                "reason": "valid_signal",
                "daily_trade_number": daily_trade_attempts[sig_day],
                "max_trades_per_day": max_trades_per_day,
                **candidate,
            }

        equity_curve.append({"time": now, "balance": balance})

    # ============================================================================
    # SUMMARY
    # ============================================================================
    closed_trades = [t for t in trades if t.get("result") in {"WIN", "LOSS"}]
    filled_n = len(closed_trades)
    gross_profit = sum(float(t["profit"]) for t in closed_trades if t["profit"] > 0)
    gross_loss = abs(sum(float(t["profit"]) for t in closed_trades if t["profit"] < 0))
    profit_factor = round((gross_profit / gross_loss) if gross_loss > 0 else 0.0, 4)
    avg_r = round(sum(float(t["r_multiple"]) for t in closed_trades) / filled_n, 4) if filled_n else 0.0
    best_r = round(max((float(t["r_multiple"]) for t in closed_trades), default=0.0), 4)
    worst_r = round(min((float(t["r_multiple"]) for t in closed_trades), default=0.0), 4)
    max_dd_pct = round((max_drawdown / peak_balance * 100.0) if peak_balance > 0 else 0.0, 2)
    win_rate = round((wins / (wins + losses) * 100.0) if (wins + losses) > 0 else 0.0, 2)

    avg_score_winners = (
        round(sum(int(t.get("zone_score", 0)) for t in closed_trades if t["result"] == "WIN") / max(1, wins), 4)
        if wins else 0.0
    )
    avg_score_losers = (
        round(sum(int(t.get("zone_score", 0)) for t in closed_trades if t["result"] == "LOSS") / max(1, losses), 4)
        if losses else 0.0
    )

    # Add zone_score bucket for grouping
    for t in trades:
        try:
            t["zone_score_bucket"] = _zone_score_bucket(int(t.get("zone_score", 0) or 0))
        except Exception:
            t["zone_score_bucket"] = "F_lt_2"

    out_dir = backend_dir / "storage" / "backtests"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    trades_path = out_dir / f"backtest_trades_zone_refinement_v4_opus_{ts}.csv"
    summary_path = out_dir / f"backtest_summary_zone_refinement_v4_opus_{ts}.json"
    equity_path = out_dir / f"backtest_equity_curve_zone_refinement_v4_opus_{ts}.csv"

    pd.DataFrame(trades).to_csv(trades_path, index=False)
    pd.DataFrame(equity_curve).to_csv(equity_path, index=False)

    perf_by_zone_grade = _group_by_key(trades, "zone_grade")
    perf_by_zone_bucket = _group_by_key(trades, "zone_score_bucket")
    perf_by_selected_tf = _group_by_key(trades, "selected_zone_timeframe")
    perf_by_h1_ctx = _group_by_key(trades, "h1_ob_context")
    perf_by_m15_avail = _group_by_key(trades, "m15_refined_ob_available")
    perf_by_pd = _group_by_key(trades, "true_pd_location")
    perf_by_decision = _group_by_key(trades, "decision")
    perf_by_mode = _group_by_key(trades, "trade_mode")
    perf_by_liq_sweep = _group_by_key(trades, "liquidity_sweep_confirmed")
    perf_by_entry_model = _group_by_key(trades, "entry_model")
    perf_by_exec_style = _group_by_key(trades, "execution_style")

    summary: dict[str, Any] = {
        "env_file": str(env_file_path),
        "strategy_version": STRATEGY_VERSION,
        "csv_date_range": {"start": str(bars["time"].min()), "end": str(bars["time"].max())},
        "total_m5_candles_tested": int(len(m5)),
        "total_signals": int(total_signals),
        "total_filled_trades": int(filled_n),
        "expired_pending_orders": int(expired),
        "wins": int(wins),
        "losses": int(losses),
        "win_rate": float(win_rate),
        "profit_factor": float(profit_factor),
        "net_profit": round(balance - settings.initial_balance, 2),
        "starting_balance": round(settings.initial_balance, 2),
        "final_balance": round(balance, 2),
        "max_drawdown_amount": round(max_drawdown, 2),
        "max_drawdown_percent": float(max_dd_pct),
        "average_r": float(avg_r),
        "best_trade_r": float(best_r),
        "worst_trade_r": float(worst_r),
        "consecutive_wins": int(max_consec_wins),
        "consecutive_losses": int(max_consec_losses),
        "skipped_due_low_score": int(skipped_due_low_score),
        "skipped_due_at_entry": int(skipped_due_at_entry),
        "skipped_due_no_decision": int(skipped_due_no_decision),
        "skipped_due_daily_trade_limit_count": int(skipped_due_daily_trade_limit_count),
        "trades_by_decision": dict(Counter(t.get("decision", "") for t in closed_trades)),
        "trades_by_trade_mode": dict(Counter(t.get("trade_mode", "") for t in closed_trades)),
        "trades_by_ob_timeframe": dict(Counter(t.get("ob_timeframe", "") for t in closed_trades)),
        "trades_by_entry_model": dict(Counter(t.get("entry_model", "") for t in closed_trades)),
        "trades_by_execution_style": dict(Counter(t.get("execution_style", "") for t in closed_trades)),
        "trades_by_zone_grade": dict(Counter(t.get("zone_grade", "") for t in closed_trades)),
        "trades_by_zone_score_bucket": dict(Counter(t.get("zone_score_bucket", "") for t in closed_trades)),
        "performance_by_zone_grade": perf_by_zone_grade,
        "performance_by_zone_score_bucket": perf_by_zone_bucket,
        "performance_by_selected_zone_timeframe": perf_by_selected_tf,
        "performance_by_h1_ob_context": perf_by_h1_ctx,
        "performance_by_m15_refined_ob_available": perf_by_m15_avail,
        "performance_by_true_pd_location": perf_by_pd,
        "performance_by_decision": perf_by_decision,
        "performance_by_trade_mode": perf_by_mode,
        "performance_by_liquidity_sweep_confirmed": perf_by_liq_sweep,
        "performance_by_entry_model": perf_by_entry_model,
        "performance_by_execution_style": perf_by_exec_style,
        "average_zone_score_winners": float(avg_score_winners),
        "average_zone_score_losers": float(avg_score_losers),
        "monthly_performance": monthly_performance(trades),
        "ticks_used": bool(ticks_enabled),
        "settings": {
            **{k: v for k, v in settings.__dict__.items()},
            "research_targets": {
                "min_trades": int(settings.target_min_trades),
                "max_trades": int(settings.target_max_trades),
                "profit_factor": float(settings.target_profit_factor),
            },
            "score_thresholds": {
                "full_entry": int(settings.score_full_entry),
                "ai_zone_entry": int(settings.score_ai_zone_entry),
            },
        },
        "research_target_check": {
            "filled_within_range": bool(
                settings.target_min_trades <= filled_n <= settings.target_max_trades
            ),
            "profit_factor_target_met": bool(profit_factor >= settings.target_profit_factor),
            "credible_overall": bool(
                profit_factor >= settings.target_profit_factor
                and filled_n >= settings.target_min_trades
            ),
        },
        "outputs": {
            "trades_csv": str(trades_path),
            "summary_json": str(summary_path),
            "equity_curve_csv": str(equity_path),
        },
    }
    summary_path.write_text(json.dumps(summary, default=str, indent=2), encoding="utf-8")

    print("\n===== V4 ZONE REFINEMENT SUMMARY =====")
    for k in [
        "csv_date_range",
        "total_m5_candles_tested",
        "total_signals",
        "total_filled_trades",
        "expired_pending_orders",
        "wins",
        "losses",
        "win_rate",
        "profit_factor",
        "net_profit",
        "starting_balance",
        "final_balance",
        "max_drawdown_amount",
        "max_drawdown_percent",
        "average_r",
        "best_trade_r",
        "worst_trade_r",
        "consecutive_wins",
        "consecutive_losses",
        "skipped_due_low_score",
        "skipped_due_at_entry",
        "skipped_due_no_decision",
        "skipped_due_daily_trade_limit_count",
        "average_zone_score_winners",
        "average_zone_score_losers",
    ]:
        print(f"{k}: {summary[k]}")
    print(f"trades_by_zone_grade: {summary['trades_by_zone_grade']}")
    print(f"trades_by_zone_score_bucket: {summary['trades_by_zone_score_bucket']}")
    print(f"trades_by_decision: {summary['trades_by_decision']}")
    print(f"trades_by_entry_model: {summary['trades_by_entry_model']}")
    print(f"trades_by_execution_style: {summary['trades_by_execution_style']}")
    print(f"research_target_check: {summary['research_target_check']}")
    print(f"Saved trades CSV: {trades_path}")
    print(f"Saved summary JSON: {summary_path}")
    print(f"Saved equity curve CSV: {equity_path}")


if __name__ == "__main__":
    main()
