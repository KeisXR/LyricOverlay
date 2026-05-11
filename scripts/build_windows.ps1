# Lyricaod Windows package builder.
# Creates dist\Lyricaod\Lyricaod.exe with bundled Python dependencies.

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$BuildVenv = Join-Path $ProjectDir ".build-venv"
$WorkDir = Join-Path $ProjectDir ".build-work"
$DistDir = Join-Path $ProjectDir "dist"
$PackageDir = Join-Path $DistDir "Lyricaod"

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
$PythonVersion = & python --version 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Python was not found. Install Python 3.12+ and retry."
}
Write-Host "    Found: $PythonVersion"

if (-not (Test-Path -LiteralPath $BuildVenv)) {
    Write-Host "==> Creating build virtual environment..."
    Invoke-Native python -m venv $BuildVenv
}

$Python = Join-Path $BuildVenv "Scripts\python.exe"
$Pip = Join-Path $BuildVenv "Scripts\pip.exe"

Write-Host "==> Installing build dependencies..."
Invoke-Native $Python -m pip install --upgrade pip setuptools wheel
Invoke-Native $Python -m pip install -r (Join-Path $ProjectDir "requirements.txt") pyinstaller

Write-Host "==> Building Windows executable..."
Invoke-Native $Python -m PyInstaller `
    --clean `
    --noconfirm `
    --distpath $DistDir `
    --workpath $WorkDir `
    (Join-Path $ProjectDir "packaging\lyricaod.spec")

if (Test-Path -LiteralPath (Join-Path $ProjectDir "extension")) {
    Write-Host "==> Copying browser extension files..."
    Copy-Item -Path (Join-Path $ProjectDir "extension") -Destination (Join-Path $PackageDir "extension") -Recurse -Force
}

$OldBuildDir = Join-Path $ProjectDir "build"
if (Test-Path -LiteralPath $OldBuildDir) {
    Write-Host "==> Removing old intermediate build files..."
    Remove-Item -LiteralPath $OldBuildDir -Recurse -Force -ErrorAction SilentlyContinue
}

if (Test-Path -LiteralPath $WorkDir) {
    Write-Host "==> Removing intermediate build files..."
    Remove-Item -LiteralPath $WorkDir -Recurse -Force
}

Write-Host ""
Write-Host "==> Package ready: $PackageDir"
Write-Host "    Run by double-clicking: $(Join-Path $PackageDir 'Lyricaod.exe')"
