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
    bar_minutes: int = 1,
) -> Optional[MarketSnapshot]:
    """``bar_minutes`` converte os horizontes de 5 e 30 minutos em número de velas."""
    if not closes:
        return None
    closes = list(closes)
    bars_5m = max(1, round(5 / max(bar_minutes, 1)))
    bars_30m = max(1, round(30 / max(bar_minutes, 1)))
    return MarketSnapshot(
        symbol=symbol,
        price=float(closes[-1]),
        bar_time=bar_time,
        rsi=_round(rsi(closes, rsi_period)),
        sma_fast=_round(sma(closes, sma_fast_period)),
        sma_slow=_round(sma(closes, sma_slow_period)),
        ema=_round(ema(closes, ema_period)),
        change_5m_pct=_round(pct_change(closes, bars_5m), 3),
        change_30m_pct=_round(pct_change(closes, bars_30m), 3),
        volume_last=float(volumes[-1]) if volumes else 0.0,
        bars_available=len(closes),
    )


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(value, digits)


# ---------------------------------------------------------------------------
# Volatilidade, agregação e dinâmica (fase 1 do roteiro)
# ---------------------------------------------------------------------------
@dataclass
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def true_ranges(bars: Sequence[Bar]) -> list[float]:
    out: list[float] = []
    prev_close: Optional[float] = None
    for b in bars:
        if prev_close is None:
            tr = b.high - b.low
        else:
            tr = max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
        out.append(tr)
        prev_close = b.close
    return out


def atr(bars: Sequence[Bar], period: int = 14) -> Optional[float]:
    """ATR de Wilder. Devolve None sem ``period + 1`` velas."""
    if period <= 0 or len(bars) < period + 1:
        return None
    trs = true_ranges(bars)[1:]
    current = sum(trs[:period]) / period
    for tr in trs[period:]:
        current = (current * (period - 1) + tr) / period
    return current


def atr_series(bars: Sequence[Bar], period: int = 14) -> list[float]:
    """ATR em cada vela (a partir da vela ``period``), para percentis/piso de vol."""
    if period <= 0 or len(bars) < period + 1:
        return []
    trs = true_ranges(bars)[1:]
    current = sum(trs[:period]) / period
    out = [current]
    for tr in trs[period:]:
        current = (current * (period - 1) + tr) / period
        out.append(current)
    return out


