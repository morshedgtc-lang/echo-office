@echo off
REM EPU - double-click this and the office starts, hidden, then the browser opens it.
cd /d "%~dp0"
start "" /MIN "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0EPU.ps1"
exit /b 0
