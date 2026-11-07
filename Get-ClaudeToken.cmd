@echo off
setlocal
title Claude 구독 토큰 발급
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\get-claude-token.ps1"
echo.
echo 창을 닫으려면 아무 키나 누르세요.
pause >nul
endlocal
