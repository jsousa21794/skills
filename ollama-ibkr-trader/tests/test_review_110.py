"""Regressões da revisão da 1.0.10 (AB01-AB06): os ensaios do auditor com as asserções invertidas para o comportamento
corrigido, mais casos adjacentes (identidade ambígua fica pendente; execuções em qualquer ordem dão o mesmo resultado)."""
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from trader.database import Database
from trader.settlement import Settler
from tests.test_engine import NOW, own_position
from tests.test_review_104 import make_engine
from tests.test_review_105 import entered_decision
from tests.test_review_107 import own_fill


def row(db, tid):
    return db._query("SELECT * FROM trades WHERE id=?", (tid,))[0]


def partial_entry():
    engine, db, s = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 20)
    db._execute("UPDATE order_groups SET qty=100 WHERE id=?", (gid,))
    db._execute("UPDATE trades SET qty=100 WHERE id=?", (tid,))
    group = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    return engine, db, s, group, tid


def late_entry(engine, group, price=100, qty=80, eid="late-entry"):
    event = own_fill(engine, group["parent_order_id"], qty, price, eid)
    event.execution.side = "BOT"
    return event


# ---------------------------------------------------------------- AB01
def test_ab01_exact_perm_id_match_takes_precedence_over_an_unknown_identity():
    engine, db, s = make_engine()
    g1, t1 = own_position(engine, db, "AAPL", 100)
    oid = db._query("SELECT sl_order_id FROM order_groups WHERE id=?", (g1,))[0]["sl_order_id"]
    db.set_perm_id(oid, 888, group_id=g1)
    db.close_trade_reconciled(t1)
    g2, t2 = own_position(engine, db, "AAPL", 20)
    db.update_group_orders(g2, sl_order_id=oid)
    assert db.perm_id_for(g2, oid) is None
    assert db.group_for_order(oid, symbol="AAPL", perm_id=888)["id"] == g1
    engine._on_fill(None, own_fill(engine, oid, 100, 98, "old888", perm_id=888))
    assert row(db, t2)["status"] == "OPEN" and row(db, t2)["exit_qty"] == 0
    assert row(db, t1)["exit_qty"] == 100 and row(db, t1)["status"] == "CLOSED"


def test_ab01_ambiguous_identity_keeps_the_fill_pending_and_blocks_the_symbol():
    engine, db, s = make_engine()
    g1, t1 = own_position(engine, db, "AAPL", 100)
    oid = db._query("SELECT sl_order_id FROM order_groups WHERE id=?", (g1,))[0]["sl_order_id"]
    g2, t2 = own_position(engine, db, "AAPL", 20)
    db.update_group_orders(g2, sl_order_id=oid)  # dois grupos, nenhum com permId conhecido para esta ordem
    assert db.group_for_order(oid, symbol="AAPL", perm_id=777) is None
    engine._on_fill(None, own_fill(engine, oid, 20, 98, "ambiguous", perm_id=777))
    assert db.allocations_for_fill("ambiguous") == [] and len(db.unallocated_fills("AAPL")) == 1
    assert row(db, t1)["exit_qty"] == 0 and row(db, t2)["exit_qty"] == 0 and "AAPL" in engine._discrepancies
    # quando a corretora revela o permId da ordem viva, a execução pendente é reconciliada para o grupo certo
    db.set_perm_id(oid, 777, group_id=g2)
    engine._reconcile_unallocated_fills("AAPL", order_id=oid)
    assert row(db, t2)["exit_qty"] == 20 and row(db, t1)["exit_qty"] == 0
    # sem permId na execução (identidade indisponível), continua a valer o grupo mais recente
    assert db.group_for_order(oid, symbol="AAPL")["id"] == g2


# ---------------------------------------------------------------- AB02
def test_ab02_real_commission_during_partial_allocation_is_charged_exactly_once():
    engine, db, s, g, tid = partial_entry()
    event = own_fill(engine, g["sl_order_id"], 100, 98, "exit100")
    event.commissionReport = None  # estimativa inicial; relatório real chega depois
    engine._on_fill(None, event)
    assert row(db, tid)["commission"] == pytest.approx(.2)
    engine._on_commission(None, event, NS(commission=3.0))
    assert row(db, tid)["commission"] == pytest.approx(.6)  # 3,00 × 20/100
    engine._on_fill(None, late_entry(engine, g))  # entrada 1,00; saída restante 3,00 × 80/100 = 2,40
    assert db.fill_by_exec("exit100")["commission"] == 3 and db.allocated_shares("exit100") == 100
    assert row(db, tid)["commission"] == pytest.approx(4.0)
    assert row(db, tid)["pnl"] == pytest.approx(-204.0)
    assert sum(a["commission"] for a in db.allocations_for_fill("exit100")) == pytest.approx(3.0)


