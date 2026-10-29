@echo off
setlocal
set APP_DIR=%~dp0

if exist "%APP_DIR%.venv\Scripts\pythonw.exe" (
    start "" "%APP_DIR%.venv\Scripts\pythonw.exe" "%APP_DIR%ui\vapor_lock_gui.py"
    exit /b 0
)

echo Vapor Lock's local Qt environment is missing.
echo Run the project setup again to create .venv.
pause
