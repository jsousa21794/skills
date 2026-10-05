"""Camada de risco determinística: o LLM propõe, este módulo decide.

Corre inteiramente em Python antes de qualquer ``placeOrder`` e custa zero
inferência. Implementa a fase 1 do roteiro:

- dimensionamento por volatilidade (risco fixo em % do equity / (k × ATR)),
  com piso de volatilidade e teto de notional por ativo;
- *protections* ao estilo freqtrade: StoplossGuard, CooldownPeriod,
  MaxDrawdown multi-dia, perdas consecutivas, limite diário de entradas e
  kill-switch diário;
- gate de custos (comissão IBKR + slippage face ao ganho esperado) e de
  horário (abertura/fecho);
- blackout de resultados e filtro de VIX;
- veto de sentimento e de volatilidade prevista (fase 3, opcionais).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from .config import Settings
from .database import Database
from .indicators import Bar, atr, atr_series, percentile

log = logging.getLogger("trader.risk")
NY = ZoneInfo("America/New_York")


@dataclass
class SizingResult:
    qty: int
    stop_price: float
    tp_price: float
    stop_distance: float
    risk_amount: float
    atr_used: float
    notes: list[str] = field(default_factory=list)


@dataclass
class GateResult:
    allowed: bool
    reason: str = ""
    size_multiplier: float = 1.0
    notes: list[str] = field(default_factory=list)

    def block(self, reason: str) -> "GateResult":
        self.allowed = False
        self.reason = reason
        return self


# ---------------------------------------------------------------------------
# Dimensionamento
# ---------------------------------------------------------------------------
class PositionSizer:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def effective_atr(self, bars: list[Bar]) -> Optional[float]:
        """ATR atual com piso no percentil configurado do histórico (pysystemtrade)."""
        current = atr(bars, self.s.atr_period)
        if current is None:
            return None
        history = atr_series(bars, self.s.atr_period)
        floor = percentile(history, self.s.atr_floor_percentile) if len(history) >= 20 else None
        return max(current, floor) if floor else current

    def size(self, *, action: str, price: float, equity: float, bars: list[Bar],
             risk_pct: Optional[float] = None, multiplier: float = 1.0) -> Optional[SizingResult]:
        if price <= 0 or equity <= 0:
            return None
        notes: list[str] = []
        risk_pct = risk_pct if risk_pct is not None else self.s.risk_per_trade_pct
        if self.s.stop_mode == "fixed":
            stop_distance = price * self.s.stop_loss_pct
            tp_distance = price * self.s.take_profit_pct
            atr_used = stop_distance / max(self.s.atr_stop_multiple, 1e-9)
            notes.append("stop fixo em %")
        else:
            atr_used = self.effective_atr(bars)
            if atr_used is None or atr_used <= 0:
                return None
            stop_distance = self.s.atr_stop_multiple * atr_used
            tp_distance = stop_distance * self.s.reward_risk_ratio
        stop_distance = max(stop_distance, self.s.tick_size * 2)
        risk_amount = equity * risk_pct * multiplier
        # jesse.utils.risk_to_qty: qty = risco / distância ao stop
        qty = math.floor(risk_amount / stop_distance)
        max_notional = equity * self.s.max_position_notional_pct
        if qty * price > max_notional:
            qty = math.floor(max_notional / price)
            notes.append(f"limitado a {self.s.max_position_notional_pct:.0%} do equity")
        if qty < 1:
            return SizingResult(0, 0.0, 0.0, stop_distance, risk_amount, atr_used, notes + ["quantidade < 1"])
        sign = 1 if action == "BUY" else -1
        stop_price = _round_tick(price - sign * stop_distance, self.s.tick_size)
        tp_price = _round_tick(price + sign * tp_distance, self.s.tick_size)
        return SizingResult(qty, stop_price, tp_price, stop_distance, qty * stop_distance, atr_used, notes)


def _round_tick(price: float, tick: float) -> float:
    return round(math.floor(price / tick + 0.5) * tick, 2)


# ---------------------------------------------------------------------------
# Custos
# ---------------------------------------------------------------------------
def commission(qty: int, price: float, s: Settings) -> float:
    raw = max(s.commission_min, qty * s.commission_per_share)
    return min(raw, qty * price * s.commission_max_pct)


def round_trip_cost(qty: int, price: float, s: Settings) -> float:
    return 2 * commission(qty, price, s) + 2 * s.slippage_ticks * s.tick_size * qty


def break_even_probability(reward_risk_ratio: float, cost: float = 0.0, risk_amount: float = 1.0) -> float:
    """p* = (1 + c/R) / (1 + a) com a = R:R e c = custo em unidades de risco."""
    if reward_risk_ratio <= 0:
        return 1.0
    c = cost / max(risk_amount, 1e-9)
    return min(1.0, (1.0 + c) / (1.0 + reward_risk_ratio))


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------
class RiskGate:
    def __init__(self, settings: Settings, db: Database, events: Any = None) -> None:
        self.s = settings
        self.db = db
        self.events = events
        self._pauses: dict[str, datetime] = {}  # nome -> até quando (UTC)
        self._halted_day: Optional[date] = None
        self._day_start_equity: Optional[float] = None
        self._day_start_date: Optional[date] = None

    # ------------------------------------------------------------ estado
    def pause(self, name: str, until: datetime, reason: str, symbol: Optional[str] = None) -> None:
        key = f"{name}:{symbol}" if symbol else name
        if self._pauses.get(key) and self._pauses[key] >= until:
            return
        self._pauses[key] = until
        self.db.add_protection_event(name, symbol, until, reason)
        log.warning("PROTECTION %s%s ativa até %s: %s", name, f" [{symbol}]" if symbol else "",
                    until.astimezone().strftime("%H:%M"), reason)

    def active_pauses(self, now: datetime) -> dict[str, datetime]:
        return {k: v for k, v in self._pauses.items() if v > now}

    def update_day_baseline(self, equity: Optional[float], now: datetime) -> None:
        if equity is None:
            return
        today = now.date()
        if self._day_start_date != today:
            persisted = self.db.first_net_liq_on(now)
            self._day_start_equity = persisted if persisted is not None else equity
            self._day_start_date = today
            if self._halted_day and self._halted_day != today:
                self._halted_day = None
                log.info("Novo dia: kill-switch diário reposto.")

    def day_pnl_pct(self, equity: Optional[float]) -> Optional[float]:
        if equity is None or not self._day_start_equity:
            return None
        return (equity - self._day_start_equity) / self._day_start_equity * 100.0

    @property
    def halted(self) -> bool:
        return self._halted_day is not None

    # ------------------------------------------------------------- global
    def check_global(self, *, equity: Optional[float], now: datetime) -> GateResult:
        """Gates que não dependem do ativo. Chamado uma vez por ciclo."""
        result = GateResult(True)
        today = now.date()
        self.update_day_baseline(equity, now)

        # Kill-switch diário.
        if self._halted_day == today:
            return result.block("kill-switch diário ativo")
        pct = self.day_pnl_pct(equity)
        if pct is not None and pct <= -self.s.daily_loss_limit_pct * 100.0:
            self._halted_day = today
            self.db.add_protection_event("daily_loss", None, None, f"{pct:.2f}%")
            log.critical("KILL-SWITCH: perda diária %.2f%% > limite %.1f%%. Sem novas entradas hoje.",
                         pct, self.s.daily_loss_limit_pct * 100)
            return result.block("kill-switch diário")

        # Pausas ativas (StoplossGuard, drawdown, perdas consecutivas).
        for key, until in self.active_pauses(now).items():
            if ":" not in key:  # pausas globais
                return result.block(f"pausa {key} até {until.astimezone().strftime('%H:%M')}")

        # StoplossGuard: N stops numa janela.
        window_start = now - timedelta(minutes=self.s.stoploss_guard_window_minutes)
        stops = self.db.recent_stop_count(window_start)
        if stops >= self.s.stoploss_guard_count:
            until = now + timedelta(minutes=self.s.stoploss_guard_pause_minutes)
            self.pause("stoploss_guard", until, f"{stops} stops em {self.s.stoploss_guard_window_minutes} min")
            return result.block("StoplossGuard")

        # Perdas consecutivas.
        losses = self.db.consecutive_losses()
        if losses >= self.s.consecutive_loss_halt:
            until = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
            self.pause("consecutive_losses", until, f"{losses} perdas seguidas")
            return result.block("perdas consecutivas")

        # Drawdown multi-dia (pico-vale do equity nos últimos N dias).
        dd = self.recent_drawdown_pct(now)
        if dd is not None:
            if dd >= self.s.max_drawdown_pct * 100.0:
                sessions = max(1, self.s.max_drawdown_pause_sessions)
                until = datetime.combine(today + timedelta(days=sessions), datetime.min.time(), tzinfo=timezone.utc)
                self.pause("max_drawdown", until, f"drawdown {dd:.2f}% em {self.s.max_drawdown_lookback_days} dias")
                return result.block("MaxDrawdown")
            if dd >= self.s.reduce_size_drawdown_pct * 100.0:
                result.size_multiplier *= 0.5
                result.notes.append(f"zona amarela: drawdown {dd:.2f}% -> tamanho a metade")

        # Limite de entradas por dia.
        day_start = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
        if self.db.entries_today(day_start) >= self.s.max_trades_per_day:
            return result.block(f"máximo de {self.s.max_trades_per_day} entradas/dia")

        # VIX.
        if self.events is not None:
            vix = self.events.vix()
            if vix is not None:
                if vix >= self.s.vix_block_threshold:
                    return result.block(f"VIX {vix:.1f} >= {self.s.vix_block_threshold:.0f}")
                if vix >= self.s.vix_reduce_threshold:
                    result.size_multiplier *= 0.5
                    result.notes.append(f"VIX {vix:.1f}: tamanho a metade")
        return result

    def recent_drawdown_pct(self, now: datetime) -> Optional[float]:
        since = now - timedelta(days=self.s.max_drawdown_lookback_days)
        series = [v for _, v in self.db.daily_equity(since)]
        series += [v for _, v in self.db.equity_series(now - timedelta(hours=1))][-1:]
        if len(series) < 2:
            return None
        peak = series[0]
        worst = 0.0
        for v in series:
            peak = max(peak, v)
            worst = max(worst, (peak - v) / peak * 100.0 if peak else 0.0)
        return worst

    # ------------------------------------------------------------ por ativo
    def check_symbol(self, *, symbol: str, now: datetime) -> GateResult:
        result = GateResult(True)
        if not self.in_trading_window(now):
            return result.block("fora da janela de negociação (abertura/fecho)")
        for key, until in self.active_pauses(now).items():
            if key.endswith(f":{symbol}"):
                return result.block(f"pausa {key.split(':')[0]} até {until.astimezone().strftime('%H:%M')}")
        last_exit = self.db.last_exit_ts(symbol)
        if last_exit and now - last_exit < timedelta(minutes=self.s.cooldown_minutes):
            return result.block(f"cooldown após saída ({self.s.cooldown_minutes} min)")
        if self.events is not None:
            blackout = self.events.in_earnings_blackout(
                symbol, self.s.earnings_blackout_days_before, self.s.earnings_blackout_days_after)
            if blackout:
                return result.block("blackout de resultados")
            if blackout is None and self.s.event_data_fail_closed:
                return result.block("sem dados de resultados (fail-closed)")
            if blackout is None:
                result.notes.append("datas de resultados indisponíveis")
        return result

    def in_trading_window(self, now_utc: datetime) -> bool:
        local = now_utc.astimezone(NY)
        if local.weekday() >= 5:
            return False
        minutes = local.hour * 60 + local.minute
        start = 9 * 60 + 30 + self.s.skip_open_minutes
        end = 16 * 60 - self.s.skip_close_minutes
        return start <= minutes < end

    # ---------------------------------------------------------------- custos
    def check_costs(self, *, qty: int, price: float, tp_distance: float) -> GateResult:
        result = GateResult(True)
        notional = qty * price
        if notional < self.s.min_position_notional:
            return result.block(f"notional {notional:.0f} USD < mínimo {self.s.min_position_notional:.0f}")
        cost = round_trip_cost(qty, price, self.s)
        expected_gain = qty * tp_distance
        if expected_gain <= 0 or cost > expected_gain * self.s.max_cost_fraction_of_tp:
            return result.block(f"custo ida+volta {cost:.2f} USD > {self.s.max_cost_fraction_of_tp:.0%} do ganho alvo {expected_gain:.2f}")
        result.notes.append(f"custo estimado {cost:.2f} USD ({cost / max(notional, 1e-9) * 100:.2f}%)")
        return result

    # -------------------------------------------------------------- fase 3
    def check_sentiment_veto(self, action: str, sentiment: Optional[float], n_news: int) -> GateResult:
        result = GateResult(True)
        if sentiment is None or n_news < self.s.sentiment_veto_min_news:
            return result
        if action == "BUY" and sentiment <= self.s.sentiment_veto_threshold:
            return result.block(f"veto de sentimento ({sentiment:+.2f} em {n_news} notícias)")
        if action == "SELL" and sentiment >= -self.s.sentiment_veto_threshold:
            return result.block(f"veto de sentimento ({sentiment:+.2f} em {n_news} notícias)")
        return result

    def check_vol_forecast(self, width_pct: Optional[float]) -> GateResult:
        result = GateResult(True)
        if width_pct is not None and width_pct >= self.s.volmodel_block_width_pct * 100.0:
            return result.block(f"volatilidade prevista {width_pct:.2f}% acima do limite")
        return result
