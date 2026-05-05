from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import UploadedFile
from app.services import file_ingestion_service as fis
from app.services.knowledge_doc_service import load_external_knowledge_raw

logger = logging.getLogger(__name__)
router = APIRouter()

FILE_TYPES = ["pine_script", "mql5", "backtest_report", "mt5_log", "screenshot", "csv", "trade_history", "notes"]


@router.post("/")
async def upload_file(
    file: UploadFile = File(...),
    file_type: str = Form(...),
    project_id: Optional[str] = Form(None),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
):
    if file_type not in FILE_TYPES:
        raise HTTPException(400, f"Invalid file_type. Must be one of: {FILE_TYPES}")
    if not file.filename:
        raise HTTPException(400, "No filename provided")

    try:
        record = await fis.save_upload(file, file_type, db, project_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Queue background processing
    background_tasks.add_task(_process_file, record.id, db)

    return {
        "id": record.id,
        "file_name": record.file_name,
        "file_type": record.file_type,
        "file_size_bytes": record.file_size_bytes,
        "processing_status": record.processing_status,
        "message": "File uploaded. Processing queued.",
    }


@router.get("/")
def list_files(project_id: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(UploadedFile)
    if project_id:
        q = q.filter(UploadedFile.project_id == project_id)
    files = q.order_by(UploadedFile.created_at.desc()).limit(100).all()
    return [
        {
            "id": f.id,
            "file_name": f.file_name,
            "file_type": f.file_type,
            "file_size_bytes": f.file_size_bytes,
            "processing_status": f.processing_status,
            "project_id": f.project_id,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in files
    ]


@router.get("/{file_id}")
def get_file(file_id: str, db: Session = Depends(get_db)):
    f = db.get(UploadedFile, file_id)
    if not f:
        raise HTTPException(404, "File not found")
    return {
        "id": f.id,
        "file_name": f.file_name,
        "file_type": f.file_type,
        "file_size_bytes": f.file_size_bytes,
        "processing_status": f.processing_status,
        "processing_error": f.processing_error,
        "parsed_summary": f.parsed_summary,
        "project_id": f.project_id,
        "meta": f.meta,
    }


@router.delete("/{file_id}")
def delete_file(file_id: str, db: Session = Depends(get_db)):
    f = db.get(UploadedFile, file_id)
    if not f:
        raise HTTPException(404, "File not found")
    from pathlib import Path
    try:
        Path(f.file_path).unlink(missing_ok=True)
    except Exception:
        pass
    db.delete(f)
    db.commit()
    return {"deleted": file_id}


def _process_file(file_id: str, db: Session) -> None:
    """Background task: update status after upload."""
    f = db.get(UploadedFile, file_id)
    if not f:
        return
    try:
        f.processing_status = "done"
        if f.file_type in ("pine_script", "mql5", "notes"):
            suffix = Path(f.file_path).suffix.lower()
            if suffix == ".docx":
                content = load_external_knowledge_raw(f.file_path)
            else:
                content = fis.read_text_file(f)
            f.parsed_summary = content[:500] + ("..." if len(content) > 500 else "")
        db.commit()
    except Exception as e:
        f.processing_status = "failed"
        f.processing_error = str(e)
        db.commit()
