"""Utilitários partilhados pelos testes (sem rede, sem TWS)."""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from trader.indicators import Bar

NY_OPEN_UTC = datetime(2026, 10, 5, 13, 30, tzinfo=timezone.utc)  # segunda-feira, 09:30 NY (EDT)


def make_bars(n: int = 400, start: Optional[datetime] = None, price: float = 100.0, drift: float = 0.0,
              vol: float = 0.1, seed: int = 1) -> list[Bar]:
    rng = random.Random(seed)
    start = start or NY_OPEN_UTC
    bars = []
    p = price
    for i in range(n):
        o = p
        c = p + drift + rng.gauss(0, vol)
        h = max(o, c) + abs(rng.gauss(0, vol / 2))
        l = min(o, c) - abs(rng.gauss(0, vol / 2))
        bars.append(Bar(start + timedelta(minutes=i), o, h, l, c, 1000 + rng.randint(0, 500)))
        p = c
    return bars


class FakeIBKR:
    def __init__(self, bars: Optional[dict[str, list[Bar]]] = None, equity: float = 100_000.0):
        self.connected = True
        self.positions: dict[str, float] = {}
        self.brackets: list[tuple] = []
        self.closes: list[str] = []
        self.protections: list[str] = []
        self._next_id = 100
        self._bars = bars or {}
        self.equity = equity
        self.data_delayed = False
        self.pending_entry = False

    def position_qty(self, symbol):
        return self.positions.get(symbol, 0.0)

    def open_trades_for(self, symbol):
        return []

    def has_pending_entry(self, symbol):
        return self.pending_entry

    def has_protective_orders(self, symbol):
        return symbol in self.protections

    def bars_as_list(self, symbol):
        return list(self._bars.get(symbol, []))

    def price_at(self, symbol, when):
        for b in self._bars.get(symbol, []):
            if b.time >= when:
                return b.close
        return None

    def portfolio_state(self):
        return {"connected": True, "net_liq": self.equity, "cash": self.equity / 2, "unrealized": 0.0, "realized": 0.0,
                "positions": [{"symbol": s, "qty": q, "avg_cost": 100.0, "market_price": 100.0, "market_value": q * 100,
                               "unrealized_pnl": 0.0, "realized_pnl": 0.0} for s, q in self.positions.items() if q]}

    async def place_bracket(self, symbol, action, quantity, ref_price, *, stop_price, tp_price, trailing=False):
        ids = [self._next_id, self._next_id + 1, self._next_id + 2]
        self._next_id += 3
        self.brackets.append((symbol, action, quantity, ref_price, stop_price, tp_price))
        return {"parent_order_id": ids[0], "tp_order_id": ids[1], "sl_order_id": ids[2],
                "tp_price": tp_price, "sl_price": stop_price, "trades": []}

    async def close_position(self, symbol):
        qty = self.positions.get(symbol, 0.0)
        if not qty:
            return None
        self.closes.append(symbol)
        oid = self._next_id
        self._next_id += 1
        return {"order_id": oid, "qty": abs(qty), "direction": 1 if qty > 0 else -1, "trade": None}

    async def ensure_protection(self, symbol, *, stop_price, tp_price):
        self.protections.append(symbol)
        return {"tp_order_id": 1, "sl_order_id": 2}

    async def subscribe_bars(self, symbol):
        return True
