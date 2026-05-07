from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from dotenv import load_dotenv

# Allow `python backend/compare_backtest_vs_forward.py` from repo root.
BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent

sys.path.insert(0, str(BACKEND_DIR))

from trade_journal import connect_db, get_db_path, init_db  # noqa: E402


KNOWLEDGE_LATEST_PATH = REPO_ROOT / "storage" / "knowledge" / "backtest_knowledge_latest.json"
KNOWLEDGE_LATEST_TXT_PATH = REPO_ROOT / "storage" / "knowledge" / "backtest_knowledge_latest.txt"
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


def parse_group_key(group_key: str) -> tuple[str, str, str]:
    decision = ""
    trade_mode = ""
    ob_timeframe = ""
    parts = str(group_key).split("|")
    for p in parts:
        if p.startswith("decision="):
            decision = p.replace("decision=", "", 1)
        elif p.startswith("trade_mode="):
            trade_mode = p.replace("trade_mode=", "", 1)
        elif p.startswith("ob_timeframe="):
            ob_timeframe = p.replace("ob_timeframe=", "", 1)
    return decision, trade_mode, ob_timeframe


def _pick_latest_file(candidates: list[Path]) -> Optional[Path]:
    existing = [p for p in candidates if p.exists()]
    if not existing:
        return None
    return sorted(existing, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def _build_knowledge_from_latest_backtest() -> dict[str, Any]:
    backtests_dirs = [
        BACKEND_DIR / "storage" / "backtests",
        REPO_ROOT / "storage" / "backtests",
    ]
    summary_candidates: list[Path] = []
    trades_candidates: list[Path] = []
    for bt_dir in backtests_dirs:
        summary_candidates.extend(bt_dir.glob("backtest_summary_*.json"))
        trades_candidates.extend(bt_dir.glob("backtest_trades_*.csv"))

    if not summary_candidates:
        raise FileNotFoundError(
            "Missing backtest knowledge and no backtest_summary_*.json found "
            "under backend/storage/backtests or storage/backtests. Run a backtest first."
        )

    # Prefer summary->outputs.trades_csv linkage, and skip unusable/empty CSV files.
    latest_summary = None
    latest_trades = None
    summary = None
    trades_df = None
    summaries_sorted = sorted(summary_candidates, key=lambda p: p.stat().st_mtime, reverse=True)
    for candidate_summary in summaries_sorted:
        try:
            candidate_payload = json.loads(candidate_summary.read_text(encoding="utf-8"))
        except Exception:
            continue
        out_trades = (((candidate_payload.get("outputs") or {}).get("trades_csv")) or "").strip()
        candidate_trades = Path(out_trades) if out_trades else None
        if candidate_trades and not candidate_trades.is_absolute():
            candidate_trades = (candidate_summary.parent / candidate_trades).resolve()
        if not candidate_trades or not candidate_trades.exists():
            # Fallback by timestamped filename from summary if possible.
            ts = candidate_summary.stem.replace("backtest_summary_", "")
            fallback = candidate_summary.parent / f"backtest_trades_{ts}.csv"
            candidate_trades = fallback if fallback.exists() else None
        if not candidate_trades:
            continue
        try:
            tmp_df = pd.read_csv(candidate_trades)
        except Exception:
            continue
        if tmp_df.empty or "result" not in tmp_df.columns:
            continue
        latest_summary = candidate_summary
        latest_trades = candidate_trades
        summary = candidate_payload
        trades_df = tmp_df
        break

    if latest_summary is None or latest_trades is None or summary is None or trades_df is None:
        # One last fallback: any non-empty trades CSV
        for candidate_trades in sorted(trades_candidates, key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                tmp_df = pd.read_csv(candidate_trades)
            except Exception:
                continue
            if tmp_df.empty or "result" not in tmp_df.columns:
                continue
            latest_trades = candidate_trades
            trades_df = tmp_df
            break
        if latest_trades is None or trades_df is None:
            # Summary-only fallback: still create knowledge so comparison script can run.
            latest_summary = _pick_latest_file(summary_candidates)
            if latest_summary is None:
                raise RuntimeError("Found backtest directories but no readable summary/trades files.")
            summary = json.loads(latest_summary.read_text(encoding="utf-8"))
            latest_trades = Path("")
            trades_df = pd.DataFrame(columns=["result", "decision", "trade_mode", "ob_timeframe", "profit", "r_multiple"])

    if "result" not in trades_df.columns:
        raise RuntimeError(f"Trades CSV missing 'result' column: {latest_trades}")

    closed_df = trades_df[trades_df["result"].isin(["WIN", "LOSS"])].copy()

    def _safe_float(v: Any, default: float = 0.0) -> float:
        try:
            if v is None:
                return default
            return float(v)
        except Exception:
            return default

    def _best_worst_by_avg_r(df, col: str) -> tuple[str, str]:
        if col not in df.columns:
            return "N/A", "N/A"
        agg = (
            df.assign(_key=df[col].fillna("").astype(str), _r=df.get("r_multiple", 0.0).fillna(0.0).astype(float))
            .groupby("_key", dropna=False)["_r"]
            .mean()
        )
        agg = agg[agg.index != ""]
        if agg.empty:
            return "N/A", "N/A"
        return str(agg.idxmax()), str(agg.idxmin())

    best_decision_type, worst_decision_type = _best_worst_by_avg_r(closed_df, "decision")
    best_ob_timeframe, worst_ob_timeframe = _best_worst_by_avg_r(closed_df, "ob_timeframe")

    grouped = closed_df.copy()
    for needed in ["decision", "trade_mode", "ob_timeframe", "profit", "r_multiple", "max_favorable_r", "max_adverse_r"]:
        if needed not in grouped.columns:
            grouped[needed] = 0.0 if needed in {"profit", "r_multiple", "max_favorable_r", "max_adverse_r"} else ""
    grouped["decision"] = grouped["decision"].fillna("").astype(str)
    grouped["trade_mode"] = grouped["trade_mode"].fillna("").astype(str)
    grouped["ob_timeframe"] = grouped["ob_timeframe"].fillna("").astype(str)
    grouped["profit"] = grouped["profit"].apply(_safe_float)
    grouped["r_multiple"] = grouped["r_multiple"].apply(_safe_float)
    grouped["max_favorable_r"] = grouped["max_favorable_r"].apply(_safe_float)
    grouped["max_adverse_r"] = grouped["max_adverse_r"].apply(_safe_float)

    expected_by_group: dict[str, Any] = {}
    if not grouped.empty:
        for (decision, trade_mode, ob_timeframe), g in grouped.groupby(["decision", "trade_mode", "ob_timeframe"]):
            g_total = int(len(g))
            g_wins = int((g["result"] == "WIN").sum())
            g_losses = int((g["result"] == "LOSS").sum())
            g_win_rate = (g_wins / g_total * 100.0) if g_total > 0 else 0.0
            g_gross_profit = float(g.loc[g["profit"] > 0, "profit"].sum())
            g_gross_loss = abs(float(g.loc[g["profit"] < 0, "profit"].sum()))
            g_pf = (g_gross_profit / g_gross_loss) if g_gross_loss > 0 else 0.0
            g_avg_r = float(g["r_multiple"].mean()) if g_total > 0 else 0.0
            g_avg_mfe = float(g["max_favorable_r"].mean()) if g_total > 0 else 0.0
            g_avg_mae = float(g["max_adverse_r"].mean()) if g_total > 0 else 0.0
            key = make_group_key(str(decision), str(trade_mode), str(ob_timeframe))
            expected_by_group[key] = {
                "total_trades": g_total,
                "win_rate": round(g_win_rate, 2),
                "profit_factor": round(g_pf, 4),
                "average_r": round(g_avg_r, 4),
                "average_mfe_r": round(g_avg_mfe, 4),
                "average_mae_r": round(g_avg_mae, 4),
                "wins": g_wins,
                "losses": g_losses,
            }

    total_trades = int(len(closed_df))
    if total_trades > 0:
        wins = int((closed_df["result"] == "WIN").sum())
        losses = int((closed_df["result"] == "LOSS").sum())
        win_rate = (wins / total_trades * 100.0)
        gross_profit = float(closed_df.loc[closed_df["profit"].apply(_safe_float) > 0, "profit"].apply(_safe_float).sum())
        gross_loss = abs(float(closed_df.loc[closed_df["profit"].apply(_safe_float) < 0, "profit"].apply(_safe_float).sum()))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0
        avg_mfe_r = float(closed_df["max_favorable_r"].apply(_safe_float).mean())
        avg_mae_r = float(closed_df["max_adverse_r"].apply(_safe_float).mean())
    else:
        wins = int(summary.get("wins", 0) or 0)
        losses = int(summary.get("losses", 0) or 0)
        total_trades = int(summary.get("total_trades_filled", wins + losses) or (wins + losses))
        win_rate = _safe_float(summary.get("win_rate"), 0.0)
        profit_factor = _safe_float(summary.get("profit_factor"), 0.0)
        avg_mfe_r = 0.0
        avg_mae_r = 0.0

    settings = summary.get("settings") or {}
    rr = _safe_float(settings.get("rr"), 0.0)
    symbol_guess = ""
    csv_date_range = summary.get("csv_date_range") or {}
    trades_csv_output = ((summary.get("outputs") or {}).get("trades_csv") or "")
    if trades_csv_output:
        stem = Path(trades_csv_output).stem
        symbol_guess = stem.split("_")[0] if "_" in stem else stem

    recommendation = (
        f"Forward demo: prioritize {best_decision_type} with OB timeframe {best_ob_timeframe}; "
        f"de-prioritize {worst_decision_type}/{worst_ob_timeframe} until forward results improve."
        if best_decision_type != "N/A"
        else "Forward demo: gather more trades first, then filter by strongest decision and OB timeframe."
    )

    knowledge = {
        "strategy_version": os.getenv("STRATEGY_VERSION", "v1_active"),
        "symbol": symbol_guess,
        "backtest_date_range": {
            "start": str(csv_date_range.get("start", "")),
            "end": str(csv_date_range.get("end", "")),
        },
        "total_trades": total_trades,
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 4),
        "max_drawdown": round(_safe_float(summary.get("max_drawdown_amount"), 0.0), 2),
        "final_balance": round(_safe_float(summary.get("final_balance"), 0.0), 2),
        "best_decision_type": best_decision_type,
        "worst_decision_type": worst_decision_type,
        "best_ob_timeframe": best_ob_timeframe,
        "worst_ob_timeframe": worst_ob_timeframe,
        "average_mfe_r": round(avg_mfe_r, 4),
        "average_mae_r": round(avg_mae_r, 4),
        "recommendation_for_forward_demo_testing": recommendation,
        "expected_by_group": expected_by_group,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "source_summary_json": str(latest_summary),
            "source_trades_csv": str(latest_trades),
            "rr": rr,
        },
    }

    KNOWLEDGE_LATEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE_LATEST_PATH.write_text(json.dumps(knowledge, indent=2), encoding="utf-8")
    txt_lines = [
        "BACKTEST KNOWLEDGE (LATEST)",
        "",
        f"Strategy version: {knowledge['strategy_version']}",
        f"Symbol: {knowledge['symbol']}",
        f"Backtest date range: {knowledge['backtest_date_range']['start']} -> {knowledge['backtest_date_range']['end']}",
        f"Total trades: {knowledge['total_trades']}",
        f"Win rate: {knowledge['win_rate']}%",
        f"Profit factor: {knowledge['profit_factor']}",
        f"Max drawdown: {knowledge['max_drawdown']}",
        f"Final balance: {knowledge['final_balance']}",
        "",
        f"Best decision type: {knowledge['best_decision_type']}",
        f"Worst decision type: {knowledge['worst_decision_type']}",
        f"Best OB timeframe: {knowledge['best_ob_timeframe']}",
        f"Worst OB timeframe: {knowledge['worst_ob_timeframe']}",
        "",
        f"Average MFE R: {knowledge['average_mfe_r']}",
        f"Average MAE R: {knowledge['average_mae_r']}",
        "",
        "Recommendation for forward demo testing:",
        f"{knowledge['recommendation_for_forward_demo_testing']}",
        "",
        f"Generated at: {knowledge['generated_at']}",
    ]
    KNOWLEDGE_LATEST_TXT_PATH.write_text("\n".join(txt_lines) + "\n", encoding="utf-8")
    print(f"[bootstrap] Generated knowledge file: {KNOWLEDGE_LATEST_PATH}")
    return knowledge


def load_knowledge_latest() -> dict[str, Any]:
    if KNOWLEDGE_LATEST_PATH.exists():
        return json.loads(KNOWLEDGE_LATEST_PATH.read_text(encoding="utf-8"))
    return _build_knowledge_from_latest_backtest()


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

