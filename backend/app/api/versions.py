from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import BacktestReport, ConfidenceScore, StrategyVersion
from app.services.version_manager_service import (
    approve_version,
    compare_backtests,
    create_version,
    get_version_history,
    reject_version,
)
from app.services.confidence_score_service import compute_confidence_score

router = APIRouter()


class CreateVersionRequest(BaseModel):
    project_id: str
    version_number: str
    label: Optional[str] = ""
    description: Optional[str] = ""
    mql5_code: Optional[str] = ""
    input_parameters: Optional[dict] = None
    improvement_ids: Optional[list] = None
    ai_explanation: Optional[str] = ""
    is_baseline: bool = False


class CompareRequest(BaseModel):
    project_id: str
    baseline_report_id: str
    improved_report_id: str
    version_id: Optional[str] = None


class ScoreRequest(BaseModel):
    project_id: str
    baseline_report_id: str
    improved_report_id: str
    comparison_id: Optional[str] = None
    version_id: Optional[str] = None
    screenshot_validation_score: float = 0.0
    smc_consistency_score: float = 75.0


@router.post("/")
def create(req: CreateVersionRequest, db: Session = Depends(get_db)):
    v = create_version(
        req.project_id, req.version_number, db,
        label=req.label or "",
        description=req.description or "",
        mql5_code=req.mql5_code or "",
        input_parameters=req.input_parameters,
        improvement_ids=req.improvement_ids,
        ai_explanation=req.ai_explanation or "",
        is_baseline=req.is_baseline,
    )
    return {"id": v.id, "version_number": v.version_number, "approval_status": v.approval_status}


@router.get("/{project_id}")
def list_versions(project_id: str, db: Session = Depends(get_db)):
    return get_version_history(project_id, db)


@router.post("/{version_id}/approve")
def approve(version_id: str, approved_by: str = "user", db: Session = Depends(get_db)):
    v = approve_version(version_id, db, approved_by)
    return {"id": v.id, "approval_status": v.approval_status}


@router.post("/{version_id}/reject")
def reject(version_id: str, reason: str = "", db: Session = Depends(get_db)):
    v = reject_version(version_id, db, reason)
    return {"id": v.id, "approval_status": v.approval_status}


@router.post("/compare")
def compare(req: CompareRequest, db: Session = Depends(get_db)):
    try:
        comparison = compare_backtests(
            req.baseline_report_id, req.improved_report_id, db, req.project_id, req.version_id
        )
        return {
            "id": comparison.id,
            "verdict": comparison.verdict,
            "profit_delta": comparison.profit_delta,
            "profit_factor_delta": comparison.profit_factor_delta,
            "win_rate_delta": comparison.win_rate_delta,
            "drawdown_delta": comparison.drawdown_delta,
            "trade_count_delta": comparison.trade_count_delta,
            "overfit_detected": comparison.overfit_detected,
            "overfit_reasons": comparison.overfit_reasons,
            "is_statistically_significant": comparison.is_statistically_significant,
        }
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/score")
def score(req: ScoreRequest, db: Session = Depends(get_db)):
    baseline: BacktestReport = db.get(BacktestReport, req.baseline_report_id)
    improved: BacktestReport = db.get(BacktestReport, req.improved_report_id)
    if not baseline or not improved:
        raise HTTPException(404, "Backtest reports not found")

    from app.models.models import BacktestComparison
    comparison = db.get(BacktestComparison, req.comparison_id) if req.comparison_id else None
    version = db.get(StrategyVersion, req.version_id) if req.version_id else None

    cs = compute_confidence_score(
        baseline, improved, comparison, version, db,
        req.screenshot_validation_score, req.smc_consistency_score
    )
    return {
        "id": cs.id,
        "overall_score": cs.overall_score,
        "readiness_level": cs.readiness_level,
        "breakdown": cs.breakdown,
        "ai_notes": cs.ai_notes,
    }


@router.get("/scores/{project_id}")
def get_scores(project_id: str, db: Session = Depends(get_db)):
    scores = (
        db.query(ConfidenceScore)
        .filter(ConfidenceScore.project_id == project_id)
        .order_by(ConfidenceScore.created_at.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "id": s.id,
            "overall_score": s.overall_score,
            "readiness_level": s.readiness_level,
            "version_id": s.version_id,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in scores
    ]
