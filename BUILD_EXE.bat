@echo off
rem =====================================================================
rem  Baut die eigenstaendige WithEase.exe fuer Endnutzer.
rem  Ergebnis:  dist\WithEase\WithEase.exe  (ganzen Ordner weitergeben)
rem  Voraussetzung: Projekt-venv mit PyInstaller + Cloud-Diktier-Deps
rem  (sonst schlaegt --collect-all fehl):
rem     .venv\Scripts\python.exe -m pip install pyinstaller sounddevice requests
rem =====================================================================
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --name WithEase ^
  --icon "src\withease\assets\icons\withease.ico" ^
  --paths src ^
  --add-data "src\withease\locales;withease\locales" ^
  --add-data "src\withease\assets;withease\assets" ^
  --hidden-import pynput.keyboard._win32 ^
  --hidden-import pynput.mouse._win32 ^
  --collect-all sounddevice ^
  --collect-all requests ^
  --hidden-import wave ^
  --hidden-import audioop ^
  --hidden-import base64 ^
  --hidden-import random ^
  --noupx ^
  src\withease\__main__.py
echo.
echo Fertig. Starten mit:  dist\WithEase\WithEase.exe
pause
endlocal
