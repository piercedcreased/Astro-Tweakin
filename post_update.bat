@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run start.bat once first so everything gets installed.
  pause
  exit /b
)
rem No file given = post the newest notes in the updates folder
.venv\Scripts\python tools\post_update.py %*
pause
