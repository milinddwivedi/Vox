<#
.SYNOPSIS
    VoxLocal Installer & Setup Script for Windows.
    Sets up the virtual environment, installs dependencies, pre-caches the local Whisper model,
    and optionally creates a Desktop shortcut.
#>
param(
    [switch]$CreateDesktopShortcut = $true,
    [switch]$SkipModelDownload = $false
)

$ErrorActionPreference = "Stop"
$AppRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $AppRoot

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  VoxLocal — Local-First AI Voice Assistant Installer" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[1/4] Creating Python virtual environment (.venv)..." -ForegroundColor Yellow
    python -m venv .venv
} else {
    Write-Host "[1/4] Virtual environment (.venv) already exists." -ForegroundColor Green
}

Write-Host "[2/4] Installing dependencies from requirements.txt..." -ForegroundColor Yellow
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip.exe install -r requirements.txt

if (-not $SkipModelDownload) {
    Write-Host "[3/4] Ensuring local Whisper speech model ('base.en') is cached in data\models..." -ForegroundColor Yellow
    .\.venv\Scripts\python.exe -c "import os; os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING']='1'; from faster_whisper import WhisperModel; os.makedirs('data/models', exist_ok=True); WhisperModel('base.en', device='cpu', compute_type='int8', download_root='data/models'); print('Model base.en verified in local storage.')"
}

if ($CreateDesktopShortcut) {
    Write-Host "[4/4] Creating Desktop shortcut 'VoxLocal Voice Assistant.lnk'..." -ForegroundColor Yellow
    $DesktopPath = [Environment]::GetFolderPath("Desktop")
    $ShortcutFile = Join-Path $DesktopPath "VoxLocal Voice Assistant.lnk"
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($ShortcutFile)
    if (Test-Path "$AppRoot\dist\VoxLocal\VoxLocal.exe") {
        $Shortcut.TargetPath = "$AppRoot\dist\VoxLocal\VoxLocal.exe"
    } else {
        $Shortcut.TargetPath = "$AppRoot\run_voxlocal.bat"
    }
    $Shortcut.WorkingDirectory = $AppRoot
    $Shortcut.Description = "VoxLocal — Local-First AI Voice Assistant (Voice Typing & Meeting Intelligence)"
    $Shortcut.Save()
    Write-Host "  Created shortcut at: $ShortcutFile" -ForegroundColor Green
}

Write-Host "`nInstallation complete! Run .\run_voxlocal.bat or double-click the Desktop shortcut to launch." -ForegroundColor Green
