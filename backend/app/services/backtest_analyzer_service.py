from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np

from sqlalchemy.orm import Session

from app.models.models import BacktestReport, Trade, UploadedFile
from app.services.llm_service import get_llm_service
from app.services.file_ingestion_service import read_text_file

logger = logging.getLogger(__name__)

SESSION_HOURS = {
    "asian": (0, 9),
    "london": (7, 16),
    "new_york": (12, 21),
    "overlap": (12, 16),
}


def parse_backtest_report(
    file_record: UploadedFile,
    db: Session,
    project_id: str,
    label: str = "baseline",
    is_baseline: bool = False,
    run_llm: bool = True,
) -> BacktestReport:
    content = read_text_file(file_record)
    ext = file_record.file_name.rsplit(".", 1)[-1].lower()

    if ext in ("htm", "html"):
        raw_metrics, trades_df = _parse_mt5_html_report(content)
    elif ext == "csv":
        raw_metrics, trades_df = _parse_csv_report(content)
    else:
        raw_metrics, trades_df = {}, pd.DataFrame()

    report = _build_report(raw_metrics, trades_df, project_id, file_record.id, label, is_baseline)
    db.add(report)
    db.commit()
    db.refresh(report)

    # Save individual trades
    if not trades_df.empty:
        _save_trades(trades_df, report.id, project_id, db)

    if run_llm:
        report.ai_summary = _llm_summarize(report)
        report.ai_failure_analysis = _llm_failure_analysis(report, trades_df)
        db.commit()

    return report


def _parse_mt5_html_report(html: str) -> tuple[dict, pd.DataFrame]:
    soup = BeautifulSoup(html, "lxml")
    metrics: dict[str, Any] = {}

    # MT5 HTML reports have specific table structures
    tables = soup.find_all("table")

    # Extract summary metrics from MT5 result tables.
    # MT5 often places multiple key/value metric pairs in one row.
    for table in tables:
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            texts = [c.get_text(strip=True) for c in cells]
            # Pair each "label:" cell with the next non-label cell.
            i = 0
            while i < len(texts):
                label = texts[i]
                if label.endswith(":"):
                    key = label.lower().replace(" ", "_").replace(":", "")
                    value = ""
                    j = i + 1
                    while j < len(texts):
                        candidate = texts[j]
                        if candidate.endswith(":"):
                            break
                        if candidate:
                            value = candidate
                            break
                        j += 1
                    if value:
                        metrics[key] = value
                    i = j if j > i else i + 1
                else:
                    i += 1

    # Extract trades table (usually last big table)
    trades_rows = []
    for table in tables:
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if any(h in headers for h in ["ticket", "deal", "time", "type", "profit"]):
            for row in table.find_all("tr")[1:]:
                cells = [c.get_text(strip=True) for c in row.find_all("td")]
                if len(cells) >= 6:
                    trades_rows.append(cells[:len(headers)])
            if trades_rows and headers:
                break

    trades_df = pd.DataFrame(trades_rows, columns=headers[:len(trades_rows[0])] if trades_rows else [])
    return metrics, trades_df


def _parse_csv_report(csv_content: str) -> tuple[dict, pd.DataFrame]:
    from io import StringIO
    metrics: dict[str, Any] = {}
    try:
        df = pd.read_csv(StringIO(csv_content))
        df.columns = [c.lower().strip() for c in df.columns]
        # Try to extract summary rows vs trade rows
        if "profit" in df.columns:
            return metrics, df
    except Exception as e:
        logger.error("CSV parse error: %s", e)
    return metrics, pd.DataFrame()


