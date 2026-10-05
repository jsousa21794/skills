from datetime import datetime, timedelta, timezone

import pytest

from trader.config import Settings
from trader.database import Database
from trader.indicators import aggregate_bars, atr
from trader.risk import PositionSizer, RiskGate, break_even_probability, commission, round_trip_cost
from tests.helpers import make_bars, NY_OPEN_UTC


def test_atr_sizing_risks_fixed_fraction():
    s = Settings()
    s.risk_per_trade_pct = 0.005
    bars = aggregate_bars(make_bars(600, vol=0.2), 5)
    sizer = PositionSizer(s)
    res = sizer.size(action="BUY", price=100.0, equity=100_000.0, bars=bars)
    assert res is not None and res.qty >= 1
    a = atr(bars, 14)
    assert res.atr_used >= a
    assert res.stop_distance == pytest.approx(s.atr_stop_multiple * res.atr_used, rel=1e-6)
    assert res.risk_amount <= 100_000 * 0.005 + res.stop_distance
    assert res.stop_price < 100.0 < res.tp_price
    assert (res.tp_price - 100.0) == pytest.approx(s.reward_risk_ratio * (100.0 - res.stop_price), abs=0.02)
    short = sizer.size(action="SELL", price=100.0, equity=100_000.0, bars=bars)
    assert short.stop_price > 100.0 > short.tp_price


def test_sizing_caps_notional_and_fixed_mode():
    s = Settings()
    s.risk_per_trade_pct = 0.05  # risco enorme para forçar o teto de notional
    s.max_position_notional_pct = 0.10
    bars = aggregate_bars(make_bars(600, vol=0.05), 5)
    res = PositionSizer(s).size(action="BUY", price=10.0, equity=100_000.0, bars=bars)
    assert res.qty * 10.0 <= 10_000 + 10
    assert any("limitado" in n for n in res.notes)
    s.stop_mode = "fixed"
    fixed = PositionSizer(s).size(action="BUY", price=100.0, equity=100_000.0, bars=bars)
    assert fixed.stop_price == pytest.approx(98.0) and fixed.tp_price == pytest.approx(105.0)


def test_costs_and_break_even():
    s = Settings()
    assert commission(10, 50.0, s) == 1.0  # mínimo 1 USD
    assert commission(1000, 50.0, s) == 5.0
    assert round_trip_cost(100, 50.0, s) == pytest.approx(2 * 1.0 + 2 * 0.01 * 100)
    assert break_even_probability(2.5) == pytest.approx(1 / 3.5)
    assert break_even_probability(2.0, cost=10.0, risk_amount=100.0) == pytest.approx(1.1 / 3.0)
    gate = RiskGate(s, Database(":memory:"))
    assert not gate.check_costs(qty=2, price=50.0, tp_distance=1.0).allowed  # notional < 200
    assert gate.check_costs(qty=100, price=50.0, tp_distance=1.0).allowed


def test_trading_window_and_cooldown():
    s = Settings()
    db = Database(":memory:")
    gate = RiskGate(s, db)
    assert not gate.in_trading_window(NY_OPEN_UTC + timedelta(minutes=5))  # primeiros 15 min
    assert gate.in_trading_window(NY_OPEN_UTC + timedelta(minutes=30))
    assert not gate.in_trading_window(NY_OPEN_UTC + timedelta(hours=6, minutes=25))  # últimos 10 min
    now = NY_OPEN_UTC + timedelta(hours=1)
    gid = db.insert_order_group(symbol="AAPL", decision_id=None, role="ENTRY", direction=1, qty=1, parent_order_id=1,
                                tp_order_id=2, sl_order_id=3, ref_price=100, tp_price=102, sl_price=99)
    tid = db.open_trade(symbol="AAPL", decision_id=None, group_id=gid, direction=1, qty=1)
    db.record_entry_fill(tid, 1, 100.0, now - timedelta(minutes=40))
    db.record_exit_fill(tid, 1, 99.0, now - timedelta(minutes=10), "SL")
    assert "cooldown" in gate.check_symbol(symbol="AAPL", now=now).reason
    assert gate.check_symbol(symbol="AAPL", now=now + timedelta(minutes=30)).allowed


def _closed_trade(db, pnl, exit_ts, reason):
    gid = db.insert_order_group(symbol="TSLA", decision_id=None, role="ENTRY", direction=1, qty=1, parent_order_id=1,
                                tp_order_id=2, sl_order_id=3, ref_price=100, tp_price=102, sl_price=99)
    tid = db.open_trade(symbol="TSLA", decision_id=None, group_id=gid, direction=1, qty=1)
    db.record_entry_fill(tid, 1, 100.0, exit_ts - timedelta(minutes=5))
    db.record_exit_fill(tid, 1, 100.0 + pnl, exit_ts, reason)


