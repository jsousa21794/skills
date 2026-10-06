"""Política de execução partilhada entre o motor (live) e o replay (backtest).

Recebe um ``DecisionOutcome`` já calibrado e o estado atual, e devolve ou um
``ExecutionPlan`` (entrada com quantidade e níveis, ou fecho) ou uma
``Skip`` com a razão. Não toca na corretora: quem chama executa o plano.

Mantém num único sítio as regras que o live e o replay têm de partilhar:
persistência do sinal, acordo mínimo, fecho por sinal contrário, gates
globais e por ativo, short, máximo de posições, fundos, sizing por ATR,
limiar de probabilidade com custos, gate de custos/EV, vetos de sentimento e
volatilidade, e validade temporal da decisão.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from .calibration import Calibrator, execution_threshold
from .config import Settings
from .indicators import Bar, MarketSnapshot
from .ollama_brain import DecisionOutcome
from .risk import GateResult, PositionSizer, RiskGate, SizingResult, round_trip_cost


@dataclass
class Skip:
    reason: str


@dataclass
class ExecutionPlan:
    kind: str  # "ENTRY" | "CLOSE"
    action: str
    symbol: str
    qty: int = 0
    sizing: Optional[SizingResult] = None
    threshold: float = 0.0
    probability: float = 0.0
    cost: float = 0.0
    limit_price: Optional[float] = None
    notes: list[str] = field(default_factory=list)


class PersistenceTracker:
    """Histórico curto de ações por ativo para exigir sinais persistentes."""

    def __init__(self, cycles: int) -> None:
        self.cycles = max(1, cycles)
        self._hist: dict[str, list[str]] = {}

    def push(self, symbol: str, action: str) -> list[str]:
        hist = self._hist.setdefault(symbol, [])
        hist.append(action)
        del hist[:-self.cycles]
        return list(hist)

    def reset(self, symbol: str) -> None:
        self._hist[symbol] = []

    def clear(self) -> None:
        self._hist.clear()


def decide_execution(
    *,
    settings: Settings,
    gate: RiskGate,
    sizer: PositionSizer,
    calibrator: Calibrator,
    outcome: DecisionOutcome,
    calibrated: Optional[float],
    snapshot: MarketSnapshot,
    agg_bars: list[Bar],
    now: datetime,
    position_qty: float,
    open_positions: int,
    pending_entries: int,
    equity_usd: Optional[float],
    available_funds_usd: Optional[float],
    reserved_notional: float,
    persistence: list[str],
    pending_close: bool,
    pending_entry: bool,
    global_gate: GateResult,
    sentiment: tuple[Optional[float], int] = (None, 0),
    vol_width: Optional[float] = None,
    risk_multiplier: float = 1.0,
    gates_passed: bool = False,
) -> ExecutionPlan | Skip:
    decision = outcome.decision
    action = decision.acao
    symbol = snapshot.symbol

    if action == "HOLD":
        return Skip("HOLD")
    if outcome.review or not decision.parse_ok:
        return Skip("resposta inválida")
    if action not in ("BUY", "SELL"):
        return Skip(f"ação desconhecida {action}")
    # Validade temporal da decisão (inferência lenta, dados antigos).
    age = (now - snapshot.bar_time).total_seconds()
    if age > settings.decision_max_age_seconds + settings.decision_bar_minutes * 60:
        return Skip(f"decisão expirada ({age:.0f}s desde a vela)")
    if outcome.agree_frac < settings.llm_min_agreement:
        return Skip(f"acordo {outcome.agree_frac:.0%} < {settings.llm_min_agreement:.0%}")
    if len(persistence) < settings.signal_persistence_cycles or any(a != action for a in persistence):
        return Skip(f"sinal ainda não persistente ({'/'.join(persistence)})")
    if pending_close:
        return Skip("fecho de posição ainda pendente")

    # Mesma direção da posição: nada (sem pirâmide). Direção oposta: fechar, não inverter.
    if (action == "BUY" and position_qty > 0) or (action == "SELL" and position_qty < 0):
        return Skip("já posicionado nessa direção")
    if (action == "BUY" and position_qty < 0) or (action == "SELL" and position_qty > 0):
        return ExecutionPlan("CLOSE", action, symbol, probability=calibrated or 0.0)

    # ---- Camada de risco ----
    if not global_gate.allowed:
        return Skip(global_gate.reason)
    sym_gate = gate.check_symbol(symbol=symbol, now=now)
    if not sym_gate.allowed:
        return Skip(sym_gate.reason)
    if action == "SELL" and not settings.allow_short:
        return Skip("short desativado na configuração")
    if pending_entry:
        return Skip("ordem de entrada ainda pendente")
    if open_positions + pending_entries >= settings.max_open_positions:
        return Skip(f"máximo de posições ({settings.max_open_positions}) atingido (inclui entradas pendentes)")
    sent_gate = gate.check_sentiment_veto(action, sentiment[0], sentiment[1])
    if not sent_gate.allowed:
        return Skip(sent_gate.reason)
    vol_gate = gate.check_vol_forecast(vol_width)
    if not vol_gate.allowed:
        return Skip(vol_gate.reason)

    if equity_usd is None or equity_usd <= 0 or snapshot.price <= 0:
        return Skip("equity (USD) desconhecido")
    if available_funds_usd is None:
        return Skip("fundos disponíveis desconhecidos")
    funds = available_funds_usd - reserved_notional
    if funds <= 0:
        return Skip(f"sem fundos disponíveis ({available_funds_usd:,.0f} USD, reservados {reserved_notional:,.0f})")

    risk_pct = settings.risk_per_trade_pct_validated if gates_passed else settings.risk_per_trade_pct
    multiplier = global_gate.size_multiplier * sym_gate.size_multiplier * (1.0 if gates_passed else risk_multiplier)
    sizing = sizer.size(action=action, price=snapshot.price, equity=equity_usd, bars=agg_bars, risk_pct=risk_pct,
                        multiplier=multiplier, available_funds=funds)
    if sizing is None:
        return Skip("ATR indisponível para dimensionar")
    if sizing.qty < 1:
        return Skip("quantidade < 1 ação (" + "; ".join(sizing.notes) + ")")

    cost = round_trip_cost(sizing.qty, snapshot.price, settings)
    rr = abs(sizing.tp_price - snapshot.price) / max(sizing.stop_distance, 1e-9)
    threshold = execution_threshold(settings, reward_risk_ratio=rr, cost=cost, risk_amount=sizing.risk_amount,
                                    calibrated=calibrator.is_fitted)
    prob = calibrated if calibrated is not None else 0.0
    if prob < threshold:
        return Skip(f"p={prob:.2f} < limiar {threshold:.2f} (break-even R:R {rr:.1f} com custo {cost:.2f} USD)")
    cost_gate = gate.check_costs(qty=sizing.qty, price=snapshot.price, tp_distance=abs(sizing.tp_price - snapshot.price),
                                 stop_distance=sizing.stop_distance, probability=prob)
    if not cost_gate.allowed:
        return Skip(cost_gate.reason)

    sign = 1 if action == "BUY" else -1
    limit = snapshot.price * (1 + sign * settings.max_entry_slippage_pct) if settings.max_entry_slippage_pct > 0 else None
    return ExecutionPlan("ENTRY", action, symbol, qty=sizing.qty, sizing=sizing, threshold=threshold, probability=prob,
                         cost=cost, limit_price=limit,
                         notes=sizing.notes + cost_gate.notes + global_gate.notes + sym_gate.notes)
