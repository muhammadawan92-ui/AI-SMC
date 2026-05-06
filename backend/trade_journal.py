"""
trade_journal.py

SQLite trade journal for the AI SMC trading system.

Purpose:
- Save every detected setup / trade attempt.
- Save every sent pending order.
- Track status updates from monitor_ai_trades.py.
- Store full SMC context as JSON so the AI can review what worked and what failed.

Place this file in:
C:/Users/osama/cursor project/ea-ai-platform/backend/trade_journal.py
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


BACKEND_DIR = Path(__file__).resolve().parent


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db_path() -> Path:
    database_url = os.getenv("DATABASE_URL", "sqlite:///./storage/ea_platform.db").strip()

    if database_url.startswith("sqlite:///"):
        raw_path = database_url.replace("sqlite:///", "", 1)
        db_path = Path(raw_path)

        if not db_path.is_absolute():
            db_path = BACKEND_DIR / db_path

        return db_path

    return BACKEND_DIR / "storage" / "ea_platform.db"


def connect_db() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def json_safe(value: Any) -> Any:
    if value is None:
        return None

    if hasattr(value, "_asdict"):
        return {k: json_safe(v) for k, v in value._asdict().items()}

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
    except TypeError:
        return str(value)


def dumps(value: Any) -> str:
    return json.dumps(json_safe(value), ensure_ascii=False, indent=2)


def init_db() -> None:
    with connect_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_trade_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,

                strategy_version TEXT,
                symbol TEXT,
                magic INTEGER,

                decision TEXT,
                direction TEXT,
                trade_mode TEXT,
                entry_status TEXT,

                status TEXT NOT NULL,
                status_reason TEXT,

                order_type TEXT,
                broker_order_ticket INTEGER,
                broker_position_ticket INTEGER,

                volume REAL,
                entry REAL,
                sl REAL,
                tp REAL,
                rr REAL,

                risk_percent REAL,
                target_risk_amount REAL,
                actual_risk_amount REAL,

                ob_timeframe TEXT,
                ob_type TEXT,
                ob_high REAL,
                ob_low REAL,

                current_price_at_signal REAL,
                spread_points REAL,

                opened_at TEXT,
                closed_at TEXT,
                close_price REAL,
                profit REAL,

                max_favorable_r REAL DEFAULT 0,
                max_adverse_r REAL DEFAULT 0,
                last_unrealized_r REAL,

                setup_json TEXT,
                trade_json TEXT,
                request_json TEXT,
                result_json TEXT
            )
            """
        )

        conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_trade_journal_status ON ai_trade_journal(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_trade_journal_symbol_magic ON ai_trade_journal(symbol, magic)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_trade_journal_order_ticket ON ai_trade_journal(broker_order_ticket)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_trade_journal_position_ticket ON ai_trade_journal(broker_position_ticket)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_ob_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                original_journal_id INTEGER,
                symbol TEXT,
                decision TEXT,
                direction TEXT,
                original_ob_timeframe TEXT,
                original_ob_high REAL,
                original_ob_low REAL,
                original_entry REAL,
                new_ob_timeframe TEXT,
                new_ob_type TEXT,
                new_ob_high REAL,
                new_ob_low REAL,
                new_ob_time TEXT,
                distance_from_original_entry_pips REAL,
                current_price REAL,
                observation_json TEXT,
                outcome_status TEXT,
                outcome_notes TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_ob_observations_origin ON ai_ob_observations(original_journal_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_ob_observations_symbol_time ON ai_ob_observations(symbol, created_at)"
        )
        conn.commit()


def record_trade_attempt(
    *,
    summary: Dict[str, Any],
    trade: Optional[Dict[str, Any]] = None,
    request: Optional[Dict[str, Any]] = None,
    result: Optional[Any] = None,
    status: str,
    reason: str = "",
    spread_points: Optional[float] = None,
    magic: Optional[int] = None,
) -> int:
    init_db()

    trade = trade or {}
    result_safe = json_safe(result)

    broker_order_ticket = None
    if isinstance(result_safe, dict):
        broker_order_ticket = result_safe.get("order") or result_safe.get("deal")
    elif result_safe is not None and hasattr(result_safe, "order"):
        broker_order_ticket = getattr(result_safe, "order", None)

    now = utc_now()

    strategy_version = os.getenv("STRATEGY_VERSION", "v1_active")
    symbol = summary.get("symbol") or trade.get("symbol")
    decision = summary.get("decision") or trade.get("decision")
    direction = summary.get("trade_direction") or trade.get("direction")
    trade_mode = summary.get("trade_mode")
    entry_status = summary.get("entry_status")

    selected_ob = summary.get("selected_ob") or {}
    if trade:
        ob_timeframe = trade.get("ob_timeframe")
        ob_type = trade.get("ob_type")
        ob_high = trade.get("ob_high")
        ob_low = trade.get("ob_low")
    else:
        ob_timeframe = selected_ob.get("timeframe")
        ob_type = selected_ob.get("type")
        ob_high = selected_ob.get("high")
        ob_low = selected_ob.get("low")

    with connect_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO ai_trade_journal (
                created_at, updated_at,
                strategy_version, symbol, magic,
                decision, direction, trade_mode, entry_status,
                status, status_reason,
                order_type, broker_order_ticket, broker_position_ticket,
                volume, entry, sl, tp, rr,
                risk_percent, target_risk_amount, actual_risk_amount,
                ob_timeframe, ob_type, ob_high, ob_low,
                current_price_at_signal, spread_points,
                setup_json, trade_json, request_json, result_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                now,
                strategy_version,
                symbol,
                magic,
                decision,
                direction,
                trade_mode,
                entry_status,
                status,
                reason,
                trade.get("order_type_name"),
                broker_order_ticket,
                None,
                trade.get("lot"),
                trade.get("entry"),
                trade.get("stop_loss"),
                trade.get("take_profit"),
                trade.get("rr"),
                trade.get("risk_percent"),
                trade.get("risk_amount"),
                trade.get("actual_risk_amount", trade.get("risk_amount")),
                ob_timeframe,
                ob_type,
                ob_high,
                ob_low,
                summary.get("current_price"),
                spread_points,
                dumps(summary),
                dumps(trade),
                dumps(request),
                dumps(result_safe),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_trade_status(
    journal_id: int,
    *,
    status: Optional[str] = None,
    reason: Optional[str] = None,
    broker_order_ticket: Optional[int] = None,
    broker_position_ticket: Optional[int] = None,
    opened_at: Optional[str] = None,
    closed_at: Optional[str] = None,
    close_price: Optional[float] = None,
    profit: Optional[float] = None,
    max_favorable_r: Optional[float] = None,
    max_adverse_r: Optional[float] = None,
    last_unrealized_r: Optional[float] = None,
) -> None:
    init_db()

    fields = {"updated_at": utc_now()}

    if status is not None:
        fields["status"] = status
    if reason is not None:
        fields["status_reason"] = reason
    if broker_order_ticket is not None:
        fields["broker_order_ticket"] = broker_order_ticket
    if broker_position_ticket is not None:
        fields["broker_position_ticket"] = broker_position_ticket
    if opened_at is not None:
        fields["opened_at"] = opened_at
    if closed_at is not None:
        fields["closed_at"] = closed_at
    if close_price is not None:
        fields["close_price"] = close_price
    if profit is not None:
        fields["profit"] = profit
    if max_favorable_r is not None:
        fields["max_favorable_r"] = max_favorable_r
    if max_adverse_r is not None:
        fields["max_adverse_r"] = max_adverse_r
    if last_unrealized_r is not None:
        fields["last_unrealized_r"] = last_unrealized_r

    set_sql = ", ".join([f"{k}=?" for k in fields.keys()])
    values = list(fields.values()) + [journal_id]

    with connect_db() as conn:
        conn.execute(f"UPDATE ai_trade_journal SET {set_sql} WHERE id=?", values)
        conn.commit()


def get_active_journal_rows() -> Iterable[sqlite3.Row]:
    init_db()

    active_statuses = (
        "ORDER_PLACED",
        "PENDING_ACTIVE",
        "FILLED_OPEN",
        "ORDER_UNKNOWN",
    )

    placeholders = ",".join(["?"] * len(active_statuses))

    with connect_db() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM ai_trade_journal
            WHERE status IN ({placeholders})
            ORDER BY created_at ASC
            """,
            active_statuses,
        ).fetchall()

    return rows


def get_latest_active_setup(symbol: str, magic: int) -> Optional[sqlite3.Row]:
    """
    Active setup means a still-relevant locked setup or pending/open lifecycle row.
    """
    init_db()
    statuses = (
        "ORDER_PLACED",
        "PENDING_ACTIVE",
        "FILLED_OPEN",
        "ORDER_UNKNOWN",
        "DRY_RUN",
    )
    placeholders = ",".join(["?"] * len(statuses))
    with connect_db() as conn:
        row = conn.execute(
            f"""
            SELECT *
            FROM ai_trade_journal
            WHERE symbol = ?
              AND magic = ?
              AND status IN ({placeholders})
              AND entry IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (symbol, int(magic), *statuses),
        ).fetchone()
    return row


def record_ob_observation(
    *,
    original_journal_id: int,
    symbol: str,
    decision: str,
    direction: str,
    original_ob_timeframe: str,
    original_ob_high: float,
    original_ob_low: float,
    original_entry: float,
    new_ob_timeframe: str,
    new_ob_type: str,
    new_ob_high: float,
    new_ob_low: float,
    new_ob_time: Any,
    distance_from_original_entry_pips: float,
    current_price: float,
    observation_json: Optional[Dict[str, Any]] = None,
    outcome_status: str = "PENDING",
    outcome_notes: str = "",
) -> int:
    init_db()
    now = utc_now()
    with connect_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO ai_ob_observations (
                created_at,
                original_journal_id,
                symbol,
                decision,
                direction,
                original_ob_timeframe,
                original_ob_high,
                original_ob_low,
                original_entry,
                new_ob_timeframe,
                new_ob_type,
                new_ob_high,
                new_ob_low,
                new_ob_time,
                distance_from_original_entry_pips,
                current_price,
                observation_json,
                outcome_status,
                outcome_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                int(original_journal_id),
                symbol,
                decision,
                direction,
                original_ob_timeframe,
                original_ob_high,
                original_ob_low,
                original_entry,
                new_ob_timeframe,
                new_ob_type,
                new_ob_high,
                new_ob_low,
                str(new_ob_time),
                distance_from_original_entry_pips,
                current_price,
                dumps(observation_json or {}),
                outcome_status,
                outcome_notes,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_recent_rows(days: int = 1) -> Iterable[sqlite3.Row]:
    init_db()

    with connect_db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM ai_trade_journal
            WHERE datetime(created_at) >= datetime('now', ?)
            ORDER BY created_at ASC
            """,
            (f"-{int(days)} days",),
        ).fetchall()

    return rows


def print_db_location() -> None:
    print(f"Trade journal DB: {get_db_path()}")


if __name__ == "__main__":
    init_db()
    print_db_location()
    print("ai_trade_journal table is ready.")
