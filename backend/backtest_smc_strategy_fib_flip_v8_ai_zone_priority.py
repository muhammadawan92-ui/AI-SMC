"""
Fib-Confirmed OB + Flip Entry V8 AI Zone Priority (research-only)

Based on V6. Identical scoring, zone selection, and flip logic.
Single targeted change: execution-risk routing.

  V6 finding: ai_zone_market PF 4.19 vs pending_limit PF 0.72.
  V8 test: route risk by execution style:
    - ai_zone_market = 1% risk  (promote the edge)
    - pending_limit   = 0.5% risk (reduce bleed)

Everything else (scoring, grading, flip logic, M15 priority) is V6 unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
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
    price_inside_ob,
    price_location,
)

try:
    from smc_core import find_rejection_order_block
except Exception:
    def find_rejection_order_block(*args, **kwargs):
        return None

# ============================================================================
# Constants
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

STRATEGY_VERSION = "fib_flip_v8_ai_zone_priority"

# Cached PD thresholds (resolved once in main from env).
_PD_THRESHOLDS: dict[str, float] = {
    "premium_start": 0.618,
    "deep_premium": 0.705,
    "extreme_premium": 0.886,
    "discount_start": 0.382,
    "deep_discount": 0.295,
    "extreme_discount": 0.114,
}


def _resolve_pd_thresholds_from_env() -> None:
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
    ai_zone_risk_percent: float
    pending_limit_risk_percent: float
    flip_risk_percent: float
    rr: float
    ob_buffer_pips: float
    order_expiry_hours: int
    max_open_trades: int
    spread_points: float
    slippage_points: float
    commission_per_lot: float
    save_skipped: bool
    use_ticks: bool
    enable_flip_entries: bool
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
# CSV loading (identical to V2/V4)
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
    out[["open", "high", "low", "close", "volume"]] = out[["open", "high", "low", "close", "volume"]].astype(float)
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
# OB width filter + H1 context helpers (from V4)
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
# Pivot annotation + liquidity / displacement (from V4)
# ============================================================================
_PIVOT_SCAN_LIMIT_DEFAULT = 8


def annotate_pivots_swept(structure_result: dict, closes_np, recent_limit: int = 16) -> None:
    if not structure_result or closes_np is None:
        return
    n = int(len(closes_np))
    if n <= 0:
        return
    for kind, op in (("pivot_highs", ">"), ("pivot_lows", "<")):
        pivots = structure_result.get(kind, []) or []
        if not pivots:
            continue
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
    if len(m5_now) < lookback:
        return 0
    closes = m5_now["close"].to_numpy()[-lookback:]
    opens = m5_now["open"].to_numpy()[-lookback:]
    highs = m5_now["high"].to_numpy()[-lookback:]
    lows = m5_now["low"].to_numpy()[-lookback:]
    bodies = closes - opens
    body_abs = np.abs(bodies)
    ranges = highs - lows
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


# ============================================================================
# FIB-CONFIRMED OB BUILDER (ported from test_smc_overlay.py)
# ============================================================================
def _price_ranges_overlap(low_a: float, high_a: float, low_b: float, high_b: float) -> bool:
    return not (max(low_a, high_a) < min(low_b, high_b) or min(low_a, high_a) > max(low_b, high_b))


def build_fib_confirmed_ob_from_event(
    df: pd.DataFrame,
    event: dict,
    bias: int,
    timeframe_label: str,
    zone_low: float | None = None,
    zone_high: float | None = None,
) -> dict | None:
    """
    Manual-style fib confirmation:
    For bearish: fib from impulse_high→impulse_low, bullish candles in 0.618-0.886 = supply.
    For bullish: fib from impulse_low→impulse_high, bearish candles in 0.618-0.886 = demand.
    """
    if not event or not event.get("order_block"):
        return None
    break_index = int(event.get("break_index", 0))
    level_index = int(event.get("level_index", 0))
    if break_index <= level_index or break_index >= len(df):
        return None

    fib_min = _PD_THRESHOLDS["premium_start"]
    fib_max = _PD_THRESHOLDS["extreme_premium"]
    max_cluster = env_int("SMC_FIB_OB_MAX_CLUSTER", 2)
    use_body = env_bool("SMC_FIB_OB_USE_BODY_OVERLAP", True)
    ob_lookback = env_int("SMC_OB_LOOKBACK", 20)

    segment = df.iloc[level_index : break_index + 1]
    if segment.empty or len(segment) < 2:
        return None

    if bias == BEARISH:
        impulse_high = float(segment["high"].max())
        impulse_low = float(segment["low"].min())
        ob_type = "supply"
        fib_range = impulse_high - impulse_low
        if fib_range <= 0:
            return None
        fib_zone_low = impulse_low + fib_range * fib_min
        fib_zone_high = impulse_low + fib_range * fib_max
    else:
        impulse_low = float(segment["low"].min())
        impulse_high = float(segment["high"].max())
        ob_type = "demand"
        fib_range = impulse_high - impulse_low
        if fib_range <= 0:
            return None
        fib_zone_high = impulse_high - fib_range * fib_min
        fib_zone_low = impulse_high - fib_range * fib_max

    search_start = max(level_index, break_index - ob_lookback)
    search = df.iloc[search_start:break_index]
    if search.empty:
        return None

    if bias == BEARISH:
        opposite = search[search["close"] > search["open"]]
    else:
        opposite = search[search["close"] < search["open"]]
    if opposite.empty:
        return None

    candidates = []
    for idx_val in opposite.index:
        row = opposite.loc[idx_val]
        if use_body:
            body_low = min(float(row["open"]), float(row["close"]))
            body_high = max(float(row["open"]), float(row["close"]))
        else:
            body_low, body_high = float(row["low"]), float(row["high"])
        if not _price_ranges_overlap(body_low, body_high, fib_zone_low, fib_zone_high):
            continue
        mid = (body_low + body_high) / 2.0
        fib_705 = (impulse_low + fib_range * 0.705) if bias == BEARISH else (impulse_high - fib_range * 0.705)
        fib_790 = (impulse_low + fib_range * 0.79) if bias == BEARISH else (impulse_high - fib_range * 0.79)
        dist_to_ideal = min(abs(mid - fib_705), abs(mid - fib_790))
        candidates.append({
            "index": int(idx_val), "body_low": body_low, "body_high": body_high,
            "dist_to_ideal": dist_to_ideal,
        })

    if not candidates:
        return None

    if zone_low is not None and zone_high is not None:
        zl, zh = min(zone_low, zone_high), max(zone_low, zone_high)
        candidates = [c for c in candidates if _price_ranges_overlap(c["body_low"], c["body_high"], zl, zh)]
        if not candidates:
            return None

    if bias == BEARISH:
        chosen = max(candidates, key=lambda c: c["body_high"])
    else:
        chosen = min(candidates, key=lambda c: c["body_low"])

    chosen_idx = chosen["index"]
    cluster_start = chosen_idx
    cluster_count = 1
    while cluster_count < max_cluster:
        prev_idx = cluster_start - 1
        if prev_idx < search_start:
            break
        prev = df.loc[prev_idx]
        is_opp = (float(prev["close"]) > float(prev["open"])) if bias == BEARISH else (float(prev["close"]) < float(prev["open"]))
        if not is_opp:
            break
        cluster_start = prev_idx
        cluster_count += 1

    cluster = df.loc[cluster_start:chosen_idx]
    if cluster.empty:
        return None

    ob = {
        "type": ob_type,
        "bias": bias,
        "index": int(cluster_start),
        "end_index": int(chosen_idx),
        "time": cluster.iloc[0]["time"],
        "end_time": cluster.iloc[-1]["time"],
        "high": float(cluster["high"].max()),
        "low": float(cluster["low"].min()),
        "open": float(cluster.iloc[0]["open"]),
        "close": float(cluster.iloc[-1]["close"]),
        "cluster_count": int(len(cluster)),
        "timeframe": timeframe_label,
        "fib_confirmed": True,
        "fib_impulse_high": float(impulse_high),
        "fib_impulse_low": float(impulse_low),
        "fib_zone_low": float(fib_zone_low),
        "fib_zone_high": float(fib_zone_high),
        "fib_level_min": float(fib_min),
        "fib_level_max": float(fib_max),
        "fib_dist_to_ideal": float(chosen["dist_to_ideal"]),
    }

    if is_ob_invalidated(df, ob, use_close=True):
        return None
    return ob


def last_fib_confirmed_ob(
    events: list[dict],
    df: pd.DataFrame,
    bias: int,
    timeframe_label: str,
    zone_low: float | None = None,
    zone_high: float | None = None,
    lookback_events: int = 6,
) -> dict | None:
    if not events:
        return None
    checked = 0
    for event in reversed(events):
        if event.get("bias") != bias:
            continue
        checked += 1
        ob = build_fib_confirmed_ob_from_event(df, event, bias, timeframe_label, zone_low, zone_high)
        if ob:
            return ob
        if checked >= lookback_events:
            break
    return None


# ============================================================================
# FLIP DETECTION (ported from test_smc_overlay.py)
# ============================================================================
def _latest_ob_from_events(events: list[dict], bias: int) -> tuple[dict | None, dict | None]:
    for e in reversed(events):
        if e.get("bias") == bias and e.get("order_block"):
            return e["order_block"], e
    return None, None


def detect_ob_flip_candidates(
    m15_result: dict,
    m5_result: dict,
    m15_now: pd.DataFrame,
    m5_now: pd.DataFrame,
    current_price: float,
) -> dict[str, dict | None]:
    out: dict[str, dict | None] = {
        "m5_bullish_flip": None, "m5_bearish_flip": None,
        "m15_bullish_flip": None, "m15_bearish_flip": None,
    }
    m5_events = (m5_result or {}).get("events", [])
    m15_events = (m15_result or {}).get("events", [])

    m5_supply, _ = _latest_ob_from_events(m5_events, BEARISH)
    m5_demand, _ = _latest_ob_from_events(m5_events, BULLISH)
    m15_supply, _ = _latest_ob_from_events(m15_events, BEARISH)
    m15_demand, _ = _latest_ob_from_events(m15_events, BULLISH)

    if m5_supply and is_ob_invalidated(m5_now, m5_supply, use_close=True):
        out["m5_bullish_flip"] = {
            "timeframe": "M5", "invalidated_ob_type": "supply", "ob": m5_supply,
            "flip_type": "M5_SUPPLY_INVALIDATED_BULLISH_FLIP", "flip_direction": "bullish",
        }
    if m5_demand and is_ob_invalidated(m5_now, m5_demand, use_close=True):
        out["m5_bearish_flip"] = {
            "timeframe": "M5", "invalidated_ob_type": "demand", "ob": m5_demand,
            "flip_type": "M5_DEMAND_INVALIDATED_BEARISH_FLIP", "flip_direction": "bearish",
        }
    if m15_supply and is_ob_invalidated(m15_now, m15_supply, use_close=True):
        out["m15_bullish_flip"] = {
            "timeframe": "M15", "invalidated_ob_type": "supply", "ob": m15_supply,
            "flip_type": "M15_SUPPLY_INVALIDATED_BULLISH_FLIP", "flip_direction": "bullish",
        }
    if m15_demand and is_ob_invalidated(m15_now, m15_demand, use_close=True):
        out["m15_bearish_flip"] = {
            "timeframe": "M15", "invalidated_ob_type": "demand", "ob": m15_demand,
            "flip_type": "M15_DEMAND_INVALIDATED_BEARISH_FLIP", "flip_direction": "bearish",
        }
    return out


def _select_relevant_flip(
    flip_candidates: dict[str, dict | None],
    trade_bias: int,
) -> dict | None:
    if trade_bias == BULLISH:
        return flip_candidates.get("m15_bullish_flip") or flip_candidates.get("m5_bullish_flip")
    if trade_bias == BEARISH:
        return flip_candidates.get("m15_bearish_flip") or flip_candidates.get("m5_bearish_flip")
    return None


def build_flip_entry_ob(
    flip_candidate: dict,
    trade_bias: int,
    m15_result: dict,
    m5_result: dict,
    m15_now: pd.DataFrame,
    m5_now: pd.DataFrame,
    zone_low: float | None = None,
    zone_high: float | None = None,
) -> tuple[dict | None, str]:
    """After a flip, search for a fib-confirmed OB in the retracement of the flip impulse."""
    if not flip_candidate:
        return None, "no_flip_candidate"

    m15_fib = last_fib_confirmed_ob(
        (m15_result or {}).get("events", []), m15_now, trade_bias, "M15", zone_low, zone_high
    )
    m5_fib = last_fib_confirmed_ob(
        (m5_result or {}).get("events", []), m5_now, trade_bias, "M5", zone_low, zone_high
    )

    selected = m15_fib or m5_fib
    if selected:
        selected = dict(selected)
        selected["flip_source"] = True
        return selected, "flip_fib_ob_found"
    return None, "no_fib_ob_after_flip"


# ============================================================================
# V6 ZONE SCORING
# ============================================================================
def score_zone_v6(
    *,
    direction: int,
    trade_mode: str,
    decision: str,
    selected_ob: dict | None,
    m15_fib_ob: dict | None,
    m5_fib_ob: dict | None,
    m15_legacy_ob: dict | None,
    m5_legacy_ob: dict | None,
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
    flip_candidate: dict | None,
    is_flip_entry: bool,
) -> dict[str, Any]:
    score = 0
    reasons: list[str] = []

    # +3 inside active H1 supply/demand
    if h1_context_label.startswith("inside_"):
        score += 3
        reasons.append("+3 inside_h1_ob")
    elif h1_context_label.startswith("recent_"):
        score += 2
        reasons.append("+2 h1_rejection_memory")

    # +2 fib-confirmed OB in retracement zone
    fib_confirmed = bool(selected_ob and selected_ob.get("fib_confirmed"))
    if fib_confirmed:
        score += 2
        reasons.append("+2 fib_confirmed_ob")

    # +2 M15 fib-confirmed OB available
    if m15_fib_ob and m15_fib_ob.get("fib_confirmed"):
        score += 2
        reasons.append("+2 m15_fib_confirmed_ob")

    # +1 M5 trigger confirms direction
    m5_trigger_confirmed = False
    last_m5_events = (m5_result or {}).get("events") or []
    if last_m5_events and last_m5_events[-1].get("bias") == direction:
        m5_trigger_confirmed = True
        score += 1
        reasons.append("+1 m5_trigger")

    # +1 OB near 0.705 or 0.79 (within 15 pips)
    if selected_ob and selected_ob.get("fib_dist_to_ideal") is not None:
        if float(selected_ob["fib_dist_to_ideal"]) / PIP_SIZE < 15:
            score += 1
            reasons.append("+1 ob_near_ideal_fib")

    # +1 strong displacement
    has_displacement = bool(_displacement_strength_score(direction, m5_now))
    if has_displacement:
        score += 1
        reasons.append("+1 displacement")

    # +1 flip aligns with H1 bias/rejection
    if is_flip_entry and flip_candidate:
        flip_dir = flip_candidate.get("flip_direction")
        if (flip_dir == "bullish" and h1_context_label in ("inside_h1_demand", "recent_h1_demand_rejection")) or \
           (flip_dir == "bearish" and h1_context_label in ("inside_h1_supply", "recent_h1_supply_rejection")):
            score += 1
            reasons.append("+1 flip_aligns_h1")

    # +1 liquidity target present
    weak_target = None
    if direction == BEARISH:
        weak_target = _find_unswept_pivot_low(m15_result, current_price)
    else:
        weak_target = _find_unswept_pivot_high(m15_result, current_price)
    if weak_target:
        score += 1
        reasons.append("+1 liquidity_target")

    # +1 liquidity sweep before CHoCH
    liq_sweep = _liquidity_sweep_before_internal_break(direction, m5_now, m5_result, m15_result)
    if liq_sweep:
        score += 1
        reasons.append("+1 liquidity_sweep_before_choch")

    # PD location bonus
    pd_label, pd_pos = _pd_label_fast(current_price, swing_low, swing_high)
    premium_labels = {"true_premium", "deep_premium", "extreme_premium"}
    deep_premium_labels = {"deep_premium", "extreme_premium"}
    discount_labels = {"true_discount", "deep_discount", "extreme_discount"}
    deep_discount_labels = {"deep_discount", "extreme_discount"}
    if direction == BEARISH and pd_label in premium_labels:
        score += 1
        reasons.append(f"+1 premium({pd_label})")
        if pd_label in deep_premium_labels:
            score += 1
            reasons.append("+1 deep_premium")
    elif direction == BULLISH and pd_label in discount_labels:
        score += 1
        reasons.append(f"+1 discount({pd_label})")
        if pd_label in deep_discount_labels:
            score += 1
            reasons.append("+1 deep_discount")

    # ========== PENALTIES ==========

    # -3 OB not inside fib zone
    if not fib_confirmed:
        score -= 3
        reasons.append("-3 ob_not_fib_confirmed")

    # -2 M5-only no M15/H1
    selected_tf = (selected_ob or {}).get("timeframe", "")
    if selected_tf == "M5" and h1_context_label == "none" and not m15_fib_ob:
        score -= 2
        reasons.append("-2 m5_only_no_m15_h1")

    # -2 AT_BUY_ENTRY / AT_SELL_ENTRY handled at candidate level, scored here for reference
    # (actual skip is in main loop)

    # -2 generic premium/discount
    if direction == BEARISH and pd_label == "above_EQ_not_true_premium":
        score -= 2
        reasons.append("-2 generic_premium_no_confirmation")
    if direction == BULLISH and pd_label == "below_EQ_not_true_discount":
        score -= 2
        reasons.append("-2 generic_discount_no_confirmation")

    # -2 opposing H1 zone nearby
    opposing_h1 = False
    if direction == BULLISH and h1_supply_ob and not h1_context_label.startswith("inside_h1_demand"):
        if float(h1_supply_ob["low"]) > float(current_price):
            opposing_h1 = True
    if direction == BEARISH and h1_demand_ob and not h1_context_label.startswith("inside_h1_supply"):
        if float(h1_demand_ob["high"]) < float(current_price):
            opposing_h1 = True
    if opposing_h1:
        score -= 2
        reasons.append("-2 opposing_h1_zone_nearby")

    # -2 against unresolved HTF liquidity
    if _unresolved_htf_liquidity_against_direction(direction, current_price, h1_result):
        score -= 2
        reasons.append("-2 unresolved_htf_liquidity_against")

    # -2 price moved too far from flip impulse
    if is_flip_entry and flip_candidate:
        flip_ob = flip_candidate.get("ob")
        if flip_ob:
            flip_mid = (float(flip_ob["high"]) + float(flip_ob["low"])) / 2.0
            dist_pips = abs(current_price - flip_mid) / PIP_SIZE
            if dist_pips > 80:
                score -= 2
                reasons.append(f"-2 flip_too_far_{dist_pips:.0f}pips")

    # OB width penalty
    if selected_ob and not _ob_passes_width_filter(selected_ob, selected_tf):
        score -= 2
        reasons.append("-2 zone_too_wide")

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
        "fib_confirmed_ob": bool(fib_confirmed),
        "liquidity_sweep_confirmed": bool(liq_sweep),
        "opposing_h1_zone_nearby": bool(opposing_h1),
        "m5_trigger_confirmed": bool(m5_trigger_confirmed),
        "m15_fib_ob_available": bool(m15_fib_ob),
        "m15_legacy_ob_available": bool(m15_legacy_ob),
        "weak_target_present": bool(weak_target),
        "has_displacement": bool(has_displacement),
    }


# ============================================================================
# V6 ZONE SELECTION (fib-confirmed priority)
# ============================================================================
def select_zone_v6(
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
    V6: fib-confirmed OBs have priority; legacy OBs are fallback.
    Returns: selected_ob, m15_fib_ob, m5_fib_ob, m15_legacy_ob, m5_legacy_ob,
             zone_name, selected_source, h1_context_ob, h1_context_label
    """
    if direction is None:
        return None, None, None, None, None, "none", "no_direction", None, "none"

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

    m15_fib_ob = last_fib_confirmed_ob(
        (m15_result or {}).get("events", []), m15_now, direction, "M15", zone_low, zone_high,
        lookback_events=env_int("SMC_FIB_OB_LOOKBACK_EVENTS", 6),
    )
    m5_fib_ob = last_fib_confirmed_ob(
        (m5_result or {}).get("events", []), m5_now, direction, "M5", zone_low, zone_high,
        lookback_events=env_int("SMC_FIB_OB_LOOKBACK_EVENTS", 6),
    )
    m15_legacy_ob = last_valid_ob(m15_result["events"], m15_now, direction, "M15", zone_low=zone_low, zone_high=zone_high)
    m5_legacy_ob = last_valid_ob(m5_result["events"], m5_now, direction, "M5", zone_low=zone_low, zone_high=zone_high)

    selected_ob = None
    selected_source = "none"

    # Priority: M15 fib > M5 fib > M15 legacy > M5 legacy
    if m15_fib_ob and _ob_passes_width_filter(m15_fib_ob, "M15"):
        selected_ob = m15_fib_ob
        selected_source = "m15_fib_confirmed"
    elif m5_fib_ob and _ob_passes_width_filter(m5_fib_ob, "M5"):
        selected_ob = m5_fib_ob
        selected_source = "m5_fib_confirmed"
    elif h1_context_ob and _ob_passes_width_filter(m15_legacy_ob, "M15"):
        selected_ob = m15_legacy_ob
        selected_source = "h1_context_m15_legacy"
    elif h1_context_ob and _ob_passes_width_filter(m5_legacy_ob, "M5"):
        selected_ob = m5_legacy_ob
        selected_source = "h1_context_m5_legacy"
    elif _ob_passes_width_filter(m15_legacy_ob, "M15") and trade_mode == "retracement":
        selected_ob = m15_legacy_ob
        selected_source = "retracement_m15_legacy"
    elif _ob_passes_width_filter(m5_legacy_ob, "M5"):
        selected_ob = m5_legacy_ob
        selected_source = "m5_legacy_fallback"
    elif _ob_passes_width_filter(m15_legacy_ob, "M15"):
        selected_ob = m15_legacy_ob
        selected_source = "m15_legacy_fallback"
    else:
        selected_ob = most_recent_ob(m15_legacy_ob, m5_legacy_ob)
        selected_source = "fallback_most_recent"

    if selected_ob:
        selected_ob = dict(selected_ob)
        if h1_context_ob:
            selected_ob["h1_context_ob"] = h1_context_ob
            selected_ob["inside_h1_ob"] = h1_context_label.startswith("inside_")
        selected_ob["h1_context_label"] = h1_context_label
        selected_ob["selected_source"] = selected_source

    return (
        selected_ob, m15_fib_ob, m5_fib_ob, m15_legacy_ob, m5_legacy_ob,
        zone_name, selected_source, h1_context_ob, h1_context_label,
    )


