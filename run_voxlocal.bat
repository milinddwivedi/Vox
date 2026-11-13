@echo off
title VoxLocal — Local-First AI Voice Assistant
cd /d "%~dp0"

if exist "dist\VoxLocal\VoxLocal.exe" (
    start "" "dist\VoxLocal\VoxLocal.exe" %*
    exit /b 0
)

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "main.py" %*
    exit /b 0
)

python "main.py" %*
