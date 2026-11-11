@echo off
REM EPU - double-click this and the office starts, hidden, then the HUD opens.
REM A second click only opens the HUD: the office never starts twice.
REM
REM Optional: EPU.cmd lan    -> also open it to your phone on this WiFi
REM           EPU.cmd firewall -> open the Windows port for that (admin once)
cd /d "%~dp0"
set "EXTRA="
if /i "%~1"=="lan" set "EXTRA=-Lan"
if /i "%~1"=="firewall" set "EXTRA=-Firewall"
start "" /MIN "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0scripts\epu-start.ps1" %EXTRA%
exit /b 0
