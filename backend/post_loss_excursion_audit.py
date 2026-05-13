"""
Post stop-loss excursion audit: for losing backtest trades, measure how far price
moved favorably after the SL time to classify poor entry/SL vs wrong bias.
"""

from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# Optional: same overrides as backtest
MANUAL_BARS_COLUMN_MAP: dict[str, str] = {}

DEFAULT_BARS_CSV = (
    r"C:\Users\osama\OneDrive\New folder\trading strateges\AI GENRATED\GBPUSD OHLC DATA\GBPUSD_mt5_bars.csv"
)

HORIZONS_H = (1, 3, 6, 12, 24)


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
    df = pd.read_csv(path)
    cols = [str(c) for c in df.columns]
    if not looks_like_data_header(cols):
        return df
    raw = pd.read_csv(path, header=None)
    col_count = raw.shape[1]
    if expected_kind == "bars":
        defaults = ["time_date", "time_clock", "open", "high", "low", "close", "tick_volume", "volume", "spread"]
    else:
        defaults = ["time_date", "time_clock", "bid", "ask", "last", "volume", "flags"]
    names = defaults[:col_count] + [f"col_{i}" for i in range(len(defaults), col_count)]
    raw.columns = names[:col_count]
    print(f"Detected headerless {expected_kind} CSV; applied fallback columns: {list(raw.columns)}")
    return raw


def map_bars_columns(df: pd.DataFrame) -> dict[str, str]:
    columns = list(df.columns)
    print(f"Bars CSV columns detected: {columns}")
    mapping = dict(MANUAL_BARS_COLUMN_MAP)
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
        raise ValueError(f"Could not detect required bars columns: {missing}. Edit MANUAL_BARS_COLUMN_MAP.")
    print(f"Bars column mapping: {mapping}")
    return mapping


