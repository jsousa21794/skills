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
    trading_mode: str = "paper"  # "paper" | "live"
    ib_port_paper: int = 7497  # TWS Paper (Gateway Paper: 4002)
    ib_port_live: int = 7496  # TWS Real (Gateway Real: 4001)
    live_confirmed: bool = False  # confirmado na GUI (escrever REAL) antes de ligar em modo real
    ui_always_on_top: bool = False
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
    cycle_seconds: int = 60  # frequência do ciclo (gestão de risco, settlement, UI)
    llm_interval_minutes: int = 15  # cadência máxima de consulta ao LLM por ativo
    signal_persistence_cycles: int = 2  # BUY/SELL só executa se se mantiver N ciclos LLM seguidos
    decision_bar_minutes: int = 5  # indicadores/ATR sobre velas agregadas de N min
    min_confidence: float = 0.65  # piso enquanto não há calibração (confiança composta)
    edge_margin: float = 0.08  # margem acima do break-even do bracket para executar
    max_open_positions: int = 4
    allow_short: bool = True
    trade_only_rth: bool = True  # só decide em horário regular (09:30-16:00 NY)
    skip_open_minutes: int = 15  # não abre posições nos primeiros N min da sessão
    skip_close_minutes: int = 10  # nem nos últimos N min
    max_bar_age_seconds: int = 180  # ignora dados mais velhos que isto
    max_trades_per_day: int = 6
    benchmark_symbol: str = "SPY"  # para alpha no settlement (não é negociado)

    # ---- Risco: dimensionamento por volatilidade ---------------------------------
    risk_per_trade_pct: float = 0.005  # 0,5% do equity por trade (fase de aprendizagem)
    risk_per_trade_pct_validated: float = 0.01  # 1% quando os gates estatísticos passam
    atr_period: int = 14
    atr_stop_multiple: float = 2.0  # stop = k × ATR
    reward_risk_ratio: float = 2.0  # TP = R × distância do stop
    atr_floor_percentile: float = 5.0  # piso de volatilidade (pysystemtrade)
    max_position_notional_pct: float = 0.30  # máx. 30% do equity num ativo
    stop_mode: str = "atr"  # "atr" | "fixed"
    stop_loss_pct: float = 0.02  # usados só em stop_mode="fixed"
    take_profit_pct: float = 0.05
    use_trailing_stop: bool = False  # TRAIL em vez de STP no filho de stop

    # ---- Risco: protections (freqtrade-style) ------------------------------------
    daily_loss_limit_pct: float = 0.03  # kill-switch diário
    stoploss_guard_count: int = 3  # N stops numa janela -> pausa
    stoploss_guard_window_minutes: int = 120
    stoploss_guard_pause_minutes: int = 60
    cooldown_minutes: int = 30  # por ativo, após qualquer saída
    max_drawdown_pct: float = 0.06  # pico-vale do equity nos últimos N dias -> pausa
    max_drawdown_lookback_days: int = 5
    max_drawdown_pause_sessions: int = 1
    reduce_size_drawdown_pct: float = 0.03  # zona amarela: tamanho a metade
    consecutive_loss_halt: int = 5  # N trades perdedores seguidos -> pausa até ao próximo dia

    # ---- Risco: custos e eventos ---------------------------------------------------
    commission_per_share: float = 0.005
    commission_min: float = 1.0
    commission_max_pct: float = 0.01
    slippage_ticks: int = 1
    tick_size: float = 0.01
    max_cost_fraction_of_tp: float = 0.20  # custo ida+volta <= 20% do ganho bruto no TP
    min_net_gain_multiple: float = 3.0  # ganho líquido no TP >= 3× o custo ida+volta
    min_position_notional: float = 200.0
    earnings_blackout_days_before: int = 1
    earnings_blackout_days_after: int = 1
    vix_reduce_threshold: float = 25.0  # acima: tamanho a metade
    vix_block_threshold: float = 35.0  # acima: sem novas entradas
    event_data_fail_closed: bool = False  # True: sem dados de resultados -> não entra

    # ---- LLM: pipeline de decisão --------------------------------------------------
    llm_two_stage: bool = True  # raciocínio livre -> JSON com schema
    llm_samples: int = 5  # N amostras para fração de acordo
    llm_sample_temperature: float = 0.7
    llm_request_logprobs: bool = True
    llm_seed: int = 7
    llm_min_agreement: float = 0.6  # acordo mínimo entre amostras
    ensemble_models: list[str] = field(default_factory=list)  # ex.: ["llama3", "qwen2.5:7b"]
    ab_test_models: list[str] = field(default_factory=list)  # round-robin para A/B
    anonymize_prompt: bool = True  # ticker e níveis de preço ocultados ao LLM
    lessons_in_prompt: int = 3
    review_retry_temperature: float = 0.0

    # ---- Calibração e gates estatísticos -----------------------------------------
    settlement_horizon_minutes: int = 30
    calibration_min_samples: int = 200
    calibration_refit_every_hours: int = 24
    gate_min_closed_trades: int = 100
    gate_max_ece: float = 0.05
    gate_min_psr: float = 0.95
    gate_min_wfe: float = 0.5
    gate_max_ruin_prob: float = 0.05
    weekly_report_weekday: int = 4  # sexta-feira

    # ---- Indicadores -------------------------------------------------------------
    rsi_period: int = 14
    sma_fast: int = 20
    sma_slow: int = 50
    ema_period: int = 9

    # ---- Retrospetiva (aprendizagem) -------------------------------------------
    retro_time_local: str = "21:30"  # hora local para a retrospetiva diária
    retro_lookahead_minutes: int = 30  # horizonte para avaliar cada decisão
    retro_max_lessons: int = 8  # nº máximo de lições guardadas
    retro_use_llm_summary: bool = False  # lições são calculadas em código (Honest Lying)

    # ---- Fase 3: módulos opcionais ------------------------------------------------
    sentiment_enabled: bool = False  # FinBERT (requer transformers/onnxruntime)
    sentiment_model: str = "ProsusAI/finbert"
    sentiment_window_hours: int = 2
    sentiment_veto_threshold: float = -0.4
    sentiment_veto_min_news: int = 3
    news_source: str = "yfinance"  # "yfinance" | "finnhub"
    finnhub_api_key: str = ""
    volmodel_enabled: bool = False  # Chronos-Bolt / TTM (requer chronos-forecasting)
    volmodel_name: str = "amazon/chronos-bolt-small"
    volmodel_horizon_bars: int = 6
    volmodel_block_width_pct: float = 0.03  # p90-p10 previsto acima disto -> sem entrada
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""

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

    @property
    def is_live(self) -> bool:
        return self.trading_mode == "live"

    @property
    def ib_port(self) -> int:
        return self.ib_port_live if self.is_live else self.ib_port_paper

    def db_path(self) -> Path:
        return app_data_dir() / "trader.sqlite3"

    def log_path(self) -> Path:
        return app_data_dir() / "trader.log"
