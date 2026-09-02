@echo off
rem =====================================================================
rem  Zeigt den einmaligen Unterstuetzungs-Hinweis SOFORT, zum Ansehen
rem  und Ausprobieren - ohne zehn Stunden Nutzung abzuwarten.
rem
rem  WICHTIG: startet bewusst aus dem QUELLCODE (src), nicht aus
rem  dist\WithEase.exe.  Der Hinweis existiert nur im Quellcode; eine
rem  aeltere gepackte .exe kennt ihn nicht.  Zum Testen der .exe diese
rem  vorher mit BUILD_EXE.bat neu bauen.
rem
rem  SO IST ES HIER EINGESTELLT (echter Ablauf, nur in Sekunden):
rem      Nach 30 Sekunden Laufzeit erscheint der Hinweis.
rem      "Spaeter"            -> er kommt 30 Sekunden spaeter noch einmal.
rem      "Nicht mehr anzeigen"-> er bleibt weg (bis zum naechsten Start
rem                              dieser Datei, die alles zuruecksetzt).
rem      "Ansehen"            -> oeffnet die Seite und beendet den Hinweis.
rem
rem  Die drei Schalter im Einzelnen:
rem
rem  WITHEASE_SUPPORT_HINT_AFTER=30
rem      Ersetzt die 10 bzw. 40 STUNDEN durch 30 SEKUNDEN.
rem
rem  WITHEASE_SUPPORT_HINT_RESET=1
rem      Setzt beim Start die gespeicherte Antwort und den Nutzungszaehler
rem      zurueck.  Ohne das waere nach einmal "Nicht mehr anzeigen" nichts
rem      mehr zu testen.
rem
rem  WITHEASE_SUPPORT_HINT_FORCE=1   (unten auskommentiert)
rem      Zeigt den Hinweis EINMAL PRO START sofort, ohne zu warten - nur
rem      zum Anschauen des Aussehens.  Damit laesst sich "Spaeter" NICHT
rem      pruefen, weil dabei gar nicht gewartet wird.
rem
rem  Alle drei sind reine Testschalter ueber Umgebungsvariablen - in
rem  der normalen Anwendung existiert davon nichts.
rem
rem  Getestet wird gegen DEINE echten Einstellungen.  Soll stattdessen ein
rem  Wegwerf-Ordner benutzt werden, die naechste Zeile einkommentieren:
rem  set "WITHEASE_CONFIG_DIR=%TEMP%\WithEase_HinweisTest"
rem =====================================================================
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"

set "WITHEASE_SUPPORT_HINT_AFTER=30"
set "WITHEASE_SUPPORT_HINT_RESET=1"
rem set "WITHEASE_SUPPORT_HINT_FORCE=1"

rem --- Die projekteigene .venv bevorzugen: dort liegen PySide6 & Co. ---
rem  pythonw.exe fehlt in dieser .venv, deshalb python.exe - dabei bleibt
rem  ein Konsolenfenster offen.  Fuer einen Test ist das eher nuetzlich:
rem  Fehlermeldungen stehen dann direkt darin.
if exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo Starte aus .venv - Hinweis nach 30 s.
  start "" "%~dp0.venv\Scripts\pythonw.exe" -m withease --open-settings
  goto :eof
)
if exist "%~dp0.venv\Scripts\python.exe" (
  echo Starte aus .venv - Hinweis nach 30 s.
  echo Dieses Fenster bitte offen lassen, es gehoert zur App.
  "%~dp0.venv\Scripts\python.exe" -m withease --open-settings
  if errorlevel 1 pause
  goto :eof
)

rem --- Sonst ein System-Python versuchen -------------------------------
pyw -3 -c "" >nul 2>&1 && (
  echo Starte mit pyw -3 - Hinweis nach 30 s.
  start "" pyw -3 -m withease --open-settings
  goto :eof
)
python -c "" >nul 2>&1 && (
  echo Starte mit python - Hinweis nach 30 s.
  python -m withease --open-settings
  if errorlevel 1 pause
  goto :eof
)

echo Kein startbares Python gefunden.
echo Erwartet wurde .venv\Scripts\python.exe im Projektordner.
pause
