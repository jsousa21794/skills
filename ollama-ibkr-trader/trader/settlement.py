"""Settlement: cada decisão só é avaliada quando o horizonte expira.

Adaptado do ``memory/settlement.py`` do TradingAgents (Apache-2.0) a um
horizonte de minutos: para cada decisão sem ``settled_ts`` cujo ``ts`` já
tenha ``horizon`` minutos, procura-se o preço do ativo e do benchmark nesse
instante, calcula-se o retorno, o alpha e um rótulo ``correct``.

Rótulo: BUY correta se retorno >= +limiar; SELL correta se <= -limiar; HOLD
correta se |retorno| < limiar de oportunidade perdida. O limiar é metade da
distância do stop em % quando há ATR, com um mínimo de 0,1%.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from .config import Settings
from .database import Database

log = logging.getLogger("trader.settlement")

PriceAt = Callable[[str, datetime], Optional[float]]


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
    def __init__(self, settings: Settings, db: Database, price_at: PriceAt) -> None:
        self.s = settings
        self.db = db
        self.price_at = price_at

    def run(self, now: Optional[datetime] = None) -> int:
        now = now or datetime.now(timezone.utc)
        horizon = timedelta(minutes=self.s.settlement_horizon_minutes)
        pending = self.db.unsettled_decisions(now - horizon)
        settled = 0
        for d in pending:
            ts = datetime.fromisoformat(d["ts"])
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
            missed = max(thr * 2, 0.5)
            self.db.settle_decision(d["id"], settled_price=price, settled_return=round(ret, 4),
                                    bench_return=round(bench_ret, 4) if bench_ret is not None else None,
                                    alpha=round(alpha, 4) if alpha is not None else None,
                                    correct=label(d["action"], ret, thr, missed),
                                    horizon_min=self.s.settlement_horizon_minutes)
            settled += 1
        if settled:
            log.info("Settlement: %d decisões avaliadas a %d min.", settled, self.s.settlement_horizon_minutes)
        return settled
