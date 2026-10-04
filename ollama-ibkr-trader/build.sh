#!/usr/bin/env bash
# Gera o executável único em dist/ (Linux/macOS).
set -euo pipefail
cd "$(dirname "$0")"
python -m pip install -r requirements.txt pyinstaller
pyinstaller --clean --noconfirm trader.spec
echo "Executável em dist/OllamaIBKRTrader"
