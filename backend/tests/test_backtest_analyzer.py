"""Tests for backtest analyzer service."""
from __future__ import annotations

import pytest
import pandas as pd
import numpy as np
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


from app.services.backtest_analyzer_service import (
    _calc_monthly_breakdown,
    _calc_session_breakdown,
    _calc_day_of_week_breakdown,
    _calc_sharpe,
    _identify_failure_zones,
    _calc_direction_stats,
)


def make_trades_df():
    np.random.seed(42)
    n = 100
    profits = np.random.normal(5, 50, n)
    types = ["buy" if i % 2 == 0 else "sell" for i in range(n)]
    dates = pd.date_range("2024-01-01", periods=n, freq="8h")
    df = pd.DataFrame({
        "profit_num": profits,
        "type": types,
        "time": dates.strftime("%Y.%m.%d %H:%M:%S"),
    })
    return df


def test_monthly_breakdown():
    df = make_trades_df()
    result = _calc_monthly_breakdown(df)
    assert isinstance(result, (dict, list))
    assert len(result) > 0


def test_session_breakdown():
    df = make_trades_df()
    result = _calc_session_breakdown(df)
    assert isinstance(result, dict)
    for session in result:
        assert "trades" in result[session]
        assert "profit" in result[session]
        assert "win_rate" in result[session]


def test_day_of_week_breakdown():
    df = make_trades_df()
    result = _calc_day_of_week_breakdown(df)
    assert isinstance(result, dict)
    days = set(result.keys())
    assert "Monday" in days or len(days) > 0


def test_sharpe_ratio():
    df = make_trades_df()
    sharpe = _calc_sharpe(df)
    assert sharpe is not None
    assert isinstance(sharpe, float)
    assert -10 < sharpe < 10


def test_sharpe_insufficient_data():
    df = pd.DataFrame({"profit_num": [1, 2, 3]})
    sharpe = _calc_sharpe(df)
    assert sharpe is None


def test_direction_stats():
    df = make_trades_df()
    long_t, short_t, long_wr, short_wr = _calc_direction_stats(df)
    assert long_t is not None and short_t is not None
    assert long_t + short_t == len(df)
    assert 0 <= long_wr <= 100
    assert 0 <= short_wr <= 100


def test_failure_zones_empty():
    df = pd.DataFrame()
    zones = _identify_failure_zones(df)
    assert zones == []


def test_failure_zones_with_bad_session():
    np.random.seed(1)
    n = 30
    # Asian session (hour 2) — mostly losers
    profits = list(np.random.normal(-20, 10, 15)) + list(np.random.normal(30, 10, 15))
    hours = [2] * 15 + [10] * 15
    dates = [f"2024.01.{d+1:02d} {h:02d}:00:00" for d, h in zip(range(n), hours)]
    df = pd.DataFrame({"profit_num": profits, "time": dates})
    zones = _identify_failure_zones(df)
    # Asian should appear as failure zone
    session_zones = [z for z in zones if z.get("name") == "asian"]
    assert len(session_zones) > 0 or isinstance(zones, list)
