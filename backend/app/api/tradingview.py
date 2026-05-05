from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel

from app.config import get_settings
from app.database import get_db
from app.services.tradingview_learning_service import compare_ea_vs_model_on_tradingview
from sqlalchemy.orm import Session

router = APIRouter()
settings = get_settings()


class TradingViewCompareRequest(BaseModel):
    symbol: str = ""
    timeframe: str = ""
    chart_url: str = ""
    ea_decision: str
    ea_reasoning: str = ""
    notes: str = ""
    project_id: Optional[str] = None


@router.post("/mock-compare")
def mock_compare(req: TradingViewCompareRequest, db: Session = Depends(get_db)):
    result = compare_ea_vs_model_on_tradingview(
        symbol=req.symbol,
        timeframe=req.timeframe,
        chart_url=req.chart_url,
        ea_decision=req.ea_decision,
        ea_reasoning=req.ea_reasoning,
        notes=req.notes,
        db=db,
        project_id=req.project_id,
    )
    return result


@router.post("/webhook")
async def webhook(request: Request):
    # Optional TradingView webhook ingestion for future live/demo learning.
    secret = request.headers.get("x-tv-secret", "")
    if settings.tradingview_webhook_secret and secret != settings.tradingview_webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid TradingView webhook secret")
    payload = await request.json()
    return {
        "received": True,
        "source": "tradingview_webhook",
        "payload": payload,
    }

