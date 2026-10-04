import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Isola a configuração/DB dos testes do perfil real do utilizador.
os.environ.setdefault("OLLAMA_TRADER_HOME", str(ROOT / ".test-home"))