# ============================================================================
# STOP-LOSS + CANDIDATE BUILDER
# ============================================================================
def _h1_midpoint(ob: dict | None) -> float | None:
    if not ob:
        return None
    return (float(ob["high"]) + float(ob["low"])) / 2.0


def _m15_swing_protected_stop(direction: str, m15_now: pd.DataFrame, fallback_stop: float, buffer_price: float) -> tuple[float, str]:
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


def build_v8_candidate(
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
    flip_candidate: dict | None,
    is_flip_entry: bool,
    m15_fib_ob_available: bool = False,
):
    if decision not in VALID_DECISIONS or not selected_ob:
        return None, "no_valid_decision_or_ob"

    direction = "buy" if decision.startswith("BUY") else "sell"
    ob_high = float(selected_ob["high"])
    ob_low = float(selected_ob["low"])
    buffer_price = settings.ob_buffer_pips * PIP_SIZE
    selected_tf = str(selected_ob.get("timeframe", "")).upper()
    fib_confirmed = bool(selected_ob.get("fib_confirmed"))
    zone_score = int(scoring.get("zone_score", 0))

    if direction == "buy":
        limit_entry = ob_high
        base_stop = ob_low - buffer_price
    else:
        limit_entry = ob_low
        base_stop = ob_high + buffer_price

    # Stop refinement (same as V4)
    stop = base_stop
    stop_source = "ltf_ob"
    if selected_tf == "M15":
        stop_source = "m15_ob_buffer"
    elif selected_tf == "M5" and h1_context_ob:
        h1_mid = _h1_midpoint(h1_context_ob)
        if h1_mid is not None:
            if direction == "buy" and h1_mid < limit_entry:
                stop = min(stop, h1_mid)
                stop_source = "h1_context_midpoint_protected"
            elif direction == "sell" and h1_mid > limit_entry:
                stop = max(stop, h1_mid)
                stop_source = "h1_context_midpoint_protected"
    elif selected_tf == "M5" and not h1_context_ob:
        stop, stop_source = _m15_swing_protected_stop(direction, m15_now, base_stop, buffer_price)

    # V8: V6 entry-model logic is identical — determines execution_style + entry_model.
    # Risk routing is overridden at the end by execution_style.
    risk_percent = float(settings.risk_percent)
    execution_style = "pending_limit"
    entry_model = "FIB_CONFIRMED_OB_ENTRY" if fib_confirmed else "LEGACY_OB_ENTRY"

    if is_flip_entry:
        if fib_confirmed and zone_score >= settings.score_full_entry:
            entry_model = "FLIP_FIB_RETEST_ENTRY"
            risk_percent = settings.flip_risk_percent
            if zone_score >= 7 and h1_context_ob:
                risk_percent = settings.risk_percent
        elif fib_confirmed:
            entry_model = "FLIP_AI_ZONE_ENTRY"
            risk_percent = settings.flip_risk_percent
            execution_style = "ai_zone_market"
        else:
            return None, "flip_no_fib_confirmed_ob"
    elif not fib_confirmed:
        risk_percent = settings.reduced_risk_percent
        if zone_score < settings.score_full_entry:
            execution_style = "ai_zone_market"
            entry_model = "LEGACY_OB_AI_ZONE"
    elif zone_score < settings.score_full_entry:
        risk_percent = settings.reduced_risk_percent
        execution_style = "ai_zone_market"
        entry_model = "FIB_OB_AI_ZONE"

    # M5-only flip always reduced (V6 rule kept)
    if is_flip_entry and flip_candidate and flip_candidate.get("timeframe") == "M5":
        risk_percent = settings.flip_risk_percent

    # Entry/TP computation
    if direction == "buy":
        risk = limit_entry - stop
        if risk <= 0:
            return None, "invalid_risk_distance"
        tp = limit_entry + risk * settings.rr
        if limit_entry >= current_close:
            execution_style = "ai_zone_market"
            if not is_flip_entry:
                entry_model = "FIB_OB_AI_ZONE" if fib_confirmed else "LEGACY_OB_AI_ZONE"
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
            if not is_flip_entry:
                entry_model = "FIB_OB_AI_ZONE" if fib_confirmed else "LEGACY_OB_AI_ZONE"
            entry = float(current_close)
            risk = stop - entry
            if risk <= 0:
                return None, "invalid_market_risk_distance"
            tp = entry - risk * settings.rr
        else:
            entry = float(limit_entry)

    # ===== V8 EXECUTION-RISK ROUTING (the only material change vs V6) =====
    # After V6 logic has fully resolved execution_style, override risk by style.
    if execution_style == "ai_zone_market":
        risk_percent = float(settings.ai_zone_risk_percent)
        v8_risk_rule = "AI_ZONE_MARKET_1_PERCENT"
        v8_execution_priority = "AI_ZONE_PRIORITY"
        v8_risk_reason = f"ai_zone_market@{settings.ai_zone_risk_percent}pct"
    else:
        risk_percent = float(settings.pending_limit_risk_percent)
        v8_risk_rule = "PENDING_LIMIT_0_5_PERCENT"
        v8_execution_priority = "PENDING_LIMIT_REDUCED_RISK"
        v8_risk_reason = f"pending_limit@{settings.pending_limit_risk_percent}pct"

    # V8 zone tracking
    v8_selected_execution_zone = selected_tf if selected_tf in ("M15", "M5") else "NONE"
    v8_m15_priority_applied = bool(selected_tf == "M15")
    v8_m5_used_as_confirmation = bool(selected_tf == "M5" and m15_fib_ob_available)

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
        "fib_confirmed": bool(fib_confirmed),
        "fib_impulse_high": float(selected_ob.get("fib_impulse_high", 0.0)) if fib_confirmed else 0.0,
        "fib_impulse_low": float(selected_ob.get("fib_impulse_low", 0.0)) if fib_confirmed else 0.0,
        "fib_zone_low": float(selected_ob.get("fib_zone_low", 0.0)) if fib_confirmed else 0.0,
        "fib_zone_high": float(selected_ob.get("fib_zone_high", 0.0)) if fib_confirmed else 0.0,
        "fib_dist_to_ideal": float(selected_ob.get("fib_dist_to_ideal", 0.0)) if fib_confirmed else 0.0,
        "is_flip_entry": bool(is_flip_entry),
        "flip_type": flip_candidate.get("flip_type", "") if (is_flip_entry and flip_candidate) else "",
        "flip_direction": flip_candidate.get("flip_direction", "") if (is_flip_entry and flip_candidate) else "",
        "flip_timeframe": flip_candidate.get("timeframe", "") if (is_flip_entry and flip_candidate) else "",
        "missed_limit_entry": float(limit_entry) if execution_style == "ai_zone_market" else None,
        "distance_from_ob_pips": float(abs(entry - limit_entry) / PIP_SIZE) if execution_style == "ai_zone_market" else 0.0,
        "moved_r_from_ob": float(abs(entry - limit_entry) / risk) if risk > 0 else 0.0,
        "v8_risk_rule": v8_risk_rule,
        "v8_execution_priority": v8_execution_priority,
        "v8_selected_execution_zone": v8_selected_execution_zone,
        "v8_m15_priority_applied": v8_m15_priority_applied,
        "v8_m5_used_as_confirmation": v8_m5_used_as_confirmation,
        "v8_risk_reason": v8_risk_reason,
    }, None


