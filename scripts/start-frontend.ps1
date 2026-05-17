# TeleGallery — start Vite dev server (PowerShell)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path (Join-Path $PSScriptRoot "..") "frontend")

if (-not (Test-Path "package.json")) {
    Write-Error "frontend/package.json not found."
}

if (-not (Test-Path "node_modules")) {
    Write-Host "Running npm install..." -ForegroundColor Cyan
    npm install
}

Write-Host "Starting frontend on http://localhost:5173" -ForegroundColor Green
npm run dev