def _build_report(
    raw: dict,
    trades_df: pd.DataFrame,
    project_id: str,
    file_id: str,
    label: str,
    is_baseline: bool,
) -> BacktestReport:
    def _count_pct_from_metric(key: str, alt_keys: list[str] = []) -> tuple[Optional[int], Optional[float]]:
        for k in [key] + alt_keys:
            v = raw.get(k, "")
            if not v:
                continue
            s = str(v).replace("\xa0", " ").strip()
            count_match = re.search(r"(-?\d+)", s)
            pct_match = re.search(r"\(([-]?\d+(?:\.\d+)?)%\)", s)
            count = int(count_match.group(1)) if count_match else None
            pct = float(pct_match.group(1)) if pct_match else None
            return count, pct
        return None, None

    def _amount_and_pct(key: str, alt_keys: list[str] = []) -> tuple[Optional[float], Optional[float]]:
        for k in [key] + alt_keys:
            v = raw.get(k, "")
            if not v:
                continue
            s = str(v).replace("\xa0", " ").strip()
            # Typical MT5: "1 031.83 (10.32%)"
            amount_match = re.search(r"(-?\d[\d\s,]*\.?\d*)", s)
            pct_match = re.search(r"\(([-]?\d+(?:\.\d+)?)%\)", s)
            amount = None
            if amount_match:
                token = amount_match.group(1).replace(" ", "").replace(",", "")
                try:
                    amount = float(token)
                except ValueError:
                    amount = None
            pct = float(pct_match.group(1)) if pct_match else None
            return amount, pct
        return None, None

    def _float(key: str, alt_keys: list[str] = []) -> Optional[float]:
        for k in [key] + alt_keys:
            v = raw.get(k, "")
            if v:
                s = str(v).replace("\xa0", " ").strip()
                # Prefer percentage when key expects percent.
                if "pct" in key or "percent" in key or "drawdown" in key:
                    pct_match = re.search(r"(-?\d+(?:\.\d+)?)\s*%", s)
                    if pct_match:
                        try:
                            return float(pct_match.group(1))
                        except ValueError:
                            pass
                # Parse first numeric token (supports spaced thousand separators).
                num_match = re.search(r"-?\d[\d\s,]*\.?\d*", s)
                if num_match:
                    token = num_match.group(0).replace(" ", "").replace(",", "")
                    try:
                        return float(token)
                    except ValueError:
                        pass
        return None

    def _int(key: str, alt_keys: list[str] = []) -> Optional[int]:
        v = _float(key, alt_keys)
        return int(v) if v is not None else None

    net_profit = _float("net_profit", ["total_net_profit", "profit"])
    gross_profit = _float("gross_profit", ["total_gross_profit"])
    gross_loss = _float("gross_loss", ["total_gross_loss"])
    total_trades = _int("total_trades") or _int("trades")
    winning = _int("profit_trades", ["winning_trades"])
    losing = _int("loss_trades", ["losing_trades"])
    if winning is None:
        winning, win_rate_from_metric = _count_pct_from_metric(
            "profit_trades_(%_of_total)", ["profit_trades"]
        )
    else:
        win_rate_from_metric = None
    if losing is None:
        losing, _ = _count_pct_from_metric("loss_trades_(%_of_total)", ["loss_trades"])

    win_rate = None
    if winning is not None and total_trades and total_trades > 0:
        win_rate = (winning / total_trades) * 100
    if win_rate is None and win_rate_from_metric is not None:
        win_rate = win_rate_from_metric

    # Calculate from trades_df if raw metrics incomplete
    if not trades_df.empty and "profit" in trades_df.columns:
        trades_df["profit_num"] = pd.to_numeric(trades_df["profit"].astype(str).str.replace(",", ""), errors="coerce")
        profit_series = trades_df["profit_num"].dropna()
        if net_profit is None:
            net_profit = float(profit_series.sum())
        if total_trades is None:
            total_trades = len(profit_series)
        wins = profit_series[profit_series > 0]
        losses = profit_series[profit_series < 0]
        if winning is None:
            winning = len(wins)
        if losing is None:
            losing = len(losses)
        if win_rate is None and total_trades > 0:
            win_rate = (winning / total_trades) * 100
        avg_win = float(wins.mean()) if len(wins) > 0 else None
        avg_loss = float(losses.mean()) if len(losses) > 0 else None
        if gross_profit is None:
            gross_profit = float(wins.sum())
        if gross_loss is None:
            gross_loss = float(losses.sum())
    else:
        avg_win = _float("average_profit_trade", ["avg_win"])
        avg_loss = _float("average_loss_trade", ["avg_loss"])

    profit_factor = None
    if gross_profit and gross_loss and gross_loss != 0:
        profit_factor = abs(gross_profit / gross_loss)
    else:
        profit_factor = _float("profit_factor")

    expectancy = None
    if win_rate is not None and avg_win is not None and avg_loss is not None:
        wr = win_rate / 100
        expectancy = round((wr * avg_win) + ((1 - wr) * avg_loss), 4)
    if expectancy is None:
        expectancy = _float("expected_payoff")

    max_dd, max_dd_pct_from_max = _amount_and_pct(
        "balance_drawdown_maximal",
        ["equity_drawdown_maximal", "maximal_drawdown", "max_drawdown", "absolute_drawdown"],
    )
    rel_dd_amount, rel_dd_pct = _amount_and_pct(
        "balance_drawdown_relative",
        ["equity_drawdown_relative", "relative_drawdown", "max_drawdown_percent"],
    )
    max_dd_pct = rel_dd_pct if rel_dd_pct is not None else max_dd_pct_from_max
    if max_dd is None:
        max_dd = rel_dd_amount

    # Build monthly breakdown
    monthly = _calc_monthly_breakdown(trades_df)
    session_bd = _calc_session_breakdown(trades_df)
    dow_bd = _calc_day_of_week_breakdown(trades_df)
    hour_bd = _calc_hour_breakdown(trades_df)
    failure_zones = _identify_failure_zones(trades_df)
    long_t, short_t, long_wr, short_wr = _calc_direction_stats(trades_df)
    # Fill long/short stats from MT5 summary row when detailed trades are unavailable.
    if short_t is None:
        short_t, short_wr = _count_pct_from_metric("short_trades_(won_%)", ["short_trades"])
    if long_t is None:
        long_t, long_wr = _count_pct_from_metric("long_trades_(won_%)", ["long_trades"])

    # Sharpe-like ratio
    sharpe = _calc_sharpe(trades_df)
    recovery = None
    if net_profit and max_dd and max_dd != 0:
        recovery = abs(net_profit / max_dd)

    return BacktestReport(
        project_id=project_id,
        file_id=file_id,
        label=label,
        is_baseline=is_baseline,
        initial_deposit=_float("initial_deposit", ["balance"]),
        net_profit=net_profit,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=profit_factor,
        win_rate=win_rate,
        total_trades=total_trades,
        winning_trades=winning,
        losing_trades=losing,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy=expectancy,
        max_drawdown_usd=max_dd,
        max_drawdown_pct=max_dd_pct,
        relative_drawdown_pct=max_dd_pct,
        sharpe_ratio=sharpe,
        recovery_factor=recovery,
        max_consecutive_wins=_int("maximum_consecutive_wins"),
        max_consecutive_losses=_int("maximum_consecutive_losses"),
        long_trades=long_t,
        short_trades=short_t,
        long_win_rate=long_wr,
        short_win_rate=short_wr,
        monthly_breakdown=monthly,
        session_breakdown=session_bd,
        day_of_week_breakdown=dow_bd,
        hour_breakdown=hour_bd,
        failure_zones=failure_zones,
    )


