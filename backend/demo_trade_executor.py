"""
demo_trade_executor.py

Places demo pending orders from the AI SMC overlay logic and logs every attempt
into the SQLite trade journal.

Place this file in:
C:/Users/osama/cursor project/ea-ai-platform/backend/demo_trade_executor.py
"""

import os
import math
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import MetaTrader5 as mt5
from dotenv import load_dotenv

from test_smc_overlay import connect_mt5, build_overlay
from trade_journal import (
    get_latest_active_setup,
    record_ob_observation,
    record_trade_attempt,
)


BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(BACKEND_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    return value in ["1", "true", "yes", "y", "on"]


def get_pip_size(symbol_info):
    digits = int(symbol_info.digits)
    point = float(symbol_info.point)

    if digits in [3, 5]:
        return point * 10

    return point


def normalize_price(price: float, digits: int) -> float:
    return round(float(price), int(digits))





def json_safe(value):
    """Make MT5/pandas/sqlite objects safe for JSON visual journal snapshots."""
    if value is None:
        return None
    if hasattr(value, "_asdict"):
        return {str(k): json_safe(v) for k, v in value._asdict().items()}
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def get_visual_journal_dir() -> Path:
    raw = os.getenv("AI_VISUAL_JOURNAL_DIR", "storage/visual_journal").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path


def get_overlay_csv_path() -> Path | None:
    raw = os.getenv("AI_OVERLAY_CSV_PATH", "").strip()
    if raw:
        return Path(raw)
    appdata = os.getenv("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files" / "AI_SMC_OVERLAY.csv"
    return None


def visual_stage_enabled(stage: str) -> bool:
    if not env_bool("AI_VISUAL_JOURNAL_ENABLED", False):
        return False
    stage = stage.lower()
    if stage in {"signal", "setup_skipped"}:
        return env_bool("AI_VISUAL_CAPTURE_ON_SIGNAL", True)
    if stage in {"dry_run", "order_sent", "order_rejected", "order_send_failed", "market_entry_sent"}:
        return env_bool("AI_VISUAL_CAPTURE_ON_ORDER", True)
    return True


def capture_screen_png(path: Path) -> str | None:
    """Optional whole-screen screenshot. MT5 must be visible for this to be useful."""
    if not env_bool("AI_VISUAL_SCREENSHOT_ENABLED", True):
        return None
    try:
        import pyautogui  # optional dependency
        img = pyautogui.screenshot()
        img.save(str(path))
        return str(path)
    except Exception as exc:
        return f"SCREENSHOT_FAILED: {exc}"


def save_visual_journal_snapshot(
    stage: str,
    *,
    summary: dict | None = None,
    trade: dict | None = None,
    request: dict | None = None,
    result=None,
    journal_id: int | None = None,
    reason: str = "",
):
    """
    Save a live visual/decision snapshot for later review.
    This writes JSON every time and tries to save a screen PNG/copy overlay CSV if available.
    It never blocks trading if capture fails.
    """
    try:
        if not visual_stage_enabled(stage):
            return None

        symbol = str((summary or {}).get("symbol") or (trade or {}).get("symbol") or os.getenv("TRADING_SYMBOL", "SYMBOL"))
        day = datetime.now().strftime("%Y-%m-%d")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        jid = f"J{journal_id}" if journal_id else "JNA"
        safe_stage = "".join(c if c.isalnum() or c in "_-" else "_" for c in stage)
        base_dir = get_visual_journal_dir() / day
        base_dir.mkdir(parents=True, exist_ok=True)
        base = f"{ts}_{symbol}_{jid}_{safe_stage}"

        png_path = base_dir / f"{base}.png"
        screenshot_status = capture_screen_png(png_path)
        screenshot_path = str(png_path) if screenshot_status == str(png_path) else None

        overlay_copy_path = None
        overlay_src = get_overlay_csv_path()
        if overlay_src and overlay_src.exists():
            overlay_dst = base_dir / f"{base}_overlay.csv"
            shutil.copy2(str(overlay_src), str(overlay_dst))
            overlay_copy_path = str(overlay_dst)

        payload = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "stage": stage,
            "journal_id": journal_id,
            "reason": reason,
            "symbol": symbol,
            "screenshot_path": screenshot_path,
            "screenshot_status": screenshot_status,
            "overlay_csv_copy": overlay_copy_path,
            "summary": summary,
            "trade": trade,
            "request": request,
            "result": json_safe(result),
        }
        json_path = base_dir / f"{base}.json"
        json_path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Visual journal saved: {json_path}")
        return str(json_path)
    except Exception as exc:
        print(f"WARNING: Visual journal capture failed at stage={stage}: {exc}")
        return None

def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return float(default)


def env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except Exception:
        return int(default)


def is_ai_zone_trade(trade: dict | None) -> bool:
    return bool(trade and (trade.get("execution_style") in ("market", "ai_zone_market") or trade.get("entry_model") == "OB_NOT_MITIGATED_ZONE_ENTRY"))

def selected_ob_passes_fib_guard(summary: dict, selected_ob: dict | None):
    """Block live execution unless the active OB came from the manual fib-confirmed selector."""
    if not env_bool("SMC_FIB_CONFIRMED_OB_REQUIRE_FOR_EXECUTION", True):
        return True, None

    if not selected_ob:
        return False, "Fib guard blocked: no selected OB."

    if bool(selected_ob.get("fib_confirmed")):
        return True, None

    source = summary.get("selected_ob_source")
    return False, (
        "Fib guard blocked: selected OB is not fib-confirmed. "
        f"selected_ob_source={source}. Wait for M15/M5 OB inside 0.618-0.886 impulse fib zone."
    )



def seconds_since_ob_time(selected_ob: dict) -> float | None:
    """Best-effort age calculation for an OB time coming from pandas/MT5."""
    if not selected_ob:
        return None

    ob_time = selected_ob.get("time")
    if ob_time is None:
        return None

    try:
        if hasattr(ob_time, "to_pydatetime"):
            ob_time = ob_time.to_pydatetime()
        if isinstance(ob_time, str):
            ob_time = datetime.fromisoformat(ob_time.replace("Z", "+00:00")).replace(tzinfo=None)
        if hasattr(ob_time, "replace"):
            return max(0.0, (datetime.now() - ob_time.replace(tzinfo=None)).total_seconds())
    except Exception:
        return None

    return None


def apply_h1_context_stop(summary: dict, decision: str, trade_direction: str, entry: float, normal_stop: float, buffer_price: float):
    """
    Execution copy of the H1-aware retracement SL rule.
    Only applies to BUY_RETRACEMENT / SELL_RETRACEMENT when overlay selected_ob
    was refined inside an H1 demand/supply OB.
    """
    if not env_bool("SMC_H1_OB_RETRACE_SL_ENABLED", True):
        return normal_stop, "ltf_ob"

    if decision not in ["BUY_RETRACEMENT", "SELL_RETRACEMENT"]:
        return normal_stop, "ltf_ob"

    selected_ob = summary.get("selected_ob") or {}
    h1_ob = selected_ob.get("h1_context_ob") or summary.get("h1_context_ob")
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

    if trade_direction == "sell":
        if mode == "extreme":
            return h1_high + buffer_price, "h1_supply_extreme"
        return max(float(normal_stop), h1_mid), "h1_supply_midpoint_protected"

    if trade_direction == "buy":
        if mode == "extreme":
            return h1_low - buffer_price, "h1_demand_extreme"
        return min(float(normal_stop), h1_mid), "h1_demand_midpoint_protected"

    return normal_stop, "ltf_ob"


def volume_decimals(step: float) -> int:
    step_text = f"{step:.10f}".rstrip("0")
    if "." not in step_text:
        return 0
    return len(step_text.split(".")[1])


def normalize_volume(volume: float, symbol_info):
    """
    True dynamic 1% risk sizing.
    This function follows broker min/max/step only.
    It does NOT enforce MAX_LOT_SIZE because user confirmed 0.32 lot was correct.
    """
    min_vol = float(symbol_info.volume_min)
    max_vol = float(symbol_info.volume_max)
    step = float(symbol_info.volume_step)

    if step <= 0:
        step = 0.01

    volume = math.floor(volume / step) * step
    volume = max(min_vol, min(volume, max_vol))

    return round(volume, volume_decimals(step))


def calculate_lot_size(symbol: str, entry: float, stop_loss: float, risk_percent: float):
    account = mt5.account_info()
    symbol_info = mt5.symbol_info(symbol)

    if account is None:
        raise RuntimeError(f"Could not read MT5 account info: {mt5.last_error()}")

    if symbol_info is None:
        raise RuntimeError(f"Could not read symbol info for {symbol}: {mt5.last_error()}")

    equity = float(account.equity)
    risk_amount = equity * (risk_percent / 100.0)

    sl_distance = abs(float(entry) - float(stop_loss))

    if sl_distance <= 0:
        raise RuntimeError("Invalid SL distance. Entry and SL are equal.")

    tick_size = float(symbol_info.trade_tick_size)
    tick_value = float(symbol_info.trade_tick_value)

    if tick_size <= 0 or tick_value <= 0:
        raise RuntimeError(
            f"Invalid tick data for {symbol}. "
            f"tick_size={tick_size}, tick_value={tick_value}"
        )

    loss_per_1_lot = (sl_distance / tick_size) * tick_value

    if loss_per_1_lot <= 0:
        raise RuntimeError("Invalid loss per lot calculation.")

    raw_lot = risk_amount / loss_per_1_lot
    lot = normalize_volume(raw_lot, symbol_info)

    actual_risk_amount = loss_per_1_lot * lot
    actual_risk_percent = (actual_risk_amount / equity) * 100.0 if equity > 0 else 0.0

    return lot, risk_amount, loss_per_1_lot, actual_risk_amount, actual_risk_percent


def is_real_account():
    account = mt5.account_info()

    if account is None:
        raise RuntimeError(f"Could not read account info: {mt5.last_error()}")

    # MT5 trade_mode commonly: 0 demo, 1 contest, 2 real
    return int(account.trade_mode) == 2


def safety_checks(symbol: str):
    enable_live_trading = env_bool("ENABLE_LIVE_TRADING", False)
    allow_real_account = env_bool("ALLOW_REAL_ACCOUNT", False)

    if not enable_live_trading:
        raise RuntimeError("ENABLE_LIVE_TRADING=false. Order sending is disabled in .env.")

    if is_real_account() and not allow_real_account:
        print("BLOCKED_REAL_ACCOUNT: Real account detected and ALLOW_REAL_ACCOUNT=false.")
        raise RuntimeError(
            "BLOCKED_REAL_ACCOUNT: Real account detected and ALLOW_REAL_ACCOUNT=false. "
            "Execution blocked for safety."
        )

    symbol_info = mt5.symbol_info(symbol)

    if symbol_info is None:
        raise RuntimeError(f"Symbol not found: {symbol}")

    if not symbol_info.visible:
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Could not select symbol {symbol}: {mt5.last_error()}")

    symbol_info = mt5.symbol_info(symbol)

    if int(symbol_info.trade_mode) == 0:
        raise RuntimeError(f"Trading disabled for symbol {symbol}")

    tick = mt5.symbol_info_tick(symbol)

    if tick is None:
        raise RuntimeError(f"No tick data for {symbol}")

    spread_points = (float(tick.ask) - float(tick.bid)) / float(symbol_info.point)
    max_spread = float(os.getenv("MAX_SPREAD_POINTS", "30"))

    if spread_points > max_spread:
        raise RuntimeError(
            f"Spread too high: {spread_points:.1f} points. "
            f"Max allowed: {max_spread:.1f}"
        )

    return symbol_info, tick, spread_points


def existing_ai_trades_count(symbol: str, magic: int):
    count = 0

    positions = mt5.positions_get(symbol=symbol)
    if positions:
        for p in positions:
            if int(p.magic) == int(magic):
                count += 1

    orders = mt5.orders_get(symbol=symbol)
    if orders:
        for o in orders:
            if int(o.magic) == int(magic):
                count += 1

    return count


def existing_ai_positions_count(symbol: str, magic: int) -> int:
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return 0
    return sum(1 for p in positions if int(p.magic) == int(magic))


def get_ai_pending_orders(symbol: str, magic: int):
    orders = mt5.orders_get(symbol=symbol)
    if not orders:
        return []
    return [o for o in orders if int(o.magic) == int(magic)]


def cancel_ai_pending_orders(symbol: str, magic: int) -> bool:
    """Cancel old AI limits before a reduced-risk market entry."""
    pending_orders = get_ai_pending_orders(symbol, magic)
    if not pending_orders:
        return True

    all_cancelled = True
    for order in pending_orders:
        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": int(order.ticket),
            "symbol": symbol,
            "magic": magic,
            "comment": "AI_ZONE_CANCEL_OLD_LIMIT",
        }
        print(f"Cancelling old AI pending order before zone market entry: {order.ticket}")
        result = mt5.order_send(request)
        if result is None or int(result.retcode) != mt5.TRADE_RETCODE_DONE:
            all_cancelled = False
            print(f"WARNING: Could not cancel pending order {order.ticket}. Result={result}, error={mt5.last_error()}")
    return all_cancelled


