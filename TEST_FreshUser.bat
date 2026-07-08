@echo off
rem =====================================================================
rem  Startet AccessMate als GANZ NEUER Nutzer – mit frischen, leeren
rem  Einstellungen in einem Testordner.  Deine echten Profile und
rem  API-Schluessel unter %APPDATA%\AccessMate bleiben unangetastet.
rem  (Nutzt die Umgebungsvariable ACCESSMATE_CONFIG_DIR.)
rem =====================================================================
setlocal
set "ACCESSMATE_CONFIG_DIR=%TEMP%\AccessMate_FreshTest"
rmdir /s /q "%ACCESSMATE_CONFIG_DIR%" 2>nul
echo Starte AccessMate als NEUER Nutzer ...
echo Frische Einstellungen: %ACCESSMATE_CONFIG_DIR%
if exist "%~dp0dist\AccessMate\AccessMate.exe" (
  start "" "%~dp0dist\AccessMate\AccessMate.exe" --open-settings
) else (
  cd /d "%~dp0"
  set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
  start "" pythonw -m accessmate --open-settings
)
endlocal
