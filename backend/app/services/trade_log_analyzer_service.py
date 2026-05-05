from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import TradeLog
from app.services.llm_service import get_llm_service

logger = logging.getLogger(__name__)


def parse_and_store_logs(
    content: str,
    project_id: Optional[str],
    db: Session,
) -> list[TradeLog]:
    lines = content.splitlines()
    records: list[TradeLog] = []
    for line in lines[:10000]:  # limit
        line = line.strip()
        if not line:
            continue
        parsed = _parse_line(line)
        is_decision = _is_decision_line(line)
        decision_type = _extract_decision_type(line) if is_decision else None
        log = TradeLog(
            project_id=project_id,
            log_time=_parse_time(parsed.get("timestamp", "")),
            log_level=parsed.get("level", "info"),
            message=parsed.get("message", line),
            source=parsed.get("source", "expert"),
            raw_line=line,
            is_decision=is_decision,
            decision_type=decision_type,
        )
        db.add(log)
        records.append(log)
    db.commit()
    return records


def analyze_log_decisions(project_id: str, db: Session) -> str:
    logs = (
        db.query(TradeLog)
        .filter(TradeLog.project_id == project_id, TradeLog.is_decision == True)
        .order_by(TradeLog.created_at.desc())
        .limit(100)
        .all()
    )
    if not logs:
        return "No trade decisions found in logs."
    llm = get_llm_service()
    log_text = "\n".join(l.message for l in logs[:50])
    prompt = f"""Analyze these MT5 EA trade decisions and identify patterns:

{log_text}

Identify:
1. Most common entry reasons
2. Most common skip/block reasons
3. Time patterns in decisions
4. Any red flags or unusual behavior
5. Comparison with expected SMC trading behavior"""
    try:
        return llm.complete(prompt)
    except Exception as e:
        return f"Analysis failed: {e}"


def _parse_line(line: str) -> dict:
    # MT5 format: YYYY.MM.DD HH:MM:SS.mmm<TAB>...
    parts = line.split("\t", 3)
    if len(parts) >= 3:
        return {"timestamp": parts[0].strip(), "level": parts[1].strip(), "source": parts[2].strip(),
                "message": parts[3].strip() if len(parts) > 3 else ""}
    return {"timestamp": "", "level": "info", "source": "log", "message": line}


def _parse_time(ts: str) -> Optional[datetime]:
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(ts[:19], fmt)
        except ValueError:
            pass
    return None


def _is_decision_line(line: str) -> bool:
    keywords = ["order", "trade", "buy", "sell", "close", "open position", "skip", "signal", "entry", "blocked"]
    return any(k in line.lower() for k in keywords)


def _extract_decision_type(line: str) -> str:
    lower = line.lower()
    if any(k in lower for k in ["order opened", "position opened", "buy opened", "sell opened"]):
        return "trade"
    if any(k in lower for k in ["order closed", "position closed"]):
        return "close"
    if any(k in lower for k in ["skip", "no signal", "waiting", "no entry"]):
        return "skip"
    if any(k in lower for k in ["blocked", "rejected", "risk", "drawdown", "spread"]):
        return "block_risk"
    return "signal"
