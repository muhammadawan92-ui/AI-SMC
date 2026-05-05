from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_settings, uses_openai_compatible_client
from app.database import init_db

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("ea_platform")


def _gemini_key_looks_invalid() -> bool:
    k = (os.environ.get("GEMINI_API_KEY") or "").strip()
    return len(k) < 12


def _openai_key_looks_invalid() -> bool:
    k = (settings.openai_api_key or "").strip()
    if not k:
        return True
    low = k.lower()
    if low in ("sk-placeholder", "sk-your-openai-key-here", "sk-your-openai-key"):
        return True
    if k.startswith("sk-") and len(k) < 24:
        return True
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting EA AI Platform backend…")
    # Re-apply backend/.env on every startup (helps uvicorn --reload / workers pick up GEMINI_*).
    try:
        from pathlib import Path

        from dotenv import load_dotenv

        _env = Path(__file__).resolve().parent.parent / ".env"
        if _env.is_file():
            load_dotenv(_env, override=True)
    except ImportError:
        pass
    settings.ensure_directories()
    init_db()
    logger.info("Database ready.")
    if (
        settings.llm_provider == "openai"
        and not settings.mock_mode
        and not settings.mock_llm
        and _openai_key_looks_invalid()
    ):
        logger.warning(
            "OPENAI_API_KEY is missing or still a placeholder, but MOCK_MODE and MOCK_LLM are false. "
            "The API will start, but any LLM/vision call will fail until you set a real key in backend/.env "
            "or set MOCK_LLM=true (and optionally MOCK_MODE=true) for offline stubs."
        )
    if (
        settings.llm_provider == "gemini"
        and not settings.mock_mode
        and not settings.mock_llm
        and _gemini_key_looks_invalid()
    ):
        logger.warning(
            "GEMINI_API_KEY is missing or too short, but MOCK_MODE and MOCK_LLM are false. "
            "Set GEMINI_API_KEY in the environment (or backend/.env), or enable MOCK_LLM=true."
        )
    if (
        uses_openai_compatible_client(settings.llm_provider)
        and not settings.mock_mode
        and not settings.mock_llm
    ):
        vision_m = (settings.local_llm_vision_model or "").strip() or settings.local_llm_model
        logger.info(
            "LLM_PROVIDER=%s — OpenAI-compatible API at %s (text model: %s, vision model: %s). "
            "Ensure the endpoint is up; for Ollama use `ollama pull` for each model name.",
            settings.llm_provider,
            settings.local_llm_base_url,
            settings.local_llm_model,
            vision_m,
        )
    yield
    logger.info("Shutting down EA AI Platform backend.")


app = FastAPI(
    title="EA AI Platform",
    description="AI-Assisted Expert Advisor Research & Trading System",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

allowed_origins = list({
    settings.frontend_url,
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
})

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static storage for uploaded files (served for frontend preview)
os.makedirs(settings.upload_dir, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")

# Register API routes
from app.api.router import api_router  # noqa: E402

app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "env": settings.app_env,
        "mock_mode": settings.mock_mode,
        "live_trading_enabled": settings.enable_live_trading,
    }
