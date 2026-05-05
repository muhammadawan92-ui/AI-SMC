from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_backend_dotenv_into_environ() -> None:
    """Populate os.environ from backend/.env so GEMINI_* are available before any reads."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.is_file():
        # override=True: if the shell/IDE injected an empty GEMINI_API_KEY (or stale value),
        # backend/.env still wins so local keys are picked up.
        load_dotenv(env_path, override=True)


_load_backend_dotenv_into_environ()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_env: Literal["development", "production", "test"] = "development"
    app_secret_key: str = "dev-secret-key-change-in-production"
    debug: bool = True
    log_level: str = "INFO"

    # Database
    database_url: str = "sqlite:///./storage/ea_platform.db"

    # Storage
    upload_dir: str = "./storage/uploads"
    reports_dir: str = "./storage/reports"
    versions_dir: str = "./storage/strategy_versions"
    max_upload_size_mb: int = 50

    # LLM — ollama uses the same OpenAI-compatible HTTP API as openai_compatible (default base Ollama:11434/v1).
    llm_provider: Literal["openai", "anthropic", "openai_compatible", "ollama", "gemini"] = "openai"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_max_tokens: int = 4096
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-sonnet-20241022"
    anthropic_max_tokens: int = 4096
    # Google Gemini — set GEMINI_API_KEY and GEMINI_MODEL in environment (or backend/.env); never in code.
    gemini_max_output_tokens: int = 8192
    local_llm_base_url: str = "http://localhost:11434/v1"
    local_llm_model: str = "llama3.1:8b"
    local_llm_vision_model: str = "llava"
    local_llm_api_key: str = "ollama"
    # For Gemini provider, use a gemini-* id unless you override (e.g. gemini-2.0-flash)
    vision_model: str = "gpt-4o"

    # MT5
    mt5_terminal_path: str = ""
    mt5_account: int = 0
    mt5_password: str = ""
    mt5_server: str = ""
    mt5_data_dir: str = ""

    # Live trading — always false by default
    enable_live_trading: bool = False

    # Risk controls
    max_daily_loss_usd: float = 100.0
    max_weekly_loss_usd: float = 300.0
    max_drawdown_percent: float = 10.0
    max_lot_size: float = 0.10
    max_trades_per_day: int = 5
    max_open_trades: int = 2
    max_consecutive_losses: int = 3
    spread_filter_pips: float = 3.0
    slippage_filter_pips: float = 2.0
    symbol_whitelist: str = "XAUUSD,EURUSD,GBPUSD"
    session_whitelist: str = "london,new_york,london_new_york_overlap"

    # TradingView
    tradingview_webhook_secret: str = ""

    # External SMC reference (Word .docx) — merged into prompts and mock analysis
    smc_knowledge_docx_path: str = ""
    smc_knowledge_max_chars: int = 12000

    # CORS
    frontend_url: str = "http://localhost:3000"

    # Mock mode
    mock_mode: bool = False
    mock_llm: bool = False

    @property
    def symbol_whitelist_list(self) -> list[str]:
        return [s.strip() for s in self.symbol_whitelist.split(",") if s.strip()]

    @property
    def session_whitelist_list(self) -> list[str]:
        return [s.strip() for s in self.session_whitelist.split(",") if s.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    def ensure_directories(self) -> None:
        for path in [self.upload_dir, self.reports_dir, self.versions_dir]:
            os.makedirs(path, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def uses_openai_compatible_client(llm_provider: str) -> bool:
    """True for local OpenAI-compatible APIs (Ollama, LM Studio, vLLM, etc.)."""
    return llm_provider in ("openai_compatible", "ollama")
