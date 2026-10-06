"""Settlement: cada decisão só é avaliada quando o horizonte expira.

Adaptado do ``memory/settlement.py`` do TradingAgents (Apache-2.0) a um
horizonte de minutos: para cada decisão sem ``settled_ts`` cujo ``ts`` já
tenha ``horizon`` minutos, procura-se o preço do ativo e do benchmark nesse
instante, calcula-se o retorno, o alpha e um rótulo ``correct``.

Rótulo: para BUY/SELL, se existirem velas do percurso e os níveis de stop/TP da
decisão, o rótulo é o do evento negociado (TP tocado antes do stop = 1, stop
antes = 0); sem toque em nenhum, cai no rótulo direcional: BUY correta se
retorno >= +limiar; SELL correta se <= -limiar; HOLD correta se |retorno| <
limiar de oportunidade perdida. O limiar é metade da
distância do stop em % quando há ATR, com um mínimo de 0,1%, e nunca abaixo
do custo estimado ida+volta em % (``cost_pct``): um movimento que não paga a
comissão é prejuízo, não acerto.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from .config import Settings
from .database import Database
from .indicators import Bar

log = logging.getLogger("trader.settlement")

PriceAt = Callable[[str, datetime], Optional[float]]
BarsBetween = Callable[[str, datetime, datetime], list[Bar]]


def first_touch_label(bars: list[Bar], entry: float, stop_pct: float, tp_pct: float, direction: int) -> Optional[int]:
    """1 se o TP é tocado antes do stop, 0 se o stop é tocado antes, None se nenhum (trajetória)."""
    if not bars or entry <= 0 or stop_pct <= 0 or tp_pct <= 0:
        return None
    stop = entry * (1 - direction * stop_pct / 100.0)
    tp = entry * (1 + direction * tp_pct / 100.0)
    for b in bars:
        hit_stop = b.low <= stop if direction > 0 else b.high >= stop
        hit_tp = b.high >= tp if direction > 0 else b.low <= tp
        if hit_stop and hit_tp:
            return 0  # ambíguo na mesma vela: assume-se o pior caso
        if hit_stop:
            return 0
        if hit_tp:
            return 1
    return None


def label(action: str, ret_pct: float, threshold_pct: float, missed_pct: float) -> Optional[int]:
    if action == "BUY":
        if ret_pct >= threshold_pct:
            return 1
        if ret_pct <= -threshold_pct:
            return 0
        return None  # neutro: não conta para calibração direcional
    if action == "SELL":
        if ret_pct <= -threshold_pct:
            return 1
        if ret_pct >= threshold_pct:
            return 0
        return None
    return 0 if abs(ret_pct) >= missed_pct else 1


class Settler:
    def __init__(self, settings: Settings, db: Database, price_at: PriceAt,
                 bars_between: Optional[BarsBetween] = None) -> None:
        self.s = settings
        self.db = db
        self.price_at = price_at
        self.bars_between = bars_between

    def run(self, now: Optional[datetime] = None) -> int:
        now = now or datetime.now(timezone.utc)
        horizon = timedelta(minutes=self.s.settlement_horizon_minutes)
        pending = self.db.unsettled_decisions(now - horizon)
        settled = 0
        for d in pending:
            # O instante de mercado é o da vela usada na decisão (market_ts), não o fim da inferência.
            ts = datetime.fromisoformat(d["market_ts"] or d["ts"])
            target = ts + horizon
            price = self.price_at(d["symbol"], target)
            if price is None:
                if now - ts > timedelta(hours=36):
                    # Sem dados nunca mais: fecha como neutra para não bloquear a fila.
                    self.db.settle_decision(d["id"], settled_price=float(d["price"]), settled_return=0.0,
                                            bench_return=None, alpha=None, correct=None,
                                            horizon_min=self.s.settlement_horizon_minutes)
                    settled += 1
                continue
            entry = float(d["price"])
            ret = (price - entry) / entry * 100.0
            bench_ret = None
            alpha = None
            if self.s.benchmark_symbol:
                b0 = self.price_at(self.s.benchmark_symbol, ts)
                b1 = self.price_at(self.s.benchmark_symbol, target)
                if b0 and b1:
                    bench_ret = (b1 - b0) / b0 * 100.0
                    direction = 1 if d["action"] == "BUY" else (-1 if d["action"] == "SELL" else 0)
                    alpha = direction * ret - direction * bench_ret if direction else None
            atr = d.get("atr")
            thr = max(0.1, (atr / entry * 100.0) * self.s.atr_stop_multiple / 2) if atr else 0.2
            # Um movimento que não cobre o custo ida+volta não é um acerto.
            cost_pct = float(d.get("cost_pct") or 0.0)
            thr = max(thr, cost_pct)
            missed = max(thr * 2, 0.5)
            correct = None
            # Rótulo do evento realmente negociado: o bracket (primeiro toque em TP ou stop).
            if d["action"] in ("BUY", "SELL") and self.bars_between is not None and d.get("stop_pct") and d.get("tp_pct"):
                path = self.bars_between(d["symbol"], ts, target)
                direction = 1 if d["action"] == "BUY" else -1
                touched = first_touch_label(path, entry, float(d["stop_pct"]), float(d["tp_pct"]), direction)
                if touched is not None:
                    correct = touched
            if correct is None:
                correct = label(d["action"], ret, thr, missed)
            self.db.settle_decision(d["id"], settled_price=price, settled_return=round(ret, 4),
                                    bench_return=round(bench_ret, 4) if bench_ret is not None else None,
                                    alpha=round(alpha, 4) if alpha is not None else None,
                                    correct=correct, horizon_min=self.s.settlement_horizon_minutes)
            settled += 1
        if settled:
            log.info("Settlement: %d decisões avaliadas a %d min.", settled, self.s.settlement_horizon_minutes)
        return settled