def ensure_single_time_column(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    if mapping.get("time") == "time_date" and "time_clock" in df.columns:
        combined = df["time_date"].astype(str).str.strip() + " " + df["time_clock"].astype(str).str.strip()
        df = df.copy()
        df["time_date"] = combined
    return df


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


def find_latest_trades_csv(backtests_dir: Path) -> Path:
    files = sorted(backtests_dir.glob("backtest_trades_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No backtest_trades_*.csv under {backtests_dir}")
    return files[0]


def _mfe_tp_for_window(
    is_buy: bool,
    entry: float,
    risk: float,
    tp: float,
    highs: np.ndarray,
    lows: np.ndarray,
    i0: int,
    i1: int,
) -> tuple[float, bool]:
    """MFE in R units and whether TP was touched in [i0, i1)."""
    if i1 <= i0 or risk <= 0:
        return float("nan"), False
    if is_buy:
        mx = float(np.max(highs[i0:i1]))
        mfe_r = (mx - entry) / risk
        reached_tp = mx >= tp
    else:
        mn = float(np.min(lows[i0:i1]))
        mfe_r = (entry - mn) / risk
        reached_tp = mn <= tp
    return mfe_r, reached_tp


def audit_losses(
    losses: pd.DataFrame,
    bar_times_ns: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
) -> pd.DataFrame:
    """For each loss, scan bars after close_time (SL) through 1h–24h windows."""
    out = losses.copy()
    n = len(out)
    for h in HORIZONS_H:
        out[f"post_sl_mfe_{h}h"] = np.nan
    for c in [
        "post_sl_reached_1r_6h",
        "post_sl_reached_2r_6h",
        "post_sl_reached_3r_6h",
        "post_sl_reached_tp_6h",
        "post_sl_reached_1r_24h",
        "post_sl_reached_2r_24h",
        "post_sl_reached_3r_24h",
        "post_sl_reached_tp_24h",
    ]:
        out[c] = False
    out["loss_type"] = ""

    loc = {c: out.columns.get_loc(c) for c in out.columns}

    for idx in range(n):
        row = out.iloc[idx]
        t_sl = pd.Timestamp(row["close_time"])
        if pd.isna(t_sl):
            continue
        try:
            entry = float(row["entry"])
            sl = float(row["stop_loss"])
            tp = float(row["take_profit"])
        except (TypeError, ValueError):
            continue
        risk = abs(entry - sl)
        if risk <= 0 or np.isnan(risk):
            continue
        d = str(row.get("direction", "")).strip().lower()
        is_buy = d == "buy"

        t_sl_ns = np.datetime64(t_sl.to_datetime64())
        i_after = int(np.searchsorted(bar_times_ns, t_sl_ns, side="right"))

        mfe_6 = np.nan
        tp_6 = False
        mfe_24 = np.nan
        tp_24 = False

        for h in HORIZONS_H:
            t_end_ns = np.datetime64((t_sl + timedelta(hours=h)).to_datetime64())
            i_end = int(np.searchsorted(bar_times_ns, t_end_ns, side="right"))
            mfe_r, tp_hit = _mfe_tp_for_window(is_buy, entry, risk, tp, highs, lows, i_after, i_end)
            out.iat[idx, loc[f"post_sl_mfe_{h}h"]] = mfe_r
            if h == 6:
                mfe_6, tp_6 = mfe_r, tp_hit
            elif h == 24:
                mfe_24, tp_24 = mfe_r, tp_hit

        if not np.isnan(mfe_6):
            out.iat[idx, loc["post_sl_reached_1r_6h"]] = mfe_6 >= 1.0
            out.iat[idx, loc["post_sl_reached_2r_6h"]] = mfe_6 >= 2.0
            out.iat[idx, loc["post_sl_reached_3r_6h"]] = mfe_6 >= 3.0
        out.iat[idx, loc["post_sl_reached_tp_6h"]] = bool(tp_6)

        if not np.isnan(mfe_24):
            out.iat[idx, loc["post_sl_reached_1r_24h"]] = mfe_24 >= 1.0
            out.iat[idx, loc["post_sl_reached_2r_24h"]] = mfe_24 >= 2.0
            out.iat[idx, loc["post_sl_reached_3r_24h"]] = mfe_24 >= 3.0
        out.iat[idx, loc["post_sl_reached_tp_24h"]] = bool(tp_24)

        r2_24 = bool(out.iat[idx, loc["post_sl_reached_2r_24h"]])
        if r2_24:
            lt = "RIGHT_BIAS_BAD_ENTRY_OR_SL"
        elif not np.isnan(mfe_24) and mfe_24 >= 1.0:
            lt = "PARTIAL_RIGHT_BIAS"
        else:
            lt = "LIKELY_WRONG_BIAS_OR_WEAK_FOLLOWTHROUGH"
        out.iat[idx, loc["loss_type"]] = lt

    return out


def pct(x: float, n: int) -> float:
    return round(100.0 * x / n, 2) if n else 0.0


def _pct_series(s: pd.Series) -> float:
    s = s.fillna(False).astype(bool)
    return pct(float(s.sum()), int(s.shape[0]))


def group_summary(df: pd.DataFrame, key: str) -> pd.DataFrame:
    """Per-group post-SL excursion rates for losing trades."""
    if key not in df.columns:
        return pd.DataFrame()
    g = df.groupby(key, dropna=False)
    out = g.agg(
        n_losses=("trade_id", "count"),
        pct_1r_6h=("post_sl_reached_1r_6h", _pct_series),
        pct_2r_6h=("post_sl_reached_2r_6h", _pct_series),
        pct_3r_6h=("post_sl_reached_3r_6h", _pct_series),
        pct_tp_6h=("post_sl_reached_tp_6h", _pct_series),
        pct_1r_24h=("post_sl_reached_1r_24h", _pct_series),
        pct_2r_24h=("post_sl_reached_2r_24h", _pct_series),
        pct_3r_24h=("post_sl_reached_3r_24h", _pct_series),
        pct_tp_24h=("post_sl_reached_tp_24h", _pct_series),
    )
    return out.sort_values("n_losses", ascending=False)


def recommendation_text(audited: pd.DataFrame, n_loss: int) -> str:
    if n_loss == 0:
        return "No losses to audit."

    def rate(mask: pd.Series) -> float:
        m = mask.fillna(False)
        return float(m.sum()) / n_loss if n_loss else 0.0

    overall_2r = rate(audited["post_sl_reached_2r_24h"])
    overall_tp = rate(audited["post_sl_reached_tp_24h"])
    rarely_moves = overall_2r < 0.15 and overall_tp < 0.10

    m5 = audited[audited["ob_timeframe"].astype(str).str.upper() == "M5"]
    m15_h1 = audited[audited["ob_timeframe"].astype(str).str.upper().isin(["M15", "H1"])]
    m5_2r = float(m5["post_sl_reached_2r_24h"].sum()) / len(m5) if len(m5) else 0.0
    m15_2r = float(m15_h1["post_sl_reached_2r_24h"].sum()) / len(m15_h1) if len(m15_h1) else 0.0

    h1_ctx = audited[
        (audited["inside_h1_ob"].astype(str).str.lower().isin(["true", "1", "yes"]))
        | (audited["stop_source"].astype(str).str.lower().str.contains("h1", na=False))
    ]
    h1_2r = float(h1_ctx["post_sl_reached_2r_24h"].sum()) / len(h1_ctx) if len(h1_ctx) else 0.0
    non_h1 = audited[~audited.index.isin(h1_ctx.index)]
    non_h1_2r = float(non_h1["post_sl_reached_2r_24h"].sum()) / len(non_h1) if len(non_h1) else 0.0

    lines = []
    if len(m5) and m5_2r >= 0.25 and (m5_2r > m15_2r + 0.05 or len(m15_h1) == 0):
        lines.append(
            "Many M5-tagged losses later reached +2R or approached TP within 24h after the stop - "
            "consider refining entries with M15/H1 OB context or widening the effective OB zone "
            "so stops are not clipped by microstructure."
        )
    if len(h1_ctx) and h1_2r >= 0.25 and h1_2r > non_h1_2r + 0.05:
        lines.append(
            "Losses that already used H1-aware stops still often reached +2R/TP after the stop - "
            "review H1 OB placement or allow a wider H1-related stop buffer if your model permits."
        )
    if rarely_moves:
        lines.append(
            "Most losses show little favorable excursion after the stop within 24h - "
            "the directional bias or entry filters may be misaligned with follow-through; "
            "prioritize signal quality and regime filters over stop width alone."
        )
    if not lines:
        lines.append(
            "Mixed post-stop behavior: use the breakdowns by decision, OB timeframe, and stop_source "
            "to decide whether to tune LTF entries, HTF context, or bias rules."
        )
    return " ".join(lines)


def main() -> None:
    backend_dir = Path(__file__).resolve().parent
    default_backtests = backend_dir / "storage" / "backtests"
    default_reports = backend_dir / "storage" / "reports"

    parser = argparse.ArgumentParser(description="Audit post-stop-loss excursion for backtest losses.")
    parser.add_argument("--trades-csv", type=str, default="", help="Override trades CSV path.")
    parser.add_argument("--bars-csv", type=str, default=DEFAULT_BARS_CSV, help="Bars CSV path.")
    parser.add_argument(
        "--reports-subdir",
        type=str,
        default="",
        help="Optional subdirectory under storage/reports (e.g. entry_refinement_v2_analysis).",
    )
    parser.add_argument(
        "--output-prefix",
        type=str,
        default="post_loss_excursion_audit",
        help="Filename prefix before timestamp (e.g. post_loss_excursion_audit_entry_refinement_v2).",
    )
    args = parser.parse_args()

    if args.reports_subdir.strip():
        default_reports = backend_dir / "storage" / "reports" / args.reports_subdir.strip()
    default_reports.mkdir(parents=True, exist_ok=True)

    trades_path = Path(args.trades_csv) if args.trades_csv.strip() else find_latest_trades_csv(default_backtests)
    bars_path = Path(args.bars_csv)
    print(f"Trades: {trades_path}")
    print(f"Bars:   {bars_path}")

    trades = pd.read_csv(trades_path)
    if "result" not in trades.columns:
        raise ValueError("Trades CSV missing 'result' column.")
    losses = trades[trades["result"].astype(str).str.upper() == "LOSS"].copy()
    losses["close_time"] = pd.to_datetime(losses["close_time"], errors="coerce")
    for col in (
        "entry_model",
        "execution_style",
        "h1_ob_context",
        "entry_status",
        "stop_source",
        "inside_h1_ob",
        "ob_timeframe",
        "trade_mode",
        "decision",
    ):
        if col not in losses.columns:
            losses[col] = ""
        else:
            losses[col] = losses[col].fillna("").astype(str)

    if not bars_path.exists():
        raise FileNotFoundError(f"Bars CSV not found: {bars_path}")

    bars_raw = read_csv_smart(str(bars_path), "bars")
    bars_map = map_bars_columns(bars_raw)
    bars_raw = ensure_single_time_column(bars_raw, bars_map)
    bars = normalize_bars(bars_raw, bars_map)
    bar_times_ns = bars["time"].values.astype("datetime64[ns]")
    highs = bars["high"].to_numpy(dtype=np.float64)
    lows = bars["low"].to_numpy(dtype=np.float64)

    audited = audit_losses(losses, bar_times_ns, highs, lows)

    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    prefix = args.output_prefix.strip() or "post_loss_excursion_audit"
    csv_out = default_reports / f"{prefix}_{ts}.csv"
    txt_out = default_reports / f"{prefix}_{ts}.txt"
    audited.to_csv(csv_out, index=False)

    n_loss = len(audited)
    valid_mfe = audited["post_sl_mfe_24h"].notna()
    n_valid = int(valid_mfe.sum())

    lines_txt = [
        f"post_loss_excursion_audit generated {pd.Timestamp.now()}",
        f"trades_csv: {trades_path}",
        f"bars_csv: {bars_path}",
        f"total_losses_audited: {n_loss}",
        f"losses_with_valid_24h_window: {n_valid}",
        "",
        "Percent of losses (all audited loss rows) reaching after SL close_time:",
        f"  1R within 6h:  {pct(float(audited['post_sl_reached_1r_6h'].sum()), n_loss)}%",
        f"  2R within 6h:  {pct(float(audited['post_sl_reached_2r_6h'].sum()), n_loss)}%",
        f"  3R within 6h:  {pct(float(audited['post_sl_reached_3r_6h'].sum()), n_loss)}%",
        f"  Original TP within 6h:  {pct(float(audited['post_sl_reached_tp_6h'].sum()), n_loss)}%",
        f"  1R within 24h: {pct(float(audited['post_sl_reached_1r_24h'].sum()), n_loss)}%",
        f"  2R within 24h: {pct(float(audited['post_sl_reached_2r_24h'].sum()), n_loss)}%",
        f"  3R within 24h: {pct(float(audited['post_sl_reached_3r_24h'].sum()), n_loss)}%",
        f"  Original TP within 24h: {pct(float(audited['post_sl_reached_tp_24h'].sum()), n_loss)}%",
        "",
        "loss_type counts:",
    ]
    for lt, c in audited["loss_type"].value_counts().items():
        lines_txt.append(f"  {lt}: {c}")

    for title, col in [
        ("By decision", "decision"),
        ("By trade_mode", "trade_mode"),
        ("By ob_timeframe", "ob_timeframe"),
        ("By entry_model", "entry_model"),
        ("By execution_style", "execution_style"),
        ("By stop_source", "stop_source"),
        ("By inside_h1_ob", "inside_h1_ob"),
        ("By h1_ob_context", "h1_ob_context"),
        ("By entry_status", "entry_status"),
    ]:
        tbl = group_summary(audited, col)
        lines_txt.extend([f"{title}:", tbl.to_string() if not tbl.empty else "  (column missing or no data)", ""])

    lines_txt.extend(["Recommendation:", recommendation_text(audited, n_loss)])

    txt_body = "\n".join(lines_txt)
    txt_out.write_text(txt_body, encoding="utf-8")

    print("\n===== POST-LOSS EXCURSION AUDIT =====")
    print(f"total_losses_audited: {n_loss}")
    print(f"Saved: {csv_out}")
    print(f"Saved: {txt_out}")
    print(
        f"% 2R@24h: {pct(float(audited['post_sl_reached_2r_24h'].sum()), n_loss)}% | "
        f"% TP@24h: {pct(float(audited['post_sl_reached_tp_24h'].sum()), n_loss)}%"
    )
    print("\nRecommendation:")
    print(recommendation_text(audited, n_loss))


if __name__ == "__main__":
    main()
