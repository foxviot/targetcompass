@echo off
setlocal
set "APPDIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%APPDIR%Launch-TargetCompassV5.ps1" %*
if errorlevel 1 (
  echo.
  echo TargetCompass V5 failed to start.
  echo Please send the logs folder to the developer:
  echo %APPDIR%logs
  echo.
  pause
)
