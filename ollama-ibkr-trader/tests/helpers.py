"""Utilitários partilhados pelos testes (sem rede, sem TWS)."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

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


class FakeOrder:
    def __init__(self, order_id: int, action: str, qty: float, order_type: str, status: str = "Submitted",
                 remaining: Optional[float] = None, order_ref: str = "OllamaIBKRTrader", parent_id: int = 0):
        self.orderId = order_id
        self.action = action
        self.totalQuantity = qty
        self.orderType = order_type
        self.status = status
        self.remaining = qty if remaining is None else remaining
        self.orderRef = order_ref
        self.parentId = parent_id


class FakeIBKR:
    """Cliente IBKR falso com o mesmo contrato que o motor usa."""

    def __init__(self, bars: Optional[dict[str, list[Bar]]] = None, equity: float = 100_000.0,
                 available_funds: Optional[float] = None, currency: str = "USD"):
        self.connected = True
        self.account = "DU000001"
        self.accounts = [self.account]
        self.base_currency = currency
        self.positions: dict[str, float] = {}
        self.brackets: list[tuple] = []
        self.closes: list[str] = []
        self.protections: list[str] = []
        self.orders: dict[str, list[FakeOrder]] = {}  # symbol -> ordens abertas
        self.cancelled: list[int] = []
        self._next_id = 100
        self._bars = bars or {}
        self.equity = equity
        self.available_funds = equity if available_funds is None else available_funds
        self.data_delayed = False
        self.pending_entry = False
        self.fills: list[Any] = []
        self.close_behaviour = "normal"  # normal | closed_by_children | abort

    # --- posições / ordens ---
    def position_qty(self, symbol):
        return self.positions.get(symbol, 0.0)

    def con_id(self, symbol):
        return abs(hash(symbol)) % 100000

    def open_trades_for(self, symbol, ours_only=True):
        return []

    def has_pending_entry(self, symbol):
        return self.pending_entry

    def our_open_orders(self):
        return list(self.open_orders_all) if hasattr(self, "open_orders_all") else []

    def last_price(self, symbol):
        if getattr(self, "quote", None) is not None:
            return self.quote
        bars = self._bars.get(symbol)
        return (bars[-1].close, bars[-1].time) if bars else None

    def is_intentional_cancel(self, order_id):
        return order_id in getattr(self, "intentional", set())

    def forget_cancel(self, order_id):
        getattr(self, "intentional", set()).discard(order_id)

    def protective_coverage(self, symbol, ours_only=False):
        qty = self.positions.get(symbol, 0.0)
        if not qty:
            return 0.0
        need = "SELL" if qty > 0 else "BUY"
        return sum(o.remaining for o in self.orders.get(symbol, [])
                   if o.orderType in ("STP", "TRAIL", "STP LMT") and o.action == need
                   and o.status in ("PreSubmitted", "Submitted", "PendingSubmit", "ApiPending")
                   and (not ours_only or o.orderRef == "OllamaIBKRTrader"))

    def has_protective_orders(self, symbol, needed_qty=None, ours_only=False):
        qty = abs(self.positions.get(symbol, 0.0))
        if needed_qty is not None:
            qty = min(qty, abs(needed_qty))
            ours_only = True
        return qty > 0 and self.protective_coverage(symbol, ours_only=ours_only) + 1e-9 >= qty

    def has_orphan_children(self, symbol):
        return False

    def external_exit_orders(self, symbol):
        return []

    def transitional_children(self, symbol):
        return []

    def _own_live_orders(self, symbol):
        return [o for o in self.orders.get(symbol, []) if o.orderRef == "OllamaIBKRTrader"
                and o.status not in ("Filled", "Cancelled", "ApiCancelled", "Inactive")]

    def own_exit_quantity(self, symbol):
        qty = self.positions.get(symbol, 0.0)
        if not qty:
            return 0.0
        need = "SELL" if qty > 0 else "BUY"
        stops = sum(o.remaining for o in self._own_live_orders(symbol) if o.orderType in ("STP", "TRAIL", "STP LMT") and o.action == need)
        tps = sum(o.remaining for o in self._own_live_orders(symbol) if o.orderType == "LMT" and o.action == need)
        return max(stops, tps)

    def has_excess_exits(self, symbol, allowed_qty=None):
        qty = abs(self.positions.get(symbol, 0.0))
        allowed = qty if allowed_qty is None else min(qty, abs(allowed_qty))
        return self.own_exit_quantity(symbol) > allowed + 1e-9

    async def resize_exits(self, symbol, *, stop_price, tp_price, max_qty=None):
        replaced = [o.orderId for o in self._own_live_orders(symbol)]
        for o in self._own_live_orders(symbol):
            o.status = "Cancelled"
            self.cancelled.append(o.orderId)
        self.resized = getattr(self, "resized", []) + [(symbol, max_qty)]
        if not self.positions.get(symbol, 0.0):
            return {"tp_order_id": None, "sl_order_id": None, "qty": 0, "direction": 0, "trades": [], "tp_price": tp_price,
                    "sl_price": stop_price, "replaced_order_ids": replaced}
        result = await self.ensure_protection(symbol, stop_price=stop_price, tp_price=tp_price, max_qty=max_qty)
        result["replaced_order_ids"] = replaced
        return result

    async def cancel_own_exits(self, symbol):
        for o in self._own_live_orders(symbol):
            o.status = "Cancelled"
            self.cancelled.append(o.orderId)
        return True

    def order_is_ours(self, order, contract=None):
        acct = getattr(order, "account", "") or ""
        if acct and acct != self.account:
            return False
        cid = getattr(order, "clientId", None)
        if cid is not None and str(cid) != "" and int(cid) != 17:
            return False
        ref = getattr(order, "orderRef", None)
        return ref is None or ref == "OllamaIBKRTrader"

    def bars_as_list(self, symbol):
        return list(self._bars.get(symbol, []))

    def price_at(self, symbol, when, max_gap_minutes=10):
        for b in self._bars.get(symbol, []):
            if b.time >= when:
                return b.close if (b.time - when).total_seconds() <= max_gap_minutes * 60 else None
        return None

    def bars_between(self, symbol, start, end):
        return [b for b in self._bars.get(symbol, []) if start <= b.time <= end]

    def recent_fills(self):
        return list(self.fills)

    def cancel_order_id(self, order_id):
        self.cancelled.append(order_id)
        return True

    def display_rate(self, currency):
        return self.base_currency, 1.0, True

    def portfolio_state(self):
        return {"connected": True, "account": self.account, "currency": self.base_currency,
                "net_liq": self.equity, "net_liq_usd": self.equity, "cash": self.equity / 2,
                "available_funds": self.available_funds, "available_funds_usd": self.available_funds,
                "buying_power": self.available_funds * 2 if self.available_funds else None,
                "unrealized": 0.0, "realized": 0.0,
                "positions": [{"symbol": s, "qty": q, "avg_cost": 100.0, "market_price": 100.0, "market_value": q * 100,
                               "unrealized_pnl": 0.0, "realized_pnl": 0.0, "sec_type": "STK", "con_id": self.con_id(s)}
                              for s, q in self.positions.items() if q]}

    async def place_bracket(self, symbol, action, quantity, ref_price, *, stop_price, tp_price, trailing=False,
                            limit_price=None, authorize=None):
        if getattr(self, "slow_qualify", None):
            await self.slow_qualify()  # simula qualificação assíncrona (R01)
        if authorize is not None and not authorize():
            self.refused = getattr(self, "refused", 0) + 1
            return None
        ids = [self._next_id, self._next_id + 1, self._next_id + 2]
        self._next_id += 3
        self.brackets.append((symbol, action, quantity, ref_price, stop_price, tp_price, limit_price))
        return {"parent_order_id": ids[0], "tp_order_id": ids[1], "sl_order_id": ids[2],
                "tp_price": tp_price, "sl_price": stop_price, "trades": [], "con_id": self.con_id(symbol),
                "account": self.account}

    async def close_position(self, symbol, authorize=None, max_qty=None):
        qty = self.positions.get(symbol, 0.0)
        if max_qty is not None:
            qty = (1 if qty > 0 else -1) * min(abs(qty), max_qty)
        if not qty:
            return None
        if getattr(self, "slow_cancel", None):
            await self.slow_cancel()  # simula a espera pelos cancelamentos (R21)
        if authorize is not None and not authorize():
            self.refused = getattr(self, "refused", 0) + 1
            return {"order_id": None, "qty": 0, "direction": 1 if qty > 0 else -1, "trade": None,
                    "aborted": True, "needs_protection": True}
        if self.close_behaviour == "closed_by_children":
            self.positions[symbol] = 0.0
            return {"order_id": None, "qty": 0, "direction": 1 if qty > 0 else -1, "trade": None,
                    "closed_by_children": True, "needs_protection": False}
        if self.close_behaviour == "abort":
            return {"order_id": None, "qty": 0, "direction": 1 if qty > 0 else -1, "trade": None,
                    "aborted": True, "needs_protection": True}
        self.closes.append(symbol)
        oid = self._next_id
        self._next_id += 1
        return {"order_id": oid, "qty": abs(qty), "direction": 1 if qty > 0 else -1, "trade": None,
                "needs_protection": False}

    async def ensure_protection(self, symbol, *, stop_price, tp_price, max_qty=None):
        self.protections.append(symbol)
        tp_id, sl_id = self._next_id, self._next_id + 1
        self._next_id += 2
        qty = self.positions.get(symbol, 0.0)
        if max_qty is not None:
            qty = (1 if qty > 0 else -1) * min(abs(qty), max_qty)
        self.orders.setdefault(symbol, []).append(FakeOrder(sl_id, "SELL" if qty > 0 else "BUY", abs(qty), "STP"))
        return {"tp_order_id": tp_id, "sl_order_id": sl_id, "qty": abs(qty), "direction": 1 if qty > 0 else -1,
                "trades": [], "tp_price": round(tp_price, 2), "sl_price": round(stop_price, 2)}

    async def subscribe_bars(self, symbol):
        return True

    async def disconnect(self):
        self.connected = False

    async def connect(self):
        self.connected = True
        return True
