#!/bin/bash
cd "$(dirname "$0")"
if [ ! -d "venv" ]; then
    echo "Création de l'environnement virtuel..."
    python3 -m venv venv
    source venv/bin/activate
    echo "Installation des dépendances..."
    pip install -r requirements.txt
else
    source venv/bin/activate
fi
python app.py
