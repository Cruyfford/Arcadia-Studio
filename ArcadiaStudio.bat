@echo off
chcp 65001 >nul
title Arcadia Studio
cd /d "%~dp0"

rem ---- find Python (py launcher or python)
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo.
    echo [X] Python is not installed.
    echo     Install Python 3.10+ from https://www.python.org/downloads/
    echo     Tick "Add Python to PATH" during setup, then run this file again.
    echo.
    start "" https://www.python.org/downloads/
    pause
    exit /b 1
)

rem ---- first run: create a private environment and install requirements
if not exist ".venv\Scripts\python.exe" (
    echo [1/2] First run: preparing environment (1-2 minutes)...
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :fail
    echo ok> ".venv\arcadia_ready"
)

rem ---- reinstall requirements if they changed after an update
if not exist ".venv\arcadia_ready" (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :fail
    echo ok> ".venv\arcadia_ready"
)

echo [2/2] Starting Arcadia Studio...
start "" ".venv\Scripts\pythonw.exe" run_studio.py
exit /b 0

:fail
echo.
echo [X] Setup failed. Check your internet connection and try again.
echo     To start fresh, delete the .venv folder.
pause
exit /b 1
