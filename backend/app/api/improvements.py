from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import BacktestReport, ImprovementIdea, MQL5Source, PineScriptSource
from app.services.improvement_engine_service import generate_improvement_ideas, generate_mql5_patch

router = APIRouter()


class GenerateIdeasRequest(BaseModel):
    project_id: str
    backtest_report_id: str
    n_ideas: int = 10


class UpdateIdeaRequest(BaseModel):
    status: Optional[str] = None
    user_notes: Optional[str] = None


class PatchRequest(BaseModel):
    idea_id: str


@router.post("/generate")
def generate_ideas(req: GenerateIdeasRequest, db: Session = Depends(get_db)):
    report: BacktestReport = db.get(BacktestReport, req.backtest_report_id)
    if not report:
        raise HTTPException(404, "Backtest report not found")

    pine_src = (
        db.query(PineScriptSource)
        .filter(PineScriptSource.project_id == req.project_id)
        .order_by(PineScriptSource.created_at.desc())
        .first()
    )
    mql5_src = (
        db.query(MQL5Source)
        .filter(MQL5Source.project_id == req.project_id)
        .order_by(MQL5Source.created_at.desc())
        .first()
    )

    ideas = generate_improvement_ideas(
        req.project_id, db, report, pine_src, mql5_src, req.n_ideas
    )
    return [_idea_to_dict(i) for i in ideas]


@router.get("/{project_id}")
def list_ideas(project_id: str, status: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(ImprovementIdea).filter(ImprovementIdea.project_id == project_id)
    if status:
        q = q.filter(ImprovementIdea.status == status)
    ideas = q.order_by(ImprovementIdea.created_at.desc()).all()
    return [_idea_to_dict(i) for i in ideas]


@router.patch("/{idea_id}")
def update_idea(idea_id: str, req: UpdateIdeaRequest, db: Session = Depends(get_db)):
    idea: ImprovementIdea = db.get(ImprovementIdea, idea_id)
    if not idea:
        raise HTTPException(404, "Improvement idea not found")
    if req.status:
        valid_statuses = ["pending", "accepted", "rejected", "tested", "deployed"]
        if req.status not in valid_statuses:
            raise HTTPException(400, f"Invalid status. Must be one of: {valid_statuses}")
        idea.status = req.status
    if req.user_notes is not None:
        idea.user_notes = req.user_notes
    db.commit()
    db.refresh(idea)
    return _idea_to_dict(idea)


@router.post("/patch")
def get_mql5_patch_for_idea(req: PatchRequest, db: Session = Depends(get_db)):
    idea: ImprovementIdea = db.get(ImprovementIdea, req.idea_id)
    if not idea:
        raise HTTPException(404, "Improvement idea not found")
    mql5_src = (
        db.query(MQL5Source)
        .filter(MQL5Source.project_id == idea.project_id)
        .order_by(MQL5Source.created_at.desc())
        .first()
    )
    patch = generate_mql5_patch(idea, mql5_src)
    idea.mql5_patch_suggestion = patch
    db.commit()
    return {"idea_id": idea_id, "patch": patch}


@router.get("/detail/{idea_id}")
def get_idea(idea_id: str, db: Session = Depends(get_db)):
    idea: ImprovementIdea = db.get(ImprovementIdea, idea_id)
    if not idea:
        raise HTTPException(404, "Not found")
    return _idea_to_dict(idea, full=True)


def _idea_to_dict(idea: ImprovementIdea, full: bool = False) -> dict:
    base = {
        "id": idea.id,
        "name": idea.name,
        "category": idea.category,
        "affected_component": idea.affected_component,
        "overfit_risk": idea.overfit_risk,
        "status": idea.status,
        "ai_generated": idea.ai_generated,
        "created_at": idea.created_at.isoformat() if idea.created_at else None,
    }
    if full:
        base.update({
            "logic_explanation": idea.logic_explanation,
            "smc_reasoning": idea.smc_reasoning,
            "expected_benefit": idea.expected_benefit,
            "expected_risk": idea.expected_risk,
            "parameters_changed": idea.parameters_changed,
            "pine_script_impact": idea.pine_script_impact,
            "mql5_patch_suggestion": idea.mql5_patch_suggestion,
            "user_notes": idea.user_notes,
        })
    return base
