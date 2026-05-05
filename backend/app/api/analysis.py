from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import BacktestReport, MQL5Source, PineScriptSource, ScreenshotAnalysis, UploadedFile
from app.services import (
    backtest_analyzer_service as bas,
    pine_parser_service as pps,
    mql5_parser_service as mps,
    screenshot_analyzer_service as sas,
    report_generator_service as rgs,
)
from app.services.file_ingestion_service import read_text_file

logger = logging.getLogger(__name__)
router = APIRouter()


class AnalyzeFileRequest(BaseModel):
    file_id: str
    project_id: str
    label: Optional[str] = "baseline"
    is_baseline: bool = False
    run_llm: bool = True


class AnalyzeScreenshotRequest(BaseModel):
    file_id: str
    project_id: Optional[str] = None
    symbol: str = ""
    timeframe: str = ""
    user_notes: str = ""
    ea_decision: str = ""
    chart_url: str = ""


@router.post("/pine")
def analyze_pine_script(req: AnalyzeFileRequest, db: Session = Depends(get_db)):
    file_record: UploadedFile = db.get(UploadedFile, req.file_id)
    if not file_record:
        raise HTTPException(404, "File not found")
    try:
        code = read_text_file(file_record)
        source = pps.parse_pine_script(code, file_record, db, req.project_id, run_llm=req.run_llm)
        return {
            "id": source.id,
            "summary": source.summary,
            "detected_smc_concepts": source.detected_smc_concepts,
            "entry_conditions": source.entry_conditions,
            "exit_conditions": source.exit_conditions,
            "session_filters": source.session_filters,
            "risk_logic": source.risk_logic,
            "ai_analysis": source.ai_analysis,
        }
    except Exception as e:
        logger.error("Pine analysis error: %s", e)
        raise HTTPException(500, str(e))


@router.post("/mql5")
def analyze_mql5(req: AnalyzeFileRequest, db: Session = Depends(get_db)):
    file_record: UploadedFile = db.get(UploadedFile, req.file_id)
    if not file_record:
        raise HTTPException(404, "File not found")
    # Get pine source if available
    pine_code = None
    pine_src = (
        db.query(PineScriptSource)
        .filter(PineScriptSource.project_id == req.project_id)
        .order_by(PineScriptSource.created_at.desc())
        .first()
    )
    if pine_src:
        pine_code = pine_src.raw_code
    try:
        code = read_text_file(file_record)
        source = mps.parse_mql5_ea(code, file_record, db, req.project_id, pine_code, run_llm=req.run_llm)
        return {
            "id": source.id,
            "summary": source.summary,
            "detected_smc_concepts": source.detected_smc_concepts,
            "input_parameters": source.input_parameters,
            "entry_logic": source.entry_logic,
            "exit_logic": source.exit_logic,
            "sl_tp_logic": source.sl_tp_logic,
            "pine_vs_ea_diff": source.pine_vs_ea_diff,
            "ai_analysis": source.ai_analysis,
        }
    except Exception as e:
        logger.error("MQL5 analysis error: %s", e)
        raise HTTPException(500, str(e))


@router.post("/backtest")
def analyze_backtest(req: AnalyzeFileRequest, db: Session = Depends(get_db)):
    file_record: UploadedFile = db.get(UploadedFile, req.file_id)
    if not file_record:
        raise HTTPException(404, "File not found")
    try:
        report = bas.parse_backtest_report(
            file_record, db, req.project_id, req.label, req.is_baseline, run_llm=req.run_llm
        )
        return _report_to_dict(report)
    except Exception as e:
        logger.error("Backtest analysis error: %s", e)
        raise HTTPException(500, str(e))


@router.post("/screenshot")
def analyze_screenshot(req: AnalyzeScreenshotRequest, db: Session = Depends(get_db)):
    file_record: UploadedFile = db.get(UploadedFile, req.file_id)
    if not file_record:
        raise HTTPException(404, "File not found")
    try:
        result = sas.analyze_screenshot(
            file_record,
            db,
            req.project_id,
            req.symbol,
            req.timeframe,
            req.user_notes,
            req.ea_decision,
            req.chart_url,
        )
        return {
            "id": result.id,
            "symbol": result.symbol,
            "timeframe": result.timeframe,
            "ai_structure_analysis": result.ai_structure_analysis,
            "detected_structures": result.detected_structures,
            "detected_bias": result.detected_bias,
            "ea_recommendation": result.ea_recommendation,
            "ai_vs_ea_comparison": result.ai_vs_ea_comparison,
            "confidence": result.confidence,
        }
    except Exception as e:
        logger.error("Screenshot analysis error: %s", e)
        raise HTTPException(500, str(e))


