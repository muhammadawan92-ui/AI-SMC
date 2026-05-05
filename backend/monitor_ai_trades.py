"""
monitor_ai_trades.py

Monitors AI SMC pending orders and open positions, then updates the SQLite trade journal.

Place this file in:
C:/Users/osama/cursor project/ea-ai-platform/backend/monitor_ai_trades.py

Run once:
python monitor_ai_trades.py

Run continuously:
python monitor_ai_trades.py --loop 30
"""

import argparse
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5
from dotenv import load_dotenv

from test_smc_overlay import connect_mt5
from trade_journal import get_active_journal_rows, update_trade_status, init_db


BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(BACKEND_DIR / ".env")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def row_float(row, key, default=0.0):
    value = row[key]
    if value is None:
        return default
    return float(value)


def row_int(row, key, default=0):
    value = row[key]
    if value is None:
        return default
    return int(value)


def calculate_unrealized_r(row, current_price: float) -> float:
    direction = str(row["direction"] or "").lower()
    entry = row_float(row, "entry")
    sl = row_float(row, "sl")

    risk = abs(entry - sl)
    if risk <= 0:
        return 0.0

    if direction == "buy":
        return (current_price - entry) / risk

    if direction == "sell":
        return (entry - current_price) / risk

    return 0.0


def get_active_orders_map():
    orders = mt5.orders_get()
    result = {}

    if orders:
        for order in orders:
            result[int(order.ticket)] = order

    return result


def get_active_positions(symbol: str, magic: int):
    positions = mt5.positions_get(symbol=symbol)
    result = []

    if positions:
        for position in positions:
            if int(position.magic) == int(magic):
                result.append(position)

    return result


def find_matching_position(row, positions):
    wanted_volume = row_float(row, "volume")
    wanted_direction = str(row["direction"] or "").lower()

    for position in positions:
        pos_type = int(position.type)
        # POSITION_TYPE_BUY = 0, POSITION_TYPE_SELL = 1
        pos_direction = "buy" if pos_type == 0 else "sell"

        if wanted_direction and pos_direction != wanted_direction:
            continue

        if wanted_volume > 0 and abs(float(position.volume) - wanted_volume) > 0.0001:
            continue

        return position

    return None


def find_history_for_row(row):
    """
    Best-effort history lookup.
    This is intentionally conservative because brokers differ in order/deal linking.
    """
    symbol = row["symbol"]
    magic = row_int(row, "magic")
    order_ticket = row_int(row, "broker_order_ticket", 0)
    position_ticket = row_int(row, "broker_position_ticket", 0)

    created_at = row["created_at"]
    try:
        start = datetime.fromisoformat(created_at.replace("Z", "+00:00")) - timedelta(hours=2)
    except Exception:
        start = datetime.now() - timedelta(days=7)

    end = datetime.now() + timedelta(minutes=5)

    deals = mt5.history_deals_get(start, end)
    matched_deals = []

    if deals:
        for deal in deals:
            if str(deal.symbol).lower() != str(symbol).lower():
                continue

            if int(deal.magic) != magic:
                continue

            if order_ticket and int(deal.order) == order_ticket:
                matched_deals.append(deal)
                continue

            if position_ticket and int(deal.position_id) == position_ticket:
                matched_deals.append(deal)
                continue

    orders = mt5.history_orders_get(start, end)
    matched_orders = []

    if orders:
        for order in orders:
            if str(order.symbol).lower() != str(symbol).lower():
                continue

            if int(order.magic) != magic:
                continue

            if order_ticket and int(order.ticket) == order_ticket:
                matched_orders.append(order)

    return matched_orders, matched_deals


