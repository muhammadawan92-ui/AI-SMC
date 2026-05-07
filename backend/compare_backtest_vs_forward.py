from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

# Allow `python backend/compare_backtest_vs_forward.py` from repo root.
BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent

sys.path.insert(0, str(BACKEND_DIR))

from trade_journal import connect_db, get_db_path, init_db  # noqa: E402


KNOWLEDGE_LATEST_PATH = REPO_ROOT / "storage" / "knowledge" / "backtest_knowledge_latest.json"
REPORTS_DIR = REPO_ROOT / "storage" / "reports"


def make_group_key(decision: str, trade_mode: str, ob_timeframe: str) -> str:
    return f"decision={decision}|trade_mode={trade_mode}|ob_timeframe={ob_timeframe}"


@dataclass
class Perf:
    total_trades: int
    wins: int
    losses: int
    breakeven: int
    win_rate: float
    profit_factor: float
    net_profit: float

    @classmethod
    def from_rows(cls, rows: list[sqlite3.Row]) -> "Perf":
        total = len(rows)
        wins = sum(1 for r in rows if str(r["status"] or "") == "CLOSED_WIN")
        losses = sum(1 for r in rows if str(r["status"] or "") == "CLOSED_LOSS")
        breakeven = sum(1 for r in rows if str(r["status"] or "") == "CLOSED_BREAKEVEN")

        gross_profit = sum(float(r["profit"]) for r in rows if float(r["profit"] or 0.0) > 0)
        gross_loss = abs(sum(float(r["profit"]) for r in rows if float(r["profit"] or 0.0) < 0))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0
        net_profit = sum(float(r["profit"] or 0.0) for r in rows)
        win_rate = (wins / total * 100.0) if total > 0 else 0.0
        return cls(
            total_trades=total,
            wins=wins,
            losses=losses,
            breakeven=breakeven,
            win_rate=round(win_rate, 2),
            profit_factor=round(profit_factor, 4),
            net_profit=round(net_profit, 2),
        )


def classify(expected_win_rate: float, expected_pf: float, forward_win_rate: float, forward_pf: float) -> str:
    # Simple rubric. "matching" means no material degradation in win rate + PF.
    if expected_pf <= 0:
        # If expected PF is 0, fall back to win rate delta.
        if forward_win_rate >= expected_win_rate - 3:
            return "matching"
        return "weaker"

    if forward_pf >= expected_pf * 0.95 and abs(forward_win_rate - expected_win_rate) <= 3:
        return "matching"

    if forward_pf >= expected_pf * 1.05 and forward_win_rate >= expected_win_rate:
        return "stronger"

    # Otherwise, consider it weaker unless PF improved and win rate wasn't too bad.
    if forward_pf < expected_pf * 0.8 or forward_win_rate < expected_win_rate - 5:
        return "weaker"

    return "matching"


def load_knowledge_latest() -> dict[str, Any]:
    if not KNOWLEDGE_LATEST_PATH.exists():
        raise FileNotFoundError(f"Missing backtest knowledge file: {KNOWLEDGE_LATEST_PATH}")
    return json.loads(KNOWLEDGE_LATEST_PATH.read_text(encoding="utf-8"))


