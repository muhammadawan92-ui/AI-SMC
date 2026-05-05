from __future__ import annotations

import logging
from typing import Any

from app.database import SessionLocal

logger = logging.getLogger(__name__)


def run_full_analysis_pipeline(project_id: str, options: dict[str, Any] | None = None) -> dict:
    """
    Runs the full analysis pipeline for a project:
    1. Parse Pine Script (if uploaded)
    2. Parse MQL5 EA (if uploaded)
    3. Analyze backtest report (if uploaded)
    4. Generate improvement ideas
    5. Compute initial confidence score

    This runs in a background thread/task.
    """
    opts = options or {}
    results: dict[str, Any] = {"project_id": project_id, "steps": {}}
    db = SessionLocal()

    try:
        from app.models.models import UploadedFile, PineScriptSource, MQL5Source, BacktestReport
        from app.services import (
            pine_parser_service as pps,
            mql5_parser_service as mps,
            backtest_analyzer_service as bas,
            improvement_engine_service as ies,
            file_ingestion_service as fis,
        )

        # Step 1: Pine Script
        pine_files = db.query(UploadedFile).filter(
            UploadedFile.project_id == project_id,
            UploadedFile.file_type == "pine_script",
        ).all()
        pine_src = None
        for pf in pine_files:
            try:
                code = fis.read_text_file(pf)
                pine_src = pps.parse_pine_script(code, pf, db, project_id, run_llm=opts.get("run_llm", True))
                results["steps"]["pine_analysis"] = {"status": "done", "id": pine_src.id}
            except Exception as e:
                results["steps"]["pine_analysis"] = {"status": "failed", "error": str(e)}

        # Step 2: MQL5
        mql5_files = db.query(UploadedFile).filter(
            UploadedFile.project_id == project_id,
            UploadedFile.file_type == "mql5",
        ).all()
        mql5_src = None
        for mf in mql5_files:
            try:
                code = fis.read_text_file(mf)
                pine_code = pine_src.raw_code if pine_src else None
                mql5_src = mps.parse_mql5_ea(code, mf, db, project_id, pine_code, run_llm=opts.get("run_llm", True))
                results["steps"]["mql5_analysis"] = {"status": "done", "id": mql5_src.id}
            except Exception as e:
                results["steps"]["mql5_analysis"] = {"status": "failed", "error": str(e)}

        # Step 3: Backtest
        bt_files = db.query(UploadedFile).filter(
            UploadedFile.project_id == project_id,
            UploadedFile.file_type == "backtest_report",
        ).all()
        bt_report = None
        for bf in bt_files:
            try:
                bt_report = bas.parse_backtest_report(
                    bf, db, project_id, "baseline", is_baseline=True, run_llm=opts.get("run_llm", True)
                )
                results["steps"]["backtest_analysis"] = {"status": "done", "id": bt_report.id}
            except Exception as e:
                results["steps"]["backtest_analysis"] = {"status": "failed", "error": str(e)}

        # Step 4: Improvements (only if backtest exists)
        if bt_report:
            try:
                ideas = ies.generate_improvement_ideas(project_id, db, bt_report, pine_src, mql5_src)
                results["steps"]["improvement_generation"] = {"status": "done", "count": len(ideas)}
            except Exception as e:
                results["steps"]["improvement_generation"] = {"status": "failed", "error": str(e)}

        results["status"] = "completed"
    except Exception as e:
        logger.error("Analysis pipeline failed for project %s: %s", project_id, e)
        results["status"] = "failed"
        results["error"] = str(e)
    finally:
        db.close()

    return results
