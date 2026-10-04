"""Configuração central da aplicação.

Todas as definições têm valores por defeito seguros para *Paper Trading*
(porta 7497). O ficheiro ``config.json`` na pasta de dados do utilizador
sobrepõe-se a estes valores e é criado automaticamente na primeira execução.

Compatível com PyInstaller: nada é gravado dentro do bundle congelado; os
dados vivem em ``~/.ollamaibkrtrader`` (ou na pasta indicada pela variável de
ambiente ``OLLAMA_TRADER_HOME``).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

APP_NAME = "OllamaIBKRTrader"


def is_frozen() -> bool:
    """True quando a aplicação corre como executável PyInstaller."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resource_path(relative: str) -> Path:
    """Caminho para recursos empacotados (funciona em dev e em PyInstaller)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / relative


def app_data_dir() -> Path:
    """Pasta persistente para base de dados, logs e configuração."""
    override = os.environ.get("OLLAMA_TRADER_HOME")
    base = Path(override) if override else Path.home() / f".{APP_NAME.lower()}"
    base.mkdir(parents=True, exist_ok=True)
    return base


@dataclass
class Settings:
    # ---- Interactive Brokers -------------------------------------------------
    ib_host: str = "127.0.0.1"
    ib_port: int = 7497  # 7497 = TWS Paper, 4002 = Gateway Paper
    ib_client_id: int = 17
    market_data_type: int = 3  # 1 = tempo real, 3 = atrasado (funciona sem subscrição)
    symbols: list[str] = field(default_factory=lambda: ["AAPL", "TSLA"])
    exchange: str = "SMART"
    currency: str = "USD"
    bar_size: str = "1 min"
    history_duration: str = "2 D"
    use_rth_for_bars: bool = True

    # ---- Ollama -----------------------------------------------------------------
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"
    ollama_timeout_seconds: int = 120
    ollama_temperature: float = 0.2
    ollama_num_ctx: int = 4096

    # ---- Ciclo de trading -------------------------------------------------------
    cycle_seconds: int = 60  # frequência do ciclo de decisão
    min_confidence: float = 0.65  # confiança mínima para executar
    risk_fraction_per_trade: float = 0.05  # % do NetLiq alocada por entrada
    max_open_positions: int = 4
    allow_short: bool = True
    trade_only_rth: bool = True  # só decide em horário regular (09:30-16:00 NY)
    max_bar_age_seconds: int = 180  # ignora dados mais velhos que isto
    daily_loss_limit_pct: float = 0.03  # kill-switch diário (3% do NetLiq inicial)

    # ---- Gestão de risco (Bracket) ---------------------------------------------
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.05

    # ---- Indicadores -------------------------------------------------------------
    rsi_period: int = 14
    sma_fast: int = 20
    sma_slow: int = 50
    ema_period: int = 9

    # ---- Retrospetiva (aprendizagem) -------------------------------------------
    retro_time_local: str = "21:30"  # hora local para a retrospetiva diária
    retro_lookahead_minutes: int = 30  # horizonte para avaliar cada decisão
    retro_max_lessons: int = 8  # nº máximo de lições injetadas no prompt
    retro_use_llm_summary: bool = True

    # ---- UI ------------------------------------------------------------------------
    ui_poll_ms: int = 100
    log_max_lines: int = 2000

    # ------------------------------------------------------------------------------
    @classmethod
    def config_path(cls) -> Path:
        return app_data_dir() / "config.json"

    @classmethod
    def load(cls) -> "Settings":
        """Carrega o config.json (se existir) por cima dos valores por defeito."""
        settings = cls()
        path = cls.config_path()
        if path.exists():
            try:
                data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
                known = {f.name for f in fields(cls)}
                for key, value in data.items():
                    if key in known:
                        setattr(settings, key, value)
            except (OSError, json.JSONDecodeError):
                # Config corrompido: continua com defaults e regrava.
                pass
        settings.save()
        return settings

    def save(self) -> None:
        try:
            self.config_path().write_text(
                json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

    def db_path(self) -> Path:
        return app_data_dir() / "trader.sqlite3"

    def log_path(self) -> Path:
        return app_data_dir() / "trader.log"
