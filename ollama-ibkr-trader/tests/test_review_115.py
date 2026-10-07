"""Regressões da revisão da 1.0.15 (AG01-AG03): os ensaios do auditor com as asserções invertidas para o comportamento
corrigido, mais casos adjacentes (execução da própria entrada liberta a reserva; reparações idempotentes)."""
import asyncio
from datetime import timedelta

import trader.database as dbmod
from trader.database import Database
from trader.settlement import Settler
from tests.test_engine import NOW, own_position
from tests.test_review_104 import make_engine
from tests.test_review_105 import entered_decision
from tests.test_review_107 import own_fill
from tests.test_review_111 import row
from tests.test_review_114 import reused_id_with_pending_entry


# ---------------------------------------------------------------- AG01
def test_ag01_old_fill_never_touches_the_pending_entry_of_the_new_group():
    engine, db, s, g1, t1, g2, t2, oid, live = reused_id_with_pending_entry()
    db._execute("UPDATE trades SET filled_qty=80 WHERE id=?", (t1,))  # ordem antiga de 100 com 80 confirmadas
    f = own_fill(engine, oid, 20, 100, "old-entry-remaining", perm_id=888)
    f.execution.side = "BOT"
    assert engine._pending_entries[oid]["group_id"] == g2
    engine._on_fill(None, f)
    assert row(db, t1)["filled_qty"] == 100
    assert row(db, t2)["filled_qty"] == 0 and row(db, t2)["status"] == "OPEN"
    assert live.orderStatus.status == "Submitted" and db.perm_id_for(g2, oid) == 999
    assert oid in engine._pending_entries and engine._pending_entries[oid]["notional"] == 2000
    # a execução da PRÓPRIA entrada liberta a reserva no momento certo
    mine = own_fill(engine, oid, 20, 100, "new-entry", perm_id=999)
    mine.execution.side = "BOT"
    engine._on_fill(None, mine)
    assert row(db, t2)["filled_qty"] == 20 and oid not in engine._pending_entries


def test_ag01_close_fill_of_another_group_never_completes_the_pending_close():
    engine, db, s = make_engine()
    g1, t1 = own_position(engine, db, "AAPL", 100)
    close_old = db.insert_order_group(symbol="AAPL", decision_id=None, role="CLOSE", direction=1, qty=100, parent_order_id=700,
                                      tp_order_id=None, sl_order_id=None, ref_price=102, tp_price=None, sl_price=None,
                                      account=engine.ibkr.account, con_id=engine.ibkr.con_id("AAPL"))
    db.add_exit_coverage(close_old, [t1], order_ids=[700])
    db.set_perm_id(700, 888, group_id=close_old)
    db.close_trade_reconciled(t1)
    g2, t2 = own_position(engine, db, "AAPL", 20)
    close_new = db.insert_order_group(symbol="AAPL", decision_id=None, role="CLOSE", direction=1, qty=20, parent_order_id=700,
                                      tp_order_id=None, sl_order_id=None, ref_price=102, tp_price=None, sl_price=None,
                                      account=engine.ibkr.account, con_id=engine.ibkr.con_id("AAPL"))
    db.add_exit_coverage(close_new, [t2], order_ids=[700])
    db.set_perm_id(700, 999, group_id=close_new)
    engine._pending_close["AAPL"] = {"state": "SENT", "order_id": 700, "group_id": close_new, "qty": 20.0, "filled": 0.0, "ts": NOW}
    engine._on_fill(None, own_fill(engine, 700, 100, 102, "old-close", perm_id=888))  # fecho ANTIGO
    assert row(db, t1)["exit_qty"] == 100 and row(db, t2)["exit_qty"] == 0
    assert "AAPL" in engine._pending_close and engine._pending_close["AAPL"]["filled"] == 0.0


