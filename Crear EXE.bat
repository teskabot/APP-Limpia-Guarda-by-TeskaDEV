@echo off
cd /d "%~dp0"
python -m pip install pyinstaller tkinterdnd2
python -m PyInstaller --noconfirm --onefile --windowed --name LimpiaGuarda --collect-all tkinterdnd2 --icon limpiateska.ico --add-data "limpiateska.ico;." limpia_guarda.py
echo.
if exist "dist\LimpiaGuarda.exe" (
    echo Listo: %~dp0dist\LimpiaGuarda.exe
    explorer dist
) else (
    echo ERROR: no se pudo crear el EXE. Revisa los mensajes de arriba.
)
pause