def update_one_row(row, active_orders):
    journal_id = int(row["id"])
    symbol = row["symbol"]
    magic = row_int(row, "magic")
    order_ticket = row_int(row, "broker_order_ticket", 0)

    # 1) Still pending in active orders.
    if order_ticket and order_ticket in active_orders:
        order = active_orders[order_ticket]
        update_trade_status(
            journal_id,
            status="PENDING_ACTIVE",
            reason="Pending order is active in MT5.",
        )
        print(f"Journal {journal_id}: pending active, order ticket {order.ticket}")
        return

    # 2) Check active position.
    positions = get_active_positions(symbol, magic)
    position = find_matching_position(row, positions)

    if position:
        current_price = float(position.price_current)
        unrealized_r = calculate_unrealized_r(row, current_price)

        old_mfe = row_float(row, "max_favorable_r", 0.0)
        old_mae = row_float(row, "max_adverse_r", 0.0)

        new_mfe = max(old_mfe, unrealized_r)
        new_mae = min(old_mae, unrealized_r)

        update_trade_status(
            journal_id,
            status="FILLED_OPEN",
            reason="Position is open in MT5.",
            broker_position_ticket=int(position.ticket),
            opened_at=utc_now() if not row["opened_at"] else row["opened_at"],
            max_favorable_r=new_mfe,
            max_adverse_r=new_mae,
            last_unrealized_r=unrealized_r,
            profit=float(position.profit),
        )
        print(
            f"Journal {journal_id}: filled/open, "
            f"position {position.ticket}, R={unrealized_r:.2f}, profit={position.profit:.2f}"
        )
        return

    # 3) Not active. Look in history.
    matched_orders, matched_deals = find_history_for_row(row)

    if matched_deals:
        total_profit = sum(float(d.profit) + float(d.commission) + float(d.swap) for d in matched_deals)
        last_deal = matched_deals[-1]

        # DEAL_ENTRY_IN=0, DEAL_ENTRY_OUT=1, DEAL_ENTRY_INOUT=2 are common values.
        has_out = any(int(getattr(d, "entry", -1)) in [1, 2] for d in matched_deals)

        if has_out:
            status = "CLOSED_WIN" if total_profit > 0 else "CLOSED_LOSS" if total_profit < 0 else "CLOSED_BREAKEVEN"
            update_trade_status(
                journal_id,
                status=status,
                reason="Closed deal found in MT5 history.",
                broker_position_ticket=int(last_deal.position_id),
                closed_at=utc_now(),
                close_price=float(last_deal.price),
                profit=total_profit,
            )
            print(f"Journal {journal_id}: {status}, profit={total_profit:.2f}")
            return

        update_trade_status(
            journal_id,
            status="ORDER_UNKNOWN",
            reason="History deal found but no active position detected.",
            profit=total_profit,
        )
        print(f"Journal {journal_id}: history deal found, no active position.")
        return

    if matched_orders:
        update_trade_status(
            journal_id,
            status="ORDER_NOT_ACTIVE",
            reason="Historical order found but no active order/position/deal. Possibly cancelled or expired.",
        )
        print(f"Journal {journal_id}: order no longer active.")
        return

    update_trade_status(
        journal_id,
        status="ORDER_UNKNOWN",
        reason="No active order, no active position, and no matching history found yet.",
    )
    print(f"Journal {journal_id}: unknown status.")


def monitor_once():
    init_db()

    active_orders = get_active_orders_map()
    rows = list(get_active_journal_rows())

    if not rows:
        print("No active AI journal rows to monitor.")
        return

    print(f"Monitoring {len(rows)} active AI journal row(s).")

    for row in rows:
        try:
            update_one_row(row, active_orders)
        except Exception as exc:
            print(f"Journal {row['id']}: monitor error: {exc}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", type=int, default=0, help="Loop every N seconds. Example: --loop 30")
    args = parser.parse_args()

    connect_mt5()

    try:
        if args.loop and args.loop > 0:
            print(f"Monitoring loop started. Interval: {args.loop} seconds. Press CTRL+C to stop.")
            while True:
                monitor_once()
                time.sleep(args.loop)
        else:
            monitor_once()
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