def read_forward_trades(symbol: Optional[str], strategy_version: Optional[str]) -> list[sqlite3.Row]:
    # trade_journal.connect_db() already uses DATABASE_URL (and the backend/.env loader in other scripts).
    init_db()
    conn = connect_db()
    try:
        conn.row_factory = sqlite3.Row
        statuses = ("CLOSED_WIN", "CLOSED_LOSS", "CLOSED_BREAKEVEN")

        clauses = ["status IN (?,?,?)"]
        params: list[Any] = list(statuses)

        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol)

        if strategy_version:
            clauses.append("strategy_version = ?")
            params.append(strategy_version)

        where_sql = " AND ".join(clauses)
        sql = f"""
            SELECT
                id,
                created_at,
                symbol,
                strategy_version,
                decision,
                trade_mode,
                ob_timeframe,
                status,
                profit
            FROM ai_trade_journal
            WHERE {where_sql}
            ORDER BY created_at ASC
        """
        return list(conn.execute(sql, params).fetchall())
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare backtest expected performance vs forward demo/live.")
    parser.add_argument("--symbol", type=str, default="", help="Override symbol filter (optional).")
    parser.add_argument("--strategy-version", type=str, default="", help="Override strategy version filter (optional).")
    parser.add_argument("--print-top", type=int, default=12, help="How many group rows to show in the report.")
    args = parser.parse_args()

    # Ensure backend/.env is loaded so the journal reads the right DATABASE_URL.
    load_dotenv(BACKEND_DIR / ".env", override=False)

    knowledge = load_knowledge_latest()
    knowledge_expected_by_group: dict[str, Any] = knowledge.get("expected_by_group") or {}

    knowledge_symbol = str(knowledge.get("symbol") or "")
    knowledge_strategy_version = str(knowledge.get("strategy_version") or "")

    symbol = args.symbol.strip() or (knowledge_symbol if knowledge_symbol else None)
    strategy_version = args.strategy_version.strip() or (knowledge_strategy_version if knowledge_strategy_version else None)

    forward_rows = read_forward_trades(symbol=symbol, strategy_version=strategy_version)
    forward_groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in forward_rows:
        key = make_group_key(
            str(r["decision"] or ""),
            str(r["trade_mode"] or ""),
            str(r["ob_timeframe"] or ""),
        )
        forward_groups[key].append(r)

    forward_overall = Perf.from_rows(forward_rows)

    # Build "expected performance" using the SAME group distribution as the forward sample.
    expected_win_rate_weighted = 0.0
    expected_pf_weighted = 0.0
    total_forward = len(forward_rows)
    for key, group_rows in forward_groups.items():
        g_n = len(group_rows)
        exp = knowledge_expected_by_group.get(key) or {}
        exp_win_rate = float(exp.get("win_rate", knowledge.get("win_rate", 0.0)) or 0.0)
        exp_pf = float(exp.get("profit_factor", knowledge.get("profit_factor", 0.0)) or 0.0)
        expected_win_rate_weighted += exp_win_rate * g_n
        expected_pf_weighted += exp_pf * g_n

    expected_win_rate_weighted = round(
        (expected_win_rate_weighted / total_forward) if total_forward > 0 else 0.0, 2
    )
    expected_pf_weighted = round((expected_pf_weighted / total_forward) if total_forward > 0 else 0.0, 4)

    verdict = classify(
        expected_win_rate=expected_win_rate_weighted,
        expected_pf=expected_pf_weighted,
        forward_win_rate=forward_overall.win_rate,
        forward_pf=forward_overall.profit_factor,
    )

    # Console summary
    print("=== Backtest expected performance (weighted by forward group distribution) ===")
    print(f"Symbol: {symbol or knowledge_symbol or 'N/A'}")
    print(f"Strategy version: {strategy_version or knowledge_strategy_version or 'N/A'}")
    print(f"Expected win rate: {expected_win_rate_weighted}%")
    print(f"Expected profit factor: {expected_pf_weighted}")
    print("")
    print("=== Forward actual performance (from trade_journal CLOSED_* rows) ===")
    print(f"Total forward trades: {forward_overall.total_trades}")
    print(f"Forward win rate: {forward_overall.win_rate}%")
    print(f"Forward profit factor: {forward_overall.profit_factor}")
    print(f"Forward net profit: {forward_overall.net_profit}")
    print(f"Match verdict: {verdict}")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS_DIR / f"backtest_vs_forward_{ts}.txt"

    # Build report text.
    top_keys = sorted(forward_groups.keys(), key=lambda k: len(forward_groups[k]), reverse=True)[: max(1, int(args.print_top))]
    lines: list[str] = []
    lines.append("BACKTEST vs FORWARD TEST COMPARISON")
    lines.append("")
    lines.append(f"Report generated at: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"Trade journal DB: {get_db_path()}")
    lines.append("")
    lines.append("BACKTEST KNOWLEDGE (LATEST)")
    lines.append(f"Strategy version: {knowledge_strategy_version}")
    lines.append(f"Symbol: {knowledge_symbol}")
    if knowledge.get("backtest_date_range"):
        dr = knowledge["backtest_date_range"]
        lines.append(f"Backtest date range: {dr.get('start')} -> {dr.get('end')}")
    lines.append(f"Total backtest trades: {knowledge.get('total_trades')}")
    lines.append(f"Backtest win rate: {knowledge.get('win_rate')}%")
    lines.append(f"Backtest profit factor: {knowledge.get('profit_factor')}")
    lines.append("")
    lines.append("EXPECTED PERFORMANCE (MATCHING FORWARD GROUP DISTRIBUTION)")
    lines.append(f"Expected win rate: {expected_win_rate_weighted}%")
    lines.append(f"Expected profit factor: {expected_pf_weighted}")
    lines.append("")
    lines.append("FORWARD ACTUAL PERFORMANCE (CLOSED_* TRADES)")
    lines.append(f"Total forward trades: {forward_overall.total_trades}")
    lines.append(f"Forward win rate: {forward_overall.win_rate}%")
    lines.append(f"Forward profit factor: {forward_overall.profit_factor}")
    lines.append(f"Forward net profit: {forward_overall.net_profit}")
    lines.append(f"Verdict: {verdict}")
    lines.append("")

    lines.append("TOP FORWARD GROUPS (decision + trade_mode + OB timeframe)")
    for key in top_keys:
        group_rows = forward_groups[key]
        forward_perf = Perf.from_rows(group_rows)
        exp = knowledge_expected_by_group.get(key) or {}
        exp_win_rate = exp.get("win_rate", knowledge.get("win_rate", 0.0))
        exp_pf = exp.get("profit_factor", knowledge.get("profit_factor", 0.0))
        lines.append(
            f"- {key} | N={forward_perf.total_trades} | "
            f"Forward WR={forward_perf.win_rate}% PF={forward_perf.profit_factor} | "
            f"Expected WR={exp_win_rate}% PF={exp_pf}"
        )

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("")
    print(f"Report written to: {report_path}")


if __name__ == "__main__":
    main()

