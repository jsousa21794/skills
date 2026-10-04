"""Composição da aplicação: config -> DB -> cérebro -> motor -> GUI."""

from __future__ import annotations

import logging
import multiprocessing
import sys

from .config import Settings, app_data_dir
from .database import Database
from .ollama_brain import OllamaBrain
from .trading_engine import TradingEngine
from .ui_bus import UIBus, setup_logging


def main() -> int:
    multiprocessing.freeze_support()  # inofensivo fora do PyInstaller; necessário no Windows
    settings = Settings.load()
    bus = UIBus()
    log = setup_logging(bus, str(settings.log_path()))
    log.info("Dados em %s", app_data_dir())

    db = Database(settings.db_path())
    brain = OllamaBrain(settings, db)
    engine = TradingEngine(settings, db, bus, brain)
    engine.start()

    from .gui import TraderApp  # import tardio: Tk só na thread principal

    app = TraderApp(engine, bus, settings)
    try:
        app.mainloop()
    finally:
        engine.shutdown()
        db.close()
        logging.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
