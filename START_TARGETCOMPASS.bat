@echo off
chcp 65001 >nul
title TargetCompass Lite Launcher
cd /d "%~dp0"
echo.
echo Starting TargetCompass Lite...
echo Project folder: %cd%
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_app_one_click.ps1"
if errorlevel 1 (
  echo.
  echo TargetCompass Lite failed to start.
  echo Please check:
  echo   %~dp0webapp.err.log
  echo.
  pause
  exit /b 1
)
echo.
echo TargetCompass Lite is running.
echo Open this address if the browser did not open automatically:
echo   http://127.0.0.1:8781/
echo.
start "" "http://127.0.0.1:8781/"
echo Press any key to close this launcher window. The web app will keep running.
pause >nul
