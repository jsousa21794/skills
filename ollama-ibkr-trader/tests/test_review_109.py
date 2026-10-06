"""Regressões da revisão da 1.0.9 (AA01-AA06 / T01-T06): os ensaios do auditor com as asserções invertidas para o
comportamento corrigido, mais casos adjacentes."""
import asyncio
from concurrent.futures import Future
from datetime import datetime, timedelta

from trader.mcp_server import RemoteSupervisor, mask_account
from trader.settlement import Settler
from tests.test_engine import NOW, own_position
from tests.test_review_104 import make_engine
from tests.test_review_105 import entered_decision
from tests.test_review_107 import own_fill, setup_position


def trade_row(db, tid, cols):
    return db._query(f"SELECT {cols} FROM trades WHERE id=?", (tid,))[0]


# ---------------------------------------------------------------- AA01 / T01
def test_t01_startup_with_an_unrecorded_reversal_places_no_orders():
    engine, db, s, gid, tid, tp, sl, _ = setup_position(-20)
    assert not db.get_kv("conflict:AAPL")
    asyncio.run(engine._reconcile())  # primeiro contacto com a inversão ocorrida com o programa desligado
    assert engine._reconciled and engine.ibkr.ib.placed == []
    assert tp.orderStatus.status == sl.orderStatus.status == "Cancelled"  # as vendas antigas não ficam vivas contra um short
    assert db.get_kv("conflict:AAPL") and "AAPL" in engine._discrepancies
    assert engine._own_qty("AAPL") == 0
    asyncio.run(engine._supervise())
    assert engine.ibkr.ib.placed == []


def test_aa01_startup_with_an_unrecorded_reduction_resizes_instead_of_protecting_blindly():
    engine, db, s, gid, tid, tp, sl, _ = setup_position(20)
    asyncio.run(engine._reconcile())
    assert engine._reconciled and db.open_adjustment_qty(tid) == 80 and db.get_kv("conflict:AAPL")
    assert all(t.order.totalQuantity == 20 and t.order.action == "SELL" for t in engine.ibkr.ib.placed)


def test_aa01_protection_refuses_a_position_with_the_opposite_sign_of_the_ledger():
    engine, db, s, gid, tid, tp, sl, _ = setup_position(-20)
    pos = {"symbol": "AAPL", "qty": -20.0, "avg_cost": 100, "market_price": 101}
    asyncio.run(engine._protect_if_naked("AAPL", pos))
    assert engine.ibkr.ib.placed == [] and "AAPL" in engine._discrepancies


# ---------------------------------------------------------------- AA02 / T02
def test_t02_reused_local_order_id_keeps_the_perm_id_during_allocation():
    engine, db, s = make_engine()
    g1, t1 = own_position(engine, db, "AAPL", 100)
    old = db._query("SELECT * FROM order_groups WHERE id=?", (g1,))[0]
    oid = old["sl_order_id"]
    db.set_perm_id(oid, 888, group_id=g1)
    db.close_trade_reconciled(t1)
    g2, t2 = own_position(engine, db, "AAPL", 20)
    db.update_group_orders(g2, sl_order_id=oid)
    db.set_perm_id(oid, 999, group_id=g2, overwrite=True)
    assert db.group_for_order(oid, symbol="AAPL", perm_id=888)["id"] == g1
    engine._on_fill(None, own_fill(engine, oid, 100, 98, "old-perm888", perm_id=888))
    assert trade_row(db, t2, "status,exit_qty") == {"status": "OPEN", "exit_qty": 0.0}
    assert trade_row(db, t1, "status,exit_qty,exit_reason") == {"status": "CLOSED", "exit_qty": 100.0, "exit_reason": "SL"}
    allocations = db.allocations_for_fill("old-perm888")
    assert [a["trade_id"] for a in allocations] == [t1] and allocations[0]["shares"] == 100
    # e a execução da ordem NOVA (permId 999) só toca no trade novo
    engine._on_fill(None, own_fill(engine, oid, 20, 98, "new-perm999", perm_id=999))
    assert trade_row(db, t2, "status,exit_qty") == {"status": "CLOSED", "exit_qty": 20.0}
    assert trade_row(db, t1, "exit_qty")["exit_qty"] == 100.0


# ---------------------------------------------------------------- AA03 / T03
def test_t03_partially_allocated_fill_stays_pending_and_completes_when_the_entry_arrives():
    engine, db, s = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 20)
    g = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    db._execute("UPDATE order_groups SET qty=100 WHERE id=?", (gid,))
    db._execute("UPDATE trades SET qty=100 WHERE id=?", (tid,))
    engine._on_fill(None, own_fill(engine, g["sl_order_id"], 100, 98, "exit100"))  # saída de 100 antes do resto da entrada
    assert sum(a["shares"] for a in db.allocations_for_fill("exit100")) == 20
    pending = db.unallocated_fills("AAPL")
    assert len(pending) == 1 and pending[0]["exec_id"] == "exit100" and pending[0]["remaining"] == 80
    assert "AAPL" in engine._discrepancies
    later = own_fill(engine, g["parent_order_id"], 80, 100, "entry-late80")
    later.execution.side = "BOT"
    engine._on_fill(None, later)  # a entrada restante chega: o saldo por alocar é recuperado automaticamente
    assert sum(a["shares"] for a in db.allocations_for_fill("exit100")) == 100
    assert trade_row(db, tid, "status,exit_qty,filled_qty") == {"status": "CLOSED", "exit_qty": 100.0, "filled_qty": 100.0}
    assert db.unallocated_fills("AAPL") == []
    engine._reconcile_unallocated_fills("AAPL")  # idempotente
    assert sum(a["shares"] for a in db.allocations_for_fill("exit100")) == 100
    assert "AAPL" not in engine._discrepancies


