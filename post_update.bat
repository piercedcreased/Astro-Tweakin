@echo off
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: post_update.bat updates\0.1.0.md
  pause
  exit /b
)
.venv\Scripts\python tools\post_update.py %*
pause
