"""Regressões da revisão da 1.0.6 (X01-X06 / C01-C06): os ensaios do auditor com as asserções invertidas
para o comportamento corrigido, mais casos adjacentes."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from trader.app import _migrate_legacy_db
from trader.config import Settings
from trader.database import Database
from trader.indicators import Bar
from trader.settlement import Settler
from tests.test_engine import NOW, _FixedDatetime, outcome, own_position, run_exec
from tests.test_review_103 import ib_order, position, real_client
from tests.test_review_104 import make_engine
from tests.test_review_105 import entered_decision


# ---------------------------------------------------------------- X01 / C05
def test_c05_reduced_position_resizes_exits_and_blocks_decisions(monkeypatch):
    engine, db, s = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 100)
    g = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    acct = engine.ibkr.account
    tp = ib_order(g["tp_order_id"], "SELL", 100, "LMT", account=acct, oca="pair")
    sl = ib_order(g["sl_order_id"], "SELL", 100, "STP", account=acct, oca="pair")
    # Uma venda externa reduziu a posição líquida de 100 para 20; o ledger do bot ainda diz 100.
    client = real_client([tp, sl], [position(20, account=acct)], account=acct)
    client.portfolio_state = lambda: {"positions": [{"symbol": "AAPL", "qty": 20, "avg_cost": 100, "market_price": 101, "sec_type": "STK"}]}
    engine.ibkr = client
    assert client.has_excess_exits("AAPL")
    asyncio.run(engine._supervise())
    assert sl.orderStatus.status == "Cancelled" and tp.orderStatus.status == "Cancelled"
    live = [t for t in client.ib.openTrades() if not t.isDone()]
    assert sum(t.order.totalQuantity for t in live if t.order.orderType == "STP") == 20
    assert sum(t.order.totalQuantity for t in live if t.order.orderType == "LMT") == 20
    assert not client.has_excess_exits("AAPL") and client.has_protective_orders("AAPL")
    trade = db._query("SELECT * FROM trades WHERE id=?", (tid,))[0]
    assert trade["exit_qty"] == 80 and trade["exit_reason"] == "EXTERNAL" and trade["status"] == "OPEN"
    assert engine._own_qty("AAPL") == 20 and "AAPL" not in engine._discrepancies
    group = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    assert group["sl_order_id"] != g["sl_order_id"] and db.order_leg(group, g["sl_order_id"]) == "SL"


def test_x01_decisions_blocked_while_discrepancy_unresolved(monkeypatch):
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    engine, db, s = make_engine()
    engine._discrepancies.add("AAPL")
    row = run_exec(engine, db, outcome("BUY"))
    assert row["executed"] == 0 and "discrepância" in row["skip_reason"]


def test_x01_vanished_position_cancels_exits_and_reconciles_trade():
    engine, db, s = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 100)
    g = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    acct = engine.ibkr.account
    sl = ib_order(g["sl_order_id"], "SELL", 100, "STP", account=acct, oca="pair")
    client = real_client([sl], [], account=acct)
    client.portfolio_state = lambda: {"positions": []}
    engine.ibkr = client
    asyncio.run(engine._supervise())
    assert sl.orderStatus.status == "Cancelled"  # nunca fica um stop que abriria uma posição inversa
    assert db._query("SELECT status, exit_reason FROM trades WHERE id=?", (tid,))[0] == {"status": "CLOSED", "exit_reason": "RECONCILED"}


def test_x01_resize_waits_for_terminal_cancellation():
    sl = ib_order(1, "SELL", 100, "STP", oca="own")
    client = real_client([sl], [position(20)])

    async def never(children, timeout=5):
        return False

    client.wait_done = never
    assert asyncio.run(client.resize_exits("AAPL", stop_price=98, tp_price=104)) is None
    assert client.ib.placed == []  # sem confirmação terminal não se cria cobertura nova


# ---------------------------------------------------------------- X02 / C01
def test_c01_rejected_fill_keeps_its_identity_when_reprocessed():
    engine, db, s = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 100)
    group = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    oid = group["tp_order_id"]
    db.set_perm_id(oid, 888, group_id=gid)
    contract = NS(symbol="AAPL", secType="STK", conId=engine.ibkr.con_id("AAPL"))
    wrong = NS(contract=contract, execution=NS(execId="wrong-identity", orderId=oid, permId=999, clientId=s.ib_client_id,
                                               orderRef=s.order_ref, acctNumber=engine.ibkr.account, side="SLD", shares=100,
                                               price=104, time=NOW), commissionReport=None)
    engine._on_fill(None, wrong)
    assert db.open_trades("AAPL") and not db.allocations_for_fill("wrong-identity")
    stored = db.fill_by_exec("wrong-identity")
    assert stored["perm_id"] == 999 and stored["client_id"] == s.ib_client_id and stored["order_ref"] == s.order_ref
    status = NS(orderStatus=NS(status="Submitted"), contract=contract,
                order=NS(orderId=oid, permId=888, clientId=s.ib_client_id, orderRef=s.order_ref, account=engine.ibkr.account, action="SELL"))
    engine._on_order_status(status)
    assert db.perm_id_for(gid, oid) == 888
    assert db._query("SELECT status FROM trades WHERE id=?", (tid,))[0]["status"] == "OPEN"
    assert not db.allocations_for_fill("wrong-identity")  # identidade incompatível continua incompatível
    # o reprocessamento usa a identidade ORIGINAL persistida (permId 999), nunca uma identidade sintetizada
    pending = db.unallocated_fills("AAPL", order_id=oid)
    assert len(pending) == 1 and pending[0]["perm_id"] == 999 and pending[0]["exec_id"] == "wrong-identity"


# ---------------------------------------------------------------- X03 / C02
def test_c02_failed_migration_blocks_entries_regardless_of_the_open_database(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_TRADER_HOME", str(tmp_path))
    s = Settings()
    Database(s.legacy_db_path()).close()
    mode = Database(s.db_path())
    mode.set_kv("bound_account", "U1")
    mode.close()
    acct = Database(s.db_path("U1"))
    acct.set_kv("bound_account", "U1")
    acct.close()

    def fail(*args, **kwargs):
        raise RuntimeError("erro de importação simulado")

    monkeypatch.setattr(Database, "merge_open_state_from", fail)
    _migrate_legacy_db(s, logging.getLogger("audit"))
    assert s.migration_flag_path().exists()
    assert Database(s.db_path("U1")).get_kv("migration_failed")  # também no destino efetivo
    engine, db, _ = make_engine()
    db.switch_path(s.db_path())
    engine.ibkr.account = "U1"
    assert engine._bind_account_db()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    engine.gate.events = None
    row = run_exec(engine, db, outcome("BUY"))
    assert row["executed"] == 0 and "migração" in row["skip_reason"]
    s.migration_flag_path().unlink()
    db.set_kv("migration_failed", "")
    assert run_exec(engine, db, outcome("BUY"))["executed"] == 1  # resolução explícita desbloqueia


def test_x03_failure_without_existing_target_also_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_TRADER_HOME", str(tmp_path))
    s = Settings()
    Database(s.legacy_db_path()).close()
    monkeypatch.setattr(Database, "copy_to", lambda self, target: (_ for _ in ()).throw(RuntimeError("disco")))
    _migrate_legacy_db(s, logging.getLogger("audit"))
    assert s.migration_flag_path().exists() and s.legacy_db_path().exists()


# ---------------------------------------------------------------- X04 / C03
def test_c03_dedup_uses_full_identity_not_numeric_order_id():
    dst = Database(":memory:")
    src = Database(":memory:")
    gid = dst.insert_order_group(symbol="AAPL", decision_id=None, role="ENTRY", direction=1, qty=1, parent_order_id=100,
                                 tp_order_id=101, sl_order_id=102, ref_price=100, tp_price=104, sl_price=98, account="U1", con_id=11)
    tid = dst.open_trade(symbol="AAPL", decision_id=None, group_id=gid, direction=1, qty=1)
    dst.record_entry_fill(tid, 1, 100, NOW - timedelta(days=10))
    dst.record_exit_fill(tid, 1, 104, NOW - timedelta(days=9), "TP")
    gid = src.insert_order_group(symbol="TSLA", decision_id=None, role="ENTRY", direction=1, qty=20, parent_order_id=100,
                                 tp_order_id=201, sl_order_id=202, ref_price=200, tp_price=208, sl_price=196, account="U1", con_id=22)
    tid = src.open_trade(symbol="TSLA", decision_id=None, group_id=gid, direction=1, qty=20)
    src.record_entry_fill(tid, 20, 200, NOW)
    counts = dst.merge_open_state_from(src)
    assert counts["trades"] == 1 and counts["duplicates"] == 0 and dst.open_trades("TSLA")
    # a MESMA ordem (símbolo, conta, contrato, id) continua a ser deduplicada
    counts = dst.merge_open_state_from(src)
    assert counts["trades"] == 0 and counts["duplicates"] == 1
    # mesmo símbolo e id mas outra conta: operação distinta
    src2 = Database(":memory:")
    gid = src2.insert_order_group(symbol="TSLA", decision_id=None, role="ENTRY", direction=1, qty=5, parent_order_id=100,
                                  tp_order_id=201, sl_order_id=202, ref_price=200, tp_price=208, sl_price=196, account="U2", con_id=22)
    tid = src2.open_trade(symbol="TSLA", decision_id=None, group_id=gid, direction=1, qty=5)
    src2.record_entry_fill(tid, 5, 200, NOW + timedelta(minutes=1))
    assert dst.merge_open_state_from(src2)["trades"] == 1


# ---------------------------------------------------------------- X05 / C04
def test_c04_pending_cancel_entry_keeps_reservation_and_block_on_restart():
    engine, db, s = make_engine()
    parent = ib_order(100, "BUY", 100, "LMT", status="PendingCancel")
    client = real_client([parent], [])
    client.portfolio_state = lambda: {"positions": []}
    engine.ibkr = client
    gid = db.insert_order_group(symbol="AAPL", decision_id=None, role="ENTRY", direction=1, qty=100, parent_order_id=100,
                                tp_order_id=101, sl_order_id=102, ref_price=100, tp_price=104, sl_price=98, account="U1")
    db.open_trade(symbol="AAPL", decision_id=None, group_id=gid, direction=1, qty=100)
    engine._restore_pending_orders()
    assert not parent.isDone()
    assert 100 in engine._pending_entries and engine._pending_entries[100]["cancel_sent"] is True
    assert engine._pending_entries[100]["notional"] == 10_000.0 and client.has_pending_entry("AAPL")
    # o supervisor não volta a pedir o cancelamento; a confirmação terminal liberta a reserva
    asyncio.run(engine._supervise())
    assert 100 in engine._pending_entries
    parent.orderStatus.status = "Cancelled"
    engine._on_order_status(NS(orderStatus=parent.orderStatus, order=NS(orderId=100, action="BUY", account="U1", orderRef=s.order_ref,
                                                                       clientId=s.ib_client_id), contract=parent.contract))
    assert 100 not in engine._pending_entries


# ---------------------------------------------------------------- X06 / C06
def test_c06_label_is_independent_of_when_the_settler_runs():
    s = Settings()

    def scenario(early):
        db = Database(":memory:")
        did, tid = entered_decision(db, NOW, entry_price=100)
        bars = [Bar(NOW + timedelta(minutes=i), 100, 101, 99, 100, 1) for i in range(1, 31)]
        settle = Settler(s, db, lambda *_: 100, bars_between=lambda *_: bars)
        if early:
            settle.run(NOW + timedelta(minutes=31))
            row = db._query("SELECT correct, label_source, label_final FROM decisions WHERE id=?", (did,))[0]
            assert row == {"correct": None, "label_source": "censurado", "label_final": 0}  # provisório
        db.record_exit_fill(tid, 100, 104, NOW + timedelta(minutes=45), "TP")
        settle.run(NOW + timedelta(minutes=60))
        return db._query("SELECT correct, label_source, label_final, provisional_correct FROM decisions WHERE id=?", (did,))[0]

    continuous, delayed = scenario(True), scenario(False)
    assert continuous["correct"] == delayed["correct"] == 1
    assert continuous["label_source"] == delayed["label_source"] == "ledger:TP"
    assert continuous["label_final"] == delayed["label_final"] == 1
    assert continuous["provisional_correct"] is None  # o provisório (censurado) fica guardado à parte


def test_x06_provisional_touch_label_is_overturned_by_the_ledger():
    s = Settings()
    db = Database(":memory:")
    did, tid = entered_decision(db, NOW, entry_price=100)
    bars = [Bar(NOW + timedelta(minutes=i), 100, 104.5, 99, 100, 1) for i in range(1, 31)]  # o percurso toca o TP…
    settle = Settler(s, db, lambda *_: 100, bars_between=lambda *_: bars)
    settle.run(NOW + timedelta(minutes=31))
    assert db._query("SELECT correct, label_final FROM decisions WHERE id=?", (did,))[0] == {"correct": 1, "label_final": 0}
    db.record_exit_fill(tid, 100, 98, NOW + timedelta(minutes=50), "SL")  # …mas a ordem fechou pelo stop
    settle.run(NOW + timedelta(minutes=60))
    row = db._query("SELECT correct, label_source, label_final, provisional_correct FROM decisions WHERE id=?", (did,))[0]
    assert row == {"correct": 0, "label_source": "ledger:SL", "label_final": 1, "provisional_correct": 1}
