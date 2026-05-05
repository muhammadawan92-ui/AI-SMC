from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings, uses_openai_compatible_client
from app.database import get_db
from app.models.models import RiskSettings, StrategyProject
from app.services.smc_logic_service import get_improvement_categories, SMC_KNOWLEDGE_BASE
from app.services.knowledge_doc_service import knowledge_doc_status
from app.gemini_env import gemini_model_id

router = APIRouter()
settings = get_settings()


class RiskSettingsUpdate(BaseModel):
    enable_live_trading: Optional[bool] = None
    max_daily_loss_usd: Optional[float] = None
    max_weekly_loss_usd: Optional[float] = None
    max_drawdown_percent: Optional[float] = None
    max_lot_size: Optional[float] = None
    max_trades_per_day: Optional[int] = None
    max_open_trades: Optional[int] = None
    max_consecutive_losses: Optional[int] = None
    spread_filter_pips: Optional[float] = None
    symbol_whitelist: Optional[list] = None
    session_whitelist: Optional[list] = None


def _active_llm_model() -> str:
    p = settings.llm_provider
    if p == "openai":
        return settings.openai_model
    if uses_openai_compatible_client(p):
        return settings.local_llm_model
    if p == "anthropic":
        return settings.anthropic_model
    if p == "gemini":
        return gemini_model_id()
    return settings.local_llm_model


@router.get("/")
def get_settings_overview():
    return {
        "llm_provider": settings.llm_provider,
        "llm_model": _active_llm_model(),
        "mock_mode": settings.mock_mode,
        "mock_llm": settings.mock_llm,
        "live_trading_enabled": settings.enable_live_trading,
        "max_daily_loss_usd": settings.max_daily_loss_usd,
        "max_lot_size": settings.max_lot_size,
        "max_trades_per_day": settings.max_trades_per_day,
        "symbol_whitelist": settings.symbol_whitelist_list,
        "session_whitelist": settings.session_whitelist_list,
        "smc_knowledge_doc": knowledge_doc_status(),
    }


@router.get("/risk/{project_id}")
def get_risk_settings(project_id: str, db: Session = Depends(get_db)):
    rs = db.query(RiskSettings).filter(RiskSettings.project_id == project_id).first()
    if not rs:
        # Return global defaults
        return {
            "project_id": project_id,
            "source": "global_defaults",
            "enable_live_trading": settings.enable_live_trading,
            "max_daily_loss_usd": settings.max_daily_loss_usd,
            "max_weekly_loss_usd": settings.max_weekly_loss_usd,
            "max_drawdown_percent": settings.max_drawdown_percent,
            "max_lot_size": settings.max_lot_size,
            "max_trades_per_day": settings.max_trades_per_day,
            "max_open_trades": settings.max_open_trades,
            "max_consecutive_losses": settings.max_consecutive_losses,
            "spread_filter_pips": settings.spread_filter_pips,
            "symbol_whitelist": settings.symbol_whitelist_list,
            "session_whitelist": settings.session_whitelist_list,
            "kill_switch_active": False,
        }
    return {
        "id": rs.id,
        "project_id": rs.project_id,
        "source": "project_settings",
        "enable_live_trading": rs.enable_live_trading,
        "max_daily_loss_usd": rs.max_daily_loss_usd,
        "max_weekly_loss_usd": rs.max_weekly_loss_usd,
        "max_drawdown_percent": rs.max_drawdown_percent,
        "max_lot_size": rs.max_lot_size,
        "max_trades_per_day": rs.max_trades_per_day,
        "max_open_trades": rs.max_open_trades,
        "max_consecutive_losses": rs.max_consecutive_losses,
        "spread_filter_pips": rs.spread_filter_pips,
        "symbol_whitelist": rs.symbol_whitelist,
        "session_whitelist": rs.session_whitelist,
        "kill_switch_active": rs.kill_switch_active,
        "kill_switch_reason": rs.kill_switch_reason,
    }


@router.put("/risk/{project_id}")
def update_risk_settings(project_id: str, payload: RiskSettingsUpdate, db: Session = Depends(get_db)):
    project = db.get(StrategyProject, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    rs = db.query(RiskSettings).filter(RiskSettings.project_id == project_id).first()
    if not rs:
        rs = RiskSettings(
            project_id=project_id,
            enable_live_trading=False,  # ALWAYS starts disabled
            max_daily_loss_usd=settings.max_daily_loss_usd,
            max_weekly_loss_usd=settings.max_weekly_loss_usd,
            max_drawdown_percent=settings.max_drawdown_percent,
            max_lot_size=settings.max_lot_size,
            max_trades_per_day=settings.max_trades_per_day,
            max_open_trades=settings.max_open_trades,
            max_consecutive_losses=settings.max_consecutive_losses,
            spread_filter_pips=settings.spread_filter_pips,
        )
        db.add(rs)

    for field, value in payload.model_dump(exclude_none=True).items():
        if field == "enable_live_trading" and value is True:
            if not settings.enable_live_trading:
                raise HTTPException(
                    403,
                    "Cannot enable live trading — ENABLE_LIVE_TRADING must be set to true in .env first. "
                    "This requires deliberate configuration after demo validation."
                )
        setattr(rs, field, value)

    db.commit()
    db.refresh(rs)
    return {"updated": True, "kill_switch_active": rs.kill_switch_active}


@router.get("/smc-knowledge")
def get_smc_knowledge():
    return {k: {"name": v["name"], "description": v["description"]} for k, v in SMC_KNOWLEDGE_BASE.items()}


@router.get("/smc-knowledge/{concept}")
def get_concept(concept: str):
    from app.services.smc_logic_service import get_concept_explanation
    result = get_concept_explanation(concept)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@router.get("/improvement-categories")
def get_categories():
    return get_improvement_categories()
