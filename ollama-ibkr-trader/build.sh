#!/usr/bin/env bash
# Gera o executável único em dist/ (Linux/macOS).
set -euo pipefail
cd "$(dirname "$0")"
python -m pip install -r requirements.lock  # mesmas versões do workflow (inclui PyInstaller)
pyinstaller --clean --noconfirm trader.spec
echo "Executável em dist/OllamaIBKRTrader"
