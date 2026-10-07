"""Regressões da revisão da 1.0.16 (AH01-AH02): os ensaios do auditor com as asserções invertidas para o comportamento
corrigido, com relógio controlado (resolução limitada) para serem determinísticos em qualquer plataforma."""
import asyncio
from datetime import timedelta

import pytest

import trader.database as dbmod
from trader.database import Database
from tests.test_engine import NOW, own_position
from tests.test_review_104 import make_engine
from tests.test_review_107 import own_fill
from tests.test_review_111 import row


@pytest.fixture
def clock(monkeypatch):
    state = [NOW]
    monkeypatch.setattr(dbmod, "utc_now", lambda: state[0])
    return state


# ---------------------------------------------------------------- AH01
@pytest.mark.parametrize("delta_us", [0, 1])
def test_ah01_explicit_coverage_survives_equal_clock_timestamps(tmp_path, clock, delta_us):
    engine, db, s = make_engine()
    g1, t1 = own_position(engine, db, "AAPL", 100)
    g2, t2 = own_position(engine, db, "AAPL", 20)
    engine.ibkr.positions["AAPL"] = 120
    clock[0] = NOW + timedelta(minutes=1)
    asyncio.run(engine._protect_if_naked("AAPL", dict(symbol="AAPL", qty=120, avg_cost=100, market_price=101)))
    oid = db._query("SELECT sl_order_id FROM order_groups WHERE id=?", (g1,))[0]["sl_order_id"]
    db.set_perm_id(oid, 999, group_id=g1)
    clock[0] += timedelta(microseconds=delta_us)  # colocação e reconciliação no MESMO tick do relógio quando delta=0
    db.close_trade_reconciled(t1)
    db.close_trade_reconciled(t2)
    assert {t["id"] for t in db.trades_for_order(oid, symbol="AAPL", perm_id=999)} == {t1, t2}
    db.set_kv("migration:coverage", "")
    path = tmp_path / "native.sqlite3"
    db.copy_to(path)
    upgraded = Database(path)
    engine.db = upgraded
    assert {t["id"] for t in upgraded.trades_for_order(oid, symbol="AAPL", perm_id=999)} == {t1, t2}  # prova explícita preservada
    f = own_fill(engine, oid, 120, 98, "native-stop", perm_id=999)
    f.execution.time = NOW + timedelta(minutes=1)
    engine._on_fill(None, f)
    assert [row(upgraded, t)["exit_qty"] for t in (t1, t2)] == [100, 20] and upgraded.allocated_shares("native-stop") == 120


def test_ah01_legacy_inferred_coverage_with_equal_timestamps_is_not_proven(tmp_path, clock):
    engine, db, s = make_engine()
    g1, t1 = own_position(engine, db, "AAPL", 100)
    g2, t2 = own_position(engine, db, "AAPL", 20)
    engine.ibkr.positions["AAPL"] = 120
    clock[0] = NOW + timedelta(minutes=1)
    db.close_trade_reconciled(t2)  # fecho e colocação no mesmo tick: a cobertura INFERIDA (legado) não fica provada
    asyncio.run(engine._protect_if_naked("AAPL", dict(symbol="AAPL", qty=120, avg_cost=100, market_price=101)))
    g1row = db._query("SELECT * FROM order_groups WHERE id=?", (g1,))[0]
    db._execute("DELETE FROM order_coverage")
    db._execute("INSERT INTO exit_coverage (group_id, trade_id) VALUES (?,?)", (g1, t2))
    path = tmp_path / "legacy.sqlite3"
    db.copy_to(path)
    upgraded = Database(path)
    assert upgraded._query("SELECT COUNT(*) AS n FROM order_coverage WHERE trade_id=?", (t2,))[0]["n"] == 0
    assert [t["id"] for t in upgraded.trades_for_order(g1row["sl_order_id"], symbol="AAPL")] == [t1]


# ---------------------------------------------------------------- AH02
def test_ah02_repair_reverses_allocations_that_depended_on_invalidated_coverage(tmp_path, clock):
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
    # relação inferida pela migração da 1.0.14 (sem proveniência) e execução já MAL atribuída pela 1.0.15
    for o in (g1row["sl_order_id"], g1row["tp_order_id"]):
        db._execute("INSERT OR IGNORE INTO order_coverage (order_id, group_id, trade_id) VALUES (?,?,?)", (o, g1, t2))
    f = own_fill(engine, oid, 120, 98, "already-wrong-stop", perm_id=999)
    f.execution.time = NOW + timedelta(minutes=6)
    engine._on_fill(None, f)
    assert [row(db, t)["exit_qty"] for t in (t1, t2, t3)] == [100, 20, 0]
    db.set_kv("migration:coverage", "")
    path = tmp_path / "wrong115.sqlite3"
    db.copy_to(path)
    upgraded = Database(path)
    engine.db = upgraded
    assert {t["id"] for t in upgraded.trades_for_order(oid, symbol="AAPL", perm_id=999)} == {t1, t3}
    # a alocação que dependia da cobertura inválida foi revertida: o trade 2 volta ao estado reconciliado
    assert [(a["trade_id"], a["shares"]) for a in upgraded.allocations_for_fill("already-wrong-stop")] == [(t1, 100)]
    r2 = row(upgraded, t2)
    assert r2["exit_qty"] == 0 and r2["gross_pnl"] == 0 and r2["status"] == "CLOSED" and r2["exit_reason"] == "RECONCILED"
    pending = upgraded.unallocated_fills("AAPL")
    assert len(pending) == 1 and pending[0]["remaining"] == 20
    # a reconciliação de arranque recupera o saldo com a cobertura reparada
    engine._reconcile_unallocated_fills("AAPL")
    assert [row(upgraded, t)["exit_qty"] for t in (t1, t2, t3)] == [100, 0, 20]
    assert upgraded.unallocated_fills("AAPL") == [] and upgraded.allocated_shares("already-wrong-stop") == 120
