"""Composição da aplicação: config -> DB -> cérebro -> motor -> GUI."""

from __future__ import annotations

import logging
import multiprocessing
import os
import sys
from datetime import datetime, timezone

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


def _migrate_legacy_db(settings: Settings, log: logging.Logger) -> None:
    """Base de dados única das versões <= 1.0.2 (``trader.sqlite3``) (N08).

    A 1.0.3 passou a ficheiros por modo e deixou o antigo sem ser lido: histórico, pausas, kill-switch
    e associações de ordens abertas desapareciam. Agora, uma única vez, o ficheiro antigo é copiado para
    a base do modo configurado (o mesmo ``trading_mode`` com que foi usado, lido do mesmo config.json),
    o original fica como ``trader.sqlite3.migrated-1.0.2`` e a reconciliação ao ligar reconstrói o
    estado antes de qualquer decisão.
    """
    import shutil

    legacy = settings.legacy_db_path()
    if not legacy.exists():
        return
    target = settings.db_path()
    archived = legacy.with_name(legacy.name + ".migrated-1.0.2")
    if target.exists():
        # Coexistência (a 1.0.3/1.0.4 correu primeiro): importa só o estado operacional do legado — proteções
        # ainda ativas e trades abertos — para a base EFETIVAMENTE usada: se a base por modo já está atribuída a
        # uma conta e a base dessa conta existe, é essa o destino (W04). Deduplicado e transacional.
        try:
            mode_db = Database(target)
            try:
                bound = mode_db.get_kv("bound_account")
            finally:
                mode_db.close()
            effective = target
            if bound and settings.db_path(bound).exists():
                effective = settings.db_path(bound)
            src = Database(legacy)
            dst = Database(effective)
            try:
                counts = dst.merge_open_state_from(src)
                dst.set_kv("migration_failed", "")
                dst.set_kv("migration_1.0.2", f"importado de {legacy.name} em {datetime.now(timezone.utc).isoformat()}")
            finally:
                src.close()
                dst.close()
            shutil.move(str(legacy), str(archived))
            log.warning("Base antiga %s coexistia com %s: importadas %d proteções ativas e %d trades abertos (%d duplicados "
                        "ignorados); o histórico fechado fica em %s (não é fundido). Confirma as posições na TWS antes de iniciar.",
                        legacy.name, effective.name, counts["protections"], counts["trades"], counts["duplicates"], archived.name)
        except Exception as exc:  # noqa: BLE001
            log.error("Falha a importar o estado da base antiga %s: %s. Entradas BLOQUEADAS até resolver (kv migration_failed).",
                      legacy, exc)
            try:
                dst = Database(target)
                dst.set_kv("migration_failed", str(exc))
                dst.close()
            except Exception:  # noqa: BLE001
                pass
        return
    try:
        src = Database(legacy)
        try:
            src.copy_to(target)
        finally:
            src.close()
        shutil.move(str(legacy), str(archived))
        log.warning("Base de dados da versão <= 1.0.2 migrada para %s (modo %s); original preservado como %s.",
                    target.name, settings.trading_mode, archived.name)
    except Exception as exc:  # noqa: BLE001
        log.error("Falha a migrar a base de dados antiga %s: %s. A continuar com %s.", legacy, exc, target.name)


def main() -> int:
    multiprocessing.freeze_support()  # inofensivo fora do PyInstaller; necessário no Windows
    settings = Settings.load()
    bus = UIBus()
    log = setup_logging(bus, str(settings.log_path()))
    log.info("Dados em %s", app_data_dir())
    _migrate_legacy_db(settings, log)

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
