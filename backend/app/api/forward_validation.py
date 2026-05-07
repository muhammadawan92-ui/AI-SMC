from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
REPO_ROOT = BACKEND_DIR.parent


def _knowledge_paths() -> list[Path]:
    return [
        REPO_ROOT / "storage" / "knowledge" / "backtest_knowledge_latest.json",
        BACKEND_DIR / "storage" / "knowledge" / "backtest_knowledge_latest.json",
    ]


def _reports_dir() -> Path:
    return REPO_ROOT / "storage" / "reports"


def _latest_existing(paths: list[Path]) -> Path | None:
    for p in paths:
        if p.exists():
            return p
    return None


def _latest_report_file() -> Path | None:
    reports_dir = _reports_dir()
    if not reports_dir.exists():
        return None
    candidates = list(reports_dir.glob("backtest_vs_forward_*.txt"))
    if not candidates:
        return None
    return sorted(candidates, key=lambda x: x.stat().st_mtime, reverse=True)[0]


class RunCompareRequest(BaseModel):
    symbol: str | None = None
    strategy_version: str | None = None


@router.get("/latest")
def latest_forward_validation() -> dict[str, Any]:
    knowledge_file = _latest_existing(_knowledge_paths())
    knowledge = {}
    if knowledge_file:
        try:
            knowledge = json.loads(knowledge_file.read_text(encoding="utf-8"))
        except Exception:
            knowledge = {}

    latest_report = _latest_report_file()
    report_text = ""
    if latest_report:
        try:
            report_text = latest_report.read_text(encoding="utf-8")
        except Exception:
            report_text = ""

    return {
        "knowledge_path": str(knowledge_file) if knowledge_file else "",
        "knowledge": knowledge,
        "latest_report_path": str(latest_report) if latest_report else "",
        "latest_report_text": report_text,
    }


@router.post("/run-compare")
def run_compare(req: RunCompareRequest) -> dict[str, Any]:
    script = BACKEND_DIR / "compare_backtest_vs_forward.py"
    if not script.exists():
        raise HTTPException(status_code=404, detail=f"Script not found: {script}")

    cmd = [sys.executable, str(script)]
    if req.symbol and req.symbol.strip():
        cmd.extend(["--symbol", req.symbol.strip()])
    if req.strategy_version and req.strategy_version.strip():
        cmd.extend(["--strategy-version", req.strategy_version.strip()])

    proc = subprocess.run(
        cmd,
        cwd=str(BACKEND_DIR),
        capture_output=True,
        text=True,
    )

    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if proc.returncode != 0:
        raise HTTPException(status_code=500, detail=output.strip() or "Comparison script failed.")

    latest_report = _latest_report_file()
    return {
        "ok": True,
        "output": output.strip(),
        "latest_report_path": str(latest_report) if latest_report else "",
    }