def test_ab02_commission_report_after_full_allocation_rescales_each_allocation():
    engine, db, s = make_engine()
    g1, t1 = own_position(engine, db, "AAPL", 60)
    g2, t2 = own_position(engine, db, "AAPL", 40)
    db.insert_order_group(symbol="AAPL", decision_id=None, role="CLOSE", direction=1, qty=100, parent_order_id=77,
                          tp_order_id=None, sl_order_id=None, ref_price=102, tp_price=None, sl_price=None,
                          account=engine.ibkr.account, con_id=engine.ibkr.con_id("AAPL"))
    db.add_exit_coverage(db._query("SELECT id FROM order_groups WHERE parent_order_id=77")[0]["id"], [t1, t2])
    event = own_fill(engine, 77, 100, 102, "close100")
    event.commissionReport = None
    engine._on_fill(None, event)
    engine._on_commission(None, event, NS(commission=5.0))
    assert row(db, t1)["commission"] == pytest.approx(3.0) and row(db, t2)["commission"] == pytest.approx(2.0)


# ---------------------------------------------------------------- AB03
def test_ab03_late_entry_reprices_the_exit_already_booked():
    engine, db, s, g, tid = partial_entry()
    engine._on_fill(None, own_fill(engine, g["sl_order_id"], 100, 98, "exit100"))
    engine._on_fill(None, late_entry(engine, g, price=101))
    result = row(db, tid)
    assert result["filled_qty"] == result["exit_qty"] == 100
    assert result["entry_price"] == pytest.approx(100.8)
    assert result["gross_pnl"] == pytest.approx(100 * 98 - (20 * 100 + 80 * 101)) == pytest.approx(-280)
    assert result["status"] == "CLOSED"


def test_ab03_same_executions_in_the_natural_order_give_the_same_result():
    engine, db, s, g, tid = partial_entry()
    engine._on_fill(None, late_entry(engine, g, price=101))
    engine._on_fill(None, own_fill(engine, g["sl_order_id"], 100, 98, "exit100"))
    result = row(db, tid)
    assert result["entry_price"] == pytest.approx(100.8) and result["gross_pnl"] == pytest.approx(-280)


# ---------------------------------------------------------------- AB04
def test_ab04_late_entry_reopens_the_trade_and_the_balance_is_own_again():
    engine, db, s, g, tid = partial_entry()
    engine._on_fill(None, own_fill(engine, g["sl_order_id"], 20, 98, "exit20"))
    assert row(db, tid)["status"] == "CLOSED"
    engine._on_fill(None, late_entry(engine, g))
    result = row(db, tid)
    assert result["filled_qty"] == 100 and result["exit_qty"] == 20 and result["status"] == "OPEN"
    assert engine._own_qty("AAPL") == 80 and "AAPL" in engine._discrepancies  # reconciliar com a corretora antes de continuar


def test_ab04_reopened_trade_invalidates_the_final_label_of_its_decision():
    engine, db, s = make_engine()
    did, tid = entered_decision(db, NOW, entry_price=100)
    db._execute("UPDATE trades SET filled_qty=20 WHERE id=?", (tid,))
    g = db._query("SELECT * FROM order_groups WHERE id=(SELECT group_id FROM trades WHERE id=?)", (tid,))[0]
    engine._on_fill(None, own_fill(engine, g["sl_order_id"], 20, 98, "exit20"))
    Settler(s, db, lambda *_: 100, bars_between=lambda *_: []).run(NOW + timedelta(hours=2))
    assert db._query("SELECT label_final FROM decisions WHERE id=?", (did,))[0]["label_final"] == 1
    db.set_kv("labels_changed_at", "")
    engine._on_fill(None, late_entry(engine, g))
    assert row(db, tid)["status"] == "OPEN"
    assert db._query("SELECT label_final FROM decisions WHERE id=?", (did,))[0]["label_final"] == 0 and db.get_kv("labels_changed_at")


