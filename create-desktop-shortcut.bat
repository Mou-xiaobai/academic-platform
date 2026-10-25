@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File make-shortcut.ps1
pause
