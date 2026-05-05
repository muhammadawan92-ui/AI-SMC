"""
daily_ai_review.py

Creates a learning/review report from the AI trade journal.

Place this file in:
C:/Users/osama/cursor project/ea-ai-platform/backend/daily_ai_review.py

Run:
python daily_ai_review.py

Optional:
python daily_ai_review.py --days 14
"""

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from trade_journal import get_recent_rows, init_db


BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(BACKEND_DIR / ".env")


def safe_float(value, default=0.0):
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        return default


def group_stats(rows, key):
    grouped = defaultdict(list)
    for row in rows:
        grouped[str(row[key] or "unknown")].append(row)

    output = {}

    for name, group in grouped.items():
        closed = [r for r in group if str(r["status"]).startswith("CLOSED")]
        wins = [r for r in closed if r["status"] == "CLOSED_WIN"]
        losses = [r for r in closed if r["status"] == "CLOSED_LOSS"]

        profit = sum(safe_float(r["profit"]) for r in group)
        avg_mfe = sum(safe_float(r["max_favorable_r"]) for r in group) / len(group) if group else 0
        avg_mae = sum(safe_float(r["max_adverse_r"]) for r in group) / len(group) if group else 0

        output[name] = {
            "setups": len(group),
            "closed": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_closed_percent": round((len(wins) / len(closed)) * 100, 2) if closed else None,
            "profit": round(profit, 2),
            "avg_mfe_r": round(avg_mfe, 2),
            "avg_mae_r": round(avg_mae, 2),
        }

    return output


def make_recommendations(rows):
    recommendations = []

    closed = [r for r in rows if str(r["status"]).startswith("CLOSED")]

    if len(closed) < 10:
        recommendations.append(
            "Do not change core strategy yet. Fewer than 10 closed AI trades are available."
        )

    if rows:
        avg_mfe = sum(safe_float(r["max_favorable_r"]) for r in rows) / len(rows)
        avg_mae = sum(safe_float(r["max_adverse_r"]) for r in rows) / len(rows)

        if avg_mfe >= 2.0 and avg_mfe < 4.0:
            recommendations.append(
                "Many trades may be reaching profit before RR 4.0. Review whether partial profit at 2R should be tested in shadow mode."
            )

        if avg_mae < -0.8:
            recommendations.append(
                "Trades are experiencing deep drawdown. Review OB quality, entry timing, or increase confirmation requirements."
            )

    by_decision = group_stats(rows, "decision")

    for decision, stats in by_decision.items():
        if stats["closed"] >= 5 and stats["win_rate_closed_percent"] is not None:
            if stats["win_rate_closed_percent"] < 35:
                recommendations.append(
                    f"{decision}: weak performance after at least 5 closed trades. Test stricter filters before allowing more of this setup type."
                )
            elif stats["win_rate_closed_percent"] > 60:
                recommendations.append(
                    f"{decision}: performing well so far. Keep enabled but do not increase risk."
                )

    if not recommendations:
        recommendations.append("No strong parameter change recommendation yet. Continue collecting data.")

    return recommendations


def build_report(days: int):
    init_db()
    rows = list(get_recent_rows(days=days))

    placed = [r for r in rows if r["status"] not in ["SETUP_SKIPPED", "DRY_RUN"]]
    skipped = [r for r in rows if r["status"] == "SETUP_SKIPPED"]
    dry_runs = [r for r in rows if r["status"] == "DRY_RUN"]
    active = [r for r in rows if r["status"] in ["ORDER_PLACED", "PENDING_ACTIVE", "FILLED_OPEN", "ORDER_UNKNOWN"]]
    closed = [r for r in rows if str(r["status"]).startswith("CLOSED")]
    wins = [r for r in closed if r["status"] == "CLOSED_WIN"]
    losses = [r for r in closed if r["status"] == "CLOSED_LOSS"]

    total_profit = sum(safe_float(r["profit"]) for r in rows)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "period_days": days,
        "summary": {
            "journal_rows": len(rows),
            "orders_or_attempts_placed": len(placed),
            "skipped_setups": len(skipped),
            "dry_runs": len(dry_runs),
            "active_or_unknown": len(active),
            "closed": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_closed_percent": round((len(wins) / len(closed)) * 100, 2) if closed else None,
            "net_profit": round(total_profit, 2),
        },
        "by_decision": group_stats(rows, "decision"),
        "by_trade_mode": group_stats(rows, "trade_mode"),
        "by_ob_timeframe": group_stats(rows, "ob_timeframe"),
        "recommendations": make_recommendations(rows),
    }

    return report


def report_to_text(report):
    lines = []
    s = report["summary"]

    lines.append("===== AI SMC LEARNING REPORT =====")
    lines.append(f"Generated at: {report['generated_at']}")
    lines.append(f"Period: last {report['period_days']} day(s)")
    lines.append("")
    lines.append("===== SUMMARY =====")
    lines.append(f"Journal rows: {s['journal_rows']}")
    lines.append(f"Orders/attempts placed: {s['orders_or_attempts_placed']}")
    lines.append(f"Skipped setups: {s['skipped_setups']}")
    lines.append(f"Dry runs: {s['dry_runs']}")
    lines.append(f"Active/unknown: {s['active_or_unknown']}")
    lines.append(f"Closed: {s['closed']}")
    lines.append(f"Wins: {s['wins']}")
    lines.append(f"Losses: {s['losses']}")
    lines.append(f"Win rate on closed trades: {s['win_rate_closed_percent']}")
    lines.append(f"Net profit: {s['net_profit']}")
    lines.append("")

    for section_name, section_key in [
        ("BY DECISION", "by_decision"),
        ("BY TRADE MODE", "by_trade_mode"),
        ("BY OB TIMEFRAME", "by_ob_timeframe"),
    ]:
        lines.append(f"===== {section_name} =====")
        for name, stats in report[section_key].items():
            lines.append(
                f"{name}: setups={stats['setups']}, closed={stats['closed']}, "
                f"wins={stats['wins']}, losses={stats['losses']}, "
                f"win_rate={stats['win_rate_closed_percent']}, "
                f"profit={stats['profit']}, avg_mfe_r={stats['avg_mfe_r']}, avg_mae_r={stats['avg_mae_r']}"
            )
        lines.append("")

    lines.append("===== RECOMMENDATIONS =====")
    for rec in report["recommendations"]:
        lines.append(f"- {rec}")

    lines.append("")
    lines.append("Rule: recommendations are advisory only. Do not auto-change live strategy without backtest or shadow test.")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1, help="How many recent days to review.")
    args = parser.parse_args()

    report = build_report(days=args.days)

    reports_dir = BACKEND_DIR / "storage" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = reports_dir / f"ai_smc_learning_report_{stamp}.json"
    txt_path = reports_dir / f"ai_smc_learning_report_{stamp}.txt"

    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    txt_path.write_text(report_to_text(report), encoding="utf-8")

    print(report_to_text(report))
    print("")
    print(f"JSON report saved: {json_path}")
    print(f"Text report saved: {txt_path}")


if __name__ == "__main__":
    main()