# ---------------------------------------------------------------- AB05
def test_ab05_upgrade_backfills_reconciled_ts_so_late_exits_keep_their_real_date(tmp_path):
    engine, db, s = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 100)
    g = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    db.close_trade_reconciled(tid)
    rec = row(db, tid)["exit_ts"]
    db._execute("ALTER TABLE trades DROP COLUMN reconciled_ts")  # esquema de trades da 1.0.9
    path = tmp_path / "legacy.sqlite3"
    db.copy_to(path)
    upgraded = Database(path)
    engine.db = upgraded
    assert row(upgraded, tid)["reconciled_ts"] == rec  # migração explícita dos fechos reconciliados anteriores
    event = own_fill(engine, g["sl_order_id"], 100, 98, "old-stop")
    actual = datetime.fromisoformat(rec) - timedelta(days=2)
    event.execution.time = actual
    engine._on_fill(None, event)
    assert row(upgraded, tid)["exit_ts"] == actual.isoformat() and row(upgraded, tid)["reconciled_ts"] == rec
    assert upgraded.recent_stop_count(datetime.fromisoformat(rec) - timedelta(hours=1)) == 0


def test_ab05_upgrade_rebuilds_the_exit_date_of_a_partially_corrected_reconciled_trade(tmp_path):
    engine, db, s = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 100)
    g = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    db.close_trade_reconciled(tid)
    rec = row(db, tid)["exit_ts"]
    db._execute("UPDATE trades SET reconciled_ts=NULL WHERE id=?", (tid,))
    # correção parcial feita por uma versão anterior: 40 ações com execução de há 3 dias, data de fecho ficou na reconciliação
    old_ts = datetime.fromisoformat(rec) - timedelta(days=3)
    db.insert_fill(exec_id="partial40", order_id=g["sl_order_id"], symbol="AAPL", side="SLD", shares=40, price=98, ts=old_ts)
    db.allocate_fill("partial40", tid, 40, 0.4)
    db._execute("UPDATE trades SET exit_qty=40, exit_reason='SL' WHERE id=?", (tid,))
    path = tmp_path / "legacy.sqlite3"
    db.copy_to(path)
    upgraded = Database(path)
    r = row(upgraded, tid)
    assert r["reconciled_ts"] == rec and r["exit_ts"] == old_ts.isoformat()


# ---------------------------------------------------------------- AB06
@pytest.mark.parametrize("reversed_arrival", [False, True])
def test_ab06_mixed_exits_give_the_same_reason_label_and_date_in_any_arrival_order(reversed_arrival):
    engine, db, s = make_engine()
    did, tid = entered_decision(db, NOW, entry_price=100)
    db.close_trade_reconciled(tid)
    Settler(s, db, lambda *_: 100, bars_between=lambda *_: []).run(NOW + timedelta(hours=2))
    tp = own_fill(engine, 2, 40, 104, "tp40")
    sl = own_fill(engine, 3, 60, 98, "sl60")
    sl.execution.time = NOW + timedelta(minutes=1)
    for event in ([sl, tp] if reversed_arrival else [tp, sl]):
        engine._on_fill(None, event)
    r = row(db, tid)
    decision = db._query("SELECT correct,label_source FROM decisions WHERE id=?", (did,))[0]
    assert r["gross_pnl"] == 40 and r["exit_ts"] == (NOW + timedelta(minutes=1)).isoformat() and r["exit_qty"] == 100
    assert r["exit_reason"] == "TP+SL"  # razões na ordem CRONOLÓGICA das execuções, não da chegada
    assert decision == {"correct": 1, "label_source": "ledger:TP+SL"}  # o primeiro toque decide o rótulo


def test_ab06_open_trade_exits_also_follow_execution_chronology():
    engine, db, s = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 100)
    g = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    sl = own_fill(engine, g["sl_order_id"], 60, 98, "sl60")
    sl.execution.time = NOW + timedelta(minutes=1)
    tp = own_fill(engine, g["tp_order_id"], 40, 104, "tp40")
    engine._on_fill(None, sl)
    engine._on_fill(None, tp)  # chega depois, executou antes
    r = row(db, tid)
    assert r["status"] == "CLOSED" and r["exit_reason"] == "TP+SL" and r["exit_ts"] == (NOW + timedelta(minutes=1)).isoformat()