def percentile(values: Sequence[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * (max(0.0, min(100.0, pct)) / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    frac = k - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def bucket_is_complete(bucket: Sequence[Bar], minutes: int) -> bool:
    """Um bucket está completo quando tem exatamente os ``minutes`` minutos do intervalo, sem falhas."""
    if len(bucket) != minutes:
        return False
    first = bucket[0].time.hour * 60 + bucket[0].time.minute
    if first % minutes != 0:
        return False
    return all(b.time.hour * 60 + b.time.minute == first + i for i, b in enumerate(bucket))


def aggregate_bars(bars: Sequence[Bar], minutes: int, *, drop_incomplete: bool = False) -> list[Bar]:
    """Agrega velas de 1 min em velas de ``minutes`` alinhadas ao relógio.

    Política explícita (N17): velas de 1 min duplicadas (mesmo minuto) ficam pela última
    recebida; com ``drop_incomplete`` QUALQUER bucket a que falte um minuto (interno ou o último,
    ainda em formação) é descartado em vez de alimentar os indicadores como vela completa.
    """
    if minutes <= 1:
        return list(bars)
    out: list[Bar] = []
    bucket: list[Bar] = []
    bucket_key: Optional[tuple] = None

    def flush() -> None:
        if bucket and (not drop_incomplete or bucket_is_complete(bucket, minutes)):
            out.append(_merge(bucket))

    for b in bars:
        key = (b.time.hour * 60 + b.time.minute) // minutes
        day_key = (b.time.date(), key)
        if bucket and day_key != bucket_key:
            flush()
            bucket = []
        if bucket and bucket[-1].time.replace(second=0, microsecond=0) == b.time.replace(second=0, microsecond=0):
            bucket[-1] = b  # duplicado do mesmo minuto: a última atualização prevalece
        else:
            bucket.append(b)
        bucket_key = day_key
    flush()
    return out


def _merge(bucket: list[Bar]) -> Bar:
    return Bar(
        time=bucket[0].time,
        open=bucket[0].open,
        high=max(b.high for b in bucket),
        low=min(b.low for b in bucket),
        close=bucket[-1].close,
        volume=sum(b.volume for b in bucket),
    )


def ewma_volatility(closes: Sequence[float], span: int = 35, min_periods: int = 10) -> Optional[float]:
    """Vol EWMA dos retornos (desenho pysystemtrade: span 35, mínimo 10 períodos)."""
    if len(closes) < min_periods + 1:
        return None
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1]]
    if len(rets) < min_periods:
        return None
    alpha = 2.0 / (span + 1)
    var = sum(r * r for r in rets[:min_periods]) / min_periods
    for r in rets[min_periods:]:
        var = alpha * r * r + (1 - alpha) * var
    return var ** 0.5


@dataclass
class Dynamics:
    """Dinâmica dos indicadores: o que tem conteúdo preditivo não são os níveis."""

    rsi_slope: Optional[float]  # variação do RSI nas últimas 3 velas
    rsi_cross_30_up: bool  # RSI cruzou 30 para cima nas últimas 3 velas
    rsi_cross_70_down: bool
    ema_cross_sma_fast: Optional[str]  # "up" | "down" | None nas últimas 3 velas
    sma_fast_cross_slow: Optional[str]
    price_vs_fast: str  # "above" | "below"
    returns_sign_change: bool  # momentum de 5 velas mudou de sinal
    atr_pct: Optional[float]  # ATR / preço em %

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_dynamics(closes: Sequence[float], bars: Optional[Sequence[Bar]] = None, *, rsi_period: int = 14,
                     sma_fast_period: int = 20, sma_slow_period: int = 50, ema_period: int = 9,
                     atr_period: int = 14, lookback: int = 3) -> Optional[Dynamics]:
    n = len(closes)
    if n < max(sma_slow_period, rsi_period + 1) + lookback:
        return None
    closes = list(closes)

    def series(fn, period):
        return [fn(closes[: n - i], period) for i in range(lookback, -1, -1)]

    rsi_s = series(rsi, rsi_period)
    ema_s = series(ema, ema_period)
    fast_s = series(sma, sma_fast_period)
    slow_s = series(sma, sma_slow_period)
    if any(v is None for v in rsi_s + ema_s + fast_s + slow_s):
        return None

    def cross(a: list[float], b: list[float]) -> Optional[str]:
        for i in range(1, len(a)):
            if a[i - 1] <= b[i - 1] and a[i] > b[i]:
                return "up"
            if a[i - 1] >= b[i - 1] and a[i] < b[i]:
                return "down"
        return None

    def level_cross(vals: list[float], level: float, upward: bool) -> bool:
        for i in range(1, len(vals)):
            if upward and vals[i - 1] < level <= vals[i]:
                return True
            if not upward and vals[i - 1] > level >= vals[i]:
                return True
        return False

    mom_now = closes[-1] - closes[-6] if n > 6 else 0.0
    mom_prev = closes[-1 - lookback] - closes[-6 - lookback] if n > 6 + lookback else mom_now
    a = atr(bars, atr_period) if bars else None
    return Dynamics(
        rsi_slope=round(rsi_s[-1] - rsi_s[0], 2),
        rsi_cross_30_up=level_cross(rsi_s, 30.0, True),
        rsi_cross_70_down=level_cross(rsi_s, 70.0, False),
        ema_cross_sma_fast=cross(ema_s, fast_s),
        sma_fast_cross_slow=cross(fast_s, slow_s),
        price_vs_fast="above" if closes[-1] > fast_s[-1] else "below",
        returns_sign_change=(mom_now > 0) != (mom_prev > 0),
        atr_pct=round(a / closes[-1] * 100.0, 3) if a and closes[-1] else None,
    )
