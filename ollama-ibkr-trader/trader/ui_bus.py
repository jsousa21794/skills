"""Canal de comunicação thread-safe entre o motor (asyncio) e a GUI (Tk).

O motor *emite* eventos para uma ``queue.Queue``; a GUI *consome* a fila com
``after()`` no seu próprio loop. Nunca se toca em widgets a partir da thread
do asyncio.
"""

from __future__ import annotations

import logging
import queue
from dataclasses import dataclass, field
from typing import Any


@dataclass
class UIEvent:
    kind: str  # log | portfolio | status | models | decision | retrospective
    payload: dict[str, Any] = field(default_factory=dict)


class UIBus:
    def __init__(self) -> None:
        self.queue: "queue.Queue[UIEvent]" = queue.Queue()

    def emit(self, kind: str, **payload: Any) -> None:
        self.queue.put(UIEvent(kind, payload))

    def log(self, level: str, message: str, category: str = "sistema") -> None:
        self.emit("log", level=level, message=message, category=category)

    def drain(self, max_items: int = 500) -> list[UIEvent]:
        items: list[UIEvent] = []
        for _ in range(max_items):
            try:
                items.append(self.queue.get_nowait())
            except queue.Empty:
                break
        return items


class QueueLogHandler(logging.Handler):
    """Encaminha registos do ``logging`` para a consola da GUI."""

    def __init__(self, bus: UIBus) -> None:
        super().__init__()
        self.bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        try:
            category = getattr(record, "category", record.name.split(".")[-1])
            self.bus.log(record.levelname, self.format(record), category)
        except Exception:  # noqa: BLE001 - nunca deixar o logging rebentar a app
            self.handleError(record)


def setup_logging(bus: UIBus, log_file: str, level: int = logging.INFO) -> logging.Logger:
    from logging.handlers import RotatingFileHandler

    root = logging.getLogger("trader")
    root.setLevel(level)
    root.handlers.clear()
    fmt = logging.Formatter("%(message)s")
    ui_handler = QueueLogHandler(bus)
    ui_handler.setFormatter(fmt)
    root.addHandler(ui_handler)
    file_handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(file_handler)
    # Reduz ruído do ib_insync na consola.
    logging.getLogger("ib_insync").setLevel(logging.WARNING)
    logging.getLogger("ib_async").setLevel(logging.WARNING)
    return root
