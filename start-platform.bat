@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul || (echo [X] Python not found. Install Python 3.10+ first. & pause & exit /b 1)
echo Starting Academic Research Platform ...
start "" pythonw platform\server.py
echo Browser will open at http://127.0.0.1:8320
timeout /t 3 >nul
