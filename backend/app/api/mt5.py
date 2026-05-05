from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.models import TradeLog
from app.services.mt5_bridge_service import MT5LogReader, get_mt5_bridge
from app.services.trading_controller_service import TradingController

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()


class TradeRequest(BaseModel):
    project_id: str
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    session: Optional[str] = ""
    spread: Optional[float] = 0.0
    smc_context: Optional[dict] = None
    timeframe: Optional[str] = ""


@router.get("/status")
def mt5_status():
    bridge = get_mt5_bridge()
    account = bridge.get_account_info()
    return {
        "connected": bridge._connected,
        "mock_mode": bridge._mock,
        "live_trading_enabled": settings.enable_live_trading,
        "data_source": "mock" if bridge._mock else "mt5_terminal",
        "account": account,
    }


@router.post("/connect")
def connect(account: int = 0, password: str = "", server: str = ""):
    bridge = get_mt5_bridge()
    result = bridge.connect(account, password, server)
    return result


@router.get("/positions")
def get_positions():
    bridge = get_mt5_bridge()
    return bridge.get_open_positions()


@router.get("/history")
def get_history(days: int = 30):
    bridge = get_mt5_bridge()
    return bridge.get_closed_positions(days)


@router.post("/evaluate-trade")
def evaluate_trade(req: TradeRequest, db: Session = Depends(get_db)):
    """Evaluate a trade through all risk gates. Returns decision (does NOT execute)."""
    from app.models.models import RiskSettings
    rs = db.query(RiskSettings).filter(RiskSettings.project_id == req.project_id).first()
    controller = TradingController(db, rs)
    decision = controller.evaluate_trade(
        req.project_id, req.symbol, req.direction,
        req.entry_price, req.stop_loss, req.take_profit, req.lot_size,
        req.session or "", req.spread or 0.0, req.smc_context, req.timeframe or "",
    )
    return {
        "id": decision.id,
        "decision_type": decision.decision_type,
        "reason": decision.reason,
        "requires_approval": decision.requires_approval,
        "risk_reward": decision.risk_reward,
    }


@router.post("/approve-trade/{decision_id}")
def approve_trade(decision_id: str, approved_by: str = "user", db: Session = Depends(get_db)):
    """Manually approve and execute a pending trade decision."""
    if not settings.enable_live_trading:
        return {
            "success": False,
            "error": "LIVE TRADING DISABLED. Set ENABLE_LIVE_TRADING=true in .env after completing demo validation.",
        }
    controller = TradingController(db)
    result = controller.approve_and_execute(decision_id, approved_by)
    return result


@router.post("/kill-switch/{project_id}")
def kill_switch(project_id: str, reason: str = "Manual kill switch activated", db: Session = Depends(get_db)):
    controller = TradingController(db)
    controller.trigger_kill_switch(project_id, reason)
    return {"activated": True, "reason": reason}


@router.post("/upload-log")
async def upload_log(
    file: UploadFile = File(...),
    project_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    content = (await file.read()).decode("utf-8", errors="ignore")
    reader = MT5LogReader()
    entries = reader.parse_uploaded_log(content)

    saved = 0
    for entry in entries[:5000]:  # limit
        log = TradeLog(
            project_id=project_id,
            log_time=None,
            log_level=entry.get("level", "info"),
            message=entry.get("message", ""),
            source=entry.get("source", "expert"),
            raw_line=entry.get("raw", ""),
        )
        db.add(log)
        saved += 1
    db.commit()
    return {"saved": saved, "total_lines": len(entries)}


@router.get("/logs/{project_id}")
def get_logs(project_id: str, limit: int = 100, db: Session = Depends(get_db)):
    logs = (
        db.query(TradeLog)
        .filter(TradeLog.project_id == project_id)
        .order_by(TradeLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": l.id,
            "log_time": l.log_time.isoformat() if l.log_time else None,
            "level": l.log_level,
            "message": l.message,
            "source": l.source,
        }
        for l in logs
    ]


@router.get("/decisions/{project_id}")
def get_decisions(project_id: str, limit: int = 50, db: Session = Depends(get_db)):
    from app.models.models import LiveTradeDecision
    decisions = (
        db.query(LiveTradeDecision)
        .filter(LiveTradeDecision.project_id == project_id)
        .order_by(LiveTradeDecision.decision_time.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": d.id,
            "decision_type": d.decision_type,
            "symbol": d.symbol,
            "direction": d.direction,
            "entry_price": d.entry_price,
            "reason": d.reason,
            "executed": d.executed,
            "requires_approval": d.requires_approval,
            "approved": d.approved,
            "decision_time": d.decision_time.isoformat() if d.decision_time else None,
        }
        for d in decisions
    ]
