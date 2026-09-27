@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title VoxMaps Pollution Source Simulator

echo.
echo  VoxMaps Pollution Source Simulator
echo  Preparing the local application...
echo.

if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>nul
    if errorlevel 1 goto :python_missing
    echo Creating a private Python environment. This happens only on first use.
    py -3.11 -m venv ".venv"
    if errorlevel 1 goto :setup_failed
)

".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 goto :python_old

echo Checking application components...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --quiet -e "."
if errorlevel 1 goto :install_failed

echo Starting VoxMaps. Your browser will open automatically.
echo.
".venv\Scripts\python.exe" -m voxmaps_sim.desktop_launcher
set "VOXMAPS_EXIT=%ERRORLEVEL%"
if "%VOXMAPS_EXIT%"=="0" exit /b 0

echo.
echo VoxMaps stopped with error code %VOXMAPS_EXIT%.
echo Review the message above, then close this window and launch again.
pause
exit /b %VOXMAPS_EXIT%

:python_missing
echo Python 3.11 or newer was not found.
echo Install Python from https://www.python.org/downloads/windows/
echo During setup, select "Add Python to PATH", then launch VoxMaps again.
pause
exit /b 10

:python_old
echo The private environment uses an older Python version.
echo Rename or remove the .venv folder, install Python 3.11 or newer, and launch again.
pause
exit /b 11

:setup_failed
echo The private Python environment could not be created.
echo Check that this folder is writable and that Python 3.11 is installed, then try again.
pause
exit /b 12

:install_failed
echo VoxMaps could not install or verify its local components.
echo Check your internet connection and available disk space, then launch again.
pause
exit /b 13