def same_ob(active_row, selected_ob: dict) -> bool:
    if not active_row or not selected_ob:
        return False
    tf_match = str(active_row["ob_timeframe"] or "") == str(selected_ob.get("timeframe") or "")
    type_match = str(active_row["ob_type"] or "") == str(selected_ob.get("type") or "")
    high_match = abs(float(active_row["ob_high"] or 0.0) - float(selected_ob.get("high") or 0.0)) < 1e-9
    low_match = abs(float(active_row["ob_low"] or 0.0) - float(selected_ob.get("low") or 0.0)) < 1e-9
    return tf_match and type_match and high_match and low_match


def log_observation_if_replaced(summary: dict, active_row, symbol_info, reason: str) -> int | None:
    selected_ob = summary.get("selected_ob") or {}
    if not selected_ob or not active_row:
        return None
    original_entry = float(active_row["entry"] or 0.0)
    if original_entry <= 0:
        return None
    pip_size = get_pip_size(symbol_info)
    if pip_size <= 0:
        pip_size = 0.0001
    new_entry = float(selected_ob["high"]) if str(summary.get("trade_direction") or "").lower() == "buy" else float(selected_ob["low"])
    distance_pips = abs(new_entry - original_entry) / pip_size
    obs_id = record_ob_observation(
        original_journal_id=int(active_row["id"]),
        symbol=str(summary.get("symbol") or symbol_info.name),
        decision=str(summary.get("decision") or ""),
        direction=str(summary.get("trade_direction") or ""),
        original_ob_timeframe=str(active_row["ob_timeframe"] or ""),
        original_ob_high=float(active_row["ob_high"] or 0.0),
        original_ob_low=float(active_row["ob_low"] or 0.0),
        original_entry=original_entry,
        new_ob_timeframe=str(selected_ob.get("timeframe") or ""),
        new_ob_type=str(selected_ob.get("type") or ""),
        new_ob_high=float(selected_ob.get("high") or 0.0),
        new_ob_low=float(selected_ob.get("low") or 0.0),
        new_ob_time=selected_ob.get("time"),
        distance_from_original_entry_pips=float(distance_pips),
        current_price=float(summary.get("current_price") or 0.0),
        observation_json={
            "original_journal_id": int(active_row["id"]),
            "symbol": str(summary.get("symbol") or symbol_info.name),
            "decision": summary.get("decision"),
            "direction": summary.get("trade_direction"),
            "original_ob_timeframe": active_row["ob_timeframe"],
            "original_ob_high": active_row["ob_high"],
            "original_ob_low": active_row["ob_low"],
            "original_entry": original_entry,
            "new_ob_timeframe": selected_ob.get("timeframe"),
            "new_ob_high": selected_ob.get("high"),
            "new_ob_low": selected_ob.get("low"),
            "new_ob_time": selected_ob.get("time"),
            "new_reference_entry": new_entry,
            "distance_from_original_entry_pips": distance_pips,
            "current_price": summary.get("current_price"),
            "observation_reason": reason,
        },
        outcome_status="PENDING" if env_bool("SMC_LOG_HYPOTHETICAL_OB_OUTCOMES", True) else "NOT_TRACKED",
        outcome_notes=reason,
    )
    return obs_id


