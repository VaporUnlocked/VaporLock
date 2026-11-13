@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Run BuildCommunity.ps1 -InstallDependencies to set up VaporLock.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m desktop
endlocal
