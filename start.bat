@echo off
cd /d "%~dp0"

if not exist venv (
    echo Creation de l'environnement virtuel...
    python -m venv venv
)

call venv\Scripts\activate.bat

echo Installation des dependances...
pip install -r requirements.txt --quiet

python app.py
pause