def build_ai_zone_market_trade(
    summary: dict,
    symbol_info,
    tick,
    selected_ob: dict,
    decision: str,
    trade_direction: str,
    limit_entry: float,
    normal_stop: float,
    buffer_price: float,
):
    """
    V8 AI zone market entry at 1% risk.

    Used when a valid active OB/zone exists but price moves away without
    mitigating the OB. Creates a market entry with strict anti-chase filters.
    Flip AI zone entries also route here at FLIP_AI_ZONE_RISK_PERCENT (1%).
    """
    if not env_bool("AI_ZONE_AUTONOMY_ENABLED", False):
        return None, "AI_ZONE_AUTONOMY_ENABLED=false. OB-not-mitigated market entry disabled."

    if not env_bool("AI_ZONE_ALLOW_OB_NOT_MITIGATED_ENTRY", True):
        return None, "AI_ZONE_ALLOW_OB_NOT_MITIGATED_ENTRY=false."

    if not selected_ob:
        return None, "AI zone entry blocked: no selected active OB."

    fib_ok, fib_reason = selected_ob_passes_fib_guard(summary, selected_ob)
    if not fib_ok:
        return None, "AI zone entry blocked: " + str(fib_reason)

    if decision not in ["BUY_CONTINUATION", "SELL_CONTINUATION", "BUY_RETRACEMENT", "SELL_RETRACEMENT"]:
        return None, f"AI zone entry blocked: decision is not executable. Decision={decision}"

    expected_status = "BUY_LIMIT_NOT_CHASED" if trade_direction == "buy" else "SELL_LIMIT_NOT_CHASED"
    entry_status = str(summary.get("entry_status") or "")
    if env_bool("AI_ZONE_REQUIRE_LIMIT_NOT_CHASED_STATUS", True) and entry_status != expected_status:
        return None, f"AI zone entry blocked: entry_status={entry_status}, expected={expected_status}."

    if env_bool("AI_ZONE_REQUIRE_ACTIVE_H1_OB", False):
        if trade_direction == "sell" and not (summary.get("inside_h1_supply") or selected_ob.get("h1_context_ob")):
            return None, "AI zone sell blocked: not inside/linked to H1 supply."
        if trade_direction == "buy" and not (summary.get("inside_h1_demand") or selected_ob.get("h1_context_ob")):
            return None, "AI zone buy blocked: not inside/linked to H1 demand."

    wait_minutes = env_float("AI_ZONE_OB_MITIGATION_WAIT_MINUTES", 0.0)
    age_seconds = seconds_since_ob_time(selected_ob)
    if age_seconds is not None and wait_minutes > 0 and age_seconds < wait_minutes * 60.0:
        return None, (
            f"AI zone entry waiting for OB mitigation window. "
            f"Age={age_seconds/60.0:.1f} min, required={wait_minutes:.1f} min."
        )

    pip_size = get_pip_size(symbol_info)
    digits = int(symbol_info.digits)

    market_entry = float(tick.ask) if trade_direction == "buy" else float(tick.bid)
    distance_from_ob_pips = (
        (market_entry - limit_entry) / pip_size if trade_direction == "buy"
        else (limit_entry - market_entry) / pip_size
    )

    if distance_from_ob_pips <= 0:
        return None, f"AI zone entry blocked: price has not moved away from OB. Distance={distance_from_ob_pips:.1f} pips."

    min_distance = env_float("AI_ZONE_MIN_DISTANCE_FROM_OB_PIPS", 0.0)
    max_distance = env_float("AI_ZONE_MAX_DISTANCE_FROM_ACTIVE_ZONE_PIPS", 25.0)

    if distance_from_ob_pips < min_distance:
        return None, f"AI zone entry blocked: distance from OB is too small: {distance_from_ob_pips:.1f} pips."

    if max_distance > 0 and distance_from_ob_pips > max_distance:
        return None, f"AI zone entry blocked: do not chase. Distance from OB={distance_from_ob_pips:.1f} pips, max={max_distance:.1f}."

    stop_loss, stop_source = apply_h1_context_stop(
        summary,
        decision,
        trade_direction,
        market_entry,
        normal_stop,
        buffer_price,
    )

    risk_distance = abs(market_entry - stop_loss)
    if risk_distance <= 0:
        return None, "AI zone entry blocked: invalid SL/risk distance."

    moved_r_from_ob = abs(market_entry - limit_entry) / risk_distance if risk_distance > 0 else 999.0
    max_moved_r = env_float("AI_ZONE_DO_NOT_CHASE_AFTER_R", 1.0)
    if max_moved_r > 0 and moved_r_from_ob > max_moved_r:
        return None, f"AI zone entry blocked: moved {moved_r_from_ob:.2f}R from OB, max={max_moved_r:.2f}R."

    rr = env_float("AI_ZONE_PREFERRED_RR", env_float("SMC_RR", 4.0))
    min_rr = env_float("AI_ZONE_MIN_RR", 2.0)
    if rr < min_rr:
        rr = min_rr

    if trade_direction == "buy":
        take_profit = market_entry + (risk_distance * rr)
        order_type = mt5.ORDER_TYPE_BUY
        order_type_name = "BUY_MARKET_AI_ZONE"
    else:
        take_profit = market_entry - (risk_distance * rr)
        order_type = mt5.ORDER_TYPE_SELL
        order_type_name = "SELL_MARKET_AI_ZONE"

    min_stop_distance = int(symbol_info.trade_stops_level) * float(symbol_info.point)
    if min_stop_distance > 0:
        if abs(market_entry - stop_loss) < min_stop_distance:
            return None, (
                f"AI zone SL too close. Required min distance={min_stop_distance}, "
                f"actual={abs(market_entry - stop_loss)}"
            )
        if abs(take_profit - market_entry) < min_stop_distance:
            return None, (
                f"AI zone TP too close. Required min distance={min_stop_distance}, "
                f"actual={abs(take_profit - market_entry)}"
            )

    market_entry = normalize_price(market_entry, digits)
    stop_loss = normalize_price(stop_loss, digits)
    take_profit = normalize_price(take_profit, digits)

    is_flip_entry = bool(summary.get("flip_used_for_entry") or selected_ob.get("flip_entry_ob"))
    risk_percent = env_float("FLIP_AI_ZONE_RISK_PERCENT", env_float("AI_ZONE_ENTRY_RISK_PERCENT", 1.0)) if is_flip_entry else env_float("AI_ZONE_ENTRY_RISK_PERCENT", 1.0)
    lot, risk_amount, loss_per_1_lot, actual_risk_amount, actual_risk_percent = calculate_lot_size(
        symbol_info.name,
        market_entry,
        stop_loss,
        risk_percent,
    )

    entry_model_name = os.getenv("FLIP_AI_ZONE_MODEL_NAME", "FLIP_AI_ZONE_ENTRY") if is_flip_entry else os.getenv("AI_ZONE_MODEL_NAME", "OB_NOT_MITIGATED_ZONE_ENTRY")
    v8_risk_rule = "AI_ZONE_MARKET_1_PERCENT"
    v8_risk_reason = f"{'flip_' if is_flip_entry else ''}ai_zone_market@{risk_percent}pct"

    trade = {
        "symbol": symbol_info.name,
        "decision": decision,
        "direction": trade_direction,
        "order_type": order_type,
        "order_type_name": order_type_name,
        "entry": market_entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "risk": abs(market_entry - stop_loss),
        "rr": rr,
        "lot": lot,
        "risk_percent": risk_percent,
        "risk_amount": risk_amount,
        "actual_risk_amount": actual_risk_amount,
        "actual_risk_percent": actual_risk_percent,
        "loss_per_1_lot": loss_per_1_lot,
        "ob_high": float(selected_ob["high"]),
        "ob_low": float(selected_ob["low"]),
        "ob_timeframe": selected_ob.get("timeframe"),
        "ob_type": selected_ob.get("type"),
        "stop_source": stop_source,
        "h1_context_ob": selected_ob.get("h1_context_ob") or summary.get("h1_context_ob"),
        "execution_style": "ai_zone_market",
        "entry_model": entry_model_name,
        "flip_used_for_entry": is_flip_entry,
        "flip_type": summary.get("flip_type") or selected_ob.get("flip_type"),
        "flip_timeframe": summary.get("flip_timeframe") or selected_ob.get("flip_timeframe"),
        "fib_confirmed_ob": bool(selected_ob.get("fib_confirmed")),
        "fib_ob_method": selected_ob.get("fib_ob_method"),
        "original_ob_mitigated": False,
        "missed_limit_entry": normalize_price(limit_entry, digits),
        "distance_from_ob_pips": float(distance_from_ob_pips),
        "moved_r_from_ob": float(moved_r_from_ob),
        "ai_zone_reason": "Valid flip AI zone market entry at 1% risk." if is_flip_entry else "Valid active zone confirmed; OB was not mitigated; AI zone market entry at 1% risk.",
        "v8_risk_rule": v8_risk_rule,
        "v8_risk_reason": v8_risk_reason,
        "strategy_version": os.getenv("STRATEGY_VERSION", "fib_flip_v8_ai_zone_priority"),
        "selected_zone_timeframe": selected_ob.get("timeframe"),
        "m15_priority_applied": bool(selected_ob.get("timeframe") == "M15"),
    }

    summary["entry_model"] = trade["entry_model"]
    summary["ai_zone_entry"] = {
        "enabled": True,
        "risk_percent": risk_percent,
        "original_ob_mitigated": False,
        "missed_limit_entry": trade["missed_limit_entry"],
        "distance_from_ob_pips": float(distance_from_ob_pips),
        "moved_r_from_ob": float(moved_r_from_ob),
        "entry_status_before_market": entry_status,
    }

    return trade, None


