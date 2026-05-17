# TeleGallery — start FastAPI backend (PowerShell)
$ErrorActionPreference = "Stop"
$root = Join-Path $PSScriptRoot ".."
Set-Location $root

$requirements = "backend\requirements.txt"
$venvDir = "backend\venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$venvPip = Join-Path $venvDir "Scripts\pip.exe"
$depsMarker = Join-Path $venvDir ".deps-installed"

if (-not (Test-Path $requirements)) {
    Write-Error "Run from TeleGallery repo root (scripts folder must exist)."
}

function Install-BackendDeps {
    Write-Host "Installing backend dependencies..." -ForegroundColor Cyan
    if (-not (Test-Path $venvDir)) {
        Write-Host "Creating virtual environment at $venvDir" -ForegroundColor Cyan
        py -m venv $venvDir
    }
    & $venvPip install -r $requirements
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Set-Content -Path $depsMarker -Value (Get-Date -Format o) -Encoding utf8
}

$needsInstall = $false
if (-not (Test-Path $venvPython)) {
    $needsInstall = $true
} elseif (-not (Test-Path $depsMarker)) {
    $needsInstall = $true
} elseif ((Get-Item $requirements).LastWriteTime -gt (Get-Item $depsMarker).LastWriteTime) {
    Write-Host "requirements.txt changed — refreshing dependencies..." -ForegroundColor Cyan
    $needsInstall = $true
}

if ($needsInstall) {
    Install-BackendDeps
}

Write-Host "Starting TeleGallery backend on http://127.0.0.1:8000" -ForegroundColor Green
& $venvPython start.py
