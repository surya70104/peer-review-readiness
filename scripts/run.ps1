$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Venv = Join-Path $Root ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"

if (-not (Test-Path $Py)) {
    Write-Host "[run] creating virtual environment..."
    python -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create venv (is Python on PATH?)" }
}

Write-Host "[run] installing requirements..."
& $Py -m pip install --quiet --disable-pip-version-check -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

if (-not (Test-Path (Join-Path $Root ".env"))) {
    Copy-Item (Join-Path $Root ".env.example") (Join-Path $Root ".env")
    Write-Host "[run] created .env from .env.example - edit the password if needed"
}

Write-Host "[run] running migrations..."
& $Py (Join-Path $Root "scripts\migrate.py")
if ($LASTEXITCODE -ne 0) { throw "Migrations failed" }

Write-Host "[run] starting server on http://127.0.0.1:8015 (Ctrl+C to stop)"
& $Py -m uvicorn app.main:app --host 127.0.0.1 --port 8015