def build_trade_from_summary(summary: dict, symbol_info, tick):
    selected_ob = summary.get("selected_ob")
    trade_direction = summary.get("trade_direction")
    decision = summary.get("decision")

    valid_decisions = [
        "BUY_CONTINUATION",
        "SELL_CONTINUATION",
        "SELL_RETRACEMENT",
        "BUY_RETRACEMENT",
    ]

    if decision not in valid_decisions:
        return None, f"No valid execution decision. Decision={decision}"

    if not selected_ob:
        return None, "No selected OB available."

    fib_ok, fib_reason = selected_ob_passes_fib_guard(summary, selected_ob)
    if not fib_ok:
        return None, str(fib_reason)

    if trade_direction not in ["buy", "sell"]:
        return None, f"No valid trade direction. Direction={trade_direction}"

    pip_size = get_pip_size(symbol_info)
    buffer_pips = env_float("OB_BUFFER_PIPS", 3.0)
    buffer_price = buffer_pips * pip_size

    rr = env_float("SMC_RR", 4.0)

    ob_high = float(selected_ob["high"])
    ob_low = float(selected_ob["low"])

    digits = int(symbol_info.digits)

    stop_source = "ltf_ob"

    if trade_direction == "buy":
        entry = ob_high
        normal_stop = ob_low - buffer_price
        stop_loss, stop_source = apply_h1_context_stop(
            summary, decision, trade_direction, entry, normal_stop, buffer_price
        )
        risk = entry - stop_loss
        take_profit = entry + (risk * rr)
        order_type = mt5.ORDER_TYPE_BUY_LIMIT

        if entry >= float(tick.ask):
            return build_ai_zone_market_trade(
                summary,
                symbol_info,
                tick,
                selected_ob,
                decision,
                trade_direction,
                entry,
                normal_stop,
                buffer_price,
            )

    else:
        entry = ob_low
        normal_stop = ob_high + buffer_price
        stop_loss, stop_source = apply_h1_context_stop(
            summary, decision, trade_direction, entry, normal_stop, buffer_price
        )
        risk = stop_loss - entry
        take_profit = entry - (risk * rr)
        order_type = mt5.ORDER_TYPE_SELL_LIMIT

        if entry <= float(tick.bid):
            return build_ai_zone_market_trade(
                summary,
                symbol_info,
                tick,
                selected_ob,
                decision,
                trade_direction,
                entry,
                normal_stop,
                buffer_price,
            )

    if risk <= 0:
        return None, "Invalid risk distance."

    min_stop_distance = int(symbol_info.trade_stops_level) * float(symbol_info.point)

    if min_stop_distance > 0:
        if abs(entry - stop_loss) < min_stop_distance:
            return None, (
                f"SL too close. Required min distance={min_stop_distance}, "
                f"actual={abs(entry - stop_loss)}"
            )

        if abs(take_profit - entry) < min_stop_distance:
            return None, (
                f"TP too close. Required min distance={min_stop_distance}, "
                f"actual={abs(take_profit - entry)}"
            )

    entry = normalize_price(entry, digits)
    stop_loss = normalize_price(stop_loss, digits)
    take_profit = normalize_price(take_profit, digits)

    is_flip_entry = bool(summary.get("flip_used_for_entry") or selected_ob.get("flip_entry_ob"))
    risk_percent = env_float("FLIP_PENDING_LIMIT_RISK_PERCENT", env_float("PENDING_LIMIT_RISK_PERCENT", 0.5)) if is_flip_entry else env_float("PENDING_LIMIT_RISK_PERCENT", 0.5)
    lot, risk_amount, loss_per_1_lot, actual_risk_amount, actual_risk_percent = calculate_lot_size(
        symbol_info.name,
        entry,
        stop_loss,
        risk_percent,
    )

    if is_flip_entry:
        entry_model = os.getenv("FLIP_PENDING_LIMIT_MODEL_NAME", "FLIP_FIB_RETEST_ENTRY")
    else:
        entry_model = "FIB_CONFIRMED_OB_ENTRY" if bool(selected_ob.get("fib_confirmed")) else "OB_MITIGATION_LIMIT_ENTRY"
    v8_risk_rule = "PENDING_LIMIT_0_5_PERCENT"
    v8_risk_reason = f"{'flip_' if is_flip_entry else ''}pending_limit@{risk_percent}pct"

    trade = {
        "symbol": symbol_info.name,
        "decision": decision,
        "direction": trade_direction,
        "order_type": order_type,
        "order_type_name": "BUY_LIMIT" if trade_direction == "buy" else "SELL_LIMIT",
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "risk": abs(entry - stop_loss),
        "rr": rr,
        "lot": lot,
        "risk_percent": risk_percent,
        "risk_amount": risk_amount,
        "actual_risk_amount": actual_risk_amount,
        "actual_risk_percent": actual_risk_percent,
        "loss_per_1_lot": loss_per_1_lot,
        "ob_high": ob_high,
        "ob_low": ob_low,
        "ob_timeframe": selected_ob.get("timeframe"),
        "ob_type": selected_ob.get("type"),
        "stop_source": stop_source,
        "h1_context_ob": selected_ob.get("h1_context_ob") or summary.get("h1_context_ob"),
        "execution_style": "pending_limit",
        "entry_model": entry_model,
        "flip_used_for_entry": is_flip_entry,
        "flip_type": summary.get("flip_type") or selected_ob.get("flip_type"),
        "flip_timeframe": summary.get("flip_timeframe") or selected_ob.get("flip_timeframe"),
        "fib_confirmed_ob": bool(selected_ob.get("fib_confirmed")),
        "fib_ob_method": selected_ob.get("fib_ob_method"),
        "original_ob_mitigated": None,
        "v8_risk_rule": v8_risk_rule,
        "v8_risk_reason": v8_risk_reason,
        "strategy_version": os.getenv("STRATEGY_VERSION", "fib_flip_v8_ai_zone_priority"),
        "selected_zone_timeframe": selected_ob.get("timeframe"),
        "m15_priority_applied": bool(selected_ob.get("timeframe") == "M15"),
    }

    return trade, None

