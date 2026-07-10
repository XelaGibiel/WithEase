@echo off
rem =====================================================================
rem  WithEase-Starter (fuer Entwickler/Tester mit installiertem Python)
rem  Doppelklick startet die App. Beim ersten Mal werden fehlende
rem  Komponenten automatisch installiert.  Endnutzer verwenden die
rem  fertige WithEase.exe statt dieser Datei.
rem =====================================================================
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"

rem --- 1) Projekteigene virtuelle Umgebung bevorzugen ------------------
rem  Wenn ".venv" existiert (z. B. nach dem ersten Einrichten), direkt
rem  daraus starten. Das ist am zuverlaessigsten und umgeht einen kaputt
rem  registrierten Windows-Launcher ("py").
if exist "%~dp0.venv\Scripts\pythonw.exe" (
  start "" "%~dp0.venv\Scripts\pythonw.exe" -m withease
  exit /b 0
)

rem --- 2) Sonst ein funktionierendes Python finden ---------------------
rem  Wichtig: nicht nur pruefen, ob "py"/"python" im PATH steht, sondern
rem  ob es sich auch WIRKLICH starten laesst (der "py"-Launcher kann auf
rem  eine geloeschte Installation zeigen).
set "PYEXE="
set "PYWEXE="
py -3 -c "" >nul 2>&1 && ( set "PYEXE=py -3" & set "PYWEXE=pyw -3" )
if not defined PYEXE (
  python -c "" >nul 2>&1 && ( set "PYEXE=python" & set "PYWEXE=pythonw" )
)
if not defined PYEXE (
  echo Python 3.11 oder neuer wurde nicht gefunden.
  echo Bitte installieren: https://www.python.org/downloads/
  echo Wichtig: beim Setup "Add Python to PATH" ankreuzen.
  pause
  exit /b 1
)

rem --- 3) Beim ersten Start Abhaengigkeiten einrichten ----------------
%PYEXE% -c "import PySide6, pynput" 2>nul
if errorlevel 1 (
  echo [WithEase] Erstmalige Einrichtung laeuft, bitte warten ...
  %PYEXE% -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Einrichtung fehlgeschlagen. Bitte Internetverbindung pruefen.
    pause
    exit /b 1
  )
)

rem --- 4) App starten (ohne Konsolenfenster) --------------------------
start "" %PYWEXE% -m withease
endlocal
