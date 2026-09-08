# Installs `immortility` onto PATH so it works from cmd.exe and PowerShell
# in any directory (PowerShell will not run a command from the current folder).
#
# Usage (from repo root or anywhere):
#   powershell -ExecutionPolicy Bypass -File scripts\install_cli.ps1

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Launcher = Join-Path $RepoRoot "immortility.bat"
$VenvPython = Join-Path $RepoRoot "venv\Scripts\python.exe"
$BinDir = Join-Path $env:USERPROFILE ".local\bin"
$Shim = Join-Path $BinDir "immortility.cmd"

if (-not (Test-Path $Launcher)) {
    throw "Launcher not found: $Launcher"
}

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

$shimBody = @"
@echo off
call "$RepoRoot\immortility.bat" %*
"@
Set-Content -Path $Shim -Value $shimBody -Encoding ASCII -NoNewline

# Drop a .bat twin too — some tools look for .bat specifically.
Copy-Item -Force $Shim (Join-Path $BinDir "immortility.bat")

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($null -eq $userPath) { $userPath = "" }

$parts = @()
$seen = @{}
foreach ($raw in ($userPath -split ";")) {
    $p = $raw.Trim()
    if (-not $p) { continue }
    # Stale entry from when the repo lived on the Desktop, not Projects\
    if ($p -match '(?i)\\Desktop\\immortility1\\?$') { continue }
    $key = $p.ToLowerInvariant()
    if ($seen.ContainsKey($key)) { continue }
    $seen[$key] = $true
    $parts += $p
}

$binKey = $BinDir.ToLowerInvariant()
if (-not $seen.ContainsKey($binKey)) {
    $parts = @($BinDir) + $parts
}

[Environment]::SetEnvironmentVariable("Path", ($parts -join ";"), "User")
if ($env:Path -notlike "*$BinDir*") {
    $env:Path = "$BinDir;$env:Path"
}

Write-Host "Installed launcher: $Shim"
Write-Host "Repo:              $RepoRoot"
if (Test-Path $VenvPython) {
    Write-Host "Venv Python:       $VenvPython"
} else {
    Write-Host "WARNING: venv python missing at $VenvPython"
    Write-Host "  python -m venv venv"
    Write-Host "  .\venv\Scripts\python.exe -m pip install -r requirements.txt"
}
Write-Host ""
Write-Host "Open a new Command Prompt or PowerShell, then run:  immortility"
Write-Host "(This window can run it now if .local\bin is already on PATH.)"
