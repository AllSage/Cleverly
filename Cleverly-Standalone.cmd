@echo off
setlocal
set "CLEVERLY_ROOT=%~dp0"
set "CLEVERLY_STANDALONE=%CLEVERLY_ROOT%Cleverly-Standalone.ps1"

if not exist "%CLEVERLY_STANDALONE%" (
  echo Cleverly standalone launcher was not found:
  echo "%CLEVERLY_STANDALONE%"
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

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%CLEVERLY_STANDALONE%" start
set "CLEVERLY_EXIT=%ERRORLEVEL%"
popd >nul 2>&1

if not "%CLEVERLY_EXIT%"=="0" (
  echo.
  echo Cleverly standalone exited with code %CLEVERLY_EXIT%.
  echo.
  pause
)
exit /b %CLEVERLY_EXIT%
