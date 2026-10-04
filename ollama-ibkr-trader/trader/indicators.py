"""Indicadores técnicos em Python puro (sem dependências externas).

Trabalha sobre listas de floats; suficiente para algumas centenas de velas de
1 minuto por ativo. Todas as funções devolvem ``None`` quando não há dados
suficientes, para que o chamador trate explicitamente o caso.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Optional, Sequence


def sma(values: Sequence[float], period: int) -> Optional[float]:
    if period <= 0 or len(values) < period:
        return None
    window = values[-period:]
    return sum(window) / period


def ema(values: Sequence[float], period: int) -> Optional[float]:
    if period <= 0 or len(values) < period:
        return None
    k = 2.0 / (period + 1)
    current = sum(values[:period]) / period  # seed com SMA
    for price in values[period:]:
        current = price * k + current * (1 - k)
    return current


def rsi(closes: Sequence[float], period: int = 14) -> Optional[float]:
    """RSI de Wilder (suavização exponencial dos ganhos/perdas)."""
    if period <= 0 or len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def pct_change(values: Sequence[float], lookback: int) -> Optional[float]:
    if lookback <= 0 or len(values) <= lookback:
        return None
    past = values[-1 - lookback]
    if past == 0:
        return None
    return (values[-1] - past) / past * 100.0


@dataclass
class MarketSnapshot:
    """Fotografia do mercado entregue ao modelo de linguagem."""

    symbol: str
    price: float
    bar_time: datetime
    rsi: Optional[float]
    sma_fast: Optional[float]
    sma_slow: Optional[float]
    ema: Optional[float]
    change_5m_pct: Optional[float]
    change_30m_pct: Optional[float]
    volume_last: float
    bars_available: int

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["bar_time"] = self.bar_time.isoformat()
        return data

    def trend_label(self) -> str:
        if self.sma_fast is None or self.sma_slow is None:
            return "indefinida"
        if self.price > self.sma_fast > self.sma_slow:
            return "alta"
        if self.price < self.sma_fast < self.sma_slow:
            return "baixa"
        return "lateral"


def build_snapshot(
    symbol: str,
    closes: Sequence[float],
    volumes: Sequence[float],
    bar_time: datetime,
    *,
    rsi_period: int = 14,
    sma_fast_period: int = 20,
    sma_slow_period: int = 50,
    ema_period: int = 9,
) -> Optional[MarketSnapshot]:
    if not closes:
        return None
    closes = list(closes)
    return MarketSnapshot(
        symbol=symbol,
        price=float(closes[-1]),
        bar_time=bar_time,
        rsi=_round(rsi(closes, rsi_period)),
        sma_fast=_round(sma(closes, sma_fast_period)),
        sma_slow=_round(sma(closes, sma_slow_period)),
        ema=_round(ema(closes, ema_period)),
        change_5m_pct=_round(pct_change(closes, 5), 3),
        change_30m_pct=_round(pct_change(closes, 30), 3),
        volume_last=float(volumes[-1]) if volumes else 0.0,
        bars_available=len(closes),
    )


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(value, digits)