# ============================================================================
# Exit helpers (identical to V4)
# ============================================================================
def classify_candle_exit(direction: str, low: float, high: float, sl: float, tp: float) -> str | None:
    if direction == "buy":
        if low <= sl and high >= tp:
            return "SL"
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


def classify_tick_exit_np(direction: str, bids, asks, sl: float, tp: float) -> str | None:
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


# ============================================================================
# Summary helpers
# ============================================================================
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
    expired_list = [r for r in rows if r.get("result") == "EXPIRED"]
    w = [r for r in closed if r["result"] == "WIN"]
    lo = [r for r in closed if r["result"] == "LOSS"]
    gp = sum(float(r.get("profit", 0) or 0) for r in w)
    gl = abs(sum(float(r.get("profit", 0) or 0) for r in lo))
    net = sum(float(r.get("profit", 0) or 0) for r in closed)
    wr = round(100.0 * len(w) / (len(w) + len(lo)), 2) if (len(w) + len(lo)) else 0.0
    return {
        "total_filled": len(closed),
        "expired": len(expired_list),
        "wins": len(w),
        "losses": len(lo),
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


def _avg_score(trades_list: list[dict], result_filter: str, key: str = "zone_score") -> float:
    vals = [int(t.get(key, 0) or 0) for t in trades_list if t.get("result") == result_filter]
    return round(sum(vals) / max(1, len(vals)), 4) if vals else 0.0


# ============================================================================
# MAIN
# ============================================================================
def main() -> None:
    backend_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Fib + Flip V8 AI Zone Priority backtest.")
    parser.add_argument("--env-file", type=str, default=".env.fib_flip_v6")
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
    parser.add_argument("--target-min-trades", type=int, default=170)
    parser.add_argument("--target-max-trades", type=int, default=240)
    parser.add_argument("--target-profit-factor", type=float, default=2.5)
    parser.add_argument("--enable-flip-entries", type=str, default="true")
    parser.add_argument("--flip-risk-percent", type=float, default=0.5)
    parser.add_argument("--score-full-entry", type=int, default=env_int("V6_SCORE_FULL_ENTRY", 6))
    parser.add_argument("--score-ai-zone-entry", type=int, default=env_int("V6_SCORE_AI_ZONE_ENTRY", 4))
    parser.add_argument("--reduced-risk-percent", type=float, default=env_float("V6_REDUCED_RISK_PERCENT", 0.5))
    parser.add_argument(
        "--ai-zone-risk-percent",
        type=float,
        default=None,
        help="Risk %% for ai_zone_market / chase entries (default: AI_ZONE_ENTRY_RISK_PERCENT or 1.0).",
    )
    parser.add_argument(
        "--pending-limit-risk-percent",
        type=float,
        default=None,
        help="Risk %% for pending_limit entries (default: 0.5).",
    )
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

    env_initial = os.getenv("BACKTEST_INITIAL_BALANCE", "").strip()
    default_initial = float(env_initial) if env_initial else 5000.0
    initial_balance = args.initial_balance if args.initial_balance is not None else env_float("INITIAL_BALANCE", default_initial)
    if initial_balance <= 0:
        initial_balance = 5000.0
    risk_percent = args.risk_percent if args.risk_percent is not None else env_float("RISK_PERCENT", 1.0)
    ai_zone_risk = (
        float(args.ai_zone_risk_percent)
        if args.ai_zone_risk_percent is not None
        else env_float("AI_ZONE_ENTRY_RISK_PERCENT", 1.0)
    )
    pending_limit_risk = (
        float(args.pending_limit_risk_percent)
        if args.pending_limit_risk_percent is not None
        else env_float("PENDING_LIMIT_RISK_PERCENT", 0.5)
    )
    rr = args.rr if args.rr is not None else env_float("RR", env_float("SMC_RR", 4.0))
    ob_buffer_pips = args.ob_buffer_pips if args.ob_buffer_pips is not None else env_float("OB_BUFFER_PIPS", 2.0)

    settings = BacktestSettings(
        initial_balance=float(initial_balance),
        risk_percent=float(risk_percent),
        reduced_risk_percent=float(args.reduced_risk_percent),
        ai_zone_risk_percent=float(ai_zone_risk),
        pending_limit_risk_percent=float(pending_limit_risk),
        flip_risk_percent=float(args.flip_risk_percent),
        rr=float(rr),
        ob_buffer_pips=float(ob_buffer_pips),
        order_expiry_hours=env_int("ORDER_EXPIRY_HOURS", 4),
        max_open_trades=env_int("MAX_OPEN_TRADES", 1),
        spread_points=env_float("SPREAD_POINTS", env_float("BACKTEST_SPREAD_POINTS", 8.0)),
        slippage_points=env_float("SLIPPAGE_POINTS", env_float("BACKTEST_SLIPPAGE_POINTS", 3.0)),
        commission_per_lot=env_float("BACKTEST_COMMISSION_PER_LOT", 0.0),
        save_skipped=args.save_skipped or env_bool("SAVE_SKIPPED_SIGNALS", False),
        use_ticks=str_to_bool(args.use_ticks),
        enable_flip_entries=str_to_bool(args.enable_flip_entries),
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
                print(f"Tick numpy arrays cached: {len(tick_times_np)} ticks for searchsorted lookup")
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
        f"V8 AI Zone Priority | score_full={settings.score_full_entry}, score_ai_zone={settings.score_ai_zone_entry}, "
        f"flip_entries={'enabled' if settings.enable_flip_entries else 'disabled'}"
    )
    print(
        f"V8 risk routing: ai_zone_market={settings.ai_zone_risk_percent}%, "
        f"pending_limit={settings.pending_limit_risk_percent}%, flip={settings.flip_risk_percent}%"
    )
    print(
        f"V8 targets: trades={settings.target_min_trades}..{settings.target_max_trades}, "
        f"PF>={settings.target_profit_factor}"
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

    # V6 perf: cap detect_structure windows to keep each call fast
    m5_struct_cap = env_int("V6_M5_STRUCTURE_LOOKBACK_CAP", 200)
    m15_struct_cap = env_int("V6_M15_STRUCTURE_LOOKBACK_CAP", 250)
    h1_struct_cap = env_int("V6_H1_STRUCTURE_LOOKBACK_CAP", 250)
    print(
        f"V8 perf: structure caps M5={m5_struct_cap}, M15={m15_struct_cap}, H1={h1_struct_cap} "
        f"(lookbacks M5={lookback_m5}, M15={lookback_m15}, H1={lookback_h1})",
        flush=True,
    )

    # State
    balance = float(settings.initial_balance)
    peak_balance = balance
    max_drawdown = 0.0
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = [{"time": m5.iloc[0]["time"], "balance": balance}]
    wins = losses = expired = total_signals = 0
    consec_wins = consec_losses = 0
    max_consec_wins = max_consec_losses = 0
    active: dict[str, Any] | None = None
    h1_end = 0
    m15_end = 0
    h1_result_cache: dict[str, Any] = {"events": []}
    m15_result_cache: dict[str, Any] = {"events": []}
    h1_cache_time = None
    m15_cache_time = None
    h1_obs_cache_time = None
    h1_supply_ob_cache: dict | None = None
    h1_demand_ob_cache: dict | None = None
    skipped_low_score = 0
    skipped_at_entry = 0
    skipped_no_decision = 0
    skipped_flip_no_fib = 0
    flip_signals = 0
    fib_confirmed_signals = 0

    skip_at_entry = env_bool("V6_SKIP_AT_ENTRY", True)
    progress_interval = max(1000, env_int("V6_PROGRESS_INTERVAL", 2000))
    last_signal_gen_m15_end = -1  # Track when signal gen last ran

    for i in range(len(m5)):
        now = pd.Timestamp(m5.iloc[i]["time"])
        if i and i % progress_interval == 0:
            print(
                f"V8 replay: {i}/{len(m5)} | signals={total_signals} | flips={flip_signals} | "
                f"fib_confirmed={fib_confirmed_signals} | balance={balance:.2f}",
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
        if len(h1_now) < 200 or len(m15_now) < 200 or len(m5_now) < 200:
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

        # ===== MANAGE ACTIVE TRADE =====
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

        # ===== SIGNAL GENERATION =====
        # V6 perf: only generate signals when M15 window advances (every ~3 M5 bars)
        # This avoids calling detect_structure(m5) on every single M5 candle.
        if not active and m15_end == last_signal_gen_m15_end:
            equity_curve.append({"time": now, "balance": balance})
            continue
        if not active:
            last_signal_gen_m15_end = m15_end
            if h1_cache_time != h1_now.iloc[-1]["time"]:
                h1_struct_window = h1_now.iloc[-h1_struct_cap:].reset_index(drop=True) if len(h1_now) > h1_struct_cap else h1_now
                h1_result_cache = detect_structure(h1_struct_window, swing_length) or {"events": []}
                h1_cache_time = h1_now.iloc[-1]["time"]
                annotate_pivots_swept(h1_result_cache, h1_struct_window["close"].to_numpy())
            if m15_cache_time != m15_now.iloc[-1]["time"]:
                m15_struct_window = m15_now.iloc[-m15_struct_cap:].reset_index(drop=True) if len(m15_now) > m15_struct_cap else m15_now
                m15_result_cache = detect_structure(m15_struct_window, internal_length) or {"events": []}
                m15_cache_time = m15_now.iloc[-1]["time"]
                annotate_pivots_swept(m15_result_cache, m15_struct_window["close"].to_numpy())
            h1_result = h1_result_cache
            m15_result = m15_result_cache
            m5_struct_window = m5_now.iloc[-m5_struct_cap:].reset_index(drop=True) if len(m5_now) > m5_struct_cap else m5_now
            m5_result = detect_structure(m5_struct_window, internal_length) or {"events": []}

            h1_last_event = h1_result["events"][-1] if h1_result["events"] else None
            external_bias = h1_last_event["bias"] if h1_last_event else (BULLISH if c_close >= h1_now.iloc[-1]["close"] else BEARISH)
            h1_for_swing = h1_now.iloc[-h1_struct_cap:].reset_index(drop=True) if len(h1_now) > h1_struct_cap else h1_now
            swing = choose_h1_swing_range(h1_for_swing, h1_result, h1_last_event)
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
                    find_rejection_order_block(h1_for_swing, h1_result, BEARISH, "H1")
                    or last_valid_ob(h1_result["events"], h1_for_swing, BEARISH, "H1")
                )
                h1_demand_ob_cache = (
                    find_rejection_order_block(h1_for_swing, h1_result, BULLISH, "H1")
                    or last_valid_ob(h1_result["events"], h1_for_swing, BULLISH, "H1")
                )
                h1_obs_cache_time = current_h1_time
            h1_supply_ob = h1_supply_ob_cache
            h1_demand_ob = h1_demand_ob_cache

            if decision not in VALID_DECISIONS or trade_bias is None:
                skipped_no_decision += 1
                equity_curve.append({"time": now, "balance": balance})
                continue

            m15_for_obs = m15_now.iloc[-m15_struct_cap:].reset_index(drop=True) if len(m15_now) > m15_struct_cap else m15_now
            # V6 zone selection with fib priority (use capped windows for OB building)
            (
                selected_ob, m15_fib_ob, m5_fib_ob, m15_legacy_ob, m5_legacy_ob,
                active_zone_name, selected_ob_source, h1_context_ob, h1_context_label,
            ) = select_zone_v6(
                direction=trade_bias,
                trade_mode=trade_mode,
                m15_result=m15_result,
                m5_result=m5_result,
                m15_now=m15_for_obs,
                m5_now=m5_struct_window,
                swing_low=swing_low,
                swing_high=swing_high,
                fibs=fibs,
                h1_supply_ob=h1_supply_ob,
                h1_demand_ob=h1_demand_ob,
                current_price=c_close,
            )

            if selected_ob and is_ob_invalidated(m5_struct_window, selected_ob, use_close=True):
                replacement = last_valid_ob(m5_result.get("events", []), m5_struct_window, trade_bias, "M5") or most_recent_ob(selected_ob)
                selected_ob = dict(replacement) if replacement else None
                if selected_ob:
                    if h1_context_ob:
                        selected_ob["h1_context_ob"] = h1_context_ob
                        selected_ob["inside_h1_ob"] = h1_context_label.startswith("inside_")
                    selected_ob["h1_context_label"] = h1_context_label
                    selected_ob["selected_source"] = (selected_ob.get("selected_source", "") or selected_ob_source) + "+replaced"

            # Flip detection
            flip_candidate_data: dict | None = None
            is_flip_entry = False
            if settings.enable_flip_entries:
                flip_candidates = detect_ob_flip_candidates(m15_result, m5_result, m15_for_obs, m5_struct_window, c_close)
                relevant_flip = _select_relevant_flip(flip_candidates, trade_bias)

                if relevant_flip:
                    if selected_ob and selected_ob.get("fib_confirmed"):
                        flip_candidate_data = relevant_flip
                        is_flip_entry = True
                    elif not selected_ob or not selected_ob.get("fib_confirmed"):
                        flip_ob, flip_reason = build_flip_entry_ob(
                            relevant_flip, trade_bias, m15_result, m5_result, m15_for_obs, m5_struct_window,
                        )
                        if flip_ob:
                            selected_ob = flip_ob
                            flip_candidate_data = relevant_flip
                            is_flip_entry = True
                            selected_ob_source = "flip_fib_entry"
                        else:
                            skipped_flip_no_fib += 1

            if not selected_ob:
                equity_curve.append({"time": now, "balance": balance})
                continue

            # Score zone
            scoring = score_zone_v6(
                direction=trade_bias,
                trade_mode=trade_mode,
                decision=decision,
                selected_ob=selected_ob,
                m15_fib_ob=m15_fib_ob,
                m5_fib_ob=m5_fib_ob,
                m15_legacy_ob=m15_legacy_ob,
                m5_legacy_ob=m5_legacy_ob,
                h1_context_ob=h1_context_ob,
                h1_context_label=h1_context_label,
                h1_supply_ob=h1_supply_ob,
                h1_demand_ob=h1_demand_ob,
                swing_low=swing_low,
                swing_high=swing_high,
                current_price=c_close,
                m5_now=m5_struct_window,
                m5_result=m5_result,
                m15_now=m15_for_obs,
                m15_result=m15_result,
                h1_now=h1_for_swing,
                h1_result=h1_result,
                flip_candidate=flip_candidate_data,
                is_flip_entry=is_flip_entry,
            )

            zone_score = int(scoring["zone_score"])
            if zone_score < settings.score_ai_zone_entry:
                skipped_low_score += 1
                if settings.save_skipped:
                    trades.append({
                        "trade_id": f"S{len(trades)+1:06d}",
                        "signal_time": now, "fill_time": None, "close_time": now,
                        "decision": decision, "trade_mode": trade_mode,
                        "direction": "buy" if trade_bias == BULLISH else "sell",
                        "ob_timeframe": selected_ob.get("timeframe") if selected_ob else "",
                        "entry": None, "stop_loss": None, "take_profit": None,
                        "rr": settings.rr, "result": "SKIPPED_LOW_SCORE",
                        "profit": 0.0, "r_multiple": 0.0, "balance_after": balance,
                        "zone_score": zone_score, "zone_grade": scoring["zone_grade"],
                        "zone_reason": scoring["zone_reason"],
                        "fib_confirmed": scoring["fib_confirmed_ob"],
                        "is_flip_entry": is_flip_entry,
                        "flip_type": flip_candidate_data.get("flip_type", "") if flip_candidate_data else "",
                        "entry_model": "SKIPPED", "entry_status": "SKIPPED",
                        "reason": "skipped_low_zone_score",
                    })
                equity_curve.append({"time": now, "balance": balance})
                continue

            candidate, reason = build_v8_candidate(
                decision=decision,
                trade_mode=trade_mode,
                selected_ob=selected_ob,
                current_close=c_close,
                settings=settings,
                scoring=scoring,
                h1_context_ob=h1_context_ob,
                h1_supply_ob=h1_supply_ob,
                h1_demand_ob=h1_demand_ob,
                m15_now=m15_for_obs,
                now=now,
                flip_candidate=flip_candidate_data,
                is_flip_entry=is_flip_entry,
                m15_fib_ob_available=bool(m15_fib_ob),
            )
            if not candidate:
                if reason == "flip_no_fib_confirmed_ob":
                    skipped_flip_no_fib += 1
                equity_curve.append({"time": now, "balance": balance})
                continue

            entry_status = get_entry_status(c_close, candidate["entry"], trade_bias, POINT_SIZE)
            if skip_at_entry and entry_status in {"AT_BUY_ENTRY", "AT_SELL_ENTRY"} and zone_score < 8:
                skipped_at_entry += 1
                if settings.save_skipped:
                    trades.append({
                        "trade_id": f"X{len(trades)+1:06d}",
                        "signal_time": now, "fill_time": None, "close_time": now,
                        "result": "SKIPPED_AT_ENTRY", "profit": 0.0, "r_multiple": 0.0,
                        "balance_after": balance, "zone_score": zone_score,
                        "zone_grade": scoring["zone_grade"],
                        "fib_confirmed": candidate.get("fib_confirmed", False),
                        "is_flip_entry": is_flip_entry,
                        "entry_model": candidate["entry_model"],
                        "entry_status": entry_status,
                        "reason": "skipped_at_entry_low_score",
                        **{k: candidate[k] for k in ("decision", "trade_mode", "direction", "ob_timeframe", "entry", "stop_loss", "take_profit", "rr")},
                    })
                equity_curve.append({"time": now, "balance": balance})
                continue

            total_signals += 1
            if is_flip_entry:
                flip_signals += 1
            if candidate.get("fib_confirmed"):
                fib_confirmed_signals += 1

            execution_style = candidate["execution_style"]
            active = {
                "trade_id": f"T{len(trades)+1:06d}",
                "signal_time": now,
                "fill_time": now if execution_style in ("ai_zone_market",) else None,
                "close_time": None,
                "state": "open" if execution_style in ("ai_zone_market",) else "pending",
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
                "h1_ob_context": h1_context_label,
                "h1_context_label": h1_context_label,
                "h1_rejection_memory": h1_context_label.startswith("recent_"),
                "m15_fib_ob_available": scoring["m15_fib_ob_available"],
                "m15_legacy_ob_available": scoring["m15_legacy_ob_available"],
                "m5_trigger_confirmed": scoring["m5_trigger_confirmed"],
                "selected_zone_timeframe": candidate["ob_timeframe"],
                "true_pd_location": scoring["true_pd_location"],
                "pd_position": scoring["pd_position"],
                "liquidity_sweep_confirmed": scoring["liquidity_sweep_confirmed"],
                "opposing_h1_zone_nearby": scoring["opposing_h1_zone_nearby"],
                "has_displacement": scoring["has_displacement"],
                "zone_score": zone_score,
                "zone_grade": scoring["zone_grade"],
                "zone_reason": scoring["zone_reason"],
                "m15_fib_ob_high": float(m15_fib_ob["high"]) if m15_fib_ob else 0.0,
                "m15_fib_ob_low": float(m15_fib_ob["low"]) if m15_fib_ob else 0.0,
                "m5_fib_ob_high": float(m5_fib_ob["high"]) if m5_fib_ob else 0.0,
                "m5_fib_ob_low": float(m5_fib_ob["low"]) if m5_fib_ob else 0.0,
                "reason": "valid_signal",
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

    for t in trades:
        try:
            t["zone_score_bucket"] = _zone_score_bucket(int(t.get("zone_score", 0) or 0))
        except Exception:
            t["zone_score_bucket"] = "F_lt_2"

    out_dir = backend_dir / "storage" / "backtests"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    trades_path = out_dir / f"backtest_trades_fib_flip_v8_ai_zone_priority_{ts}.csv"
    summary_path = out_dir / f"backtest_summary_fib_flip_v8_ai_zone_priority_{ts}.json"
    equity_path = out_dir / f"backtest_equity_curve_fib_flip_v8_ai_zone_priority_{ts}.csv"

    pd.DataFrame(trades).to_csv(trades_path, index=False)
    pd.DataFrame(equity_curve).to_csv(equity_path, index=False)

    # Performance breakdowns
    perf_by_zone_grade = _group_by_key(trades, "zone_grade")
    perf_by_zone_bucket = _group_by_key(trades, "zone_score_bucket")
    perf_by_selected_tf = _group_by_key(trades, "selected_zone_timeframe")
    perf_by_h1_ctx = _group_by_key(trades, "h1_ob_context")
    perf_by_pd = _group_by_key(trades, "true_pd_location")
    perf_by_decision = _group_by_key(trades, "decision")
    perf_by_mode = _group_by_key(trades, "trade_mode")
    perf_by_entry_model = _group_by_key(trades, "entry_model")
    perf_by_exec_style = _group_by_key(trades, "execution_style")
    perf_by_fib = _group_by_key(trades, "fib_confirmed")
    perf_by_flip = _group_by_key(trades, "is_flip_entry")
    perf_by_flip_type = _group_by_key(trades, "flip_type")
    perf_by_flip_dir = _group_by_key(trades, "flip_direction")
    perf_by_flip_tf = _group_by_key(trades, "flip_timeframe")
    perf_by_liq_sweep = _group_by_key(trades, "liquidity_sweep_confirmed")
    # V8 breakdowns
    perf_by_v8_risk_rule = _group_by_key(trades, "v8_risk_rule")
    perf_by_v8_exec_priority = _group_by_key(trades, "v8_execution_priority")
    perf_by_v8_selected_zone = _group_by_key(trades, "v8_selected_execution_zone")

    # V8 aggregate metrics
    ai_zone_closed = [t for t in closed_trades if t.get("v8_risk_rule") == "AI_ZONE_MARKET_1_PERCENT"]
    pending_closed = [t for t in closed_trades if t.get("v8_risk_rule") == "PENDING_LIMIT_0_5_PERCENT"]
    m15_zone_closed = [t for t in closed_trades if t.get("v8_selected_execution_zone") == "M15"]
    m5_zone_closed = [t for t in closed_trades if t.get("v8_selected_execution_zone") == "M5"]

    def _pf_from_list(tlist):
        gp = sum(float(t["profit"]) for t in tlist if float(t.get("profit", 0)) > 0)
        gl = abs(sum(float(t["profit"]) for t in tlist if float(t.get("profit", 0)) < 0))
        return _safe_pf(gp, gl)

    ai_zone_pf = _pf_from_list(ai_zone_closed)
    ai_zone_net = round(sum(float(t.get("profit", 0)) for t in ai_zone_closed), 2)
    pending_pf = _pf_from_list(pending_closed)
    pending_net = round(sum(float(t.get("profit", 0)) for t in pending_closed), 2)
    m15_zone_pf = _pf_from_list(m15_zone_closed)
    m5_zone_pf = _pf_from_list(m5_zone_closed)

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
        "skipped_low_score": int(skipped_low_score),
        "skipped_at_entry": int(skipped_at_entry),
        "skipped_no_decision": int(skipped_no_decision),
        "skipped_flip_no_fib": int(skipped_flip_no_fib),
        "flip_signals": int(flip_signals),
        "fib_confirmed_signals": int(fib_confirmed_signals),
        "trades_by_decision": dict(Counter(t.get("decision", "") for t in closed_trades)),
        "trades_by_trade_mode": dict(Counter(t.get("trade_mode", "") for t in closed_trades)),
        "trades_by_ob_timeframe": dict(Counter(t.get("ob_timeframe", "") for t in closed_trades)),
        "trades_by_entry_model": dict(Counter(t.get("entry_model", "") for t in closed_trades)),
        "trades_by_execution_style": dict(Counter(t.get("execution_style", "") for t in closed_trades)),
        "trades_by_zone_grade": dict(Counter(t.get("zone_grade", "") for t in closed_trades)),
        "trades_by_zone_score_bucket": dict(Counter(t.get("zone_score_bucket", "") for t in closed_trades)),
        "trades_by_fib_confirmed": dict(Counter(str(t.get("fib_confirmed", False)) for t in closed_trades)),
        "trades_by_is_flip_entry": dict(Counter(str(t.get("is_flip_entry", False)) for t in closed_trades)),
        "trades_by_flip_type": dict(Counter(t.get("flip_type", "") for t in closed_trades if t.get("flip_type"))),
        "performance_by_zone_grade": perf_by_zone_grade,
        "performance_by_zone_score_bucket": perf_by_zone_bucket,
        "performance_by_selected_zone_timeframe": perf_by_selected_tf,
        "performance_by_h1_ob_context": perf_by_h1_ctx,
        "performance_by_true_pd_location": perf_by_pd,
        "performance_by_decision": perf_by_decision,
        "performance_by_trade_mode": perf_by_mode,
        "performance_by_entry_model": perf_by_entry_model,
        "performance_by_execution_style": perf_by_exec_style,
        "performance_by_fib_confirmed": perf_by_fib,
        "performance_by_is_flip_entry": perf_by_flip,
        "performance_by_flip_type": perf_by_flip_type,
        "performance_by_flip_direction": perf_by_flip_dir,
        "performance_by_flip_timeframe": perf_by_flip_tf,
        "performance_by_liquidity_sweep_confirmed": perf_by_liq_sweep,
        "performance_by_v8_risk_rule": perf_by_v8_risk_rule,
        "performance_by_v8_execution_priority": perf_by_v8_exec_priority,
        "performance_by_v8_selected_execution_zone": perf_by_v8_selected_zone,
        "ai_zone_market_total_trades": len(ai_zone_closed),
        "ai_zone_market_profit_factor": ai_zone_pf,
        "ai_zone_market_net_profit": ai_zone_net,
        "pending_limit_total_trades": len(pending_closed),
        "pending_limit_profit_factor": pending_pf,
        "pending_limit_net_profit": pending_net,
        "m15_selected_zone_profit_factor": m15_zone_pf,
        "m5_selected_zone_profit_factor": m5_zone_pf,
        "average_zone_score_winners": _avg_score(closed_trades, "WIN", "zone_score"),
        "average_zone_score_losers": _avg_score(closed_trades, "LOSS", "zone_score"),
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
            "filled_within_range": bool(settings.target_min_trades <= filled_n <= settings.target_max_trades),
            "profit_factor_target_met": bool(profit_factor >= settings.target_profit_factor),
            "credible_overall": bool(
                profit_factor >= settings.target_profit_factor and filled_n >= settings.target_min_trades
            ),
        },
        "outputs": {
            "trades_csv": str(trades_path),
            "summary_json": str(summary_path),
            "equity_curve_csv": str(equity_path),
        },
    }
    summary_path.write_text(json.dumps(summary, default=str, indent=2), encoding="utf-8")

    print("\n===== V8 AI ZONE PRIORITY SUMMARY =====")
    for k in [
        "csv_date_range", "total_m5_candles_tested", "total_signals",
        "total_filled_trades", "expired_pending_orders",
        "wins", "losses", "win_rate", "profit_factor",
        "net_profit", "starting_balance", "final_balance",
        "max_drawdown_amount", "max_drawdown_percent",
        "average_r", "best_trade_r", "worst_trade_r",
        "consecutive_wins", "consecutive_losses",
        "skipped_low_score", "skipped_at_entry", "skipped_no_decision",
        "skipped_flip_no_fib", "flip_signals", "fib_confirmed_signals",
        "average_zone_score_winners", "average_zone_score_losers",
    ]:
        print(f"{k}: {summary[k]}")
    print(f"\n--- V8 Risk Routing ---")
    print(f"ai_zone_market: trades={summary['ai_zone_market_total_trades']}, PF={summary['ai_zone_market_profit_factor']}, net={summary['ai_zone_market_net_profit']}")
    print(f"pending_limit:  trades={summary['pending_limit_total_trades']}, PF={summary['pending_limit_profit_factor']}, net={summary['pending_limit_net_profit']}")
    print(f"M15 zone PF: {summary['m15_selected_zone_profit_factor']}")
    print(f"M5  zone PF: {summary['m5_selected_zone_profit_factor']}")
    print(f"\ntrades_by_zone_grade: {summary['trades_by_zone_grade']}")
    print(f"trades_by_fib_confirmed: {summary['trades_by_fib_confirmed']}")
    print(f"trades_by_is_flip_entry: {summary['trades_by_is_flip_entry']}")
    print(f"trades_by_flip_type: {summary['trades_by_flip_type']}")
    print(f"trades_by_entry_model: {summary['trades_by_entry_model']}")
    print(f"trades_by_execution_style: {summary['trades_by_execution_style']}")
    print(f"research_target_check: {summary['research_target_check']}")
    print(f"Saved trades CSV: {trades_path}")
    print(f"Saved summary JSON: {summary_path}")
    print(f"Saved equity curve CSV: {equity_path}")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
