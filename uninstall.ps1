<#
.SYNOPSIS
    VoxLocal Uninstaller Script for Windows.
    Removes Windows login startup entry, removes Desktop shortcut, and optionally removes local data/venv.
#>
param(
    [switch]$RemoveUserData = $false,
    [switch]$RemoveVirtualEnv = $false
)

$AppRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $AppRoot

Write-Host "Removing Windows login startup registration (if present)..." -ForegroundColor Yellow
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
if (Get-ItemProperty -Path $RunKey -Name "VoxLocalVoiceAssistant" -ErrorAction SilentlyContinue) {
    Remove-ItemProperty -Path $RunKey -Name "VoxLocalVoiceAssistant" -ErrorAction SilentlyContinue
    Write-Host "  Removed HKCU Run entry." -ForegroundColor Green
}

$DesktopPath = [Environment]::GetFolderPath("Desktop")
$ShortcutFile = Join-Path $DesktopPath "VoxLocal Voice Assistant.lnk"
if (Test-Path $ShortcutFile) {
    Remove-Item $ShortcutFile -Force
    Write-Host "  Removed Desktop shortcut." -ForegroundColor Green
}

if ($RemoveUserData -and (Test-Path "$AppRoot\data")) {
    Remove-Item "$AppRoot\data" -Recurse -Force
    Write-Host "  Removed local data directory." -ForegroundColor Green
}

if ($RemoveVirtualEnv -and (Test-Path "$AppRoot\.venv")) {
    Remove-Item "$AppRoot\.venv" -Recurse -Force
    Write-Host "  Removed .venv directory." -ForegroundColor Green
}

Write-Host "VoxLocal uninstallation steps complete." -ForegroundColor Green