def _calc_monthly_breakdown(trades_df: pd.DataFrame) -> dict:
    if trades_df.empty or "profit_num" not in trades_df.columns:
        return {}
    if "time" not in trades_df.columns and "open_time" not in trades_df.columns:
        return {}
    time_col = "time" if "time" in trades_df.columns else "open_time"
    try:
        trades_df["_dt"] = pd.to_datetime(trades_df[time_col], errors="coerce")
        trades_df["_month"] = trades_df["_dt"].dt.to_period("M").astype(str)
        monthly = trades_df.groupby("_month")["profit_num"].agg(
            profit="sum", trades="count", wins=lambda x: (x > 0).sum()
        ).reset_index()
        monthly["win_rate"] = (monthly["wins"] / monthly["trades"] * 100).round(1)
        return monthly.to_dict(orient="records")
    except Exception as e:
        logger.warning("Monthly breakdown failed: %s", e)
        return {}


def _calc_session_breakdown(trades_df: pd.DataFrame) -> dict:
    if trades_df.empty or "profit_num" not in trades_df.columns:
        return {}
    time_col = "time" if "time" in trades_df.columns else "open_time"
    if time_col not in trades_df.columns:
        return {}
    try:
        trades_df["_dt"] = pd.to_datetime(trades_df[time_col], errors="coerce")
        trades_df["_hour"] = trades_df["_dt"].dt.hour
        result = {}
        for session, (start, end) in SESSION_HOURS.items():
            mask = (trades_df["_hour"] >= start) & (trades_df["_hour"] < end)
            subset = trades_df[mask]["profit_num"].dropna()
            if len(subset) > 0:
                result[session] = {
                    "trades": len(subset),
                    "profit": round(float(subset.sum()), 2),
                    "win_rate": round(float((subset > 0).sum() / len(subset) * 100), 1),
                }
        return result
    except Exception as e:
        logger.warning("Session breakdown failed: %s", e)
        return {}


