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

# SIG # Begin signature block
# MIIGCwYJKoZIhvcNAQcCoIIF/DCCBfgCAQExCzAJBgUrDgMCGgUAMGkGCisGAQQB
# gjcCAQSgWzBZMDQGCisGAQQBgjcCAR4wJgIDAQAABBAfzDtgWUsITrck0sYpfvNR
# AgEAAgEAAgEAAgEAAgEAMCEwCQYFKw4DAhoFAAQUi7jqd2RmdwA08MppfUVhX5Ho
# 8PGgggNyMIIDbjCCAlagAwIBAgIQHVj3ZSS1ZIpFvwMp46ANwzANBgkqhkiG9w0B
# AQsFADBOMUwwSgYDVQQDDENQb3dlclNoZWxs57mn772557mn772v57md772q57md
# 5Yqx44Oo6YSC772y6Jy35ZKy55WR6Zqq77286K2P5Y+W5baMMCAXDTI2MDUxMTA5
# MjkwMloYDzIwOTgxMjMxMTUwMDAwWjBOMUwwSgYDVQQDDENQb3dlclNoZWxs57mn
# 772557mn772v57md772q57md5Yqx44Oo6YSC772y6Jy35ZKy55WR6Zqq77286K2P
# 5Y+W5baMMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAoFjguOzfIn/a
# v8oE8WMxcPlzshIOJXsj4bH9fXSYQzAZNrwLDS0r5r8i/TLvfMvJWdriPKL7T05A
# xXMjEPX95Cbto+dA8P4Phnu5nqHDnK0jfLT/zvV4Gof1WyPc5hfPPMuX4Imiw0Qw
# kItTNQPzs7B6wzF4XxC85XCA7LboapdilO60uZ1YQm172SteLCCjZNuDViRmldG4
# +hhwYKiRdoIzuOEetFJomuTMT2zIIbo9fv+KxuwthVLfUJSM3nno8kkWIOlCnnbX
# tPa4PCSZX44qPx/lLospdrXZH+I2S2jWpaSuZCcw+ekQmWXvlcULhyeiVIQaUIx8
# b+C2ln7OXQIDAQABo0YwRDAOBgNVHQ8BAf8EBAMCB4AwEwYDVR0lBAwwCgYIKwYB
# BQUHAwMwHQYDVR0OBBYEFJLfgb1Q8Yk4Z62AlI9KyhEQfMNkMA0GCSqGSIb3DQEB
# CwUAA4IBAQB1ZEYT29QNOgZsr/KuGbHD7g5DmNAABk9xbz6IPYwprzwsPU6IQzDV
# B0zBqCEGeKEUw0Ynb3KDa7Ge4Q3g8rpllyHjxNwYMNbg/nVzMv5VFvCwsd48eOF2
# NEWn+EiTZejEPqd1UptLl1UGrvz34a5VcWY3B9KhuGAR2gI7Sv3y908Qx4aORAGd
# IgTEfyi0pZ5NM6tJ8pnBm2nrnsWTUxfMMVbqMrflaMYGQzuBWcU30fDWcD5TqRAz
# ZlwL+nD3Dmay3eRrIw8aND1crrWsVWXzAamMDAEXyqGb149Sov7I5xuQqG01Q9Vx
# MCad6n3V2Zue8Lb5vqo3IjTO/Pa28Y76MYICAzCCAf8CAQEwYjBOMUwwSgYDVQQD
# DENQb3dlclNoZWxs57mn772557mn772v57md772q57md5Yqx44Oo6YSC772y6Jy3
# 5ZKy55WR6Zqq77286K2P5Y+W5baMAhAdWPdlJLVkikW/AynjoA3DMAkGBSsOAwIa
# BQCgeDAYBgorBgEEAYI3AgEMMQowCKACgAChAoAAMBkGCSqGSIb3DQEJAzEMBgor
# BgEEAYI3AgEEMBwGCisGAQQBgjcCAQsxDjAMBgorBgEEAYI3AgEVMCMGCSqGSIb3
# DQEJBDEWBBRAbpMhWxgcA7N9pix+ww6/uKhGTDANBgkqhkiG9w0BAQEFAASCAQBA
# V6twEHmGwIlX5HoH1e5PBOaIhv+N1AMYmy/Zrp8hsC00C1M2Dgh3OUuRPSE3+g5K
# gqVea4HoHX3egoO1868JwjosFCttJq3lHyrzDLJ20G3x4qbZkcM5OGWB0B8+MNrH
# 4SJMyu9xdmUP0u/pvrvrxlpa2TUqwIxxCvVtmGU9uF8JRbsX44ATB+ksOxo+B/bt
# pHybG95sVeWtTVYKlEj0Bzzf16q/gepHlIj/ipwGq173tm3vhKrNHIqx59+nrTCE
# bSAPvGtbV2AnTSZ/d/bRvu3J0DoLND7IlPV7KPyDmulBURIjF98sbmvyCkviUp/X
# foTAb1/GfVWomM8jgxmv
# SIG # End signature block
