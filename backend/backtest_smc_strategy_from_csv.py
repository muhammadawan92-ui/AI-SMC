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


def build_trade_candidate(decision: str, trade_mode: str, selected_ob: dict, current_close: float, settings: BacktestSettings):
    if decision not in VALID_DECISIONS or not selected_ob:
        return None, "no_valid_decision_or_ob"
    direction = "buy" if decision.startswith("BUY") else "sell"
    ob_high = float(selected_ob["high"])
    ob_low = float(selected_ob["low"])
    buffer_price = settings.ob_buffer_pips * PIP_SIZE
    if direction == "buy":
        entry = ob_high
        stop = ob_low - buffer_price
        risk = entry - stop
        tp = entry + risk * settings.rr
        if entry >= current_close:
            return None, "buy_limit_not_below_current_price"
    else:
        entry = ob_low
        stop = ob_high + buffer_price
        risk = stop - entry
        tp = entry - risk * settings.rr
        if entry <= current_close:
            return None, "sell_limit_not_above_current_price"
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
        "entry": float(entry),
        "stop_loss": float(stop),
        "take_profit": float(tp),
        "rr": float(settings.rr),
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
    if args.ticks_csv and Path(args.ticks_csv).exists():
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

    for i in range(len(m5)):
        now = pd.Timestamp(m5.iloc[i]["time"])
        if i and i % 5000 == 0:
            print(f"Replay progress: {i}/{len(m5)} M5 candles processed...")
        h1_now = h1[h1["time"] <= now].reset_index(drop=True)
        m15_now = m15[m15["time"] <= now].reset_index(drop=True)
        m5_now = m5[m5["time"] <= now].reset_index(drop=True)
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
                    risk_amount = balance * (settings.risk_percent / 100.0)
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
            h1_result = detect_structure(h1_now, swing_length) or {"events": []}
            m15_result = detect_structure(m15_now, internal_length)
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
            selected_ob, _, _, _ = select_active_ob(
                trade_bias, trade_mode, m15_result, m5_result, m15_now, m5_now, swing_low, swing_high, equilibrium, fibs
            )
            if selected_ob and is_ob_invalidated(m5_now, selected_ob, use_close=True):
                selected_ob = last_valid_ob(
                    m5_result["events"], m5_now, trade_bias if trade_bias else external_bias, "M5"
                ) or most_recent_ob(selected_ob)
            candidate, reason = build_trade_candidate(decision, trade_mode, selected_ob, c_close, settings)
            if candidate:
                total_signals += 1
                entry_status = get_entry_status(c_close, candidate["entry"], trade_bias, POINT_SIZE)
                active = {
                    "trade_id": f"T{len(trades)+1:06d}",
                    "signal_time": now,
                    "fill_time": None,
                    "close_time": None,
                    "state": "pending",
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
    print(f"monthly_performance: {summary['monthly_performance']}")
    print(f"Saved trades CSV: {trades_path}")
    print(f"Saved summary JSON: {summary_path}")
    print(f"Saved equity curve CSV: {equity_path}")


if __name__ == "__main__":
    main()
