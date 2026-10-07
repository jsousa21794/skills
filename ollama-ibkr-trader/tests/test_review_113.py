"""Regressões da revisão da 1.0.13 (AE01-AE04): os ensaios do auditor com as asserções invertidas para o comportamento
corrigido, mais casos adjacentes (cancelamento legítimo continua a funcionar; migração idempotente)."""
import asyncio
from datetime import timedelta

import pytest

from trader.database import Database
from trader.settlement import Settler
from tests.test_engine import NOW, _FixedDatetime, outcome, own_position, run_exec
from tests.test_review_103 import ib_order
from tests.test_review_104 import make_engine
from tests.test_review_105 import entered_decision
from tests.test_review_107 import own_fill
from tests.test_review_111 import row


def status_for(engine, s, oid, perm_id, status, qty=20, action="BUY", typ="LMT"):
    ev = ib_order(oid, action, qty, typ, account=engine.ibkr.account, status=status)
    ev.order.permId = perm_id
    ev.order.clientId = s.ib_client_id
    ev.contract.conId = engine.ibkr.con_id("AAPL")
    return ev


def pending_entry(engine, db, s):
    decision = run_exec(engine, db, outcome("BUY"))
    assert decision["executed"] == 1 and len(engine._pending_entries) == 1
    oid, pending = next(iter(engine._pending_entries.items()))
    gid = db.group_by_parent(oid)["id"]
    db.set_perm_id(oid, 999, group_id=gid)
    live = status_for(engine, s, oid, 999, "Submitted", qty=db.group_by_parent(oid)["qty"])
    engine.ibkr.open_orders_all = [live]
    return oid, pending, gid, live


# ---------------------------------------------------------------- AE01
def test_ae01_incompatible_cancel_never_touches_a_live_pending_entry(monkeypatch):
    engine, db, s = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    oid, pending, gid, live = pending_entry(engine, db, s)
    tid, before = pending["trade_id"], pending["notional"]
    engine._on_order_status(status_for(engine, s, oid, 888, "Cancelled"))  # estado antigo, identidade incompatível
    assert db.perm_id_for(gid, oid) == 999 and live.orderStatus.status == "Submitted"
    assert oid in engine._pending_entries and row(db, tid)["status"] == "OPEN"
    assert sum(e["notional"] for e in engine._pending_entries.values()) == before > 0


def test_ae01_matching_cancel_still_releases_the_entry_and_its_reservation(monkeypatch):
    engine, db, s = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    oid, pending, gid, live = pending_entry(engine, db, s)
    tid = pending["trade_id"]
    engine._on_order_status(status_for(engine, s, oid, 999, "Cancelled"))
    assert oid not in engine._pending_entries and row(db, tid)["status"] == "CANCELLED"
    assert sum(e["notional"] for e in engine._pending_entries.values()) == 0


# ---------------------------------------------------------------- AE02
def test_ae02_order_coverage_is_restricted_to_the_validated_group():
    engine, db, s = make_engine()
    g1, t1 = own_position(engine, db, "AAPL", 100)
    oid = db._query("SELECT sl_order_id FROM order_groups WHERE id=?", (g1,))[0]["sl_order_id"]
    db.set_perm_id(oid, 888, group_id=g1)
    db.close_trade_reconciled(t1)
    g2, t2 = own_position(engine, db, "AAPL", 20)
    db.update_group_orders(g2, sl_order_id=oid)
    db.set_perm_id(oid, 999, group_id=g2, overwrite=True)
    db.add_exit_coverage(g2, [t2], order_ids=[oid])
    assert db.group_for_order(oid, symbol="AAPL", perm_id=888)["id"] == g1
    assert {t["id"] for t in db.trades_for_order(oid, symbol="AAPL", perm_id=888, group_id=g1)} == {t1}
    assert {t["id"] for t in db.trades_for_order(oid, symbol="AAPL", perm_id=999, group_id=g2)} == {t2}
    engine._on_fill(None, own_fill(engine, oid, 100, 98, "old-exit", perm_id=888))
    assert row(db, t2)["exit_qty"] == 0 and row(db, t1)["exit_qty"] == 100


