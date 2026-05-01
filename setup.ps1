# Lyricaod Windows setup script
# Creates a Python venv and installs dependencies.
#
# Usage (PowerShell):
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\setup.ps1

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir   = Join-Path $ScriptDir ".venv"

Write-Host "==> Checking Python..."
try {
    $PythonVersion = & python --version 2>&1
    Write-Host "    Found: $PythonVersion"
} catch {
    Write-Error "Python not found. Install Python 3.10+ from https://python.org and retry."
    exit 1
}

if (-not (Test-Path $VenvDir)) {
    Write-Host "==> Creating virtual environment..."
    & python -m venv $VenvDir
} else {
    Write-Host "==> Using existing virtual environment at $VenvDir"
}

$Pip    = Join-Path $VenvDir "Scripts\pip.exe"
$Python = Join-Path $VenvDir "Scripts\python.exe"

Write-Host "==> Upgrading pip..."
& $Pip install --upgrade pip setuptools wheel

Write-Host "==> Installing dependencies (PySide6 is ~200 MB, this may take a while)..."
& $Pip install -r (Join-Path $ScriptDir "requirements.txt")

Write-Host ""
Write-Host "==> Setup complete!"
Write-Host "   Run:  $Python $ScriptDir\src\main.py"
Write-Host "   Or:   & '$VenvDir\Scripts\Activate.ps1' ; python src\main.py"
