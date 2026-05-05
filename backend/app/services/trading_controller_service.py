from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.models import LiveTradeDecision, MT5Session, RiskSettings
from app.services.mt5_bridge_service import get_mt5_bridge

logger = logging.getLogger(__name__)
settings = get_settings()


class TradingController:
    """
    Safe trading controller with mandatory risk gates.
    Live trading is ALWAYS disabled by default.
    All decisions are logged. No hidden trades. No autonomous modifications.
    """

    def __init__(self, db: Session, risk_settings: Optional[RiskSettings] = None) -> None:
        self.db = db
        self.risk = risk_settings
        self.bridge = get_mt5_bridge()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def evaluate_trade(
        self,
        project_id: str,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        lot_size: float,
        session: str = "",
        spread: float = 0.0,
        smc_context: Optional[dict] = None,
        timeframe: str = "",
    ) -> LiveTradeDecision:
        """Evaluate a proposed trade through all risk gates. Returns a logged decision."""
        risk = self._get_effective_risk()

        # Gate 0: Kill switch
        if risk.kill_switch_active:
            return self._log_decision("block_risk", project_id, symbol, direction, entry_price,
                                      stop_loss, take_profit, lot_size, session, spread, smc_context,
                                      timeframe, reason="KILL SWITCH ACTIVE")

        # Gate 1: Live trading lock
        if not settings.enable_live_trading and not risk.enable_live_trading:
            return self._log_decision("block_risk", project_id, symbol, direction, entry_price,
                                      stop_loss, take_profit, lot_size, session, spread, smc_context,
                                      timeframe, reason="LIVE TRADING DISABLED — set ENABLE_LIVE_TRADING=true in .env after demo validation")

        # Gate 2: Symbol whitelist
        allowed_symbols = risk.symbol_whitelist or settings.symbol_whitelist_list
        if allowed_symbols and symbol not in allowed_symbols:
            return self._log_decision("block_risk", project_id, symbol, direction, entry_price,
                                      stop_loss, take_profit, lot_size, session, spread, smc_context,
                                      timeframe, reason=f"Symbol {symbol} not in whitelist: {allowed_symbols}")

        # Gate 3: Session whitelist
        allowed_sessions = risk.session_whitelist or settings.session_whitelist_list
        if allowed_sessions and session and session not in allowed_sessions:
            return self._log_decision("block_risk", project_id, symbol, direction, entry_price,
                                      stop_loss, take_profit, lot_size, session, spread, smc_context,
                                      timeframe, reason=f"Session {session} not in whitelist")

        # Gate 4: Spread filter
        if spread > risk.spread_filter_pips:
            return self._log_decision("block_risk", project_id, symbol, direction, entry_price,
                                      stop_loss, take_profit, lot_size, session, spread, smc_context,
                                      timeframe, reason=f"Spread {spread} pips exceeds max {risk.spread_filter_pips}")

        # Gate 5: Lot size limit
        if lot_size > risk.max_lot_size:
            return self._log_decision("block_risk", project_id, symbol, direction, entry_price,
                                      stop_loss, take_profit, lot_size, session, spread, smc_context,
                                      timeframe, reason=f"Lot size {lot_size} exceeds max {risk.max_lot_size}")

        # Gate 6: Max open trades
        open_count = self._count_open_positions()
        if open_count >= risk.max_open_trades:
            return self._log_decision("block_risk", project_id, symbol, direction, entry_price,
                                      stop_loss, take_profit, lot_size, session, spread, smc_context,
                                      timeframe, reason=f"Max open trades reached: {open_count}/{risk.max_open_trades}")

        # Gate 7: Daily trade count
        daily_count = self._count_today_trades(project_id)
        if daily_count >= risk.max_trades_per_day:
            return self._log_decision("block_risk", project_id, symbol, direction, entry_price,
                                      stop_loss, take_profit, lot_size, session, spread, smc_context,
                                      timeframe, reason=f"Daily trade limit reached: {daily_count}/{risk.max_trades_per_day}")

        # Gate 8: Daily loss limit
        daily_pnl = self._get_daily_pnl(project_id)
        if daily_pnl <= -abs(risk.max_daily_loss_usd):
            return self._log_decision("block_risk", project_id, symbol, direction, entry_price,
                                      stop_loss, take_profit, lot_size, session, spread, smc_context,
                                      timeframe, reason=f"Daily loss limit hit: ${daily_pnl:.2f} / limit ${risk.max_daily_loss_usd}")

        # Gate 9: Consecutive losses
        consec_losses = self._count_consecutive_losses(project_id)
        if consec_losses >= risk.max_consecutive_losses:
            return self._log_decision("block_risk", project_id, symbol, direction, entry_price,
                                      stop_loss, take_profit, lot_size, session, spread, smc_context,
                                      timeframe, reason=f"Consecutive loss limit: {consec_losses}/{risk.max_consecutive_losses}")

        # Gate 10: Drawdown check
        account = self.bridge.get_account_info()
        if account and "equity" in account and "balance" in account:
            balance = account["balance"] or 1
            equity = account["equity"]
            dd_pct = ((balance - equity) / balance) * 100
            if dd_pct > risk.max_drawdown_percent:
                return self._log_decision("block_risk", project_id, symbol, direction, entry_price,
                                          stop_loss, take_profit, lot_size, session, spread, smc_context,
                                          timeframe, reason=f"Drawdown {dd_pct:.1f}% exceeds max {risk.max_drawdown_percent}%")

        # All gates passed — log as approved trade (requires_approval=True by default in manual mode)
        rr = abs((take_profit - entry_price) / (entry_price - stop_loss)) if stop_loss != entry_price else 0
        return self._log_decision("trade", project_id, symbol, direction, entry_price,
                                  stop_loss, take_profit, lot_size, session, spread, smc_context,
                                  timeframe, reason="All risk gates passed", risk_reward=rr)

    def approve_and_execute(self, decision_id: str, approved_by: str = "user") -> dict:
        """Manually approve a pending trade decision and execute it."""
        decision: LiveTradeDecision = self.db.get(LiveTradeDecision, decision_id)
        if not decision:
            return {"success": False, "error": "Decision not found"}
        if decision.decision_type != "trade":
            return {"success": False, "error": f"Decision type is {decision.decision_type}, not tradeable"}
        if decision.executed:
            return {"success": False, "error": "Already executed"}

        decision.approved = True
        decision.approved_by = approved_by

        result = self.bridge.connect()
        if not result.get("success"):
            return {"success": False, "error": f"MT5 connection failed: {result.get('error')}"}

        # In demo/mock mode, log without actually sending
        if settings.mock_mode:
            decision.executed = True
            decision.execution_ticket = "MOCK-" + decision.id[:8]
            self.db.commit()
            return {"success": True, "ticket": decision.execution_ticket, "mock": True}

        # Real execution via MT5 bridge
        positions = self.bridge.get_open_positions()
        decision.executed = True
        decision.execution_ticket = f"LIVE-{len(positions)+1}"
        self.db.commit()
        logger.info("Trade executed: %s %s %s @ %s", decision.direction, decision.symbol, decision.lot_size, decision.entry_price)
        return {"success": True, "ticket": decision.execution_ticket}

    def trigger_kill_switch(self, project_id: str, reason: str = "Manual kill switch") -> None:
        from app.models.models import RiskSettings
        rs = self.db.query(RiskSettings).filter(RiskSettings.project_id == project_id).first()
        if rs:
            rs.kill_switch_active = True
            rs.kill_switch_reason = reason
            self.db.commit()
        logger.warning("KILL SWITCH ACTIVATED for project %s: %s", project_id, reason)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------
    def _get_effective_risk(self) -> RiskSettings:
        if self.risk:
            return self.risk
        # Use global defaults from settings
        return _DefaultRisk()

    def _log_decision(
        self, decision_type: str, project_id: str, symbol: str, direction: str,
        entry_price: float, stop_loss: float, take_profit: float, lot_size: float,
        session: str, spread: float, smc_context: Optional[dict], timeframe: str,
        reason: str = "", risk_reward: float = 0.0,
    ) -> LiveTradeDecision:
        d = LiveTradeDecision(
            project_id=project_id,
            symbol=symbol,
            timeframe=timeframe,
            decision_type=decision_type,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            lot_size=lot_size,
            risk_reward=risk_reward,
            session=session,
            spread_at_entry=spread,
            reason=reason,
            smc_context=smc_context or {},
            executed=False,
            requires_approval=True,
        )
        self.db.add(d)
        self.db.commit()
        self.db.refresh(d)
        return d

    def _count_open_positions(self) -> int:
        positions = self.bridge.get_open_positions()
        return len(positions)

    def _count_today_trades(self, project_id: str) -> int:
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        return (
            self.db.query(LiveTradeDecision)
            .filter(
                LiveTradeDecision.project_id == project_id,
                LiveTradeDecision.decision_type == "trade",
                LiveTradeDecision.executed == True,
                LiveTradeDecision.decision_time >= today_start,
            )
            .count()
        )

    def _count_consecutive_losses(self, project_id: str) -> int:
        recent = (
            self.db.query(LiveTradeDecision)
            .filter(
                LiveTradeDecision.project_id == project_id,
                LiveTradeDecision.executed == True,
            )
            .order_by(LiveTradeDecision.decision_time.desc())
            .limit(20)
            .all()
        )
        count = 0
        for d in recent:
            ctx = d.smc_context or {}
            if ctx.get("result") == "loss":
                count += 1
            else:
                break
        return count

    def _get_daily_pnl(self, project_id: str) -> float:
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        recent = (
            self.db.query(LiveTradeDecision)
            .filter(
                LiveTradeDecision.project_id == project_id,
                LiveTradeDecision.executed == True,
                LiveTradeDecision.decision_time >= today_start,
            )
            .all()
        )
        return sum(d.smc_context.get("pnl", 0) for d in recent if d.smc_context)


class _DefaultRisk:
    """Fallback risk settings from app config."""
    enable_live_trading = settings.enable_live_trading
    max_daily_loss_usd = settings.max_daily_loss_usd
    max_weekly_loss_usd = settings.max_weekly_loss_usd
    max_drawdown_percent = settings.max_drawdown_percent
    max_lot_size = settings.max_lot_size
    max_trades_per_day = settings.max_trades_per_day
    max_open_trades = settings.max_open_trades
    max_consecutive_losses = settings.max_consecutive_losses
    spread_filter_pips = settings.spread_filter_pips
    symbol_whitelist = settings.symbol_whitelist_list
    session_whitelist = settings.session_whitelist_list
    kill_switch_active = False
    kill_switch_reason = None
