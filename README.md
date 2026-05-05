# EA AI Platform — AI-Assisted EA Research & Trading System

A full-stack AI-powered platform for analyzing, improving, and eventually deploying a profitable Expert Advisor built from Pine Script and Smart Money Concepts (SMC) logic.

---

## Architecture

```
ea-ai-platform/
├── backend/          # Python FastAPI backend
│   ├── app/
│   │   ├── api/      # Route handlers
│   │   ├── models/   # SQLAlchemy ORM models
│   │   ├── services/ # Business logic services
│   │   ├── agents/   # LLM strategy agent
│   │   ├── utils/    # Helpers
│   │   └── workers/  # Background job workers
│   └── tests/
├── frontend/         # Next.js + Tailwind CSS dashboard
├── storage/
│   ├── uploads/           # Uploaded files (Pine, MQL5, CSV, screenshots)
│   ├── reports/           # Generated reports
│   └── strategy_versions/ # EA version snapshots
└── docs/
```

---

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+
- (Optional) MetaTrader5 installed locally (Windows only)

### 1. Clone & configure

```bash
cp .env.example .env
# Edit .env with your LLM API keys and settings
```

### 2. Backend setup

```bash
cd backend
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

pip install -r requirements.txt
python -m app.database   # Initialize database
uvicorn app.main:app --reload --port 8000
```

### 3. Frontend setup

```bash
cd frontend
npm install
npm run dev
# Opens at http://localhost:3000
```

---

## System Phases

| Phase | Status | Description |
|-------|--------|-------------|
| 1 | MVP | File ingestion & strategy understanding |
| 2 | MVP | Baseline backtest analysis |
| 3 | MVP | AI improvement engine |
| 4 | MVP | Backtest comparison & versioning |
| 5 | Next | Screenshot & live chart analysis |
| 6 | Next | MT5 log monitoring |
| 7 | Locked | Controlled demo/live trading |

---

## Live Trading Safety

Live trading is **disabled by default** and requires:
1. `ENABLE_LIVE_TRADING=true` in `.env`
2. Demo validation phase completed
3. Manual user approval in dashboard
4. All risk limits configured

**Risk controls**: max daily loss, max drawdown, max lot size, max trades/day, consecutive loss stop, kill switch.

---

## LLM Providers

Supported (configure via `.env`):
- OpenAI GPT-4o / GPT-4
- Anthropic Claude 3.5 Sonnet / Claude 3 Opus
- Any OpenAI-compatible local model (Ollama, LM Studio)

---

## API Docs

Once backend is running, visit:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

---

## Environment Variables

See `.env.example` for all configuration options.

---

## Key Features

- **Pine Script Parser** — Extracts SMC logic, conditions, filters from Pine Script source
- **MQL5 Parser** — Reads EA code and maps it to Pine Script logic
- **Backtest Analyzer** — Parses MT5 HTML backtest reports into structured metrics
- **SMC Knowledge Module** — Built-in SMC concept dictionary (BOS, CHOCH, OB, FVG, etc.)
- **Improvement Engine** — LLM generates hypothesis-driven improvements with SMC reasoning
- **Confidence Scoring** — Multi-factor score to determine demo/live readiness
- **Version Manager** — Track all EA versions with changelogs and test results
- **Screenshot Analyzer** — Vision AI analysis of TradingView chart screenshots
- **MT5 Bridge** — Read MT5 logs, positions, and history
- **Trading Controller** — Safe, logged, approval-gated trade execution

---

## License

Research/personal use only. Not financial advice. Live trading at own risk.
