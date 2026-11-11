@echo off
REM EPU laptop install: one double-click, no questions, no windows left open.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-laptop.ps1"
pause
