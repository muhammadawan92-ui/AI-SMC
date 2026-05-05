"""Gemini credentials — from process environment, with reliable load from backend/.env."""

from __future__ import annotations

import os
from pathlib import Path


def _backend_env_file() -> Path:
    # This file lives at backend/app/gemini_env.py → .env is backend/.env
    return Path(__file__).resolve().parent.parent / ".env"


def _parse_value_from_env_file(var_name: str) -> str:
    """Read KEY=value from backend/.env (handles CRLF, UTF-8 BOM) if dotenv/os.environ miss it."""
    path = _backend_env_file()
    if not path.is_file():
        return ""
    for enc in ("utf-8-sig", "utf-8", "utf-16", "utf-16-le", "latin-1"):
        try:
            text = path.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, OSError):
            continue
    else:
        return ""
    for raw_line in text.splitlines():
        line = raw_line.strip("\r").strip()
        if not line or line.startswith("#"):
            continue
        prefix = f"{var_name}="
        if line.startswith(prefix):
            return line[len(prefix) :].strip().strip('"').strip("'")
    return ""


def _refresh_dotenv_from_backend_file() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    p = _backend_env_file()
    if p.is_file():
        load_dotenv(p, override=True)


def gemini_api_key() -> str:
    k = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if k:
        return k
    _refresh_dotenv_from_backend_file()
    k = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if k:
        return k
    return _parse_value_from_env_file("GEMINI_API_KEY").strip()


def gemini_model_id() -> str:
    k = (os.environ.get("GEMINI_MODEL") or "").strip()
    if k:
        return k
    _refresh_dotenv_from_backend_file()
    k = (os.environ.get("GEMINI_MODEL") or "").strip()
    if k:
        return k
    return (_parse_value_from_env_file("GEMINI_MODEL").strip() or "gemini-2.0-flash")


def gemini_vision_model_id() -> str:
    vm = (os.environ.get("VISION_MODEL") or "").strip()
    if vm.lower().startswith("gemini"):
        return vm
    _refresh_dotenv_from_backend_file()
    vm = (os.environ.get("VISION_MODEL") or "").strip()
    if vm.lower().startswith("gemini"):
        return vm
    return gemini_model_id()