@router.get("/backtest/{project_id}")
def get_backtests(project_id: str, db: Session = Depends(get_db)):
    reports = (
        db.query(BacktestReport)
        .filter(BacktestReport.project_id == project_id)
        .order_by(BacktestReport.created_at.desc())
        .all()
    )
    return [_report_to_dict(r, brief=True) for r in reports]


@router.get("/backtest/detail/{report_id}")
def get_backtest_detail(report_id: str, db: Session = Depends(get_db)):
    r = db.get(BacktestReport, report_id)
    if not r:
        raise HTTPException(404, "Report not found")
    return _report_to_dict(r)


@router.get("/pine/{project_id}")
def get_pine_sources(project_id: str, db: Session = Depends(get_db)):
    sources = (
        db.query(PineScriptSource)
        .filter(PineScriptSource.project_id == project_id)
        .all()
    )
    return [{"id": s.id, "summary": s.summary, "detected_smc_concepts": s.detected_smc_concepts, "ai_analysis": s.ai_analysis} for s in sources]


@router.get("/mql5/{project_id}")
def get_mql5_sources(project_id: str, db: Session = Depends(get_db)):
    sources = (
        db.query(MQL5Source)
        .filter(MQL5Source.project_id == project_id)
        .all()
    )
    return [{"id": s.id, "summary": s.summary, "input_parameters": s.input_parameters, "pine_vs_ea_diff": s.pine_vs_ea_diff, "ai_analysis": s.ai_analysis} for s in sources]


@router.post("/report/baseline/{project_id}")
def generate_baseline_report(project_id: str, db: Session = Depends(get_db)):
    from app.models.models import StrategyProject
    project = db.get(StrategyProject, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    baseline = db.query(BacktestReport).filter(
        BacktestReport.project_id == project_id, BacktestReport.is_baseline == True
    ).first()
    if not baseline:
        raise HTTPException(404, "No baseline backtest report found")
    content = rgs.generate_baseline_summary_report(project, baseline, db)
    return {"report": content}


@router.get("/screenshots/{project_id}")
def get_screenshots(project_id: str, db: Session = Depends(get_db)):
    shots = (
        db.query(ScreenshotAnalysis)
        .filter(ScreenshotAnalysis.project_id == project_id)
        .order_by(ScreenshotAnalysis.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": s.id,
            "symbol": s.symbol,
            "timeframe": s.timeframe,
            "detected_bias": s.detected_bias,
            "ea_recommendation": s.ea_recommendation,
            "confidence": s.confidence,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in shots
    ]


def _report_to_dict(r: BacktestReport, brief: bool = False) -> dict:
    base = {
        "id": r.id,
        "label": r.label,
        "is_baseline": r.is_baseline,
        "symbol": r.symbol,
        "timeframe": r.timeframe,
        "net_profit": r.net_profit,
        "profit_factor": r.profit_factor,
        "win_rate": r.win_rate,
        "total_trades": r.total_trades,
        "max_drawdown_pct": r.max_drawdown_pct,
        "sharpe_ratio": r.sharpe_ratio,
        "recovery_factor": r.recovery_factor,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
    if not brief:
        base.update({
            "avg_win": r.avg_win,
            "avg_loss": r.avg_loss,
            "expectancy": r.expectancy,
            "long_win_rate": r.long_win_rate,
            "short_win_rate": r.short_win_rate,
            "monthly_breakdown": r.monthly_breakdown,
            "session_breakdown": r.session_breakdown,
            "day_of_week_breakdown": r.day_of_week_breakdown,
            "failure_zones": r.failure_zones,
            "ai_summary": r.ai_summary,
            "ai_failure_analysis": r.ai_failure_analysis,
        })
    return base
