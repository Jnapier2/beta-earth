@echo off
rem Asset-ID: BE-NEXT-CANONICAL-WINDOWS-LAUNCHER | Version: 0.51.1 | Status: current.
rem Execution-Namespace: BetaEarthSovereignty
rem Copyright © 2026 Gateway Information Group LLC. All rights reserved.
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto :bad_root
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
set "BETA_EARTH_LAUNCHED_BY_BAT=1"
set "BETA_EARTH_INVOKED_ENTRYPOINT=BetaEarthSovereignty.bat"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sqlite3,sys; raise SystemExit(0 if (3,11) <= sys.version_info[:2] < (3,14) else 1)" >nul 2>nul
  if not errorlevel 1 goto :run_venv
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3.13 -c "import sqlite3,sys" >nul 2>nul
  if not errorlevel 1 goto :run313
  py -3.12 -c "import sqlite3,sys" >nul 2>nul
  if not errorlevel 1 goto :run312
  py -3.11 -c "import sqlite3,sys" >nul 2>nul
  if not errorlevel 1 goto :run311
)

where python >nul 2>nul
if not errorlevel 1 (
  python -c "import sqlite3,sys; raise SystemExit(0 if (3,11) <= sys.version_info[:2] < (3,14) else 1)" >nul 2>nul
  if not errorlevel 1 goto :run_python
)

where python3 >nul 2>nul
if not errorlevel 1 (
  python3 -c "import sqlite3,sys; raise SystemExit(0 if (3,11) <= sys.version_info[:2] < (3,14) else 1)" >nul 2>nul
  if not errorlevel 1 goto :run_python3
)

goto :no_python

:run_venv
".venv\Scripts\python.exe" BetaEarthSovereignty.py %*
goto :finish

:run313
py -3.13 BetaEarthSovereignty.py %*
goto :finish

:run312
py -3.12 BetaEarthSovereignty.py %*
goto :finish

:run311
py -3.11 BetaEarthSovereignty.py %*
goto :finish

:run_python
python BetaEarthSovereignty.py %*
goto :finish

:run_python3
python3 BetaEarthSovereignty.py %*
goto :finish

:no_python
if not exist "runtime\diagnostics" mkdir "runtime\diagnostics" >nul 2>nul
(
  echo BETA EARTH STARTUP FAILURE
  echo ==========================
  echo Execution namespace: BetaEarthSovereignty
  echo Canonical entrypoint: BetaEarthSovereignty.bat
  echo No supported Python runtime was found.
  echo Required: Python 3.11, 3.12, or 3.13 with the standard sqlite3 module.
  echo Install Python from the official Python website, then rerun this launcher.
  echo No security setting, PATH entry, or firewall rule was changed.
) > "runtime\diagnostics\STARTUP_FAILURE_LATEST.txt"
echo.
echo Beta Earth could not find Python 3.11, 3.12, or 3.13.
echo Install a supported Python version, then double-click this file again.
echo Details: runtime\diagnostics\STARTUP_FAILURE_LATEST.txt
pause
exit /b 9009

:bad_root
echo Beta Earth could not open its extracted project folder.
echo Move the extracted folder to a normal writable location and try again.
pause
exit /b 3

:finish
set "BE_EXIT=%errorlevel%"
if not "%BE_EXIT%"=="0" (
  echo.
  echo Beta Earth did not complete successfully. Exit code: %BE_EXIT%
  echo Run BetaEarthSovereignty.bat --self-test for the safest launch diagnosis.
  if exist "runtime\diagnostics\STARTUP_FAILURE_LATEST.txt" echo Startup report: runtime\diagnostics\STARTUP_FAILURE_LATEST.txt
  pause
)
exit /b %BE_EXIT%