# ---------------------------------------------------------------- AA04 / T04
def test_t04_late_exit_correction_refreshes_the_final_label_and_invalidates_calibration():
    engine, db, s = make_engine()
    did, tid = entered_decision(db, NOW, entry_price=100)
    db.close_trade_reconciled(tid)
    settle = Settler(s, db, lambda *_: 100, bars_between=lambda *_: [])
    settle.run(NOW + timedelta(hours=1))
    before = db._query("SELECT correct,label_source,label_final FROM decisions WHERE id=?", (did,))[0]
    assert before == {"correct": None, "label_source": "ledger:RECONCILED", "label_final": 1}
    db.set_kv("labels_changed_at", "")
    engine._on_fill(None, own_fill(engine, 2, 100, 104, "late-tp"))
    assert trade_row(db, tid, "exit_reason,gross_pnl") == {"exit_reason": "TP", "gross_pnl": 400.0}
    after = db._query("SELECT correct,label_source,label_final FROM decisions WHERE id=?", (did,))[0]
    assert after == {"correct": 1, "label_source": "ledger:TP", "label_final": 1}
    assert db.get_kv("labels_changed_at")
    settle.run(NOW + timedelta(hours=2))
    assert db._query("SELECT correct,label_source FROM decisions WHERE id=?", (did,))[0] == {"correct": 1, "label_source": "ledger:TP"}


# ---------------------------------------------------------------- AA05 / T05
def run_queued(engine):
    def call(coro):
        f = Future()

        async def apply():
            f.set_result(await coro)

        asyncio.get_event_loop().create_task(apply())
        return f

    engine.call = call


def test_t05_mask_collision_is_rejected_by_the_opaque_context_id(tmp_path):
    engine, db, s = make_engine()
    account_a, account_b = "U1234567", "U1987667"
    assert mask_account(account_a) == mask_account(account_b)
    engine.ibkr.account = account_a
    sup = RemoteSupervisor(engine, s, db)
    status_a = sup.status()
    assert status_a["context_id"] and status_a["account"] == mask_account(account_a)
    engine.ibkr.account = account_b  # a conta mudou depois de o cliente ler o estado
    db.switch_path(tmp_path / "account_b.sqlite3")
    run_queued(engine)
    result = asyncio.run(sup.pause_entries(status_a["account"], s.trading_mode, "pedido após consulta da conta A",
                                           context_id=status_a["context_id"]))
    assert result["applied"] is False and "contexto" in result["error"] and not engine.entries_paused
    assert db.get_kv("entries_paused") is None
    # sem context_id o pedido é recusado; com o contexto atual é aplicado
    assert asyncio.run(sup.pause_entries(mask_account(account_b), s.trading_mode, "sem contexto", context_id=""))["applied"] is False
    current = sup.status()["context_id"]
    assert current != status_a["context_id"]
    ok = asyncio.run(sup.pause_entries(mask_account(account_b), s.trading_mode, "com contexto", context_id=current))
    assert ok["applied"] is True and engine.entries_paused


def test_aa05_context_id_changes_with_mode_and_generation():
    engine, db, s = make_engine()
    sup = RemoteSupervisor(engine, s, db)
    first = sup.context_id()
    engine.generation += 1
    second = sup.context_id()
    s.trading_mode = "paper"
    third = sup.context_id()
    assert len({first, second, third}) == 3 and len(first) >= 12


# ---------------------------------------------------------------- AA06 / T06
def test_t06_late_exit_keeps_the_execution_date_and_records_the_reconciliation_separately():
    engine, db, s = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 100)
    g = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    db.close_trade_reconciled(tid)
    reconciled_at = trade_row(db, tid, "exit_ts")["exit_ts"]
    event = own_fill(engine, g["sl_order_id"], 100, 98, "old-dated-stop")
    actual = datetime.fromisoformat(reconciled_at) - timedelta(days=2)
    event.execution.time = actual
    engine._on_fill(None, event)
    row = trade_row(db, tid, "exit_ts,exit_reason,reconciled_ts,status")
    assert row == {"exit_ts": actual.isoformat(), "exit_reason": "SL", "reconciled_ts": reconciled_at, "status": "CLOSED"}
    assert db.fill_by_exec("old-dated-stop")["ts"] == actual.isoformat()
    assert db.recent_stop_count(datetime.fromisoformat(reconciled_at) - timedelta(hours=1)) == 0


def test_aa06_multiple_late_exits_use_the_latest_execution_as_the_close_date():
    engine, db, s = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 100)
    g = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    db.close_trade_reconciled(tid)
    reconciled_at = datetime.fromisoformat(trade_row(db, tid, "exit_ts")["exit_ts"])
    second = own_fill(engine, g["sl_order_id"], 40, 98, "late-b")
    second.execution.time = reconciled_at - timedelta(days=1)
    first = own_fill(engine, g["sl_order_id"], 60, 98, "late-a")
    first.execution.time = reconciled_at - timedelta(days=2)
    engine._on_fill(None, second)
    engine._on_fill(None, first)  # chega depois, mas executou antes: não recua a data de fecho
    assert trade_row(db, tid, "exit_ts,exit_qty")["exit_ts"] == (reconciled_at - timedelta(days=1)).isoformat()
    assert trade_row(db, tid, "exit_qty")["exit_qty"] == 100.0
