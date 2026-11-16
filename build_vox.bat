@echo off
setlocal
cd /d "%~dp0"
echo Rebuilding standalone VoxLocal executable with PyInstaller...
set PYTHONPATH=%~dp0src
".venv\Scripts\python.exe" -m PyInstaller --noconfirm VoxLocal.spec
if %ERRORLEVEL% equ 0 (
    echo.
    echo =======================================================
    echo Build successful! Standalone executable updated at:
    echo dist\VoxLocal\VoxLocal.exe
    echo =======================================================
) else (
    echo.
    echo Build failed with error code %ERRORLEVEL%.
)
endlocal
