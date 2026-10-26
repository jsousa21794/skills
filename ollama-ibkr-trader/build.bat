@echo off
REM Gera o executavel unico em dist\ (Windows).
cd /d %~dp0
python -m pip install -r requirements.lock
pyinstaller --clean --noconfirm trader.spec
echo Executavel em dist\OllamaIBKRTrader.exe
