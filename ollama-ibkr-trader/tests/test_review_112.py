"""Regressões da revisão da 1.0.12 (AD01-AD05): os ensaios do auditor com as asserções invertidas para o comportamento
corrigido, mais casos adjacentes (prova pela ordem viva, cobertura por ordem no Z05, migração idempotente)."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace as NS

import pytest

from trader.database import Database
from trader.settlement import Settler
from tests.helpers import FakeOrder
from tests.test_engine import NOW, own_position
from tests.test_review_104 import make_engine
from tests.test_review_105 import entered_decision
from tests.test_review_107 import own_fill
from tests.test_review_111 import row, status_event, two_groups


# ---------------------------------------------------------------- AD01
def test_ad01_single_candidate_keeps_its_known_identity_against_an_incompatible_status():
    engine, db, s = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 20)
    oid = db._query("SELECT sl_order_id FROM order_groups WHERE id=?", (gid,))[0]["sl_order_id"]
    db.set_perm_id(oid, 999, group_id=gid)
    engine._on_order_status(status_event(engine, s, oid, 888, qty=20))  # estado de uma ordem antiga, sem história nesta base
    assert db.perm_id_for(gid, oid) == 999
    engine._on_fill(None, own_fill(engine, oid, 20, 98, "foreign-old", perm_id=888))
    assert row(db, tid)["status"] == "OPEN" and row(db, tid)["exit_qty"] == 0
    assert len(db.unallocated_fills("AAPL")) == 1 and "AAPL" in engine._discrepancies  # pendente, nunca consome o trade novo


def test_ad01_inherited_wrong_perm_id_is_repaired_only_with_proof_from_the_live_order():
    engine, db, s = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 20)
    oid = db._query("SELECT sl_order_id FROM order_groups WHERE id=?", (gid,))[0]["sl_order_id"]
    db.set_perm_id(oid, 777, group_id=gid)  # associação herdada errada
    engine.ibkr.open_orders_all = [NS(order=NS(orderId=oid, permId=888, parentId=0), contract=NS(symbol="AAPL"),
                                      orderStatus=NS(status="Submitted"))]  # a ordem VIVA na corretora tem permId 888
    engine._on_order_status(status_event(engine, s, oid, 888, qty=20))
    assert db.perm_id_for(gid, oid) == 888


# ---------------------------------------------------------------- AD02
@pytest.mark.parametrize("path", ["resize", "vanished"])
def test_ad02_every_supervision_path_keeps_the_block_while_a_fill_is_pending(path):
    engine, db, s, g1, t1, g2, t2, oid = two_groups()
    db.close_trade_reconciled(t1)
    engine._on_fill(None, own_fill(engine, oid, 20, 98, "ambiguous", perm_id=777))
    assert db.unallocated_fills("AAPL") and "AAPL" in engine._discrepancies
    if path == "resize":
        engine.ibkr.orders["AAPL"] = [FakeOrder(oid, "SELL", 100, "STP")]
    else:
        engine.ibkr.positions["AAPL"] = 0
    for _ in range(2):
        asyncio.run(engine._supervise())
        assert db.unallocated_fills("AAPL") and not db.allocations_for_fill("ambiguous")
        assert "AAPL" in engine._discrepancies


# ---------------------------------------------------------------- AD03
def test_ad03_new_aggregated_protection_never_extends_the_coverage_of_old_orders():
    engine, db, s = make_engine()
    g1, t1 = own_position(engine, db, "AAPL", 100)
    old_sl = db._query("SELECT sl_order_id FROM order_groups WHERE id=?", (g1,))[0]["sl_order_id"]
    db.set_perm_id(old_sl, 888, group_id=g1)
    g2, t2 = own_position(engine, db, "AAPL", 20)
    engine.ibkr.positions["AAPL"] = 120
    asyncio.run(engine._protect_if_naked("AAPL", dict(symbol="AAPL", qty=120, avg_cost=100, market_price=101)))
    new_sl = db._query("SELECT sl_order_id FROM order_groups WHERE id=?", (g1,))[0]["sl_order_id"]
    assert new_sl != old_sl
    assert {t["id"] for t in db.trades_for_order(new_sl, symbol="AAPL")} == {t1, t2}  # a proteção nova cobre os dois
    assert [t["id"] for t in db.trades_for_order(old_sl, symbol="AAPL")] == [t1]  # a ordem antiga só cobre o seu trade
    db.close_trade_reconciled(t1)
    engine._on_fill(None, own_fill(engine, old_sl, 100, 98, "old-sl", perm_id=888))
    assert row(db, t2)["exit_qty"] == 0 and row(db, t1)["exit_qty"] == 100


def test_ad03_adjustment_evidence_follows_the_order_level_coverage():
    engine, db, s = make_engine()
    g1, t1 = own_position(engine, db, "AAPL", 100)
    g2, t2 = own_position(engine, db, "AAPL", 20)
    engine.ibkr.positions["AAPL"] = 120
    asyncio.run(engine._protect_if_naked("AAPL", dict(symbol="AAPL", qty=120, avg_cost=100, market_price=101)))
    g1row = db._query("SELECT * FROM order_groups WHERE id=?", (g1,))[0]
    ids_t2 = db.exit_order_ids_for_trade(t2)
    assert g1row["sl_order_id"] in ids_t2 and g1row["tp_order_id"] in ids_t2
    old = db._query("SELECT order_id FROM order_history WHERE group_id=? AND replaced_ts IS NOT NULL", (g1,))
    assert old and all(int(o["order_id"]) not in ids_t2 for o in old)  # as ordens antigas do grupo 1 não cobrem o trade 2


# ---------------------------------------------------------------- AD04
def test_ad04_migration_recomputes_closed_summaries_and_final_labels(tmp_path):
    engine, db, s = make_engine()
    did, tid = entered_decision(db, NOW, entry_price=100)
    engine._on_fill(None, own_fill(engine, 2, 40, 104, "old-tp"))
    db._execute("UPDATE fill_allocations SET reason=NULL")  # alocação legada (sem razão)
    sl = own_fill(engine, 3, 60, 98, "last-sl")
    sl.execution.time = NOW + timedelta(minutes=1)
    engine._on_fill(None, sl)
    Settler(s, db, lambda *_: 100, bars_between=lambda *_: []).run(NOW + timedelta(hours=2))
    before = db._query("SELECT correct,label_source FROM decisions WHERE id=?", (did,))[0]
    assert before["correct"] is None and "?" in before["label_source"]  # resumo incompleto, censurado
    db.set_kv("labels_changed_at", "")
    db._execute("ALTER TABLE fill_allocations DROP COLUMN reason")  # esquema anterior
    path = tmp_path / "legacy.sqlite3"
    db.copy_to(path)
    upgraded = Database(path)  # migração: razão, resumo e rótulo recalculados sem nova execução
    r = upgraded._query("SELECT * FROM trades WHERE id=?", (tid,))[0]
    assert r["exit_reason"] == "TP+SL" and r["exit_price"] == pytest.approx(100.4) and r["gross_pnl"] == 40
    d = upgraded._query("SELECT correct,label_source,label_final FROM decisions WHERE id=?", (did,))[0]
    assert d == dict(correct=1, label_source="ledger:TP+SL", label_final=1) and upgraded.get_kv("labels_changed_at")
    Settler(s, upgraded, lambda *_: 100, bars_between=lambda *_: []).run(NOW + timedelta(hours=3))
    assert upgraded._query("SELECT correct FROM decisions WHERE id=?", (did,))[0]["correct"] == 1
    again = Database(path)  # idempotente
    assert again._query("SELECT exit_reason FROM trades WHERE id=?", (tid,))[0]["exit_reason"] == "TP+SL"


# ---------------------------------------------------------------- AD05
def test_ad05_a_later_tie_does_not_erase_a_proven_first_touch():
    engine, db, s = make_engine()
    did, tid = entered_decision(db, NOW, entry_price=100)
    for oid, qty, price, eid, minute in [(2, 20, 104, "first-tp", 0), (2, 40, 104, "later-tp", 1), (3, 40, 98, "later-sl", 1)]:
        f = own_fill(engine, oid, qty, price, eid)
        f.execution.time = NOW + timedelta(minutes=minute)
        engine._on_fill(None, f)
    Settler(s, db, lambda *_: 100, bars_between=lambda *_: []).run(NOW + timedelta(hours=2))
    assert row(db, tid)["exit_reason"] == "TP+SL" and row(db, tid)["status"] == "CLOSED"
    assert db._query("SELECT correct,label_source FROM decisions WHERE id=?", (did,))[0] == dict(correct=1, label_source="ledger:TP+SL")


def test_ad05_a_tie_at_the_first_instant_is_still_indeterminate():
    engine, db, s = make_engine()
    did, tid = entered_decision(db, NOW, entry_price=100)
    for oid, qty, price, eid, minute in [(3, 40, 98, "sl-first", 0), (2, 40, 104, "tp-first", 0), (2, 20, 104, "tp-later", 1)]:
        f = own_fill(engine, oid, qty, price, eid)
        f.execution.time = NOW + timedelta(minutes=minute)
        engine._on_fill(None, f)
    Settler(s, db, lambda *_: 100, bars_between=lambda *_: []).run(NOW + timedelta(hours=2))
    assert row(db, tid)["exit_reason"] == "SL|TP"
    assert db._query("SELECT correct FROM decisions WHERE id=?", (did,))[0]["correct"] is None
