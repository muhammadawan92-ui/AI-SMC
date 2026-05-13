from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

from smc_core import (
    BULLISH,
    BEARISH,
    choose_h1_swing_range,
    choose_internal_event,
    decide_trade_context,
    detect_structure,
    fib_prices,
    get_entry_status,
    is_ob_invalidated,
    last_valid_ob,
    most_recent_ob,
    price_location,
    select_active_ob,
)

try:
    from smc_core import find_rejection_order_block
except Exception:
    def find_rejection_order_block(*args, **kwargs):
        return None

# Manual mapping override section (edit here if auto-detection fails)
MANUAL_BARS_COLUMN_MAP: dict[str, str] = {
    # "time": "DateTime",
    # "open": "Open",
    # "high": "High",
    # "low": "Low",
    # "close": "Close",
    # "volume": "Volume",
}
MANUAL_TICKS_COLUMN_MAP: dict[str, str] = {
    # "time": "DateTime",
    # "bid": "Bid",
    # "ask": "Ask",
}

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


@dataclass
class BacktestSettings:
    initial_balance: float
    risk_percent: float
    rr: float
    ob_buffer_pips: float
    order_expiry_hours: int
    max_open_trades: int
    spread_points: float
    slippage_points: float
    commission_per_lot: float
    save_skipped: bool
    use_ticks: bool


