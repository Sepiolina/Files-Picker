@echo off
rem Double-click to build the engine. Runs build.ps1 without changing your
rem PowerShell execution policy (and works for files downloaded from GitHub).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
set CODE=%ERRORLEVEL%
echo.
pause
exit /b %CODE%
