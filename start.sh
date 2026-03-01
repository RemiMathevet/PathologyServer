#!/bin/bash
echo ""
echo "  =========================================="
echo "   FoetoPath MRXS Slide Viewer"
echo "  =========================================="
echo ""

PORT=${1:-5000}

# Install deps if needed
pip show flask >/dev/null 2>&1 || pip install -r requirements.txt

echo "Démarrage sur http://127.0.0.1:$PORT"
echo ""
python3 app.py --port "$PORT" --debug
