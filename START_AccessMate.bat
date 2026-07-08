@echo off
rem =====================================================================
rem  AccessMate-Starter (fuer Entwickler/Tester mit installiertem Python)
rem  Doppelklick startet die App. Beim ersten Mal werden fehlende
rem  Komponenten automatisch installiert.  Endnutzer verwenden die
rem  fertige AccessMate.exe statt dieser Datei.
rem =====================================================================
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"

rem --- Python finden (bevorzugt der Windows-Launcher "py") --------------
set "PYEXE="
set "PYWEXE="
where py >nul 2>&1 && ( set "PYEXE=py -3" & set "PYWEXE=pyw -3" )
if not defined PYEXE (
  where python >nul 2>&1 && ( set "PYEXE=python" & set "PYWEXE=pythonw" )
)
if not defined PYEXE (
  echo Python 3.11 oder neuer wurde nicht gefunden.
  echo Bitte installieren: https://www.python.org/downloads/
  echo Wichtig: beim Setup "Add Python to PATH" ankreuzen.
  pause
  exit /b 1
)

rem --- Beim ersten Start Abhaengigkeiten einrichten --------------------
%PYEXE% -c "import PySide6, pynput" 2>nul
if errorlevel 1 (
  echo [AccessMate] Erstmalige Einrichtung laeuft, bitte warten ...
  %PYEXE% -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Einrichtung fehlgeschlagen. Bitte Internetverbindung pruefen.
    pause
    exit /b 1
  )
)

rem --- App starten (ohne Konsolenfenster) -----------------------------
start "" %PYWEXE% -m accessmate
endlocal
