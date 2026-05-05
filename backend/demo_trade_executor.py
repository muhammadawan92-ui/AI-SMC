"""
demo_trade_executor.py

Places demo pending orders from the AI SMC overlay logic and logs every attempt
into the SQLite trade journal.

Place this file in:
C:/Users/osama/cursor project/ea-ai-platform/backend/demo_trade_executor.py
"""

import os
import math
from datetime import datetime, timedelta
from pathlib import Path

import MetaTrader5 as mt5
from dotenv import load_dotenv

from test_smc_overlay import connect_mt5, build_overlay
from trade_journal import record_trade_attempt


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
        raise RuntimeError(
            "Real account detected and ALLOW_REAL_ACCOUNT=false. "
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

    if trade_direction not in ["buy", "sell"]:
        return None, f"No valid trade direction. Direction={trade_direction}"

    pip_size = get_pip_size(symbol_info)
    buffer_pips = float(os.getenv("OB_BUFFER_PIPS", "3"))
    buffer_price = buffer_pips * pip_size

    rr = float(os.getenv("SMC_RR", "4.0"))

    ob_high = float(selected_ob["high"])
    ob_low = float(selected_ob["low"])

    digits = int(symbol_info.digits)

    if trade_direction == "buy":
        entry = ob_high
        stop_loss = ob_low - buffer_price
        risk = entry - stop_loss
        take_profit = entry + (risk * rr)
        order_type = mt5.ORDER_TYPE_BUY_LIMIT

        if entry >= float(tick.ask):
            return None, (
                f"Buy limit entry is not below Ask. "
                f"Entry={entry:.5f}, Ask={tick.ask:.5f}. "
                f"Skipping auto order."
            )

    else:
        entry = ob_low
        stop_loss = ob_high + buffer_price
        risk = stop_loss - entry
        take_profit = entry - (risk * rr)
        order_type = mt5.ORDER_TYPE_SELL_LIMIT

        if entry <= float(tick.bid):
            return None, (
                f"Sell limit entry is not above Bid. "
                f"Entry={entry:.5f}, Bid={tick.bid:.5f}. "
                f"Skipping auto order."
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

    risk_percent = float(os.getenv("RISK_PERCENT", "1.0"))
    lot, risk_amount, loss_per_1_lot, actual_risk_amount, actual_risk_percent = calculate_lot_size(
        symbol_info.name,
        entry,
        stop_loss,
        risk_percent,
    )

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
    }

    return trade, None


def build_order_request(trade: dict, magic: int):
    expiry_hours = int(os.getenv("ORDER_EXPIRY_HOURS", "24"))
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
        "comment": f"AI_SMC_{trade['decision']}",
        "type_time": mt5.ORDER_TIME_SPECIFIED,
        "expiration": int(expiry.timestamp()),
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }


def send_pending_order(summary: dict, trade: dict, magic: int, spread_points: float):
    request = build_order_request(trade, magic)

    dry_run = env_bool("DRY_RUN", True)

    print("\n===== ORDER REQUEST =====")
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
        raise RuntimeError(
            f"Order failed. Journal ID: {journal_id}. "
            f"Retcode={result.retcode}, comment={result.comment}"
        )

    journal_id = record_trade_attempt(
        summary=summary,
        trade=trade,
        request=request,
        result=result,
        status="ORDER_PLACED",
        reason="Pending order placed successfully.",
        spread_points=spread_points,
        magic=magic,
    )

    print(f"\nOrder placed and logged. Journal ID: {journal_id}")
    return result


def main():
    symbol = os.getenv("TRADING_SYMBOL", "GBPUSDm")
    magic = int(os.getenv("SMC_TRADE_MAGIC", "260786"))
    max_open_trades = int(os.getenv("MAX_OPEN_TRADES", "1"))

    connect_mt5()

    try:
        symbol_info, tick, spread_points = safety_checks(symbol)

        current_count = existing_ai_trades_count(symbol, magic)

        if current_count >= max_open_trades:
            print(
                f"Existing AI SMC trades/orders: {current_count}. "
                f"Max allowed: {max_open_trades}. No new order."
            )
            return

        summary = build_overlay(symbol)
        trade, reason = build_trade_from_summary(summary, symbol_info, tick)

        print("\n===== SMC EXECUTION SUMMARY =====")
        print(f"Symbol: {symbol}")
        print(f"Decision: {summary.get('decision')}")
        print(f"Trade direction: {summary.get('trade_direction')}")
        print(f"Current price: {summary.get('current_price')}")
        print(f"Spread points: {spread_points:.1f}")

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
            print("\nNo order placed:")
            print(reason)
            print(f"Skipped setup logged. Journal ID: {journal_id}")
            return

        print("\n===== RISK PLAN =====")
        print(f"Order type: {trade['order_type_name']}")
        print(f"Entry: {trade['entry']}")
        print(f"SL: {trade['stop_loss']}")
        print(f"TP: {trade['take_profit']}")
        print(f"RR: {trade['rr']}")
        print(f"Target risk %: {trade['risk_percent']}%")
        print(f"Target risk amount: {trade['risk_amount']:.2f}")
        print(f"Actual risk %: {trade['actual_risk_percent']:.2f}%")
        print(f"Actual risk amount: {trade['actual_risk_amount']:.2f}")
        print(f"Lot size: {trade['lot']}")
        print(f"OB: {trade['ob_timeframe']} {trade['ob_type']}")
        print(f"OB High: {trade['ob_high']}")
        print(f"OB Low: {trade['ob_low']}")

        send_pending_order(summary, trade, magic, spread_points)

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