# ---------------------------------------------------------------- AG02
def test_ag02_versioned_repair_removes_coverage_wrongly_inferred_by_the_previous_version(tmp_path, monkeypatch):
    clock = [NOW]
    monkeypatch.setattr(dbmod, "utc_now", lambda: clock[0])  # relógio controlado (AH01): sem dependência da resolução
    engine, db, s = make_engine()
    g1, t1 = own_position(engine, db, "AAPL", 100)
    g2, t2 = own_position(engine, db, "AAPL", 20)
    engine.ibkr.positions["AAPL"] = 120
    pos = dict(symbol="AAPL", qty=120, avg_cost=100, market_price=101)
    clock[0] = NOW + timedelta(minutes=1)
    asyncio.run(engine._protect_if_naked("AAPL", pos))
    clock[0] = NOW + timedelta(minutes=2)
    db.close_trade_reconciled(t2)
    clock[0] = NOW + timedelta(minutes=3)
    g3, t3 = own_position(engine, db, "AAPL", 20)
    engine.ibkr.positions["AAPL"] = 120
    clock[0] = NOW + timedelta(minutes=4)
    asyncio.run(engine._resize_exits("AAPL", pos, None, "repor proteção"))
    g1row = db._query("SELECT * FROM order_groups WHERE id=?", (g1,))[0]
    oid = g1row["sl_order_id"]
    db.set_perm_id(oid, 999, group_id=g1)
    clock[0] = NOW + timedelta(minutes=5)
    db.close_trade_reconciled(t1)
    db.close_trade_reconciled(t3)
    # estado gravado pela migração da 1.0.14: a cobertura inferida incluiu o trade 2, já fechado quando a ordem foi colocada
    for o in (g1row["sl_order_id"], g1row["tp_order_id"]):
        db._execute("INSERT OR IGNORE INTO order_coverage (order_id, group_id, trade_id) VALUES (?,?,?)", (o, g1, t2))
    db.set_kv("migration:coverage", "")
    path = tmp_path / "via114.sqlite3"
    db.copy_to(path)
    upgraded = Database(path)
    engine.db = upgraded
    assert {t["id"] for t in upgraded.trades_for_order(oid, symbol="AAPL", perm_id=999)} == {t1, t3}
    engine._on_fill(None, own_fill(engine, oid, 120, 98, "last-stop", perm_id=999))
    assert [row(upgraded, t)["exit_qty"] for t in (t1, t2, t3)] == [100, 0, 20]
    assert upgraded.get_kv("migration:coverage")
    before = upgraded._query("SELECT COUNT(*) AS n FROM order_coverage")[0]["n"]
    Database(path)  # idempotente: a cobertura válida criada diretamente pelas ordens é preservada
    assert upgraded._query("SELECT COUNT(*) AS n FROM order_coverage")[0]["n"] == before > 0


# ---------------------------------------------------------------- AG03
def test_ag03_repair_restores_the_provisional_label_of_an_open_trade_finalized_by_the_previous_version(tmp_path):
    engine, db, s = make_engine()
    did, tid = entered_decision(db, NOW, entry_price=100)
    engine._on_fill(None, own_fill(engine, 2, 40, 104, "partial-tp"))
    Settler(s, db, lambda *_: 100, bars_between=lambda *_: []).run(NOW + timedelta(hours=2))
    before = db._query("SELECT correct, label_source FROM decisions WHERE id=?", (did,))[0]
    assert db._query("SELECT label_final FROM decisions WHERE id=?", (did,))[0]["label_final"] == 0
    # estado gravado pela 1.0.14: finalização indevida de um trade ainda aberto
    db._execute("UPDATE decisions SET provisional_correct=correct, correct=NULL, label_source='ledger:OUTRO', label_final=1 WHERE id=?", (did,))
    db.set_kv("labels_changed_at", "")
    path = tmp_path / "via114.sqlite3"
    db.copy_to(path)
    upgraded = Database(path)
    d = upgraded._query("SELECT label_final, correct FROM decisions WHERE id=?", (did,))[0]
    assert d["label_final"] == 0 and d["correct"] == before["correct"]  # estado provisório restaurado
    assert [x["id"] for x in upgraded.provisional_decisions()] == [did] and upgraded.get_kv("labels_changed_at")
    Settler(s, upgraded, lambda *_: 100, bars_between=lambda *_: []).run(NOW + timedelta(hours=3))
    assert upgraded._query("SELECT label_final FROM decisions WHERE id=?", (did,))[0]["label_final"] == 0
    engine.db = upgraded
    sl = own_fill(engine, 3, 60, 98, "closing-sl")
    sl.execution.time = NOW + timedelta(minutes=1)
    engine._on_fill(None, sl)
    Settler(s, upgraded, lambda *_: 100, bars_between=lambda *_: []).run(NOW + timedelta(hours=4))
    assert upgraded._query("SELECT label_final,correct,label_source FROM decisions WHERE id=?", (did,))[0] == \
        dict(label_final=1, correct=1, label_source="ledger:TP+SL")
