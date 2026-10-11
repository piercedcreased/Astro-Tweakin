@echo off
cd /d "%~dp0"
title Astro Tweakin
if not exist .venv (
  echo Setting up Python environment, first run only...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt
)
if not exist .env (
  copy .env.example .env >nul
  echo Created .env. Open it, paste your bot token, save, then run start.bat again.
  notepad .env
  pause
  exit /b
)
:loop
rem picks up any new libraries an update needs (quick when nothing changed)
.venv\Scripts\python -m pip install -q -r requirements.txt >nul 2>&1
.venv\Scripts\python bot.py
echo Bot stopped. Restarting in 5 seconds, close this window to quit.
timeout /t 5 >nul
goto loop
