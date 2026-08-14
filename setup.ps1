# Lyricaod Windows setup script.
# Creates a virtual environment and installs the locked runtime dependencies.
#
# Usage (PowerShell):
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\setup.ps1

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ScriptDir ".venv"

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $FilePath $Arguments"
    }
}

Write-Host "==> Checking Python..."
Invoke-Native python --version

if (-not (Test-Path -LiteralPath $VenvDir)) {
    Write-Host "==> Creating virtual environment..."
    Invoke-Native python -m venv $VenvDir
} else {
    Write-Host "==> Using existing virtual environment at $VenvDir"
}

$Python = Join-Path $VenvDir "Scripts\python.exe"

Write-Host "==> Installing locked runtime dependencies..."
Invoke-Native $Python -m pip install --disable-pip-version-check "pip==26.1.1"
Invoke-Native $Python -m pip install --disable-pip-version-check -r (Join-Path $ScriptDir "requirements.lock")

Write-Host ""
Write-Host "==> Setup complete!"
Write-Host "   Run:  $Python $ScriptDir\src\main.py"
Write-Host "   Or:   & '$VenvDir\Scripts\Activate.ps1' ; python src\main.py"
