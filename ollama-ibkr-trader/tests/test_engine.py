"""Testa o motor com um cliente IBKR falso (sem rede, sem TWS)."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from trader.config import Settings
from trader.database import Database
from trader.indicators import MarketSnapshot
from trader.ollama_brain import Decision, OllamaBrain
from trader.trading_engine import TradingEngine, in_regular_hours
from trader.ui_bus import UIBus


class FakeIBKR:
    def __init__(self):
        self.connected = True
        self.positions = {}
        self.brackets = []
        self.closes = []
        self._next_id = 100

    def position_qty(self, symbol):
        return self.positions.get(symbol, 0.0)

    def open_trades_for(self, symbol):
        return []

    def portfolio_state(self):
        return {"connected": True, "net_liq": 100_000.0, "cash": 50_000.0, "unrealized": 0.0,
                "realized": 0.0, "positions": [{"symbol": s, "qty": q} for s, q in self.positions.items() if q]}

    async def place_bracket(self, symbol, action, quantity, ref_price, stop_loss_pct, take_profit_pct):
        ids = [self._next_id, self._next_id + 1, self._next_id + 2]
        self._next_id += 3
        sign = 1 if action == "BUY" else -1
        self.brackets.append((symbol, action, quantity, ref_price))
        return {"parent_order_id": ids[0], "tp_order_id": ids[1], "sl_order_id": ids[2],
                "tp_price": round(ref_price * (1 + sign * take_profit_pct), 2),
                "sl_price": round(ref_price * (1 - sign * stop_loss_pct), 2), "trades": []}

    async def close_position(self, symbol):
        qty = self.positions.get(symbol, 0.0)
        if not qty:
            return None
        self.closes.append(symbol)
        oid = self._next_id
        self._next_id += 1
        return {"order_id": oid, "qty": abs(qty), "direction": 1 if qty > 0 else -1, "trade": None}


def make_engine():
    settings = Settings()
    settings.min_confidence = 0.65
    db = Database(":memory:")
    engine = TradingEngine(settings, db, UIBus(), OllamaBrain(settings, db))
    engine.ibkr = FakeIBKR()
    return engine, db, settings


def snap(symbol="AAPL", price=200.0):
    return MarketSnapshot(symbol, price, datetime.now(timezone.utc), 55.0, 199.0, 198.0, 199.5, 0.1, 0.4, 1000, 120)


def decide(engine, db, decision, symbol="AAPL", price=200.0, position=0.0):
    s = snap(symbol, price)
    state = engine.ibkr.portfolio_state()
    did = db.insert_decision(symbol=symbol, model="m", action=decision.acao, confidence=decision.confianca,
                             reason=decision.razao, snapshot=s.to_dict(), position_qty=position, net_liq=100_000,
                             prompt_version=0, parse_ok=decision.parse_ok, raw_response="")
    asyncio.run(engine._execute(decision, s, state, did, position))
    return db._query("SELECT * FROM decisions WHERE id=?", (did,))[0]


def fill(engine, order_id, symbol, side, shares, price, exec_id):
    trade = SimpleNamespace()
    f = SimpleNamespace(
        contract=SimpleNamespace(symbol=symbol),
        execution=SimpleNamespace(execId=exec_id, orderId=order_id, side=side, shares=shares, price=price,
                                  time=datetime.now(timezone.utc)),
    )
    engine._on_fill(trade, f)


def test_buy_places_bracket_with_risk_sizing():
    engine, db, settings = make_engine()
    row = decide(engine, db, Decision("BUY", 0.8, "ok"))
    assert row["executed"] == 1
    symbol, action, qty, price = engine.ibkr.brackets[0]
    assert (symbol, action, price) == ("AAPL", "BUY", 200.0)
    assert qty == int(100_000 * settings.risk_fraction_per_trade / 200.0) == 25
    group = db.group_for_order(100)
    assert group["role"] == "ENTRY" and group["tp_price"] == 210.0 and group["sl_price"] == 196.0
    assert db.open_trades("AAPL")


def test_low_confidence_hold_and_invalid_are_skipped():
    engine, db, _ = make_engine()
    assert decide(engine, db, Decision("BUY", 0.5, "fraco"))["executed"] == 0
    assert decide(engine, db, Decision("HOLD", 0.9, "x"))["skip_reason"] == "HOLD"
    assert decide(engine, db, Decision.hold("lixo", error="unparseable"))["executed"] == 0
    assert engine.ibkr.brackets == []


def test_opposite_signal_closes_position_instead_of_reversing():
    engine, db, _ = make_engine()
    engine.ibkr.positions["AAPL"] = 25
    row = decide(engine, db, Decision("SELL", 0.9, "inversão"), position=25)
    assert row["executed"] == 1
    assert engine.ibkr.closes == ["AAPL"] and engine.ibkr.brackets == []
    # Enquanto o fecho está pendente, novas entradas no ativo são bloqueadas.
    row2 = decide(engine, db, Decision("SELL", 0.9, "outra vez"), position=25)
    assert "pendente" in row2["skip_reason"]


def test_same_direction_and_short_disabled():
    engine, db, settings = make_engine()
    engine.ibkr.positions["AAPL"] = 10
    assert "posicionado" in decide(engine, db, Decision("BUY", 0.9, "x"), position=10)["skip_reason"]
    settings.allow_short = False
    assert "short" in decide(engine, db, Decision("SELL", 0.9, "x"), symbol="TSLA")["skip_reason"]


def test_fills_open_and_close_trade_with_pnl():
    engine, db, _ = make_engine()
    decide(engine, db, Decision("BUY", 0.8, "ok"))
    fill(engine, 100, "AAPL", "BOT", 25, 200.0, "x1")
    fill(engine, 100, "AAPL", "BOT", 25, 200.0, "x1")  # duplicado ignorado
    trade = db.open_trades("AAPL")[0]
    assert trade["filled_qty"] == 25 and trade["entry_price"] == 200.0
    fill(engine, 102, "AAPL", "SLD", 25, 196.0, "x2")  # stop loss
    closed = db.trades_since(datetime(2000, 1, 1, tzinfo=timezone.utc))[0]
    assert closed["status"] == "CLOSED" and closed["exit_reason"] == "SL"
    assert closed["pnl"] == pytest.approx(-100.0)


def test_signal_close_fill_closes_open_trade():
    engine, db, _ = make_engine()
    decide(engine, db, Decision("BUY", 0.8, "ok"))
    fill(engine, 100, "AAPL", "BOT", 25, 200.0, "e1")
    engine.ibkr.positions["AAPL"] = 25
    decide(engine, db, Decision("SELL", 0.9, "sai"), position=25)
    close_group = db._query("SELECT * FROM order_groups WHERE role='CLOSE'")[0]
    fill(engine, close_group["parent_order_id"], "AAPL", "SLD", 25, 204.0, "e2")
    closed = db.trades_since(datetime(2000, 1, 1, tzinfo=timezone.utc))[0]
    assert closed["status"] == "CLOSED" and closed["exit_reason"] == "SIGNAL"
    assert closed["pnl"] == pytest.approx(100.0)
    assert "AAPL" not in engine._pending_close


def test_kill_switch_halts_new_entries():
    engine, db, settings = make_engine()
    settings.daily_loss_limit_pct = 0.03
    engine._update_day_baseline({"net_liq": 100_000.0})
    assert engine._check_kill_switch({"net_liq": 98_000.0}) is False
    assert engine._check_kill_switch({"net_liq": 96_900.0}) is True
    assert engine.halted_day is not None
    assert engine._check_kill_switch({"net_liq": 100_000.0}) is True  # fica travado até ao dia seguinte


def test_regular_hours():
    assert in_regular_hours(datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc))      # 10:00 NY, segunda-feira
    assert not in_regular_hours(datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc))  # 08:00 NY
    assert not in_regular_hours(datetime(2026, 10, 3, 14, 0, tzinfo=timezone.utc))  # sábado
