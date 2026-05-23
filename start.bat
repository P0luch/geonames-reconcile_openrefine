@echo off
cd /d "%~dp0"

where python >nul 2>&1 && set PYTHON=python
if "%PYTHON%"=="" where py >nul 2>&1 && set PYTHON=py
if "%PYTHON%"=="" (
    echo Python introuvable. Installez Python depuis https://python.org et relancez.
    pause
    exit /b 1
)

if not exist .venv (
    echo Creation de l'environnement virtuel...
    %PYTHON% -m venv .venv
)

call .venv\Scripts\activate.bat

echo Installation des dependances...
pip install -r requirements.txt --quiet

python app.py
pause
