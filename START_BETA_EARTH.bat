@echo off
rem Asset-ID: BE-NEXT-LEGACY-WINDOWS-LAUNCHER-ALIAS | Version: 0.50.0 | Status: compatibility-alias.
rem Copyright © 2026 Gateway Information Group LLC. All rights reserved.
setlocal EnableExtensions DisableDelayedExpansion
set "BETA_EARTH_INVOKED_ENTRYPOINT=START_BETA_EARTH.bat"
call "%~dp0BetaEarthSovereignty.bat" %*
exit /b %errorlevel%
