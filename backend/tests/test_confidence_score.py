"""Tests for confidence score service."""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import MagicMock

from app.services.confidence_score_service import (
    _score_improvement,
    _score_drawdown,
    _score_profit_factor,
    _score_trade_count,
    _score_monthly_robustness,
    _score_direction_balance,
    _determine_readiness,
    _score_overfit,
    READINESS_THRESHOLDS,
)


def make_report(**kwargs):
    r = MagicMock()
    r.net_profit = kwargs.get("net_profit", 1000.0)
    r.profit_factor = kwargs.get("profit_factor", 1.5)
    r.win_rate = kwargs.get("win_rate", 55.0)
    r.total_trades = kwargs.get("total_trades", 80)
    r.max_drawdown_pct = kwargs.get("max_drawdown_pct", 8.0)
    r.expectancy = kwargs.get("expectancy", 12.5)
    r.long_win_rate = kwargs.get("long_win_rate", 57.0)
    r.short_win_rate = kwargs.get("short_win_rate", 53.0)
    r.monthly_breakdown = kwargs.get("monthly_breakdown", [
        {"_month": "2024-01", "profit": 150, "trades": 8, "win_rate": 62.5},
        {"_month": "2024-02", "profit": 80, "trades": 6, "win_rate": 50.0},
        {"_month": "2024-03", "profit": -40, "trades": 7, "win_rate": 42.9},
        {"_month": "2024-04", "profit": 200, "trades": 9, "win_rate": 66.7},
        {"_month": "2024-05", "profit": 120, "trades": 8, "win_rate": 62.5},
        {"_month": "2024-06", "profit": 90, "trades": 7, "win_rate": 57.1},
    ])
    r.session_breakdown = kwargs.get("session_breakdown", {
        "london": {"profit": 600, "trades": 40, "win_rate": 60},
        "new_york": {"profit": 400, "trades": 35, "win_rate": 54},
        "overlap": {"profit": 150, "trades": 15, "win_rate": 60},
    })
    return r


class TestScoreImprovement:
    def test_improved_profit(self):
        baseline = make_report(net_profit=1000)
        improved = make_report(net_profit=1250)
        score = _score_improvement(baseline, improved)
        assert score > 50

    def test_regression(self):
        baseline = make_report(net_profit=1000)
        improved = make_report(net_profit=800)
        score = _score_improvement(baseline, improved)
        assert score <= 50

    def test_no_data(self):
        baseline = make_report(net_profit=None)
        improved = make_report(net_profit=None)
        score = _score_improvement(baseline, improved)
        assert 0 <= score <= 100


class TestScoreDrawdown:
    def test_good_drawdown(self):
        baseline = make_report(max_drawdown_pct=8)
        improved = make_report(max_drawdown_pct=6)
        score = _score_drawdown(baseline, improved)
        assert score >= 80

    def test_bad_drawdown_increase(self):
        baseline = make_report(max_drawdown_pct=8)
        improved = make_report(max_drawdown_pct=15)
        score = _score_drawdown(baseline, improved)
        assert score < 60

    def test_very_high_drawdown(self):
        score = _score_drawdown(make_report(), make_report(max_drawdown_pct=25))
        assert score < 40


class TestScoreProfitFactor:
    def test_excellent_pf(self):
        score = _score_profit_factor(make_report(), make_report(profit_factor=2.5))
        assert score >= 90

    def test_good_pf(self):
        score = _score_profit_factor(make_report(), make_report(profit_factor=1.5))
        assert score >= 70

    def test_poor_pf(self):
        score = _score_profit_factor(make_report(), make_report(profit_factor=1.05))
        assert score < 50


class TestScoreTradeCount:
    def test_sufficient_trades(self):
        assert _score_trade_count(make_report(total_trades=100)) == 100.0
        assert _score_trade_count(make_report(total_trades=60)) == 85.0
        assert _score_trade_count(make_report(total_trades=40)) == 70.0

    def test_insufficient_trades(self):
        assert _score_trade_count(make_report(total_trades=5)) == 10.0


class TestScoreMonthlyRobustness:
    def test_mostly_profitable(self):
        report = make_report()  # 5/6 months profitable
        score = _score_monthly_robustness(report)
        assert score >= 75

    def test_half_profitable(self):
        report = make_report(monthly_breakdown=[
            {"profit": 100}, {"profit": -50}, {"profit": 80}, {"profit": -30}
        ])
        score = _score_monthly_robustness(report)
        assert 40 <= score <= 70

    def test_no_monthly_data(self):
        report = make_report(monthly_breakdown=None)
        score = _score_monthly_robustness(report)
        assert score == 50.0


class TestReadiness:
    def test_thresholds(self):
        assert _determine_readiness(95) == "live_ready"
        assert _determine_readiness(87) == "live_candidate"
        assert _determine_readiness(77) == "demo_testing"
        assert _determine_readiness(67) == "demo_candidate"
        assert _determine_readiness(40) == "research"


class TestOverfitScore:
    def test_overfit_detected(self):
        comparison = MagicMock()
        comparison.overfit_detected = True
        comparison.verdict = "overfit"
        score = _score_overfit(comparison)
        assert score == 10.0

    def test_clear_improvement(self):
        comparison = MagicMock()
        comparison.overfit_detected = False
        comparison.verdict = "improvement"
        score = _score_overfit(comparison)
        assert score == 90.0

    def test_no_comparison(self):
        score = _score_overfit(None)
        assert score == 60.0
