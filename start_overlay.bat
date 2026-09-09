@echo off
setlocal
cd /d "%~dp0"
python overlay.py
if errorlevel 1 (
    echo.
    echo Erreur: impossible de lancer l'overlay.
    pause
)
endlocal