# ---------------------------------------------------------------- AE03
def test_ae03_legacy_group_coverage_is_migrated_to_the_orders_that_could_cover_each_trade(tmp_path):
    engine, db, s = make_engine()
    g1, t1 = own_position(engine, db, "AAPL", 100)
    old_sl = db._query("SELECT sl_order_id FROM order_groups WHERE id=?", (g1,))[0]["sl_order_id"]
    db.set_perm_id(old_sl, 888, group_id=g1)
    g2, t2 = own_position(engine, db, "AAPL", 20)
    engine.ibkr.positions["AAPL"] = 120
    asyncio.run(engine._protect_if_naked("AAPL", dict(symbol="AAPL", qty=120, avg_cost=100, market_price=101)))
    g1row = db._query("SELECT * FROM order_groups WHERE id=?", (g1,))[0]
    # estado gravado pela 1.0.12: cobertura AMPLA por grupo, sem cobertura por ordem
    db._execute("DELETE FROM order_coverage")
    db._execute("INSERT INTO exit_coverage (group_id, trade_id) VALUES (?,?)", (g1, t1))
    db._execute("INSERT INTO exit_coverage (group_id, trade_id) VALUES (?,?)", (g1, t2))
    db.close_trade_reconciled(t1)
    path = tmp_path / "coverage112.sqlite3"
    db.copy_to(path)
    upgraded = Database(path)
    engine.db = upgraded
    migrated = {(r["order_id"], r["trade_id"]) for r in upgraded._query("SELECT order_id, trade_id FROM order_coverage")}
    assert (g1row["sl_order_id"], t2) in migrated and (g1row["tp_order_id"], t2) in migrated  # o par agregado cobria t2
    assert (old_sl, t2) not in migrated  # a ordem antiga nunca cobriu o trade novo
    assert upgraded._query("SELECT COUNT(*) AS n FROM exit_coverage")[0]["n"] == 0
    engine._on_fill(None, own_fill(engine, old_sl, 100, 98, "old-after-upgrade", perm_id=888))
    assert row(upgraded, t2)["exit_qty"] == 0 and row(upgraded, t1)["exit_qty"] == 100
    Database(path)  # idempotente
    assert upgraded._query("SELECT COUNT(*) AS n FROM order_coverage")[0]["n"] == len(migrated)


# ---------------------------------------------------------------- AE04
def test_ae04_versioned_migration_repairs_summaries_left_stale_by_the_previous_version(tmp_path):
    engine, db, s = make_engine()
    did, tid = entered_decision(db, NOW, entry_price=100)
    engine._on_fill(None, own_fill(engine, 2, 40, 104, "old-tp"))
    sl = own_fill(engine, 3, 60, 98, "last-sl")
    sl.execution.time = NOW + timedelta(minutes=1)
    engine._on_fill(None, sl)
    Settler(s, db, lambda *_: 100, bars_between=lambda *_: []).run(NOW + timedelta(hours=2))
    # estado deixado pela 1.0.12: razões já preenchidas, mas resumo e rótulo final desatualizados
    db._execute("UPDATE trades SET exit_reason='SL', exit_price=98 WHERE id=?", (tid,))
    db._execute("UPDATE decisions SET correct=0, label_source='ledger:SL', label_final=1 WHERE id=?", (did,))
    db.set_kv("labels_changed_at", "")
    db.set_kv("migration:summaries", "")
    path = tmp_path / "via112.sqlite3"
    db.copy_to(path)
    upgraded = Database(path)
    r = upgraded._query("SELECT exit_reason, exit_price FROM trades WHERE id=?", (tid,))[0]
    assert r["exit_reason"] == "TP+SL" and r["exit_price"] == pytest.approx(100.4)
    d = upgraded._query("SELECT correct, label_source FROM decisions WHERE id=?", (did,))[0]
    assert d == dict(correct=1, label_source="ledger:TP+SL") and upgraded.get_kv("labels_changed_at")
    Settler(s, upgraded, lambda *_: 100, bars_between=lambda *_: []).run(NOW + timedelta(hours=3))
    assert upgraded._query("SELECT correct FROM decisions WHERE id=?", (did,))[0]["correct"] == 1
    assert upgraded.get_kv("migration:summaries")  # identificada por versão: não volta a correr
