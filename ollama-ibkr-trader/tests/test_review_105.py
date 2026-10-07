"""Regressões da revisão da 1.0.5 (W01-W07 / B01-B11): os ensaios do auditor com as asserções invertidas
para o comportamento corrigido, mais casos adjacentes."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from trader.analytics import Analytics
from trader.app import _migrate_legacy_db
from trader.config import Settings
from trader.database import Database
from trader.indicators import Bar
from trader.risk import RiskGate
from trader.settlement import Settler, first_touch_absolute
from tests.test_engine import NOW, own_position
from tests.test_review_103 import ib_order, position, real_client
from tests.test_review_104 import make_engine


# ---------------------------------------------------------------- W01 / B01 / B02
def test_b01_pending_cancel_manual_stop_blocks_close():
    sl = ib_order(81, "SELL", 100, "STP", ref="manual", status="PendingCancel")
    client = real_client([sl], [position(100)])
    assert not sl.isDone()
    result = asyncio.run(client.close_position("AAPL", authorize=lambda: True))
    assert result["aborted"] and result["conflict"] == [81] and client.ib.placed == []


def test_b02_manual_stop_arriving_during_wait_is_reconciled_before_send():
    sl = ib_order(1, "SELL", 100, "STP", oca="own")
    tp = ib_order(2, "SELL", 100, "LMT", oca="own")
    trades = [sl, tp]
    client = real_client(trades, [position(100)])
    manual = ib_order(81, "SELL", 100, "STP", ref="manual")
    calls = {"n": 0}
    orig_wait = client.wait_done

    async def wait_and_new_order(children, timeout=5):
        calls["n"] += 1
        if calls["n"] == 1:
            trades.append(manual)  # surge um stop manual durante a espera pelos cancelamentos
        return await orig_wait(children, timeout)

    client.wait_done = wait_and_new_order
    result = asyncio.run(client.close_position("AAPL", authorize=lambda: True))
    assert result["aborted"] and result["conflict"] == [81] and manual.orderStatus.status == "Submitted"
    assert client.ib.placed == []
    # com autorização para cancelar saídas manuais: espera o estado terminal e só então envia
    client.settings.cancel_external_exits_on_close = True
    calls["n"] = 0
    trades[:] = [ib_order(3, "SELL", 100, "STP", oca="own2")]
    result = asyncio.run(client.close_position("AAPL", authorize=lambda: True))
    assert result["qty"] == 100 and manual.orderStatus.status == "Cancelled"
    assert all(t.isDone() for t in trades if t.order.orderId != client.ib.placed[-1].order.orderId)


def test_w01_pending_cancel_own_child_is_waited_not_ignored():
    sl = ib_order(1, "SELL", 100, "STP", oca="own", status="PendingCancel")
    client = real_client([sl], [position(100)])

    async def never(children, timeout=5):
        return False

    client.wait_done = never
    result = asyncio.run(client.close_position("AAPL", authorize=lambda: True))
    assert result["aborted"] and client.ib.placed == []


# ---------------------------------------------------------------- W02 / B03
def test_b03_repair_waits_for_nonterminal_stop():
    old = ib_order(1, "SELL", 100, "STP", oca="own", status="PendingCancel")
    client = real_client([old], [position(100)])

    async def never(children, timeout=5):
        return False

    client.wait_done = never
    assert asyncio.run(client.ensure_protection("AAPL", stop_price=98, tp_price=104)) is None
    assert client.ib.placed == [] and not old.isDone()
    # quando o cancelamento confirma, a cobertura é recalculada e só então reposta
    old.orderStatus.status = "Cancelled"
    client.wait_done = real_client([], []).wait_done.__func__.__get__(client)
    result = asyncio.run(client.ensure_protection("AAPL", stop_price=98, tp_price=104))
    assert result["qty"] == 100
    stops = [t for t in client.ib.openTrades() if t.order.orderType == "STP" and not t.isDone()]
    assert sum(t.order.totalQuantity for t in stops) == 100


# ---------------------------------------------------------------- W03 / B04 / B05
def test_b04_foreign_status_never_poisons_identity_and_real_fill_closes_trade():
    engine, db, s = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 100)
    group = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    oid = group["tp_order_id"]
    foreign = NS(orderStatus=NS(status="Submitted"),
                 order=NS(orderId=oid, action="SELL", permId=777, clientId=0, account="U2", orderRef="manual"),
                 contract=NS(symbol="MSFT", secType="STK", conId=999))
    engine._on_order_status(foreign)
    assert db.perm_id_for(gid, oid) is None
    f = NS(contract=NS(symbol="AAPL", secType="STK", conId=engine.ibkr.con_id("AAPL")),
           execution=NS(execId="actual-tp", orderId=oid, side="SLD", shares=100, price=104, time=NOW,
                        acctNumber=engine.ibkr.account, clientId=s.ib_client_id, orderRef=s.order_ref, permId=888), commissionReport=None)
    engine._on_fill(None, f)
    assert db._query("SELECT status, exit_reason FROM trades WHERE id=?", (tid,))[0] == {"status": "CLOSED", "exit_reason": "TP"}


def test_w03_unallocated_fill_is_reconciled_when_identity_arrives():
    engine, db, s = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 100)
    group = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    oid = group["tp_order_id"]
    db.set_perm_id(oid, 777, group_id=gid)  # associação errada herdada
    f = NS(contract=NS(symbol="AAPL", secType="STK", conId=engine.ibkr.con_id("AAPL")),
           execution=NS(execId="late-tp", orderId=oid, side="SLD", shares=100, price=104, time=NOW,
                        acctNumber=engine.ibkr.account, clientId=s.ib_client_id, orderRef=s.order_ref, permId=888), commissionReport=None)
    engine._on_fill(None, f)
    assert db.fill_by_exec("late-tp") is not None and db.allocations_for_fill("late-tp") == []
    # o estado correto (nosso) corrige o permId — com a PROVA da ordem viva na corretora (AD01) — e reprocessa o fill
    engine.ibkr.open_orders_all = [NS(order=NS(orderId=oid, permId=888, parentId=0), contract=NS(symbol="AAPL"),
                                      orderStatus=NS(status="Filled"))]
    engine._on_order_status(NS(orderStatus=NS(status="Filled"),
                               order=NS(orderId=oid, action="SELL", permId=888, clientId=s.ib_client_id, account=engine.ibkr.account,
                                        orderRef=s.order_ref), contract=NS(symbol="AAPL", secType="STK", conId=engine.ibkr.con_id("AAPL"))))
    assert db.perm_id_for(gid, oid) == 888
    assert db._query("SELECT status, exit_reason FROM trades WHERE id=?", (tid,))[0] == {"status": "CLOSED", "exit_reason": "TP"}
    assert len(db.allocations_for_fill("late-tp")) == 1


def test_b05_foreign_cancellation_keeps_our_entry_reservation():
    engine, db, s = make_engine()
    engine._pending_entries[100] = {"symbol": "AAPL", "qty": 100, "notional": 10000, "filled": 0, "trade_id": None}
    foreign = NS(orderStatus=NS(status="Cancelled"),
                 order=NS(orderId=100, action="BUY", clientId=0, account="U2", orderRef="manual"),
                 contract=NS(symbol="MSFT", secType="STK"))
    engine._on_order_status(foreign)
    assert 100 in engine._pending_entries
    ours = NS(orderStatus=NS(status="Cancelled"),
              order=NS(orderId=100, action="BUY", clientId=s.ib_client_id, account=engine.ibkr.account, orderRef=s.order_ref),
              contract=NS(symbol="AAPL", secType="STK", conId=engine.ibkr.con_id("AAPL")))
    engine._on_order_status(ours)
    assert 100 not in engine._pending_entries


# ---------------------------------------------------------------- W04 / B07 / B08
def test_b07_legacy_state_reaches_the_account_database_in_use(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_TRADER_HOME", str(tmp_path))
    s = Settings()
    old = Database(s.legacy_db_path())
    old.add_protection_event("daily_loss", None, datetime.now(timezone.utc) + timedelta(hours=3), "halt")
    old.close()
    mode = Database(s.db_path())
    mode.set_kv("bound_account", "U1")
    mode.close()
    account = Database(s.db_path("U1"))
    account.set_kv("bound_account", "U1")
    account.close()
    _migrate_legacy_db(s, logging.getLogger("audit"))
    engine, db, _ = make_engine()
    db.switch_path(s.db_path())
    engine.ibkr.account = "U1"
    assert engine._bind_account_db()
    assert engine.gate.halted and db.active_protections(datetime.now(timezone.utc))
    assert db.get_kv("migration_1.0.2") and not db.get_kv("migration_failed")


def test_b08_migration_never_duplicates_a_position_already_present(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_TRADER_HOME", str(tmp_path))
    engine, db, s = make_engine()
    db.switch_path(s.legacy_db_path())
    engine.ibkr.account = "U1"
    own_position(engine, db, "AAPL", 100)
    db.copy_to(s.db_path())
    db.close()
    _migrate_legacy_db(s, logging.getLogger("audit"))
    merged = Database(s.db_path())
    rows = merged.open_trades("AAPL")
    assert len(rows) == 1 and rows[0]["filled_qty"] == 100
    # repetir a importação também é idempotente (proteções e trades)
    src = Database(tmp_path / "trader.sqlite3.migrated-1.0.2")
    counts = merged.merge_open_state_from(src)
    assert counts["trades"] == 0 and counts["duplicates"] == 1


def test_w04_failed_migration_blocks_entries(monkeypatch):
    from tests.test_engine import _FixedDatetime, outcome, run_exec

    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    engine, db, s = make_engine()
    db.set_kv("migration_failed", "erro simulado")
    row = run_exec(engine, db, outcome("BUY"))
    assert row["executed"] == 0 and "migração" in row["skip_reason"]


# ---------------------------------------------------------------- W05 / B06
def test_b06_new_prompt_does_not_inherit_validated_gates():
    engine, db, s = make_engine()
    model = engine.brain.model
    original = engine.brain.prompt_version
    engine._apply_gates({"all_passed": True, "checks": {}, "experiment": Analytics.experiment_id(model, original)})
    assert engine._gates_for(model)[0] is True and engine.gates_passed
    engine.brain.update_lessons(["uma regra nova"], {})
    cal = engine._calibrator_for(model)
    assert cal.prompt_version != original and not cal.is_fitted
    assert engine._gates_for(model)[0] is False and not engine.gates_passed
    assert db.get_kv(f"gates_passed:{Analytics.experiment_id(model, original)}") == "1"  # a validação antiga fica com o prompt antigo
    assert db.get_kv(f"gates_passed:{model}") is None  # chave só por modelo nunca é escrita nem lida


# ---------------------------------------------------------------- W06 / B09 / B10
def entered_decision(db, ts, entry_price=100.2):
    did = db.insert_decision(symbol="AAPL", model="m", action="BUY", confidence=.8, reason="r", snapshot={"price": 100},
                             position_qty=0, net_liq=10000, prompt_version=0, parse_ok=True, raw_response="", ts=ts,
                             extra={"market_ts": ts.isoformat(), "stop_pct": 2, "tp_pct": 4})
    db.mark_decision(did, executed=True)
    gid = db.insert_order_group(symbol="AAPL", decision_id=did, role="ENTRY", direction=1, qty=100, parent_order_id=1,
                                tp_order_id=2, sl_order_id=3, ref_price=100, tp_price=104, sl_price=98, ts=ts)
    tid = db.open_trade(symbol="AAPL", decision_id=did, group_id=gid, direction=1, qty=100, stop_price=98, tp_price=104)
    db.record_entry_fill(tid, 100, entry_price, ts)
    return did, tid


def test_b09_executed_tp_is_labelled_from_the_ledger():
    db = Database(":memory:")
    s = Settings()
    did, tid = entered_decision(db, NOW)
    bars = [Bar(NOW + timedelta(minutes=1), 100.2, 104.1, 99, 104, 1)]
    db.record_exit_fill(tid, 100, 104, NOW + timedelta(minutes=1), "TP")
    Settler(s, db, lambda *_: 104, bars_between=lambda *_: bars).run(NOW + timedelta(hours=2))
    assert db._query("SELECT correct, label_source FROM decisions WHERE id=?", (did,))[0] == {"correct": 1, "label_source": "ledger:TP"}


def test_b10_entry_minute_exit_uses_the_ledger_not_the_next_bar():
    db = Database(":memory:")
    s = Settings()
    start = NOW + timedelta(seconds=30)
    did, tid = entered_decision(db, start, entry_price=100)
    db.record_exit_fill(tid, 100, 104, NOW + timedelta(seconds=45), "TP")
    bars = [Bar(NOW, 100, 105, 99, 104, 1), Bar(NOW + timedelta(minutes=1), 100, 101, 97, 98, 1)]
    Settler(s, db, lambda *_: 98, bars_between=lambda *_: bars).run(NOW + timedelta(hours=2))
    assert db._query("SELECT correct, label_source FROM decisions WHERE id=?", (did,))[0] == {"correct": 1, "label_source": "ledger:TP"}


def test_w06_open_trade_uses_absolute_levels_after_the_entry_minute_and_signal_exits_are_censored():
    db = Database(":memory:")
    s = Settings()
    did, tid = entered_decision(db, NOW + timedelta(seconds=30), entry_price=100.2)
    # a vela da entrada toca o stop ANTES da entrada: não é usada; a seguinte toca o TP absoluto (104)
    bars = [Bar(NOW, 100, 100.5, 97.0, 100.2, 1), Bar(NOW + timedelta(minutes=1), 100.2, 104.05, 99.5, 103, 1)]
    Settler(s, db, lambda *_: 103, bars_between=lambda *_: bars).run(NOW + timedelta(hours=2))
    assert db._query("SELECT correct, label_source FROM decisions WHERE id=?", (did,))[0] == {"correct": 1, "label_source": "bracket:entrada"}
    assert first_touch_absolute([Bar(NOW, 107, 108, 97, 99, 1)], 98.0, 106.0, 1) == 1
    did2, tid2 = entered_decision(db, NOW)
    db.record_exit_fill(tid2, 100, 101, NOW + timedelta(minutes=2), "SIGNAL")
    Settler(s, db, lambda *_: 101, bars_between=lambda *_: bars).run(NOW + timedelta(hours=2))
    assert db._query("SELECT correct, label_source FROM decisions WHERE id=?", (did2,))[0] == {"correct": None, "label_source": "ledger:SIGNAL"}


def test_w06_open_trade_inside_horizon_waits_for_its_outcome():
    db = Database(":memory:")
    s = Settings()
    did, tid = entered_decision(db, NOW - timedelta(minutes=40), entry_price=100)
    db._execute("UPDATE trades SET entry_ts=? WHERE id=?", ((NOW - timedelta(minutes=5)).isoformat(), tid))  # entrou há 5 min
    Settler(s, db, lambda *_: 100, bars_between=lambda *_: []).run(NOW)
    assert db._query("SELECT settled_ts FROM decisions WHERE id=?", (did,))[0]["settled_ts"] is None


# ---------------------------------------------------------------- W07 / B11
def test_b11_manual_stop_on_external_shares_does_not_cover_own_shares():
    manual = ib_order(81, "SELL", 80, "STP", ref="manual")
    client = real_client([manual], [position(100)])
    assert not client.has_protective_orders("AAPL", needed_qty=20)
    assert client.protective_coverage("AAPL") == 80 and not client.has_protective_orders("AAPL")  # a posição inteira vê o stop manual, mas não chega
    result = asyncio.run(client.ensure_protection("AAPL", stop_price=98, tp_price=104, max_qty=20))
    assert result["qty"] == 20 and client.protective_coverage("AAPL") == 100 and client.protective_coverage("AAPL", ours_only=True) == 20


def test_w07_engine_supervises_own_shares_with_own_stops_only(monkeypatch):
    from tests.helpers import FakeOrder

    engine, db, s = make_engine(manage_external_positions=False)
    own_position(engine, db, "AAPL", 20)
    engine.ibkr.positions["AAPL"] = 100
    engine.ibkr.orders["AAPL"] = [FakeOrder(81, "SELL", 80, "STP", order_ref="manual")]
    asyncio.run(engine._supervise())
    assert engine.ibkr.orders["AAPL"][-1].totalQuantity == 20 and engine.ibkr.orders["AAPL"][-1].orderRef == "OllamaIBKRTrader"
