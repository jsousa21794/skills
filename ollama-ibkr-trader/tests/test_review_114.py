"""Regressões da revisão da 1.0.14 (AF01-AF03): os ensaios do auditor com as asserções invertidas para o comportamento
corrigido, mais casos adjacentes (cancelamento da própria entrada continua a libertá-la; fecho posterior finaliza o rótulo)."""
import asyncio
from datetime import timedelta

from trader.database import Database
from trader.settlement import Settler
from tests.test_engine import NOW, own_position
from tests.test_review_104 import make_engine
from tests.test_review_105 import entered_decision
from tests.test_review_107 import own_fill
from tests.test_review_111 import row
from tests.test_review_113 import status_for


def reused_id_with_pending_entry():
    engine, db, s = make_engine()
    g1, t1 = own_position(engine, db, "AAPL", 100)
    oid = db.group_for_order(engine.ibkr._next_id - 3)["parent_order_id"]
    db.set_perm_id(oid, 888, group_id=g1)
    db.close_trade_reconciled(t1)
    engine.ibkr._next_id = oid  # reutilização do número local noutra sessão
    g2, t2 = own_position(engine, db, "AAPL", 20)
    db._execute("UPDATE trades SET filled_qty=0,entry_price=NULL,entry_ts=NULL WHERE id=?", (t2,))
    engine.ibkr.positions["AAPL"] = 0
    db.set_perm_id(oid, 999, group_id=g2, overwrite=True)
    live = status_for(engine, s, oid, 999, "Submitted", qty=20)
    engine.ibkr.open_orders_all = [live]
    engine._restore_pending_orders()
    assert engine._pending_entries[oid]["trade_id"] == t2 and engine._pending_entries[oid]["notional"] == 2000
    return engine, db, s, g1, t1, g2, t2, oid, live


# ---------------------------------------------------------------- AF01
def test_af01_cancel_of_a_known_old_group_never_touches_the_pending_entry_of_the_new_group():
    engine, db, s, g1, t1, g2, t2, oid, live = reused_id_with_pending_entry()
    assert db.group_for_order(oid, symbol="AAPL", perm_id=888)["id"] == g1
    engine._on_order_status(status_for(engine, s, oid, 888, "Cancelled", qty=100))  # estado do grupo ANTIGO
    assert live.orderStatus.status == "Submitted" and db.perm_id_for(g2, oid) == 999
    assert oid in engine._pending_entries and engine._pending_entries[oid]["notional"] == 2000
    assert row(db, t2)["status"] == "OPEN"


def test_af01_cancel_of_the_pending_entry_itself_still_releases_it():
    engine, db, s, g1, t1, g2, t2, oid, live = reused_id_with_pending_entry()
    engine._on_order_status(status_for(engine, s, oid, 999, "Cancelled", qty=20))
    assert oid not in engine._pending_entries and row(db, t2)["status"] == "CANCELLED"


# ---------------------------------------------------------------- AF02
def test_af02_legacy_coverage_migration_excludes_orders_placed_after_the_trade_closed(tmp_path):
    engine, db, s = make_engine()
    g1, t1 = own_position(engine, db, "AAPL", 100)
    g2, t2 = own_position(engine, db, "AAPL", 20)
    engine.ibkr.positions["AAPL"] = 120
    pos = dict(symbol="AAPL", qty=120, avg_cost=100, market_price=101)
    asyncio.run(engine._protect_if_naked("AAPL", pos))
    db.close_trade_reconciled(t2)
    g3, t3 = own_position(engine, db, "AAPL", 20)
    engine.ibkr.positions["AAPL"] = 120
    asyncio.run(engine._resize_exits("AAPL", pos, None, "repor proteção"))
    oid = db._query("SELECT sl_order_id FROM order_groups WHERE id=?", (g1,))[0]["sl_order_id"]
    db.set_perm_id(oid, 999, group_id=g1)
    db.close_trade_reconciled(t1)
    db.close_trade_reconciled(t3)
    # estado gravado pela 1.0.12: cobertura AMPLA por grupo acumulada ao longo das substituições
    db._execute("DELETE FROM order_coverage")
    for tid in (t1, t2, t3):
        db._execute("INSERT INTO exit_coverage (group_id, trade_id) VALUES (?,?)", (g1, tid))
    path = tmp_path / "multiple112.sqlite3"
    db.copy_to(path)
    upgraded = Database(path)
    engine.db = upgraded
    assert {t["id"] for t in upgraded.trades_for_order(oid, symbol="AAPL", perm_id=999)} == {t1, t3}  # t2 já estava fechado
    engine._on_fill(None, own_fill(engine, oid, 120, 98, "last-stop", perm_id=999))
    assert [row(upgraded, t)["exit_qty"] for t in (t1, t2, t3)] == [100, 0, 20]


# ---------------------------------------------------------------- AF03
def test_af03_summary_migration_never_finalizes_the_label_of_an_open_trade(tmp_path):
    engine, db, s = make_engine()
    did, tid = entered_decision(db, NOW, entry_price=100)
    engine._on_fill(None, own_fill(engine, 2, 40, 104, "partial-tp"))
    Settler(s, db, lambda *_: 100, bars_between=lambda *_: []).run(NOW + timedelta(hours=2))
    assert row(db, tid)["status"] == "OPEN" and db._query("SELECT label_final FROM decisions WHERE id=?", (did,))[0]["label_final"] == 0
    db.set_kv("migration:summaries", "")  # força a migração identificada por versão
    path = tmp_path / "open113.sqlite3"
    db.copy_to(path)
    upgraded = Database(path)
    assert upgraded._query("SELECT status,exit_qty FROM trades WHERE id=?", (tid,))[0] == dict(status="OPEN", exit_qty=40)
    d = upgraded._query("SELECT label_final,correct FROM decisions WHERE id=?", (did,))[0]
    assert d["label_final"] == 0 and [x["id"] for x in upgraded.provisional_decisions()] == [did]
    Settler(s, upgraded, lambda *_: 100, bars_between=lambda *_: []).run(NOW + timedelta(hours=3))
    assert upgraded._query("SELECT label_final FROM decisions WHERE id=?", (did,))[0]["label_final"] == 0
    # o fecho posterior finaliza o rótulo pela regra do ledger
    engine.db = upgraded
    sl = own_fill(engine, 3, 60, 98, "closing-sl")
    sl.execution.time = NOW + timedelta(minutes=1)
    engine._on_fill(None, sl)
    Settler(s, upgraded, lambda *_: 100, bars_between=lambda *_: []).run(NOW + timedelta(hours=4))
    assert upgraded._query("SELECT label_final,correct,label_source FROM decisions WHERE id=?", (did,))[0] == \
        dict(label_final=1, correct=1, label_source="ledger:TP+SL")