def env_float(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def str_to_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    return str_to_bool(value)



def apply_h1_context_stop_backtest(decision: str, direction: str, selected_ob: dict, entry: float, normal_stop: float, buffer_price: float):
    if str(os.getenv("SMC_H1_OB_RETRACE_SL_ENABLED", "true")).strip().lower() not in {"1", "true", "yes", "y", "on"}:
        return normal_stop, "ltf_ob"
    if decision not in {"BUY_RETRACEMENT", "SELL_RETRACEMENT"}:
        return normal_stop, "ltf_ob"
    h1_ob = selected_ob.get("h1_context_ob") if selected_ob else None
    if not h1_ob:
        return normal_stop, "ltf_ob"
    mode = os.getenv("SMC_H1_OB_RETRACE_SL_MODE", "midpoint").strip().lower()
    if mode not in {"auto", "midpoint", "extreme", "off"}:
        mode = "midpoint"
    if mode == "off":
        return normal_stop, "ltf_ob"
    h1_high = float(h1_ob["high"])
    h1_low = float(h1_ob["low"])
    h1_mid = (h1_high + h1_low) / 2.0
    if direction == "sell":
        if mode == "extreme":
            return h1_high + buffer_price, "h1_supply_extreme"
        return max(float(normal_stop), h1_mid), "h1_supply_midpoint_protected"
    if direction == "buy":
        if mode == "extreme":
            return h1_low - buffer_price, "h1_demand_extreme"
        return min(float(normal_stop), h1_mid), "h1_demand_midpoint_protected"
    return normal_stop, "ltf_ob"


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
    """True when pandas likely used first data row as header."""
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
    """Read CSV and auto-handle headerless MT5/Tickstory exports."""
    df = pd.read_csv(path)
    cols = [str(c) for c in df.columns]
    if not looks_like_data_header(cols):
        return df
    # Header appears to be first data row, so re-read as headerless.
    raw = pd.read_csv(path, header=None)
    col_count = raw.shape[1]
    if expected_kind == "bars":
        # Common MT5 bars: date,time,open,high,low,close,tick_volume,volume,spread
        defaults = ["time_date", "time_clock", "open", "high", "low", "close", "tick_volume", "volume", "spread"]
    else:
        # Common MT5 ticks: date,time,bid,ask,last,volume,flags
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
        tick_required = {
            "time": ["time", "date", "datetime", "timestamp", "time_date", "time_clock"],
            "bid": ["bid"],
            "ask": ["ask"],
        }
        for k, aliases in tick_required.items():
            if k not in mapping:
                found = detect_column(columns, aliases)
                if found:
                    mapping[k] = found
        missing = [k for k in tick_required if k not in mapping]
        if missing:
            raise ValueError(
                f"Could not detect required ticks columns: {missing}. "
                "Edit MANUAL_TICKS_COLUMN_MAP near top of script."
            )
    else:
        required = {
            "time": ["time", "date", "datetime", "timestamp", "time_date", "time_clock"],
            "open": ["open", "o"],
            "high": ["high", "h"],
            "low": ["low", "l"],
            "close": ["close", "c"],
        }
        optional = {"volume": ["volume", "tick_volume", "real_volume"]}
        for k, aliases in required.items():
            if k not in mapping:
                found = detect_column(columns, aliases)
                if found:
                    mapping[k] = found
        if "volume" not in mapping:
            found = detect_column(columns, optional["volume"])
            if found:
                mapping["volume"] = found
        missing = [k for k in required if k not in mapping]
        if missing:
            raise ValueError(
                f"Could not detect required bars columns: {missing}. "
                "Edit MANUAL_BARS_COLUMN_MAP near top of script."
            )
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
            "volume": pd.to_numeric(df[mapping["volume"]], errors="coerce")
            if "volume" in mapping
            else 0.0,
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
    """Merge split date/time columns into one timestamp when needed."""
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


def _minutes_since_ob(selected_ob: dict, now: pd.Timestamp | None) -> float | None:
    if not selected_ob or now is None:
        return None
    raw_time = selected_ob.get("time") or selected_ob.get("selected_ob_time")
    if raw_time is None:
        return None
    try:
        ob_time = pd.Timestamp(raw_time)
        return max(0.0, (pd.Timestamp(now) - ob_time).total_seconds() / 60.0)
    except Exception:
        return None


def build_ai_zone_market_candidate(
    decision: str,
    trade_mode: str,
    selected_ob: dict,
    current_close: float,
    direction: str,
    limit_entry: float,
    normal_stop: float,
    buffer_price: float,
    settings: BacktestSettings,
    now: pd.Timestamp | None = None,
):
    """
    Backtest version of the live AI zone autonomy rule.

    If the ideal OB limit is already missed / not chased, this creates a reduced-risk
    market-style entry instead of incorrectly counting the missed limit as a normal
    OB mitigation trade.
    """
    if not env_bool("AI_ZONE_AUTONOMY_ENABLED", False):
        return None, "AI_ZONE_AUTONOMY_ENABLED=false"
    if not env_bool("AI_ZONE_ALLOW_OB_NOT_MITIGATED_ENTRY", True):
        return None, "AI_ZONE_ALLOW_OB_NOT_MITIGATED_ENTRY=false"
    if decision not in VALID_DECISIONS or not selected_ob:
        return None, "ai_zone_no_valid_decision_or_ob"

    if env_bool("AI_ZONE_REQUIRE_ACTIVE_H1_OB", False):
        has_h1_context = bool(selected_ob.get("h1_context_ob") or selected_ob.get("inside_h1_ob"))
        if not has_h1_context:
            return None, "ai_zone_requires_h1_ob_context"

    wait_minutes = env_float("AI_ZONE_OB_MITIGATION_WAIT_MINUTES", 0.0)
    age_minutes = _minutes_since_ob(selected_ob, now)
    if wait_minutes > 0 and age_minutes is not None and age_minutes < wait_minutes:
        return None, f"ai_zone_waiting_for_ob_mitigation_window_{age_minutes:.1f}_of_{wait_minutes:.1f}_minutes"

    market_entry = float(current_close)
    distance_from_ob_pips = (
        (market_entry - float(limit_entry)) / PIP_SIZE
        if direction == "buy"
        else (float(limit_entry) - market_entry) / PIP_SIZE
    )
    if distance_from_ob_pips <= 0:
        return None, f"ai_zone_price_has_not_moved_away_from_ob_{distance_from_ob_pips:.1f}_pips"

    min_distance = env_float("AI_ZONE_MIN_DISTANCE_FROM_OB_PIPS", 0.0)
    max_distance = env_float("AI_ZONE_MAX_DISTANCE_FROM_ACTIVE_ZONE_PIPS", 25.0)
    if distance_from_ob_pips < min_distance:
        return None, f"ai_zone_distance_too_small_{distance_from_ob_pips:.1f}_pips"
    if max_distance > 0 and distance_from_ob_pips > max_distance:
        return None, f"ai_zone_do_not_chase_distance_{distance_from_ob_pips:.1f}_pips_gt_{max_distance:.1f}"

    stop, stop_source = apply_h1_context_stop_backtest(
        decision, direction, selected_ob, market_entry, normal_stop, buffer_price
    )
    risk = abs(market_entry - stop)
    if risk <= 0:
        return None, "ai_zone_invalid_risk_distance"

    moved_r_from_ob = abs(market_entry - float(limit_entry)) / risk if risk > 0 else 999.0
    max_moved_r = env_float("AI_ZONE_DO_NOT_CHASE_AFTER_R", 1.0)
    if max_moved_r > 0 and moved_r_from_ob > max_moved_r:
        return None, f"ai_zone_do_not_chase_moved_{moved_r_from_ob:.2f}R_gt_{max_moved_r:.2f}R"

    rr = env_float("AI_ZONE_PREFERRED_RR", settings.rr)
    min_rr = env_float("AI_ZONE_MIN_RR", 2.0)
    if rr < min_rr:
        rr = min_rr

    if direction == "buy":
        tp = market_entry + risk * rr
        entry_status = "BUY_MARKET_AI_ZONE"
    else:
        tp = market_entry - risk * rr
        entry_status = "SELL_MARKET_AI_ZONE"

    h1_context_ob = selected_ob.get("h1_context_ob")
    return {
        "decision": decision,
        "trade_mode": trade_mode,
        "direction": direction,
        "ob_timeframe": selected_ob.get("timeframe"),
        "ob_type": selected_ob.get("type"),
        "selected_ob_high": float(selected_ob["high"]),
        "selected_ob_low": float(selected_ob["low"]),
        "selected_ob_time": selected_ob.get("time"),
        "stop_source": stop_source,
        "inside_h1_ob": bool(selected_ob.get("inside_h1_ob") or h1_context_ob),
        "h1_context_ob_high": float(h1_context_ob.get("high", 0.0)) if h1_context_ob else 0.0,
        "h1_context_ob_low": float(h1_context_ob.get("low", 0.0)) if h1_context_ob else 0.0,
        "entry": float(market_entry),
        "stop_loss": float(stop),
        "take_profit": float(tp),
        "rr": float(rr),
        "risk_percent": env_float("AI_ZONE_ENTRY_RISK_PERCENT", 0.5),
        "execution_style": "market",
        "entry_model": os.getenv("AI_ZONE_MODEL_NAME", "OB_NOT_MITIGATED_ZONE_ENTRY"),
        "original_ob_mitigated": False,
        "missed_limit_entry": float(limit_entry),
        "distance_from_ob_pips": float(distance_from_ob_pips),
        "moved_r_from_ob": float(moved_r_from_ob),
        "entry_status": entry_status,
    }, None


def build_trade_candidate(
    decision: str,
    trade_mode: str,
    selected_ob: dict,
    current_close: float,
    settings: BacktestSettings,
    now: pd.Timestamp | None = None,
):
    if decision not in VALID_DECISIONS or not selected_ob:
        return None, "no_valid_decision_or_ob"
    direction = "buy" if decision.startswith("BUY") else "sell"
    ob_high = float(selected_ob["high"])
    ob_low = float(selected_ob["low"])
    buffer_price = settings.ob_buffer_pips * PIP_SIZE
    h1_context_ob = selected_ob.get("h1_context_ob")

    if direction == "buy":
        entry = ob_high
        normal_stop = ob_low - buffer_price
        stop, stop_source = apply_h1_context_stop_backtest(decision, direction, selected_ob, entry, normal_stop, buffer_price)
        risk = entry - stop
        tp = entry + risk * settings.rr
        if entry >= current_close:
            return build_ai_zone_market_candidate(
                decision, trade_mode, selected_ob, current_close, direction, entry, normal_stop, buffer_price, settings, now
            )
    else:
        entry = ob_low
        normal_stop = ob_high + buffer_price
        stop, stop_source = apply_h1_context_stop_backtest(decision, direction, selected_ob, entry, normal_stop, buffer_price)
        risk = stop - entry
        tp = entry - risk * settings.rr
        if entry <= current_close:
            return build_ai_zone_market_candidate(
                decision, trade_mode, selected_ob, current_close, direction, entry, normal_stop, buffer_price, settings, now
            )

    if risk <= 0:
        return None, "invalid_risk_distance"
    return {
        "decision": decision,
        "trade_mode": trade_mode,
        "direction": direction,
        "ob_timeframe": selected_ob.get("timeframe"),
        "ob_type": selected_ob.get("type"),
        "selected_ob_high": ob_high,
        "selected_ob_low": ob_low,
        "selected_ob_time": selected_ob.get("time"),
        "stop_source": stop_source,
        "inside_h1_ob": bool(selected_ob.get("inside_h1_ob") or h1_context_ob),
        "h1_context_ob_high": float(h1_context_ob.get("high", 0.0)) if h1_context_ob else 0.0,
        "h1_context_ob_low": float(h1_context_ob.get("low", 0.0)) if h1_context_ob else 0.0,
        "entry": float(entry),
        "stop_loss": float(stop),
        "take_profit": float(tp),
        "rr": float(settings.rr),
        "risk_percent": float(settings.risk_percent),
        "execution_style": "pending_limit",
        "entry_model": "OB_MITIGATION_LIMIT_ENTRY",
        "original_ob_mitigated": None,
        "missed_limit_entry": None,
        "distance_from_ob_pips": None,
        "moved_r_from_ob": None,
    }, None

def classify_candle_exit(direction: str, low: float, high: float, sl: float, tp: float) -> str | None:
    if direction == "buy":
        hit_sl = low <= sl
        hit_tp = high >= tp
        if hit_sl and hit_tp:
            return "SL"  # conservative worst-case
        if hit_sl:
            return "SL"
        if hit_tp:
            return "TP"
    else:
        hit_sl = high >= sl
        hit_tp = low <= tp
        if hit_sl and hit_tp:
            return "SL"  # conservative worst-case
        if hit_sl:
            return "SL"
        if hit_tp:
            return "TP"
    return None


def classify_tick_exit(direction: str, ticks: pd.DataFrame, sl: float, tp: float) -> str | None:
    for _, row in ticks.iterrows():
        bid = float(row["bid"])
        ask = float(row["ask"])
        if direction == "buy":
            if bid <= sl:
                return "SL"
            if bid >= tp:
                return "TP"
        else:
            if ask >= sl:
                return "SL"
            if ask <= tp:
                return "TP"
    return None


def monthly_performance(rows: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for r in rows:
        if r["result"] not in {"WIN", "LOSS"}:
            continue
        m = pd.Timestamp(r["close_time"]).strftime("%Y-%m")
        out[m] += float(r["profit"])
    return dict(sorted(out.items()))


def main() -> None:
    backend_dir = Path(__file__).resolve().parent
    load_dotenv(backend_dir / ".env", override=False)
    parser = argparse.ArgumentParser(description="Backtest SMC strategy from historical CSV bars/ticks.")
    parser.add_argument("--start", type=str, default="")
    parser.add_argument("--end", type=str, default="")
    parser.add_argument("--bars-csv", type=str, default=BARS_CSV)
    parser.add_argument("--ticks-csv", type=str, default=TICKS_CSV)
    parser.add_argument("--initial-balance", type=float, default=None)
    parser.add_argument("--risk-percent", type=float, default=env_float("RISK_PERCENT", 1.0))
    parser.add_argument("--rr", type=float, default=env_float("SMC_RR", 4.0))
    parser.add_argument("--ob-buffer-pips", type=float, default=env_float("OB_BUFFER_PIPS", 3.0))
    parser.add_argument("--save-skipped", action="store_true")
    parser.add_argument("--use-ticks", type=str, default="true")
    parser.add_argument(
        "--structure-lookback",
        type=int,
        default=env_int("BACKTEST_STRUCTURE_LOOKBACK", 2000),
        help="Max bars per timeframe passed to structure detection. Use 0 for full-history (slow).",
    )
    parser.add_argument("--m5-lookback", type=int, default=0, help="Override M5 structure lookback.")
    parser.add_argument("--m15-lookback", type=int, default=0, help="Override M15 structure lookback.")
    parser.add_argument("--h1-lookback", type=int, default=0, help="Override H1 structure lookback.")
    args = parser.parse_args()

    env_initial = os.getenv("BACKTEST_INITIAL_BALANCE", "").strip()
    default_initial = float(env_initial) if env_initial else 5000.0
    initial_balance = args.initial_balance if args.initial_balance is not None else default_initial
    if initial_balance <= 0:
        initial_balance = 5000.0

    settings = BacktestSettings(
        initial_balance=initial_balance,
        risk_percent=args.risk_percent,
        rr=args.rr,
        ob_buffer_pips=args.ob_buffer_pips,
        order_expiry_hours=env_int("ORDER_EXPIRY_HOURS", 24),
        max_open_trades=env_int("MAX_OPEN_TRADES", 1),
        spread_points=env_float("BACKTEST_SPREAD_POINTS", 20.0),
        slippage_points=env_float("BACKTEST_SLIPPAGE_POINTS", 5.0),
        commission_per_lot=env_float("BACKTEST_COMMISSION_PER_LOT", 0.0),
        save_skipped=args.save_skipped,
        use_ticks=str_to_bool(args.use_ticks),
    )

    bars_df = read_csv_smart(args.bars_csv, expected_kind="bars")
    bars_map = map_columns(bars_df, is_ticks=False)
    bars_df = ensure_single_time_column(bars_df, bars_map)
    bars = normalize_bars(bars_df, bars_map)

    ticks = pd.DataFrame(columns=["time", "bid", "ask"])
    ticks_enabled = False
    if settings.use_ticks and args.ticks_csv and Path(args.ticks_csv).exists():
        try:
            ticks_df = read_csv_smart(args.ticks_csv, expected_kind="ticks")
            ticks_map = map_columns(ticks_df, is_ticks=True)
            ticks_df = ensure_single_time_column(ticks_df, ticks_map)
            ticks = normalize_ticks(ticks_df, ticks_map)
            ticks_enabled = settings.use_ticks and not ticks.empty
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
    if bars.empty:
        raise RuntimeError("No bars found after date filtering.")

    m5 = resample_ohlc(bars, "5min")
    m15 = resample_ohlc(bars, "15min")
    h1 = resample_ohlc(bars, "1h")
    print(
        f"Bars loaded: raw={len(bars)}, M5={len(m5)}, M15={len(m15)}, H1={len(h1)} | "
        f"time range {bars['time'].min()} -> {bars['time'].max()}"
    )
    if settings.use_ticks and args.ticks_csv and not ticks_enabled:
        print("Ticks were requested, but no valid ticks dataset was loaded. Falling back to candle-only exits.")
    if not settings.use_ticks:
        print("Tick replay disabled (--use-ticks false): skipping tick CSV load and using candle-based exits.")

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
    balance = float(settings.initial_balance)
    peak_balance = balance
    max_drawdown = 0.0
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = [{"time": m5.iloc[0]["time"], "balance": balance}]
    trades_by_decision = Counter()
    trades_by_mode = Counter()
    trades_by_ob_tf = Counter()
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

    for i in range(len(m5)):
        now = pd.Timestamp(m5.iloc[i]["time"])
        if i and i % 5000 == 0:
            print(f"Replay progress: {i}/{len(m5)} M5 candles processed...")
        while h1_end < len(h1) and pd.Timestamp(h1.iloc[h1_end]["time"]) <= now:
            h1_end += 1
        while m15_end < len(m15) and pd.Timestamp(m15.iloc[m15_end]["time"]) <= now:
            m15_end += 1

        if lookback_h1 > 0:
            h1_start = max(0, h1_end - lookback_h1)
        else:
            h1_start = 0
        if lookback_m15 > 0:
            m15_start = max(0, m15_end - lookback_m15)
        else:
            m15_start = 0
        if lookback_m5 > 0:
            m5_start = max(0, (i + 1) - lookback_m5)
        else:
            m5_start = 0

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
        tick_slice = pd.DataFrame()
        if ticks_enabled and i > 0:
            prev_t = pd.Timestamp(m5.iloc[i - 1]["time"])
            tick_slice = ticks[(ticks["time"] > prev_t) & (ticks["time"] <= now)]

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
                exit_hit = classify_tick_exit(active["direction"], tick_slice, active["stop_loss"], active["take_profit"])
                if exit_hit is None:
                    exit_hit = classify_candle_exit(
                        active["direction"], c_low, c_high, active["stop_loss"], active["take_profit"]
                    )
                if exit_hit:
                    active_risk_percent = float(active.get("risk_percent", settings.risk_percent) or settings.risk_percent)
                    risk_amount = balance * (active_risk_percent / 100.0)
                    risk_price = abs(active["entry"] - active["stop_loss"])
                    cost_price = (settings.spread_points + settings.slippage_points) * POINT_SIZE
                    r_cost = (cost_price / risk_price) if risk_price > 0 else 0.0
                    r_mult = settings.rr - r_cost if exit_hit == "TP" else -1.0 - r_cost
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

        if not active:
            if h1_cache_time != h1_now.iloc[-1]["time"]:
                h1_result_cache = detect_structure(h1_now, swing_length) or {"events": []}
                h1_cache_time = h1_now.iloc[-1]["time"]
            if m15_cache_time != m15_now.iloc[-1]["time"]:
                m15_result_cache = detect_structure(m15_now, internal_length)
                m15_cache_time = m15_now.iloc[-1]["time"]

            h1_result = h1_result_cache
            m15_result = m15_result_cache
            m5_result = detect_structure(m5_now, internal_length)
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
            h1_supply_ob = (
                find_rejection_order_block(h1_now, h1_result, BEARISH, "H1")
                or last_valid_ob(h1_result["events"], h1_now, BEARISH, "H1")
            )
            h1_demand_ob = (
                find_rejection_order_block(h1_now, h1_result, BULLISH, "H1")
                or last_valid_ob(h1_result["events"], h1_now, BULLISH, "H1")
            )
            selected_ob, _, _, _ = select_active_ob(
                trade_bias,
                trade_mode,
                m15_result,
                m5_result,
                m15_now,
                m5_now,
                swing_low,
                swing_high,
                equilibrium,
                fibs,
                h1_supply_ob=h1_supply_ob,
                h1_demand_ob=h1_demand_ob,
                current_price=c_close,
            )
            if selected_ob and is_ob_invalidated(m5_now, selected_ob, use_close=True):
                selected_ob = last_valid_ob(
                    m5_result["events"], m5_now, trade_bias if trade_bias else external_bias, "M5"
                ) or most_recent_ob(selected_ob)
            candidate, reason = build_trade_candidate(decision, trade_mode, selected_ob, c_close, settings, now)
            if candidate:
                total_signals += 1
                entry_status = candidate.get("entry_status") or get_entry_status(c_close, candidate["entry"], trade_bias, POINT_SIZE)
                execution_style = candidate.get("execution_style", "pending_limit")
                active = {
                    "trade_id": f"T{len(trades)+1:06d}",
                    "signal_time": now,
                    "fill_time": now if execution_style == "market" else None,
                    "close_time": None,
                    "state": "open" if execution_style == "market" else "pending",
                    "expiry_time": now + timedelta(hours=settings.order_expiry_hours),
                    "entry_status": entry_status,
                    "result": "",
                    "profit": 0.0,
                    "r_multiple": 0.0,
                    "balance_after": balance,
                    "max_drawdown_at_trade": max_drawdown,
                    "h1_bias": "bullish" if external_bias == BULLISH else "bearish",
                    "current_location": current_location,
                    "reason": "valid_signal",
                    **candidate,
                }
                trades_by_decision[candidate["decision"]] += 1
                trades_by_mode[candidate["trade_mode"]] += 1
                trades_by_ob_tf[candidate["ob_timeframe"]] += 1
            elif settings.save_skipped:
                cancelled += 1
                trades.append(
                    {
                        "trade_id": f"S{len(trades)+1:06d}",
                        "signal_time": now,
                        "fill_time": None,
                        "close_time": now,
                        "decision": decision,
                        "trade_mode": trade_mode,
                        "direction": "buy" if trade_bias == BULLISH else "sell" if trade_bias == BEARISH else "",
                        "ob_timeframe": selected_ob.get("timeframe") if selected_ob else "",
                        "ob_type": selected_ob.get("type") if selected_ob else "",
                        "entry": None,
                        "stop_loss": None,
                        "take_profit": None,
                        "rr": settings.rr,
                        "result": "CANCELLED",
                        "profit": 0.0,
                        "r_multiple": 0.0,
                        "balance_after": balance,
                        "max_drawdown_at_trade": max_drawdown,
                        "h1_bias": "bullish" if external_bias == BULLISH else "bearish",
                        "current_location": current_location,
                        "selected_ob_high": float(selected_ob["high"]) if selected_ob else None,
                        "selected_ob_low": float(selected_ob["low"]) if selected_ob else None,
                        "selected_ob_time": selected_ob.get("time") if selected_ob else None,
                        "reason": reason or "skipped",
                    }
                )

        equity_curve.append({"time": now, "balance": balance})

    closed_trades = [t for t in trades if t.get("result") in {"WIN", "LOSS"}]
    trades_by_entry_model = Counter(t.get("entry_model", "UNKNOWN") for t in closed_trades)
    trades_by_execution_style = Counter(t.get("execution_style", "UNKNOWN") for t in closed_trades)
    gross_profit = sum(t["profit"] for t in closed_trades if t["profit"] > 0)
    gross_loss = abs(sum(t["profit"] for t in closed_trades if t["profit"] < 0))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0
    avg_r = sum(t["r_multiple"] for t in closed_trades) / len(closed_trades) if closed_trades else 0.0
    best_r = max((t["r_multiple"] for t in closed_trades), default=0.0)
    worst_r = min((t["r_multiple"] for t in closed_trades), default=0.0)
    max_dd_pct = (max_drawdown / peak_balance * 100.0) if peak_balance > 0 else 0.0
    win_rate = (wins / (wins + losses) * 100.0) if (wins + losses) > 0 else 0.0

    out_dir = backend_dir / "storage" / "backtests"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    trades_path = out_dir / f"backtest_trades_{ts}.csv"
    summary_path = out_dir / f"backtest_summary_{ts}.json"
    equity_path = out_dir / f"backtest_equity_curve_{ts}.csv"

    pd.DataFrame(trades).to_csv(trades_path, index=False)
    pd.DataFrame(equity_curve).to_csv(equity_path, index=False)
    summary = {
        "csv_date_range": {"start": str(bars["time"].min()), "end": str(bars["time"].max())},
        "total_m5_candles_tested": int(len(m5)),
        "total_signals": int(total_signals),
        "total_trades_filled": int(wins + losses),
        "expired_pending_orders": int(expired),
        "wins": int(wins),
        "losses": int(losses),
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 4),
        "net_profit": round(balance - settings.initial_balance, 2),
        "starting_balance": round(settings.initial_balance, 2),
        "final_balance": round(balance, 2),
        "max_drawdown_amount": round(max_drawdown, 2),
        "max_drawdown_percent": round(max_dd_pct, 2),
        "average_r": round(avg_r, 4),
        "best_trade_r": round(best_r, 4),
        "worst_trade_r": round(worst_r, 4),
        "consecutive_wins": int(max_consec_wins),
        "consecutive_losses": int(max_consec_losses),
        "trades_by_decision": dict(trades_by_decision),
        "trades_by_trade_mode": dict(trades_by_mode),
        "trades_by_ob_timeframe": dict(trades_by_ob_tf),
        "trades_by_entry_model": dict(trades_by_entry_model),
        "trades_by_execution_style": dict(trades_by_execution_style),
        "monthly_performance": monthly_performance(trades),
        "ticks_used": bool(ticks_enabled),
        "settings": settings.__dict__,
        "outputs": {
            "trades_csv": str(trades_path),
            "summary_json": str(summary_path),
            "equity_curve_csv": str(equity_path),
        },
    }
    summary_path.write_text(json.dumps(summary, default=str, indent=2), encoding="utf-8")

    print("\n===== BACKTEST SUMMARY =====")
    for k in [
        "csv_date_range",
        "total_m5_candles_tested",
        "total_signals",
        "total_trades_filled",
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
    ]:
        print(f"{k}: {summary[k]}")
    print(f"trades_by_decision: {summary['trades_by_decision']}")
    print(f"trades_by_trade_mode: {summary['trades_by_trade_mode']}")
    print(f"trades_by_ob_timeframe: {summary['trades_by_ob_timeframe']}")
    print(f"trades_by_entry_model: {summary['trades_by_entry_model']}")
    print(f"trades_by_execution_style: {summary['trades_by_execution_style']}")
    print(f"monthly_performance: {summary['monthly_performance']}")
    print(f"Saved trades CSV: {trades_path}")
    print(f"Saved summary JSON: {summary_path}")
    print(f"Saved equity curve CSV: {equity_path}")


if __name__ == "__main__":
    main()
