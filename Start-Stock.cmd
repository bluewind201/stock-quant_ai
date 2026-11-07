@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-stock.ps1" -OpenBrowser
if errorlevel 1 (
    echo.
    echo Stock Quant could not start. Please keep the error above for troubleshooting.
    pause
)
endlocal
