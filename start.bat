@echo off
cd /d "%~dp0"
if not exist venv (
    echo Création de l'environnement virtuel...
    python -m venv venv
    call venv\Scripts\activate.bat
    echo Installation des dépendances...
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)
python app.py
