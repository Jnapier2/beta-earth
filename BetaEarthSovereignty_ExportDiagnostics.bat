@echo off
set "BETA_EARTH_INVOKED_ENTRYPOINT=BetaEarthSovereignty_ExportDiagnostics.bat"
rem Asset-ID: BE-NEXT-CANONICAL-DIAGNOSTIC-LAUNCHER | Version: 0.50.0 | Status: current.
rem Copyright © 2026 Gateway Information Group LLC. All rights reserved.
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 exit /b 3
call "%~dp0BetaEarthSovereignty.bat" --export-diagnostics
set "BE_EXIT=%errorlevel%"
if "%BE_EXIT%"=="0" (
  echo.
  echo Open exports\support, open this computer's sanitized folder, and upload both UPLOAD_THIS_ files.
  if exist "%~dp0exports\support" start "" explorer.exe "%~dp0exports\support"
) else (
  echo Diagnostic export did not complete successfully.
)
exit /b %BE_EXIT%
