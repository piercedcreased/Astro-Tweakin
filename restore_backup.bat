@echo off
cd /d "%~dp0"
echo Close the bot window before restoring!
.venv\Scripts\python tools\restore_backup.py %*
pause
