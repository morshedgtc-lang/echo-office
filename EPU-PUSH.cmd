@echo off
REM EPU - push the office to GitHub, then ask before forgetting the token.
REM One double-click. The token is never written to disk unless you say yes.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$t = Read-Host 'Paste your GitHub token (ghp_... or github_pat_...)'; " ^
  "if (-not $t) { 'No token - nothing pushed.'; pause; exit 1 }; " ^
  "Set-Content -Path \"$env:TEMP\epu-ask.cmd\" -Value \"@echo off`r`nif `\"%~1`\"==`\"Username `\" echo OWNER`r`necho $t\"; " ^
  "$env:GIT_ASKPASS = \"$env:TEMP\epu-ask.cmd\"; " ^
  "git -c credential.helper= push -u origin main; " ^
  "Remove-Item \"$env:TEMP\epu-ask.cmd\" -ErrorAction SilentlyContinue; " ^
  "if ($LASTEXITCODE -eq 0) { 'DONE, sir - pushed to GitHub.' } else { 'Push failed - check the token has the repo scope.' }; " ^
  "Remove-Item Env:\GIT_ASKPASS"
pause