def _calc_day_of_week_breakdown(trades_df: pd.DataFrame) -> dict:
    if trades_df.empty or "profit_num" not in trades_df.columns:
        return {}
    time_col = "time" if "time" in trades_df.columns else "open_time"
    if time_col not in trades_df.columns:
        return {}
    try:
        trades_df["_dt"] = pd.to_datetime(trades_df[time_col], errors="coerce")
        trades_df["_dow"] = trades_df["_dt"].dt.day_name()
        result = {}
        for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
            subset = trades_df[trades_df["_dow"] == day]["profit_num"].dropna()
            result[day] = {
                "trades": len(subset),
                "profit": round(float(subset.sum()), 2) if len(subset) > 0 else 0,
                "win_rate": round(float((subset > 0).sum() / len(subset) * 100), 1) if len(subset) > 0 else 0,
            }
        return result
    except Exception as e:
        logger.warning("DOW breakdown failed: %s", e)
        return {}


def _calc_hour_breakdown(trades_df: pd.DataFrame) -> dict:
    if trades_df.empty or "profit_num" not in trades_df.columns:
        return {}
    time_col = "time" if "time" in trades_df.columns else "open_time"
    if time_col not in trades_df.columns:
        return {}
    try:
        trades_df["_dt"] = pd.to_datetime(trades_df[time_col], errors="coerce")
        trades_df["_hour"] = trades_df["_dt"].dt.hour
        result = {}
        for h in range(24):
            subset = trades_df[trades_df["_hour"] == h]["profit_num"].dropna()
            if len(subset) > 0:
                result[str(h)] = {
                    "trades": len(subset),
                    "profit": round(float(subset.sum()), 2),
                    "win_rate": round(float((subset > 0).sum() / len(subset) * 100), 1),
                }
        return result
    except Exception as e:
        logger.warning("Hour breakdown failed: %s", e)
        return {}


def _identify_failure_zones(trades_df: pd.DataFrame) -> list[dict]:
    if trades_df.empty or "profit_num" not in trades_df.columns:
        return []
    zones: list[dict] = []
    session_data = _calc_session_breakdown(trades_df)
    for session, data in session_data.items():
        if data.get("win_rate", 100) < 40:
            zones.append({"type": "session", "name": session, "win_rate": data["win_rate"], "severity": "high"})
    dow_data = _calc_day_of_week_breakdown(trades_df)
    for day, data in dow_data.items():
        if data.get("win_rate", 100) < 35 and data.get("trades", 0) >= 3:
            zones.append({"type": "day_of_week", "name": day, "win_rate": data["win_rate"], "severity": "medium"})
    return zones


def _calc_direction_stats(trades_df: pd.DataFrame) -> tuple:
    if trades_df.empty or "profit_num" not in trades_df.columns:
        return None, None, None, None
    type_col = "type" if "type" in trades_df.columns else None
    if not type_col:
        return None, None, None, None
    try:
        longs = trades_df[trades_df[type_col].str.lower().str.contains("buy", na=False)]["profit_num"].dropna()
        shorts = trades_df[trades_df[type_col].str.lower().str.contains("sell", na=False)]["profit_num"].dropna()
        long_wr = round(float((longs > 0).sum() / len(longs) * 100), 1) if len(longs) > 0 else None
        short_wr = round(float((shorts > 0).sum() / len(shorts) * 100), 1) if len(shorts) > 0 else None
        return len(longs), len(shorts), long_wr, short_wr
    except Exception:
        return None, None, None, None


