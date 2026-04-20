#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d venv ]; then
    echo "Creation de l'environnement virtuel..."
    python3 -m venv venv
fi

source venv/bin/activate

echo "Installation des dependances..."
pip install -r requirements.txt --quiet

echo ""
echo "Demarrage du service de reconciliation GeoNames..."
echo "Acces : http://localhost:5000"
echo "Appuyez sur Ctrl+C pour arreter."
echo ""

python app.py
