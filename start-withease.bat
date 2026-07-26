@echo off
REM Startet WithEase immer mit der projekteigenen .venv (in der faster-whisper,
REM sounddevice usw. installiert sind). Einfach diese Datei doppelklicken.
cd /d "%~dp0"
set "PYTHONPATH=%CD%\src"
".venv\Scripts\python.exe" -m withease
if errorlevel 1 pause
