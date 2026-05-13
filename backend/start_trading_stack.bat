@echo off
title AI SMC Trading Stack - Laptop

setlocal

set "BACKEND_DIR=C:\Users\osama\cursor project\ea-ai-platform\backend"

REM Prefer EXNESS MT5 if installed, otherwise use standard MT5 path.
set "MT5_EXNESS=C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe"
set "MT5_STANDARD=C:\Program Files\MetaTrader 5\terminal64.exe"

if exist "%MT5_EXNESS%" (
    set "MT5_EXE=%MT5_EXNESS%"
) else (
    set "MT5_EXE=%MT5_STANDARD%"
)

echo ==================================================
echo Starting AI SMC Trading Stack
echo Backend: %BACKEND_DIR%
echo MT5:     %MT5_EXE%
echo ==================================================
echo.

if not exist "%BACKEND_DIR%" (
    echo ERROR: Backend folder not found:
    echo %BACKEND_DIR%
    echo.
    pause
    exit /b 1
)

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python was not found in PATH.
    echo Install Python or add it to PATH, then run this BAT again.
    echo.
    pause
    exit /b 1
)

echo Starting MT5...
if exist "%MT5_EXE%" (
    start "MT5" "%MT5_EXE%"
) else (
    echo WARNING: MT5 terminal was not found at:
    echo %MT5_EXE%
    echo Update MT5_EXNESS or MT5_STANDARD inside this BAT if needed.
)

echo.
echo Checking whether Ollama should be started...
findstr /I /R "^LLM_PROVIDER=ollama" "%BACKEND_DIR%\.env" >nul 2>&1
if not errorlevel 1 (
    where ollama >nul 2>&1
    if not errorlevel 1 (
        echo Starting Ollama server...
        start "Ollama Server" cmd /k "ollama serve"
        timeout /t 5 /nobreak >nul
    ) else (
        echo WARNING: LLM_PROVIDER=ollama but Ollama command was not found.
    )
) else (
    echo Ollama not enabled in .env, skipping.
)

echo.
echo Waiting for MT5 to load...
timeout /t 12 /nobreak >nul

echo.
echo Starting FastAPI backend...
start "EA AI Backend" /D "%BACKEND_DIR%" cmd /k "python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"

timeout /t 4 /nobreak >nul

echo.
echo Starting SMC overlay auto-refresh loop...
start "SMC Overlay Loop" /D "%BACKEND_DIR%" cmd /k "python run_smc_overlay_loop.py"

timeout /t 3 /nobreak >nul

echo.
echo Starting AI executor loop...
start "AI Executor Loop" /D "%BACKEND_DIR%" cmd /k "python run_ai_executor_loop.py --interval 300"

timeout /t 3 /nobreak >nul

echo.
echo Starting AI trade monitor...
start "AI Trade Monitor" /D "%BACKEND_DIR%" cmd /k "python monitor_ai_trades.py --loop 30"

echo.
echo ==================================================
echo AI SMC stack started.
echo Keep the opened CMD windows running.
echo.
echo Windows opened:
echo 1. EA AI Backend
echo 2. SMC Overlay Loop
echo 3. AI Executor Loop
echo 4. AI Trade Monitor
echo 5. Ollama Server, only if enabled/found
echo ==================================================
echo.
pause

endlocal
