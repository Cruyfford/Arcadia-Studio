@echo off
chcp 65001 >nul
title Arcadia Studio (debug)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call ArcadiaStudio.bat
".venv\Scripts\python.exe" run_studio.py
echo.
echo Program closed. If an error appears above, take a screenshot.
pause
