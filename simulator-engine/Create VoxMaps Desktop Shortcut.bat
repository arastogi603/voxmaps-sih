@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Create VoxMaps Desktop Shortcut.ps1"
if errorlevel 1 (
    echo.
    echo The shortcut could not be created. Check the message above.
    pause
    exit /b 1
)
echo.
pause