def test_stoploss_guard_and_consecutive_losses():
    s = Settings()
    db = Database(":memory:")
    gate = RiskGate(s, db)
    now = NY_OPEN_UTC + timedelta(hours=2)
    for i in range(3):
        _closed_trade(db, -1.0, now - timedelta(minutes=10 * (i + 1)), "SL")
    res = gate.check_global(equity=100_000.0, now=now)
    assert not res.allowed and res.reason == "StoplossGuard"
    assert gate.active_pauses(now)
    # após a pausa expirar, as perdas consecutivas (>= 5) travam até ao dia seguinte
    for i in range(2):
        _closed_trade(db, -1.0, now + timedelta(minutes=5 * (i + 1)), "SIGNAL")
    later = now + timedelta(hours=3)
    res2 = gate.check_global(equity=100_000.0, now=later)
    assert not res2.allowed


def test_kill_switch_and_drawdown_zones():
    s = Settings()
    db = Database(":memory:")
    gate = RiskGate(s, db)
    now = datetime.now(timezone.utc)
    gate.update_day_baseline(100_000.0, now)
    assert gate.check_global(equity=98_000.0, now=now).allowed
    assert not gate.check_global(equity=96_900.0, now=now).allowed
    assert gate.halted
    # zona amarela: drawdown de 3-6% nos últimos dias -> tamanho a metade
    db2 = Database(":memory:")
    gate2 = RiskGate(s, db2)
    for i, eq in enumerate([100_000, 101_000, 97_500]):
        db2._execute("INSERT INTO pnl_snapshots (ts, net_liq, cash, unrealized, realized) VALUES (?,?,?,?,?)",
                     ((now - timedelta(days=2 - i)).isoformat(), eq, 0, 0, 0))
    gate2.update_day_baseline(97_500.0, now)
    res = gate2.check_global(equity=97_500.0, now=now)
    assert res.allowed and res.size_multiplier == 0.5


class _Events:
    def __init__(self, vix=None, blackout=None):
        self._vix, self._blackout = vix, blackout

    def vix(self):
        return self._vix

    def in_earnings_blackout(self, symbol, before, after, today=None):
        return self._blackout


def test_vix_and_earnings_gates():
    s = Settings()
    db = Database(":memory:")
    now = NY_OPEN_UTC + timedelta(hours=1)
    gate = RiskGate(s, db, _Events(vix=28.0, blackout=True))
    gate.update_day_baseline(100_000.0, now)
    res = gate.check_global(equity=100_000.0, now=now)
    assert res.allowed and res.size_multiplier == 0.5
    assert "blackout" in gate.check_symbol(symbol="AAPL", now=now).reason
    gate_block = RiskGate(s, db, _Events(vix=40.0))
    gate_block.update_day_baseline(100_000.0, now)
    assert not gate_block.check_global(equity=100_000.0, now=now).allowed
    s.event_data_fail_closed = True
    gate_none = RiskGate(s, db, _Events(blackout=None))
    assert "fail-closed" in gate_none.check_symbol(symbol="AAPL", now=now).reason


def test_cost_gate_rejects_commission_eating_profit():
    """1 USD de comissão por lado contra 0,50 USD de lucro esperado é prejuízo, não trade."""
    s = Settings()
    s.min_position_notional = 0
    gate = RiskGate(s, Database(":memory:"))
    # 10 ações a 50 USD, TP a 0,05 USD de distância -> ganho bruto 0,50; custo ida+volta 2,20
    res = gate.check_costs(qty=10, price=50.0, tp_distance=0.05)
    assert not res.allowed and "custo" in res.reason
    # ganho bruto 4 USD, custo 2,20 -> recusado (custo > 20% do bruto)
    res2 = gate.check_costs(qty=10, price=50.0, tp_distance=0.40)
    assert not res2.allowed and "custo" in res2.reason
    # custo 2,20 = 18% do bruto 12 USD, mas líquido 9,8 > 3x custo -> passa; com múltiplo 5x recusa pelo líquido
    assert gate.check_costs(qty=10, price=50.0, tp_distance=1.2).allowed
    s.min_net_gain_multiple = 5.0
    res3 = gate.check_costs(qty=10, price=50.0, tp_distance=1.2)
    assert not res3.allowed and "ganho líquido" in res3.reason
    s.min_net_gain_multiple = 3.0
    # 200 ações, TP a 1 USD: bruto 200, custo 2+4=6 -> ok; com p=0,30 e stop 0,5 o EV líquido é negativo
    assert gate.check_costs(qty=200, price=50.0, tp_distance=1.0).allowed
    bad_ev = gate.check_costs(qty=200, price=50.0, tp_distance=1.0, stop_distance=0.5, probability=0.30)
    assert not bad_ev.allowed and "valor esperado" in bad_ev.reason
    good_ev = gate.check_costs(qty=200, price=50.0, tp_distance=1.0, stop_distance=0.5, probability=0.45)
    assert good_ev.allowed and any("EV líquido" in n for n in good_ev.notes)
