from __future__ import annotations

import hashlib
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.models import StrategyProject, UploadedFile

logger = logging.getLogger(__name__)
settings = get_settings()

ALLOWED_TYPES: dict[str, list[str]] = {
    "pine_script": [".pine", ".txt"],
    "mql5": [".mq5", ".mq4", ".mqh", ".txt"],
    "backtest_report": [".htm", ".html", ".xml", ".csv"],
    "mt5_log": [".log", ".txt"],
    "screenshot": [".png", ".jpg", ".jpeg", ".webp", ".gif"],
    "csv": [".csv"],
    "trade_history": [".csv", ".htm", ".html", ".xlsx"],
    "notes": [".txt", ".md", ".docx"],
}

MIME_TYPES: dict[str, str] = {
    ".pine": "text/plain",
    ".txt": "text/plain",
    ".mq5": "text/plain",
    ".mq4": "text/plain",
    ".mqh": "text/plain",
    ".htm": "text/html",
    ".html": "text/html",
    ".xml": "text/xml",
    ".csv": "text/csv",
    ".log": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
}


async def save_upload(
    upload: UploadFile,
    file_type: str,
    db: Session,
    project_id: Optional[str] = None,
) -> UploadedFile:
    suffix = Path(upload.filename or "file.txt").suffix.lower()
    allowed = ALLOWED_TYPES.get(file_type, [])
    if allowed and suffix not in allowed:
        raise ValueError(f"File type '{suffix}' not allowed for category '{file_type}'. Allowed: {allowed}")

    content = await upload.read()
    size = len(content)
    if size > settings.max_upload_bytes:
        raise ValueError(f"File too large: {size / 1024 / 1024:.1f} MB (max {settings.max_upload_size_mb} MB)")

    # Store under project-specific folder
    dest_folder = Path(settings.upload_dir) / (project_id or "general") / file_type
    dest_folder.mkdir(parents=True, exist_ok=True)

    safe_name = f"{uuid.uuid4().hex}_{_safe_filename(upload.filename or 'upload')}"
    dest_path = dest_folder / safe_name

    dest_path.write_bytes(content)
    logger.info("Saved upload: %s (%d bytes)", dest_path, size)

    file_record = UploadedFile(
        project_id=project_id,
        file_name=upload.filename or safe_name,
        file_type=file_type,
        file_path=str(dest_path),
        file_size_bytes=size,
        mime_type=MIME_TYPES.get(suffix),
        processing_status="pending",
        meta={"sha256": _sha256(content)},
    )
    db.add(file_record)
    db.commit()
    db.refresh(file_record)
    return file_record


def read_text_file(file_record: UploadedFile) -> str:
    path = Path(file_record.file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    # Try common encodings used by MT5 and trading tools.
    for enc in ("utf-8", "utf-16", "utf-16-le", "utf-16-be", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    # Final fallback with replacement to avoid hard failure.
    return path.read_text(encoding="utf-8", errors="replace")


def ensure_project_exists(db: Session, project_id: Optional[str], name: str = "Default Project") -> StrategyProject:
    if project_id:
        proj = db.get(StrategyProject, project_id)
        if proj:
            return proj
    proj = StrategyProject(name=name)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    return proj


def _safe_filename(name: str) -> str:
    keep = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    return "".join(c if c in keep else "_" for c in name)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
