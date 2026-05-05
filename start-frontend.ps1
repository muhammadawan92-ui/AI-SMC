# EA AI Platform - Frontend Startup Script
# Requires Node.js 18+ with npm installed
# Download from: https://nodejs.org/en/download

Write-Host ""
Write-Host "=== EA AI Platform - Frontend ===" -ForegroundColor Cyan

# Check for npm
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "ERROR: npm not found in PATH." -ForegroundColor Red
    Write-Host "Please install Node.js 18+ from: https://nodejs.org/en/download" -ForegroundColor Yellow
    Write-Host "After installation, reopen this terminal and run this script again." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

Set-Location "$PSScriptRoot\frontend"

# Create .env.local if missing
if (-not (Test-Path ".env.local")) {
    "NEXT_PUBLIC_API_URL=http://localhost:8000" | Set-Content ".env.local"
    Write-Host "Created .env.local" -ForegroundColor Green
}

# Install dependencies if needed
if (-not (Test-Path "node_modules")) {
    Write-Host "Installing dependencies (this takes a minute)..." -ForegroundColor Gray
    npm install
}

Write-Host ""
Write-Host "Starting Next.js frontend on http://localhost:3000" -ForegroundColor Cyan
Write-Host "Make sure backend is running on port 8000" -ForegroundColor Yellow
Write-Host ""

npm run dev
