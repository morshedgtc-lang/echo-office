@echo off
REM EPU - allow the owner's phone and laptop to reach the office on this WiFi.
REM One click, it asks for admin once (that is Windows, not EPU).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-Command',"
  "'New-NetFirewallRule -DisplayName \"EPU office 8790\" -Direction Inbound -Protocol TCP -LocalPort 8790 -Action Allow -Profile Private; "
  "New-NetFirewallRule -DisplayName \"EPU download 8799\" -Direction Inbound -Protocol TCP -LocalPort 8799 -Action Allow -Profile Private; "
  "Write-Host done'"''
echo If Windows asked you to allow changes, press Yes.
echo EPU ports 8790 and 8799 are now open on your home network.
pause
