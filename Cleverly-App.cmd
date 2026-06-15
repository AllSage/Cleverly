@echo off
setlocal
set "CLEVERLY_ROOT=%~dp0"
set "CLEVERLY_LAUNCHER=%CLEVERLY_ROOT%Cleverly-Launcher.ps1"

if not exist "%CLEVERLY_LAUNCHER%" (
  echo Cleverly launcher was not found:
  echo "%CLEVERLY_LAUNCHER%"
  echo.
  pause
  exit /b 1
)

pushd "%CLEVERLY_ROOT%" >nul 2>&1
if errorlevel 1 (
  echo Could not open Cleverly install folder:
  echo "%CLEVERLY_ROOT%"
  echo.
  pause
  exit /b 1
)

powershell.exe -NoLogo -NoProfile -STA -ExecutionPolicy Bypass -File "%CLEVERLY_LAUNCHER%"
set "CLEVERLY_EXIT=%ERRORLEVEL%"
popd >nul 2>&1

if not "%CLEVERLY_EXIT%"=="0" (
  echo.
  echo Cleverly launcher exited with code %CLEVERLY_EXIT%.
  echo.
  pause
)
exit /b %CLEVERLY_EXIT%
