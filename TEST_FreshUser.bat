@echo off
rem =====================================================================
rem  Startet WithEase als GANZ NEUER Nutzer – mit frischen, leeren
rem  Einstellungen in einem Testordner.  Deine echten Profile und
rem  API-Schluessel unter %APPDATA%\WithEase bleiben unangetastet.
rem  (Nutzt die Umgebungsvariable WITHEASE_CONFIG_DIR.)
rem =====================================================================
setlocal
set "WITHEASE_CONFIG_DIR=%TEMP%\WithEase_FreshTest"
rmdir /s /q "%WITHEASE_CONFIG_DIR%" 2>nul
echo Starte WithEase als NEUER Nutzer ...
echo Frische Einstellungen: %WITHEASE_CONFIG_DIR%
if exist "%~dp0dist\WithEase\WithEase.exe" (
  start "" "%~dp0dist\WithEase\WithEase.exe" --open-settings
) else (
  cd /d "%~dp0"
  set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
  start "" pythonw -m withease --open-settings
)
endlocal