def build_order_request(trade: dict, magic: int):
    if trade.get("execution_style") in ("market", "ai_zone_market"):
        return {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": trade["symbol"],
            "volume": trade["lot"],
            "type": trade["order_type"],
            "price": trade["entry"],
            "sl": trade["stop_loss"],
            "tp": trade["take_profit"],
            "deviation": env_int("AI_ZONE_MARKET_DEVIATION_POINTS", 30),
            "magic": magic,
            "comment": "AI_V8_ZONE_1PCT",
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

    expiry_hours = env_int("ORDER_EXPIRY_HOURS", 24)
    expiry = datetime.now() + timedelta(hours=expiry_hours)

    return {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": trade["symbol"],
        "volume": trade["lot"],
        "type": trade["order_type"],
        "price": trade["entry"],
        "sl": trade["stop_loss"],
        "tp": trade["take_profit"],
        "deviation": 20,
        "magic": magic,
        "comment": f"AI_V8_LIMIT_0.5_{trade['decision']}",
        "type_time": mt5.ORDER_TIME_SPECIFIED,
        "expiration": int(expiry.timestamp()),
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }

def send_pending_order(summary: dict, trade: dict, magic: int, spread_points: float):
    request = build_order_request(trade, magic)

    dry_run = env_bool("DRY_RUN", True)

    print("\n===== ORDER REQUEST =====")
    print(f"Execution style: {trade.get('execution_style', 'pending_limit')}")
    print(f"Entry model: {trade.get('entry_model', 'OB_MITIGATION_LIMIT_ENTRY')}")
    for key, value in request.items():
        print(f"{key}: {value}")

    if dry_run:
        journal_id = record_trade_attempt(
            summary=summary,
            trade=trade,
            request=request,
            result=None,
            status="DRY_RUN",
            reason="DRY_RUN=true. No order was sent.",
            spread_points=spread_points,
            magic=magic,
        )
        save_visual_journal_snapshot(
            "dry_run",
            summary=summary,
            trade=trade,
            request=request,
            result=None,
            journal_id=journal_id,
            reason="DRY_RUN=true. No order was sent.",
        )
        print(f"\nDRY_RUN=true. No order was sent. Journal ID: {journal_id}")
        return None

    result = mt5.order_send(request)

    if result is None:
        journal_id = record_trade_attempt(
            summary=summary,
            trade=trade,
            request=request,
            result=None,
            status="ORDER_SEND_FAILED",
            reason=f"order_send returned None: {mt5.last_error()}",
            spread_points=spread_points,
            magic=magic,
        )
        save_visual_journal_snapshot(
            "order_send_failed",
            summary=summary,
            trade=trade,
            request=request,
            result=None,
            journal_id=journal_id,
            reason=f"order_send returned None: {mt5.last_error()}",
        )
        raise RuntimeError(f"order_send returned None. Journal ID: {journal_id}. Error: {mt5.last_error()}")

    print("\n===== ORDER RESULT =====")
    print(result)

    retcode = int(result.retcode)

    if retcode != mt5.TRADE_RETCODE_DONE:
        journal_id = record_trade_attempt(
            summary=summary,
            trade=trade,
            request=request,
            result=result,
            status="ORDER_REJECTED",
            reason=f"Retcode={result.retcode}, comment={result.comment}",
            spread_points=spread_points,
            magic=magic,
        )
        save_visual_journal_snapshot(
            "order_rejected",
            summary=summary,
            trade=trade,
            request=request,
            result=result,
            journal_id=journal_id,
            reason=f"Retcode={result.retcode}, comment={result.comment}",
        )
        raise RuntimeError(
            f"Order failed. Journal ID: {journal_id}. "
            f"Retcode={result.retcode}, comment={result.comment}"
        )

    is_market = trade.get("execution_style") in ("market", "ai_zone_market")
    success_status = "FILLED_OPEN" if is_market else "ORDER_PLACED"
    success_reason = (
        f"AI zone market order sent at {trade.get('risk_percent', 1.0)}% risk."
        if is_market
        else f"Pending limit order placed at {trade.get('risk_percent', 0.5)}% risk."
    )

    journal_id = record_trade_attempt(
        summary=summary,
        trade=trade,
        request=request,
        result=result,
        status=success_status,
        reason=success_reason,
        spread_points=spread_points,
        magic=magic,
    )

    save_visual_journal_snapshot(
        "market_entry_sent" if is_market else "order_sent",
        summary=summary,
        trade=trade,
        request=request,
        result=result,
        journal_id=journal_id,
        reason=success_reason,
    )
    print(f"\nOrder placed and logged. Journal ID: {journal_id}")
    return result


def main():
    symbol = os.getenv("TRADING_SYMBOL", "GBPUSDm")
    magic = int(os.getenv("SMC_TRADE_MAGIC", "260786"))
    max_open_trades = int(os.getenv("MAX_OPEN_TRADES", "1"))
    pending_order_lock = env_bool("SMC_PENDING_ORDER_LOCK", True)
    allow_pending_replace = env_bool("SMC_ALLOW_PENDING_REPLACE", False)
    log_new_ob_after_signal = env_bool("SMC_LOG_NEW_OB_AFTER_SIGNAL", True)

    connect_mt5()

    try:
        symbol_info, tick, spread_points = safety_checks(symbol)

        summary = build_overlay(symbol)
        trade, reason = build_trade_from_summary(summary, symbol_info, tick)
        save_visual_journal_snapshot(
            "signal",
            summary=summary,
            trade=trade,
            reason=reason or "Signal evaluated by demo_trade_executor.",
        )

        print("\n===== SMC EXECUTION SUMMARY =====")
        print(f"Symbol: {symbol}")
        print(f"Decision: {summary.get('decision')}")
        print(f"Trade direction: {summary.get('trade_direction')}")
        print(f"Current price: {summary.get('current_price')}")
        print(f"Spread points: {spread_points:.1f}")

        active_setup = get_latest_active_setup(symbol_info.name, magic)
        current_count = existing_ai_trades_count(symbol, magic)

        market_zone_trade = is_ai_zone_trade(trade)

        if pending_order_lock and active_setup and trade and not market_zone_trade:
            selected_ob = summary.get("selected_ob") or {}
            if selected_ob and not same_ob(active_setup, selected_ob):
                if log_new_ob_after_signal:
                    obs_id = log_observation_if_replaced(
                        summary,
                        active_setup,
                        symbol_info,
                        reason="Pending/setup lock active; new OB observed while original setup remains active.",
                    )
                    if obs_id:
                        print(f"New OB logged for learning. Existing order not moved. Observation ID: {obs_id}")
                    else:
                        print("New OB logged for learning. Existing order not moved.")
                else:
                    print("Pending/setup lock active. Existing order not moved.")
                return
            if not allow_pending_replace:
                print(
                    f"Pending/setup lock active on journal ID {active_setup['id']}. "
                    "Existing order/setup retained; no replacement order."
                )
                return

        if market_zone_trade:
            open_positions = existing_ai_positions_count(symbol, magic)
            pending_orders = get_ai_pending_orders(symbol, magic)

            if open_positions > 0:
                print(f"AI zone entry blocked: existing AI position count={open_positions}.")
                return

            if pending_orders:
                if env_bool("AI_ZONE_CANCEL_PENDING_BEFORE_MARKET", True):
                    cancelled = cancel_ai_pending_orders(symbol, magic)
                    if not cancelled:
                        print("AI zone entry blocked: could not cancel old pending order safely.")
                        return
                    current_count = existing_ai_trades_count(symbol, magic)
                else:
                    print("AI zone entry blocked: pending order exists and AI_ZONE_CANCEL_PENDING_BEFORE_MARKET=false.")
                    return

        if current_count >= max_open_trades:
            print(
                f"Existing AI SMC trades/orders: {current_count}. "
                f"Max allowed: {max_open_trades}. No new order."
            )
            return

        if reason:
            journal_id = record_trade_attempt(
                summary=summary,
                trade=None,
                request=None,
                result=None,
                status="SETUP_SKIPPED",
                reason=reason,
                spread_points=spread_points,
                magic=magic,
            )
            save_visual_journal_snapshot(
                "setup_skipped",
                summary=summary,
                trade=None,
                request=None,
                result=None,
                journal_id=journal_id,
                reason=reason,
            )
            print("\nNo order placed:")
            print(reason)
            print(f"Skipped setup logged. Journal ID: {journal_id}")
            return

        print("\n===== V8 RISK PLAN =====")
        print(f"Strategy: {trade.get('strategy_version', 'fib_flip_v8_ai_zone_priority')}")
        print(f"Order type: {trade['order_type_name']}")
        print(f"Entry: {trade['entry']}")
        print(f"SL: {trade['stop_loss']}")
        print(f"TP: {trade['take_profit']}")
        print(f"RR: {trade['rr']}")
        print(f"V8 Risk Rule: {trade.get('v8_risk_rule', 'N/A')}")
        print(f"Target risk %: {trade['risk_percent']}%")
        print(f"Target risk amount: {trade['risk_amount']:.2f}")
        print(f"Actual risk %: {trade['actual_risk_percent']:.2f}%")
        print(f"Actual risk amount: {trade['actual_risk_amount']:.2f}")
        print(f"Lot size: {trade['lot']}")
        print(f"Execution style: {trade.get('execution_style')}")
        print(f"Entry model: {trade.get('entry_model')}")
        print(f"Selected zone TF: {trade.get('selected_zone_timeframe')}")
        print(f"M15 priority applied: {trade.get('m15_priority_applied')}")
        if trade.get("distance_from_ob_pips") is not None:
            print(f"Distance from missed OB: {trade['distance_from_ob_pips']:.1f} pips")
            print(f"Moved from OB: {trade['moved_r_from_ob']:.2f}R")
        print(f"OB: {trade['ob_timeframe']} {trade['ob_type']}")
        print(f"OB High: {trade['ob_high']}")
        print(f"OB Low: {trade['ob_low']}")

        send_pending_order(summary, trade, magic, spread_points)

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
