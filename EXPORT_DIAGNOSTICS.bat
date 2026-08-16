@echo off
set "BETA_EARTH_INVOKED_ENTRYPOINT=EXPORT_DIAGNOSTICS.bat"
rem Asset-ID: BE-NEXT-LEGACY-DIAGNOSTIC-ALIAS | Version: 0.50.0 | Status: compatibility-alias.
rem Copyright © 2026 Gateway Information Group LLC. All rights reserved.
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0BetaEarthSovereignty_ExportDiagnostics.bat" %*
exit /b %errorlevel%
