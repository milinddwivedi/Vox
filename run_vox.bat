@echo off
setlocal
cd /d "%~dp0"
echo Starting VOX Local AI Voice Assistant from source...
set PYTHONPATH=%~dp0src;%~dp0
".venv\Scripts\python.exe" "%~dp0main.py" %*
endlocal