def _calc_sharpe(trades_df: pd.DataFrame) -> Optional[float]:
    if trades_df.empty or "profit_num" not in trades_df.columns:
        return None
    profits = trades_df["profit_num"].dropna()
    if len(profits) < 10:
        return None
    mean_r = profits.mean()
    std_r = profits.std()
    if std_r == 0:
        return None
    return round(float(mean_r / std_r * np.sqrt(252)), 3)


def _save_trades(trades_df: pd.DataFrame, report_id: str, project_id: str, db: Session) -> None:
    if trades_df.empty:
        return
    saved = 0
    for _, row in trades_df.iterrows():
        try:
            profit = row.get("profit_num", None)
            if profit is None:
                continue
            t = Trade(
                backtest_report_id=report_id,
                project_id=project_id,
                ticket=str(row.get("ticket", row.get("deal", ""))),
                direction="buy" if "buy" in str(row.get("type", "")).lower() else "sell",
                profit=float(profit) if profit is not None else None,
                trade_source="backtest",
            )
            db.add(t)
            saved += 1
        except Exception as e:
            logger.debug("Skip trade row: %s", e)
    db.commit()
    logger.info("Saved %d trades for report %s", saved, report_id)


def _llm_summarize(report: BacktestReport) -> str:
    llm = get_llm_service()
    metrics = {
        "net_profit": report.net_profit,
        "profit_factor": report.profit_factor,
        "win_rate": report.win_rate,
        "total_trades": report.total_trades,
        "max_drawdown_pct": report.max_drawdown_pct,
        "sharpe_ratio": report.sharpe_ratio,
        "recovery_factor": report.recovery_factor,
        "avg_win": report.avg_win,
        "avg_loss": report.avg_loss,
        "expectancy": report.expectancy,
        "long_win_rate": report.long_win_rate,
        "short_win_rate": report.short_win_rate,
        "monthly_breakdown": report.monthly_breakdown,
    }
    prompt = f"""Analyze this backtest report for a Smart Money Concepts (SMC) Expert Advisor:

{metrics}

Provide:
1. Overall performance assessment
2. Strengths of this EA
3. Weaknesses and risks
4. Best performing conditions (sessions, months, direction)
5. Worst performing conditions
6. Risk-adjusted performance evaluation
7. Robustness assessment
8. Priority improvement areas (top 3)

Be specific with numbers. Reference the metrics provided."""
    try:
        return llm.complete(prompt)
    except Exception as e:
        return f"LLM summary failed: {e}"


def _llm_failure_analysis(report: BacktestReport, trades_df: pd.DataFrame) -> str:
    llm = get_llm_service()
    failure_zones = report.failure_zones or []
    session_bd = report.session_breakdown or {}
    monthly = report.monthly_breakdown or {}

    prompt = f"""Perform a detailed failure analysis for this SMC EA backtest:

Failure zones detected: {failure_zones}
Session performance: {session_bd}
Monthly breakdown: {monthly}
Win rate: {report.win_rate}%
Max drawdown: {report.max_drawdown_pct}%
Long win rate: {report.long_win_rate}%
Short win rate: {report.short_win_rate}%

Identify:
1. Primary failure patterns (with specific times/conditions)
2. Where EA loses money most consistently
3. Where EA misses valid trade opportunities
4. Whether losses are random or pattern-based
5. Whether buy or sell trades underperform and why
6. Which months had losses and likely market conditions
7. Session-based weaknesses
8. Structural improvements that would address the main failures

Focus on SMC logic — where might the EA's SMC implementation be causing missed or bad trades?"""
    try:
        return llm.complete(prompt)
    except Exception as e:
        return f"Failure analysis failed: {e}"


def compute_baseline_metrics(report: BacktestReport) -> dict:
    return {
        "net_profit": report.net_profit,
        "profit_factor": report.profit_factor,
        "win_rate": report.win_rate,
        "total_trades": report.total_trades,
        "avg_win": report.avg_win,
        "avg_loss": report.avg_loss,
        "expectancy": report.expectancy,
        "max_drawdown_pct": report.max_drawdown_pct,
        "sharpe_ratio": report.sharpe_ratio,
        "recovery_factor": report.recovery_factor,
        "long_win_rate": report.long_win_rate,
        "short_win_rate": report.short_win_rate,
    }
