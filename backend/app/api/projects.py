from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import StrategyProject, BacktestReport, ConfidenceScore

router = APIRouter()


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    symbol: Optional[str] = None
    timeframe: Optional[str] = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    symbol: Optional[str]
    timeframe: Optional[str]
    is_active: bool

    class Config:
        from_attributes = True


@router.get("/")
def list_projects(db: Session = Depends(get_db)):
    projects = db.query(StrategyProject).filter(StrategyProject.is_active == True).all()
    return [{"id": p.id, "name": p.name, "symbol": p.symbol, "timeframe": p.timeframe} for p in projects]


@router.post("/")
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    project = StrategyProject(
        name=payload.name,
        description=payload.description,
        symbol=payload.symbol,
        timeframe=payload.timeframe,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return {"id": project.id, "name": project.name}


@router.get("/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_db)):
    project = db.get(StrategyProject, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    # Get baseline backtest
    baseline = (
        db.query(BacktestReport)
        .filter(BacktestReport.project_id == project_id, BacktestReport.is_baseline == True)
        .first()
    )

    # Get best confidence score
    best_cs = (
        db.query(ConfidenceScore)
        .filter(ConfidenceScore.project_id == project_id)
        .order_by(ConfidenceScore.overall_score.desc())
        .first()
    )

    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "symbol": project.symbol,
        "timeframe": project.timeframe,
        "baseline": {
            "net_profit": baseline.net_profit,
            "profit_factor": baseline.profit_factor,
            "win_rate": baseline.win_rate,
            "total_trades": baseline.total_trades,
            "max_drawdown_pct": baseline.max_drawdown_pct,
        } if baseline else None,
        "best_confidence_score": best_cs.overall_score if best_cs else None,
        "readiness_level": best_cs.readiness_level if best_cs else "research",
    }
