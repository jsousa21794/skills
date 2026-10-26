"""Conselho de verificação da 1.0.17 (três revisores internos): regressões AI01–AI08 com as asserções invertidas
em relação aos diagnósticos que confirmaram os defeitos."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers import FakeOrder, make_bars
from tests.test_engine import NOW, _FixedDatetime, outcome, own_position, run_exec
from tests.test_review_103 import ib_order
from tests.test_review_104 import make_engine
from tests.test_review_107 import own_fill
from tests.test_review_111 import row
from tests.test_review_113 import pending_entry, status_for
from trader.config import Settings
from trader.database import Database
from trader.indicators import Bar
from trader.risk import RiskGate
from trader.settlement import Settler


# ---------------------------------------------------------------- AI01 (P0): entrada cancelada nunca bloqueia o settlement
def cancelled_entry(db, ts):
    did = db.insert_decision(symbol="AAPL", model="m", action="BUY", confidence=.8, reason="r", snapshot={"price": 100},
                             position_qty=0, net_liq=10000, prompt_version=0, parse_ok=True, raw_response="", ts=ts,
                             extra={"market_ts": ts.isoformat(), "stop_pct": 2, "tp_pct": 4})
    db.mark_decision(did, executed=True)
    gid = db.insert_order_group(symbol="AAPL", decision_id=did, role="ENTRY", direction=1, qty=100, parent_order_id=1,
                                tp_order_id=2, sl_order_id=3, ref_price=100, tp_price=104, sl_price=98, ts=ts)
    tid = db.open_trade(symbol="AAPL", decision_id=did, group_id=gid, direction=1, qty=100, stop_price=98, tp_price=104)
    db._execute("UPDATE trades SET status='CANCELLED', exit_reason='CANCELLED' WHERE id=? AND filled_qty=0", (tid,))
    return did, tid


def test_ai01_settler_evaluates_cancelled_entry_and_the_queue_behind_it():
    db = Database(":memory:")
    did, tid = cancelled_entry(db, NOW)
    did2 = db.insert_decision(symbol="AAPL", model="m", action="HOLD", confidence=.5, reason="r", snapshot={"price": 100},
                              position_qty=0, net_liq=10000, prompt_version=0, parse_ok=True, raw_response="",
                              ts=NOW + timedelta(minutes=1), extra={"market_ts": (NOW + timedelta(minutes=1)).isoformat()})
    bars = [Bar(NOW + timedelta(minutes=i), 100, 100.5, 99.5, 100, 1) for i in range(1, 40)]
    settler = Settler(Settings(), db, lambda *_: 100.0, bars_between=lambda *_: bars)
    settler.run(NOW + timedelta(hours=2))  # não rebenta
    rows = {r["id"]: r for r in db._query("SELECT id, settled_ts, label_source FROM decisions ORDER BY id")}
    assert rows[did]["settled_ts"] is not None and rows[did2]["settled_ts"] is not None
    assert not (rows[did]["label_source"] or "").startswith("ledger")  # avaliada como decisão NÃO negociada


def test_ai01_engine_path_cancelled_entry_is_settled_and_cycle_survives(monkeypatch):
    engine, db, s = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    oid, pending, gid, live = pending_entry(engine, db, s)
    tid = pending["trade_id"]
    engine._on_order_status(status_for(engine, s, oid, 999, "Cancelled"))
    t = row(db, tid)
    assert t["status"] == "CANCELLED" and t["entry_ts"] is None
    engine.ibkr._bars["AAPL"] = make_bars(700, start=NOW - timedelta(minutes=599), vol=.2)
    real = datetime.now(timezone.utc)
    engine.settler.run(real + timedelta(hours=1))  # não levanta
    assert db._query("SELECT settled_ts FROM decisions WHERE id=?", (t["decision_id"],))[0]["settled_ts"] is not None


def test_ai01_one_broken_decision_does_not_block_the_others(monkeypatch):
    db = Database(":memory:")
    did, tid = cancelled_entry(db, NOW)
    good = db.insert_decision(symbol="AAPL", model="m", action="HOLD", confidence=.5, reason="r", snapshot={"price": 100},
                              position_qty=0, net_liq=10000, prompt_version=0, parse_ok=True, raw_response="",
                              ts=NOW + timedelta(minutes=1), extra={"market_ts": (NOW + timedelta(minutes=1)).isoformat()})
    settler = Settler(Settings(), db, lambda *_: 100.0, bars_between=lambda *_: [])
    original = settler._settle_one

    def boom(d, now, horizon):
        if d["id"] == did:
            raise RuntimeError("dados inconsistentes")
        return original(d, now, horizon)

    monkeypatch.setattr(settler, "_settle_one", boom)
    assert settler.run(NOW + timedelta(hours=2)) == 1
    assert db._query("SELECT settled_ts FROM decisions WHERE id=?", (good,))[0]["settled_ts"] is not None


# ---------------------------------------------------------------- AI03 (P1): gate PDT compara equity em USD
def three_day_trades(db, symbol="MSFT"):
    for i in range(3):
        t0 = NOW - timedelta(days=1, hours=2) + timedelta(minutes=10 * i)
        gid = db.insert_order_group(symbol=symbol, decision_id=None, role="ENTRY", direction=1, qty=10, parent_order_id=900 + i,
                                    tp_order_id=None, sl_order_id=None, ref_price=100, tp_price=104, sl_price=98, ts=t0)
        tid = db.open_trade(symbol=symbol, decision_id=None, group_id=gid, direction=1, qty=10, stop_price=98, tp_price=104)
        db.record_entry_fill(tid, 10, 100, t0)
        db.record_exit_fill(tid, 10, 104, t0 + timedelta(minutes=5), "TP")


def test_ai03_pdt_rule_applies_to_the_usd_equity_of_a_non_usd_account(monkeypatch):
    engine, db, s = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    three_day_trades(db)
    base = engine.ibkr.portfolio_state

    def jpy_state():
        st = base()
        st.update({"currency": "JPY", "net_liq": 3_000_000.0, "net_liq_usd": 20_000.0,
                   "available_funds": 3_000_000.0, "available_funds_usd": 20_000.0})
        return st

    engine.ibkr.portfolio_state = jpy_state
    decision = run_exec(engine, db, outcome("BUY"))
    assert decision["executed"] == 0 and "PDT" in decision["skip_reason"], decision["skip_reason"]
    assert engine.ibkr.brackets == []


def test_ai03_pdt_still_blocks_a_small_usd_account(monkeypatch):
    engine, db, s = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    engine.ibkr.equity = engine.ibkr.available_funds = 20_000.0
    three_day_trades(db)
    decision = run_exec(engine, db, outcome("BUY"))
    assert decision["executed"] == 0 and "PDT" in decision["skip_reason"]


# ---------------------------------------------------------------- AI04 (P2): validação da configuração
def test_ai04_config_rejects_inverted_sentiment_threshold_and_out_of_promise_risk():
    s = Settings()
    warnings = s.apply({"sentiment_veto_threshold": 0.4, "risk_per_trade_pct": 0.9, "risk_per_trade_pct_validated": 1.0,
                        "daily_loss_limit_pct": 0.0, "mcp_port": 70000, "ib_port_live": 99999})
    assert len(warnings) == 6
    assert s.sentiment_veto_threshold == -0.4 and s.risk_per_trade_pct == 0.10 and s.risk_per_trade_pct_validated == 0.10
    assert s.daily_loss_limit_pct == 0.20 and s.mcp_port == 8765 and s.ib_port_live == 7496
    assert s.apply({"risk_per_trade_pct": 0.05, "daily_loss_limit_pct": 0.2, "sentiment_veto_threshold": -0.5, "mcp_port": 9000}) == []
    gate = RiskGate(s, Database(":memory:"))
    assert not gate.check_sentiment_veto("BUY", -0.6, 5).allowed and gate.check_sentiment_veto("BUY", +0.3, 5).allowed


# ---------------------------------------------------------------- AI05 (P1): ajustes provisórios só sobre quantidade executada
def stale_unfilled_then_filled():
    engine, db, s = make_engine()
    gb, tb = own_position(engine, db, "AAPL", 50)
    db._execute("UPDATE trades SET filled_qty=0, entry_price=NULL, entry_ts=NULL WHERE id=?", (tb,))
    ga, ta = own_position(engine, db, "AAPL", 100)
    return engine, db, s, gb, tb, ga, ta


def adj(db, tid):
    return db._query("SELECT COALESCE(SUM(qty-consumed_qty),0) q, COUNT(*) n FROM ledger_adjustments WHERE trade_id=?", (tid,))[0]


def test_ai05_reduced_position_adjusts_only_the_filled_trade_and_the_fill_releases_the_block():
    engine, db, s, gb, tb, ga, ta = stale_unfilled_then_filled()
    engine.ibkr.positions["AAPL"] = 80.0
    asyncio.run(engine._supervise())
    assert adj(db, tb)["q"] == 0 and adj(db, ta)["q"] == 20
    n_resized = len(getattr(engine.ibkr, "resized", []))
    asyncio.run(engine._supervise())
    assert adj(db, ta)["q"] == 20 and len(getattr(engine.ibkr, "resized", [])) == n_resized  # sem churn
    sl = db._query("SELECT sl_order_id FROM order_groups WHERE id=?", (ga,))[0]["sl_order_id"]
    engine._on_fill(None, own_fill(engine, sl, 20, 98, "x-real"))
    assert adj(db, ta)["q"] == 0 and db.open_adjustments_for_symbol("AAPL") == 0
    asyncio.run(engine._supervise())
    assert not engine._conflict_pending("AAPL")


def test_ai05_reversed_position_never_adjusts_an_unfilled_trade():
    engine, db, s, gb, tb, ga, ta = stale_unfilled_then_filled()
    engine.ibkr.positions["AAPL"] = -20.0
    asyncio.run(engine._supervise())
    assert adj(db, tb)["q"] == 0 and adj(db, ta)["q"] == 100
    sl = db._query("SELECT sl_order_id FROM order_groups WHERE id=?", (ga,))[0]["sl_order_id"]
    engine._on_fill(None, own_fill(engine, sl, 100, 98, "x1"))
    assert db.open_adjustments_for_symbol("AAPL") == 0


# ---------------------------------------------------------------- AI06 (P2): entrada morta com o bot desligado
def test_ai06_dead_entry_without_live_order_is_cancelled_at_reconciliation():
    engine, db, s = make_engine()
    gb, tb = own_position(engine, db, "AAPL", 50)
    db._execute("UPDATE trades SET filled_qty=0, entry_price=NULL, entry_ts=NULL WHERE id=?", (tb,))
    engine.ibkr.positions["AAPL"] = 0.0
    engine.ibkr.open_orders_all = []
    asyncio.run(engine._reconcile())
    assert row(db, tb)["status"] == "CANCELLED" and "AAPL" not in engine._managed_symbols()


def test_ai06_pending_entry_with_live_order_is_kept(monkeypatch):
    engine, db, s = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    oid, pending, gid, live = pending_entry(engine, db, s)
    engine.ibkr.positions.pop("AAPL", None)
    asyncio.run(engine._reconcile())
    assert row(db, pending["trade_id"])["status"] == "OPEN" and oid in engine._pending_entries


def test_ai06_expired_entry_unknown_to_the_broker_cancels_the_trade(monkeypatch):
    engine, db, s = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    oid, pending, gid, live = pending_entry(engine, db, s)
    engine.ibkr.open_orders_all = []  # a ordem já não existe na corretora
    engine.ibkr.cancel_order_id = lambda _oid: False  # a TWS não conhece a ordem: nada a cancelar
    pending["deadline"] = -1.0  # expirada
    asyncio.run(engine._supervise())
    assert oid not in engine._pending_entries and row(db, pending["trade_id"])["status"] == "CANCELLED"


# ---------------------------------------------------------------- AI07 (P2): execução depois do Cancelled reabre o trade
def test_ai07_fill_after_cancelled_status_reopens_the_trade_as_own_position(monkeypatch):
    engine, db, s = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    oid, pending, gid, live = pending_entry(engine, db, s)
    tid = pending["trade_id"]
    engine._on_order_status(status_for(engine, s, oid, 999, "Cancelled", qty=pending["qty"]))
    assert row(db, tid)["status"] == "CANCELLED"
    f = own_fill(engine, oid, 30, 100.0, "late-entry", perm_id=999)
    f.execution.side = "BOT"
    engine._on_fill(None, f)
    t = row(db, tid)
    assert t["status"] == "OPEN" and t["filled_qty"] == 30 and t["exit_reason"] is None
    engine.ibkr.positions["AAPL"] = 30.0
    assert engine._own_qty("AAPL") == 30 and not engine._is_external("AAPL", 30.0)
    assert "AAPL" in engine._discrepancies  # reconciliar com a corretora antes de novas decisões


# ---------------------------------------------------------------- AI08 (P2): ordem própria viva sem trade nunca adota a posição
def test_ai08_live_own_exit_without_trade_does_not_adopt_the_manual_position():
    engine, db, s = make_engine()
    assert not s.manage_external_positions
    live = ib_order(500, "SELL", 100, "STP", account=engine.ibkr.account)
    live.contract.conId = engine.ibkr.con_id("AAPL")
    engine.ibkr.open_orders_all = [live]
    engine.ibkr.orders["AAPL"] = [FakeOrder(500, "SELL", 100, "STP")]
    engine.ibkr.positions["AAPL"] = 300.0
    asyncio.run(engine._supervise())
    assert db.open_trades("AAPL") == [] and engine.ibkr.protections == []
    assert engine._own_qty("AAPL") == 0 and "AAPL" in engine._external_positions
    assert 500 in engine.ibkr.cancelled  # a saída órfã do bot não atua sobre a posição alheia
