"""Configuração central da aplicação.

O modo predefinido é a conta REAL (porta 7496). O ficheiro ``config.json`` na
pasta de dados do utilizador sobrepõe-se a estes valores, é validado ao
carregar (tipos, números finitos, intervalos) e é gravado atomicamente.
Valores inválidos são substituídos pelo defeito e registados em
``Settings.load_warnings``.

Compatível com PyInstaller: nada é gravado dentro do bundle congelado; os
dados vivem em ``~/.ollamaibkrtrader`` (ou na pasta indicada pela variável de
ambiente ``OLLAMA_TRADER_HOME``).
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, ClassVar

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
    trading_mode: str = "live"  # "live" (predefinido, porta 7496) | "paper" (7497)
    ib_port_paper: int = 7497  # TWS Paper (Gateway Paper: 4002)
    ib_port_live: int = 7496  # TWS Real (Gateway Real: 4001)
    live_confirmed: bool = False  # confirmação única na GUI (escrever REAL); fica guardada
    ui_always_on_top: bool = False
    ib_client_id: int = 17
    ib_account: str = ""  # conta/subconta autorizada; "" = primeira conta gerida
    order_ref: str = "OllamaIBKRTrader"  # etiqueta das ordens do bot (não toca em ordens manuais)
    manage_external_positions: bool = False  # posições fora da lista de ativos: só avisa
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
    max_open_positions: int = 10  # o verdadeiro limite são os fundos disponíveis na corretora
    allow_short: bool = True
    trade_only_rth: bool = True  # só decide em horário regular (09:30-16:00 NY)
    skip_open_minutes: int = 15  # não abre posições nos primeiros N min da sessão
    skip_close_minutes: int = 10  # nem nos últimos N min
    max_bar_age_seconds: int = 180  # ignora dados mais velhos que isto
    decision_max_age_seconds: int = 180  # uma decisão mais velha que isto não é executada
    max_entry_slippage_pct: float = 0.003  # entrada em limit marketable: ref × (1 ± 0,3%)
    entry_timeout_seconds: int = 120  # entrada não executada ao fim deste tempo é cancelada
    max_trades_per_day: int = 0  # 0 = sem limite (aplica-se só quando o dia está em perda)
    relax_limits_when_in_profit: bool = True  # em lucro no dia: sem limite de entradas, sem StoplossGuard, sem travão de perdas seguidas
    pdt_guard_enabled: bool = True  # contas < 25k USD: máx. 3 day trades em 5 dias úteis (regra da corretora)
    pdt_equity_threshold: float = 25_000.0
    pdt_max_day_trades: int = 3
    benchmark_symbol: str = "SPY"  # para alpha no settlement (não é negociado)

    # ---- Risco: dimensionamento por volatilidade ---------------------------------
    risk_per_trade_pct: float = 0.10  # até 10% do equity por trade (limitado pelos fundos disponíveis)
    risk_per_trade_pct_validated: float = 0.10
    learning_risk_multiplier: float = 1.0  # aplicado ao risco enquanto os gates estatísticos não passam
    atr_period: int = 14
    atr_stop_multiple: float = 2.0  # stop = k × ATR
    reward_risk_ratio: float = 2.0  # TP = R × distância do stop
    atr_floor_percentile: float = 5.0  # piso de volatilidade (pysystemtrade)
    max_position_notional_pct: float = 1.0  # até 100% do equity num ativo; os fundos disponíveis da corretora mandam
    stop_mode: str = "atr"  # "atr" | "fixed"
    stop_loss_pct: float = 0.02  # usados só em stop_mode="fixed"
    take_profit_pct: float = 0.05
    use_trailing_stop: bool = False  # TRAIL em vez de STP no filho de stop

    # ---- Risco: protections (freqtrade-style) ------------------------------------
    daily_loss_limit_pct: float = 0.20  # kill-switch diário: a -20% para o ciclo automaticamente
    stoploss_guard_count: int = 3  # N stops numa janela -> pausa
    stoploss_guard_window_minutes: int = 120
    stoploss_guard_pause_minutes: int = 60
    cooldown_minutes: int = 30  # por ativo, só após uma saída em perda
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
    llm_min_agreement: float = 0.6  # acordo mínimo entre amostras (sobre as amostras PEDIDAS)
    llm_min_valid_fraction: float = 0.6  # fração mínima de amostras válidas; abaixo -> REVIEW
    ensemble_models: list[str] = field(default_factory=list)  # ex.: ["llama3", "qwen2.5:7b"]
    ab_test_models: list[str] = field(default_factory=list)  # round-robin para A/B
    anonymize_prompt: bool = True  # ticker e níveis de preço ocultados ao LLM
    lessons_in_prompt: int = 3
    seed_lessons_file: str = "data/seed_lessons.json"  # lições iniciais com fonte; importado uma vez ("" desliga)
    review_retry_temperature: float = 0.0

    # ---- Calibração e gates estatísticos -----------------------------------------
    settlement_horizon_minutes: int = 30
    settlement_max_gap_minutes: int = 10  # sem vela a menos de N min do alvo, a decisão não é avaliada
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
    load_warnings: ClassVar[list[str]] = []

    @classmethod
    def config_path(cls) -> Path:
        return app_data_dir() / "config.json"

    @classmethod
    def load(cls) -> "Settings":
        """Carrega e valida o config.json por cima dos valores por defeito."""
        settings = cls()
        cls.load_warnings = []
        path = cls.config_path()
        if path.exists():
            try:
                data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                cls.load_warnings.append(f"config.json ilegível ({exc}); a usar valores por defeito")
                data = {}
            settings.apply(data)
        settings.save()
        return settings

    def apply(self, data: dict[str, Any]) -> list[str]:
        """Aplica valores com coerção de tipo e validação; devolve os avisos."""
        warnings: list[str] = []
        defaults = type(self)()
        for f in fields(self):
            if f.name not in data:
                continue
            raw = data[f.name]
            default = getattr(defaults, f.name)
            ok, value = _coerce(raw, default)
            if not ok:
                warnings.append(f"{f.name}: valor inválido {raw!r}; a usar {default!r}")
                continue
            valid, reason = _validate(f.name, value)
            if not valid:
                warnings.append(f"{f.name}: {reason} ({value!r}); a usar {default!r}")
                continue
            setattr(self, f.name, value)
        # Relações entre parâmetros.
        if self.reward_risk_ratio <= 0 or self.atr_stop_multiple <= 0:
            warnings.append("reward_risk_ratio/atr_stop_multiple têm de ser positivos; a repor")
            self.reward_risk_ratio, self.atr_stop_multiple = defaults.reward_risk_ratio, defaults.atr_stop_multiple
        if self.trading_mode not in ("live", "paper"):
            warnings.append(f"trading_mode inválido ({self.trading_mode!r}); a usar 'live'")
            self.trading_mode = "live"
        if self.stop_mode not in ("atr", "fixed"):
            warnings.append(f"stop_mode inválido ({self.stop_mode!r}); a usar 'atr'")
            self.stop_mode = "atr"
        type(self).load_warnings.extend(warnings)
        return warnings

    def save(self) -> None:
        """Gravação atómica: escreve num temporário e substitui."""
        path = self.config_path()
        try:
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(asdict(self), fh, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            pass

    @property
    def is_live(self) -> bool:
        return self.trading_mode == "live"

    @property
    def ib_port(self) -> int:
        return self.ib_port_live if self.is_live else self.ib_port_paper

    def db_path(self) -> Path:
        """Base de dados separada por modo de conta (paper e real nunca se misturam)."""
        return app_data_dir() / f"trader_{self.trading_mode}.sqlite3"

    def log_path(self) -> Path:
        return app_data_dir() / "trader.log"


_PCT_FIELDS_0_1 = ("risk_per_trade_pct", "risk_per_trade_pct_validated", "max_position_notional_pct", "stop_loss_pct",
                   "take_profit_pct", "daily_loss_limit_pct", "max_drawdown_pct", "reduce_size_drawdown_pct",
                   "commission_max_pct", "max_cost_fraction_of_tp", "min_confidence", "edge_margin", "llm_min_agreement",
                   "llm_min_valid_fraction", "max_entry_slippage_pct", "volmodel_block_width_pct", "gate_max_ece",
                   "gate_min_psr", "gate_max_ruin_prob", "atr_floor_percentile_frac")
_POSITIVE_FIELDS = ("cycle_seconds", "llm_interval_minutes", "signal_persistence_cycles", "decision_bar_minutes",
                    "rsi_period", "sma_fast", "sma_slow", "ema_period", "atr_period", "llm_samples",
                    "settlement_horizon_minutes", "ollama_timeout_seconds", "ollama_num_ctx", "ib_port_paper",
                    "ib_port_live", "ui_poll_ms", "log_max_lines", "lessons_in_prompt", "retro_max_lessons")
_NON_NEGATIVE_FIELDS = ("max_open_positions", "max_trades_per_day", "skip_open_minutes", "skip_close_minutes",
                        "cooldown_minutes", "stoploss_guard_count", "stoploss_guard_window_minutes",
                        "stoploss_guard_pause_minutes", "consecutive_loss_halt", "slippage_ticks",
                        "commission_per_share", "commission_min", "min_position_notional", "tick_size",
                        "earnings_blackout_days_before", "earnings_blackout_days_after", "learning_risk_multiplier",
                        "decision_max_age_seconds", "entry_timeout_seconds", "settlement_max_gap_minutes",
                        "max_bar_age_seconds", "calibration_min_samples", "gate_min_closed_trades",
                        "pdt_max_day_trades", "pdt_equity_threshold", "vix_reduce_threshold", "vix_block_threshold",
                        "ollama_temperature", "llm_sample_temperature", "review_retry_temperature",
                        "min_net_gain_multiple", "sentiment_veto_min_news", "sentiment_window_hours",
                        "volmodel_horizon_bars", "max_drawdown_lookback_days", "max_drawdown_pause_sessions",
                        "retro_lookahead_minutes", "calibration_refit_every_hours")


def _coerce(raw: Any, default: Any) -> tuple[bool, Any]:
    """Converte ``raw`` para o tipo do valor por defeito; False se impossível."""
    if isinstance(default, bool):
        if isinstance(raw, bool):
            return True, raw
        if isinstance(raw, (int, float)) and raw in (0, 1):
            return True, bool(raw)
        if isinstance(raw, str) and raw.strip().lower() in ("true", "false", "1", "0", "yes", "no", "sim", "não", "nao"):
            return True, raw.strip().lower() in ("true", "1", "yes", "sim")
        return False, None
    if isinstance(default, int):
        if isinstance(raw, bool):
            return False, None
        if isinstance(raw, int):
            return True, raw
        if isinstance(raw, float) and math.isfinite(raw) and raw.is_integer():
            return True, int(raw)
        if isinstance(raw, str):
            try:
                return True, int(raw.strip())
            except ValueError:
                return False, None
        return False, None
    if isinstance(default, float):
        if isinstance(raw, bool):
            return False, None
        if isinstance(raw, (int, float)):
            value = float(raw)
        elif isinstance(raw, str):
            try:
                value = float(raw.strip().replace(",", "."))
            except ValueError:
                return False, None
        else:
            return False, None
        return (True, value) if math.isfinite(value) else (False, None)
    if isinstance(default, str):
        return (True, raw) if isinstance(raw, str) else (False, None)
    if isinstance(default, list):
        if isinstance(raw, list) and all(isinstance(x, str) for x in raw):
            return True, raw
        if isinstance(raw, str):
            return True, [x.strip() for x in raw.split(",") if x.strip()]
        return False, None
    return True, raw


def _validate(name: str, value: Any) -> tuple[bool, str]:
    if name in _PCT_FIELDS_0_1 and not (0.0 <= float(value) <= 1.0):
        return False, "tem de estar entre 0 e 1"
    if name == "atr_floor_percentile" and not (0.0 <= float(value) <= 100.0):
        return False, "tem de estar entre 0 e 100"
    if name in _POSITIVE_FIELDS and not (float(value) > 0):
        return False, "tem de ser positivo"
    if name in _NON_NEGATIVE_FIELDS and float(value) < 0:
        return False, "não pode ser negativo"
    if name == "symbols" and not value:
        return False, "lista vazia"
    return True, ""
