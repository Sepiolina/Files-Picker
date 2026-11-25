@echo off
rem Double-click to build dist\FilePicker.exe (one file with GUI + engine).
rem Pass -OneFolder for a faster-starting folder build:  package.cmd -OneFolder
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0package.ps1" %*
set CODE=%ERRORLEVEL%
echo.
pause
exit /b %CODE%
