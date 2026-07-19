
@echo off
setlocal
pushd "%~dp0" >nul 2>nul
if errorlevel 1 (
    echo Could not enter the Beta Earth project folder.
    endlocal & exit /b 3
)
call "tools\invoke_supported_python.cmd" "run_beta_earth.py"
set "RC=%ERRORLEVEL%"
popd
endlocal & exit /b %RC%
