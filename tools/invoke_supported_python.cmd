
@echo off
setlocal
for %%I in ("%~dp0..") do set "BETA_EARTH_ROOT=%%~fI"
set "PYTHON_MANAGER_AUTOMATIC_INSTALL=0"

if exist "%BETA_EARTH_ROOT%\.venv\Scripts\python.exe" (
    "%BETA_EARTH_ROOT%\.venv\Scripts\python.exe" -I -B -X utf8 -c "import sys;raise SystemExit(0 if (3,11)<=sys.version_info[:2]<(3,14) else 1)" >nul 2>nul
    if not errorlevel 1 (
        "%BETA_EARTH_ROOT%\.venv\Scripts\python.exe" -I -B -X utf8 %*
        set "RC=%ERRORLEVEL%"
        endlocal & exit /b %RC%
    )
)
where py >nul 2>nul
if not errorlevel 1 (
    py -V:3.13 -I -B -X utf8 -c "import sys;raise SystemExit(0 if sys.version_info[:2]==(3,13) else 1)" >nul 2>nul
    if not errorlevel 1 (
        py -V:3.13 -I -B -X utf8 %*
        set "RC=%ERRORLEVEL%"
        endlocal & exit /b %RC%
    )
    py -V:3.12 -I -B -X utf8 -c "import sys;raise SystemExit(0 if sys.version_info[:2]==(3,12) else 1)" >nul 2>nul
    if not errorlevel 1 (
        py -V:3.12 -I -B -X utf8 %*
        set "RC=%ERRORLEVEL%"
        endlocal & exit /b %RC%
    )
    py -V:3.11 -I -B -X utf8 -c "import sys;raise SystemExit(0 if sys.version_info[:2]==(3,11) else 1)" >nul 2>nul
    if not errorlevel 1 (
        py -V:3.11 -I -B -X utf8 %*
        set "RC=%ERRORLEVEL%"
        endlocal & exit /b %RC%
    )
)
where python >nul 2>nul
if not errorlevel 1 (
    python -I -B -X utf8 -c "import sys;raise SystemExit(0 if (3,11)<=sys.version_info[:2]<(3,14) else 1)" >nul 2>nul
    if not errorlevel 1 (
        python -I -B -X utf8 %*
        set "RC=%ERRORLEVEL%"
        endlocal & exit /b %RC%
    )
)
echo Beta Earth requires an installed Python 3.11, 3.12, or 3.13 runtime.
echo No runtime was installed or changed automatically.
endlocal & exit /b 2
