@echo off
REM aiva 개발 서버 더블클릭 런처.
REM PowerShell 실행정책(ExecutionPolicy)에 막히지 않도록 Bypass 로 dev.ps1 을 실행한다.
REM 자동 pull 끄려면:  dev.bat -NoPull
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev.ps1" %*
pause
