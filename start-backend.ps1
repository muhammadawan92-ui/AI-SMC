# EA AI Platform - Backend Startup Script
# Run ONLY this line in PowerShell (copy/paste one line at a time):
#   & "c:\Users\osama\cursor project\ea-ai-platform\start-backend.ps1"
# Do not paste another command on the same line as .\start-backend.ps1

Write-Host ""
Write-Host "=== EA AI Platform - Backend ===" -ForegroundColor Cyan
Write-Host "Uses backend/.env for MOCK_MODE / LLM keys (this script no longer forces mock)" -ForegroundColor Yellow
Write-Host ""

Set-Location "$PSScriptRoot\backend"

# LLM SDK (openai) — full requirements.txt can fail on Python 3.14 while building pandas
Write-Host "Checking OpenAI SDK..." -ForegroundColor Gray
python -c "import openai" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing openai + anthropic (needed when MOCK_LLM=false)..." -ForegroundColor Yellow
    pip install "openai>=1.57.0" "anthropic>=0.40.0"
}

Write-Host "Checking Google Generative AI SDK (Gemini)..." -ForegroundColor Gray
python -c "import google.generativeai" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing google-generativeai (for LLM_PROVIDER=gemini)..." -ForegroundColor Yellow
    pip install "google-generativeai>=0.8.0"
}

# Create storage dirs if needed
@("storage\uploads", "storage\reports", "storage\strategy_versions") | ForEach-Object {
    if (-not (Test-Path $_)) {
        New-Item -ItemType Directory -Path $_ -Force | Out-Null
    }
}

# Create .env if missing
if (-not (Test-Path ".env")) {
    $envLines = @(
        "APP_ENV=development"
        "DATABASE_URL=sqlite:///./storage/ea_platform.db"
        "UPLOAD_DIR=./storage/uploads"
        "REPORTS_DIR=./storage/reports"
        "VERSIONS_DIR=./storage/strategy_versions"
        "LLM_PROVIDER=openai"
        "OPENAI_API_KEY=sk-placeholder"
        "MOCK_MODE=true"
        "MOCK_LLM=true"
        "ENABLE_LIVE_TRADING=false"
        "FRONTEND_URL=http://localhost:3000"
        "DEBUG=false"
        "LOG_LEVEL=INFO"
    )
    $envLines | Set-Content ".env" -Encoding UTF8
    Write-Host "Created .env with mock mode enabled" -ForegroundColor Green
}

# Initialize DB
Write-Host "Initializing database..." -ForegroundColor Gray
python -m app.database
Write-Host "Database ready." -ForegroundColor Green

# Start server
Write-Host ""
Write-Host "Starting FastAPI server on http://localhost:8000" -ForegroundColor Cyan
Write-Host "API docs: http://localhost:8000/docs" -ForegroundColor Gray
Write-Host "Press Ctrl+C to stop" -ForegroundColor Gray
Write-Host ""

python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
