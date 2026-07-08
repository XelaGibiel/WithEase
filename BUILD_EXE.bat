@echo off
rem =====================================================================
rem  Baut die eigenstaendige AccessMate.exe fuer Endnutzer.
rem  Ergebnis:  dist\AccessMate\AccessMate.exe  (ganzen Ordner weitergeben)
rem  Voraussetzung: Projekt-venv mit PyInstaller + Cloud-Diktier-Deps
rem  (sonst schlaegt --collect-all fehl):
rem     .venv\Scripts\python.exe -m pip install pyinstaller sounddevice requests
rem =====================================================================
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --name AccessMate ^
  --icon "src\accessmate\assets\icons\accessmate.ico" ^
  --paths src ^
  --add-data "src\accessmate\locales;accessmate\locales" ^
  --add-data "src\accessmate\assets;accessmate\assets" ^
  --hidden-import pynput.keyboard._win32 ^
  --hidden-import pynput.mouse._win32 ^
  --collect-all sounddevice ^
  --collect-all requests ^
  --noupx ^
  src\accessmate\__main__.py
echo.
echo Fertig. Starten mit:  dist\AccessMate\AccessMate.exe
pause
endlocal
