"""Composição da aplicação: config -> DB -> cérebro -> motor -> GUI."""

from __future__ import annotations

import logging
import multiprocessing
import os
import sys

from .config import Settings, app_data_dir
from .database import Database
from .ollama_brain import OllamaBrain
from .trading_engine import TradingEngine
from .ui_bus import UIBus, setup_logging


def _import_seed_lessons(settings: Settings, db: Database, log: logging.Logger) -> None:
    """Carrega as lições iniciais uma única vez (marca em kv)."""
    path = settings.seed_lessons_file
    if not path:
        return
    from pathlib import Path

    from .config import resource_path
    from .lessons import import_seed_lessons

    candidate = Path(path)
    if not candidate.is_absolute() and not candidate.exists():
        candidate = resource_path(path)
    if not candidate.exists():
        log.warning("Ficheiro de lições iniciais não encontrado: %s", path)
        return
    marker = f"seed_imported:{candidate.name}:{candidate.stat().st_mtime_ns}"
    if db.get_kv(marker):
        return
    try:
        n = import_seed_lessons(db, str(candidate))
        db.set_kv(marker, "1")
        log.info("Lições iniciais: %d importadas de %s", n, candidate)
    except (OSError, ValueError) as exc:
        log.error("Falha a importar lições iniciais: %s", exc)


def main() -> int:
    multiprocessing.freeze_support()  # inofensivo fora do PyInstaller; necessário no Windows
    settings = Settings.load()
    bus = UIBus()
    log = setup_logging(bus, str(settings.log_path()))
    log.info("Dados em %s", app_data_dir())

    db = Database(settings.db_path())
    _import_seed_lessons(settings, db, log)
    brain = OllamaBrain(settings, db)
    engine = TradingEngine(settings, db, bus, brain)
    engine.start()

    from .gui import TraderApp  # import tardio: Tk só na thread principal

    app = TraderApp(engine, bus, settings)
    smoke = os.environ.get("OLLAMA_TRADER_SMOKE")
    if smoke:
        # Modo de verificação do binário: arranca GUI e motor, espera N segundos, encerra limpo.
        seconds = max(1, int(smoke)) if smoke.isdigit() else 3
        log.info("Smoke test: a encerrar automaticamente em %ds", seconds)
        app.after(seconds * 1000, app._on_close)
    try:
        app.mainloop()
    finally:
        engine.shutdown()
        db.close()
        logging.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
