@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Vapor Lock's local Qt environment is missing.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "ui\vapor_lock_gui.py"
endlocal
