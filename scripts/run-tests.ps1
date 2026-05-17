# TeleGallery — run backend unit tests (PowerShell)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path (Join-Path $PSScriptRoot "..") "backend")

Write-Host "Installing dependencies..." -ForegroundColor Cyan
pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip install had errors (common on Python 3.14 without MSVC). Trying tests anyway..." -ForegroundColor Yellow
    Write-Host "Tip: use Python 3.11 or 3.12, or install Visual C++ Build Tools." -ForegroundColor Yellow
}

Write-Host "Running pytest..." -ForegroundColor Cyan
py -m pytest tests -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "All tests passed." -ForegroundColor Green
