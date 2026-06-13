@echo off
setlocal
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Cleverly-Launcher.ps1"
if errorlevel 1 pause
