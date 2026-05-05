@echo off
set BACKEND_DIR=C:\Users\osama\cursor project\ea-ai-platform\backend
set MT5_EXE=C:\Program Files\MetaTrader 5\terminal64.exe

echo Starting MT5...
if exist "%MT5_EXE%" start "MT5" "%MT5_EXE%"

echo Starting Ollama server if needed...
start "Ollama Server" cmd /k "ollama serve"

timeout /t 5 /nobreak >nul

echo Starting FastAPI backend...
start "EA AI Backend" cmd /k "cd /d "%BACKEND_DIR%" && python -m uvicorn app.main:app --reload"

echo Starting SMC overlay auto-refresh loop...
start "SMC Overlay Loop" cmd /k "cd /d "%BACKEND_DIR%" && python run_smc_overlay_loop.py"

echo Done. Keep these windows open.
