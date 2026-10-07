"""Regressões da revisão da 1.0.11 (AC01-AC06): os ensaios do auditor com as asserções invertidas para o comportamento
corrigido, mais casos adjacentes (identidade revelada em grupo sem permId, bloqueio após reinício, contadores sem duplicação)."""
import asyncio
from datetime import timedelta

import pytest

from trader.database import Database
from trader.ollama_brain import OllamaBrain
from trader.settlement import Settler
from trader.trading_engine import TradingEngine
from trader.ui_bus import UIBus
from tests.test_engine import NOW, own_position
from tests.test_review_103 import ib_order
from tests.test_review_104 import make_engine
from tests.test_review_105 import entered_decision
from tests.test_review_107 import own_fill


def row(db, tid):
    return db._query("SELECT * FROM trades WHERE id=?", (tid,))[0]


def two_groups():
    engine, db, s = make_engine()
    g1, t1 = own_position(engine, db, "AAPL", 100)
    oid = db._query("SELECT sl_order_id FROM order_groups WHERE id=?", (g1,))[0]["sl_order_id"]
    g2, t2 = own_position(engine, db, "AAPL", 20)
    db.update_group_orders(g2, sl_order_id=oid)
    return engine, db, s, g1, t1, g2, t2, oid


def status_event(engine, s, oid, perm_id, qty=100):
    status = ib_order(oid, "SELL", qty, "STP", account=engine.ibkr.account, status="Filled", filled=qty)
    status.order.permId = perm_id
    status.order.clientId = s.ib_client_id
    status.contract.conId = engine.ibkr.con_id("AAPL")
    return status


# ---------------------------------------------------------------- AC01
def test_ac01_late_order_status_never_overwrites_a_known_different_perm_id():
    engine, db, s, g1, t1, g2, t2, oid = two_groups()
    db.set_perm_id(oid, 888, group_id=g1)
    db.set_perm_id(oid, 999, group_id=g2, overwrite=True)
    db.close_trade_reconciled(t1)
    engine._on_order_status(status_event(engine, s, oid, 888))
    assert db.perm_id_for(g2, oid) == 999 and db.perm_id_for(g1, oid) == 888  # identidades intactas
    engine._on_fill(None, own_fill(engine, oid, 100, 98, "old-exit", perm_id=888))
    assert row(db, t2)["exit_qty"] == 0 and row(db, t2)["status"] == "OPEN"
    assert row(db, t1)["exit_qty"] == 100


def test_ac01_order_status_assigns_the_perm_id_to_the_group_without_identity():
    engine, db, s, g1, t1, g2, t2, oid = two_groups()
    db.set_perm_id(oid, 888, group_id=g1)
    assert db.perm_id_for(g2, oid) is None
    engine._on_order_status(status_event(engine, s, oid, 999, qty=20))  # estado da ordem NOVA
    assert db.perm_id_for(g2, oid) == 999 and db.perm_id_for(g1, oid) == 888


# ---------------------------------------------------------------- AC02
def test_ac02_block_survives_supervision_cycles_and_restart_until_the_identity_is_revealed():
    engine, db, s, g1, t1, g2, t2, oid = two_groups()
    db.close_trade_reconciled(t1)
    engine._on_fill(None, own_fill(engine, oid, 20, 98, "ambiguous", perm_id=777))
    assert "AAPL" in engine._discrepancies and db.unallocated_fills("AAPL")
    pos = dict(symbol="AAPL", qty=20, avg_cost=100, market_price=101)
    for _ in range(3):
        asyncio.run(engine._supervise_symbol("AAPL", pos))
        assert "AAPL" in engine._discrepancies and db.allocations_for_fill("ambiguous") == []
    fresh = TradingEngine(s, db, UIBus(), OllamaBrain(s, db))  # reinício
    fresh.ibkr = engine.ibkr
    fresh._load_conflicts()
    assert "AAPL" in fresh._discrepancies
    # a corretora revela o permId da ordem viva (grupo novo): a execução pendente é reconciliada e o bloqueio sai
    engine._on_order_status(status_event(engine, s, oid, 777, qty=20))
    assert db.perm_id_for(g2, oid) == 777 and db.unallocated_fills("AAPL") == []
    assert row(db, t2)["exit_qty"] == 20 and row(db, t1)["exit_qty"] == 0
    asyncio.run(engine._supervise_symbol("AAPL", dict(symbol="AAPL", qty=0, avg_cost=100, market_price=101)))
    assert "AAPL" not in engine._discrepancies


# ---------------------------------------------------------------- AC03
def test_ac03_exit_received_before_any_entry_waits_for_the_proven_entry_cost():
    engine, db, s = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 100)
    db._execute("UPDATE trades SET filled_qty=0,entry_price=NULL,entry_ts=NULL WHERE id=?", (tid,))
    g = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    engine._on_fill(None, own_fill(engine, g["sl_order_id"], 100, 98, "exit-first"))
    assert db.allocations_for_fill("exit-first") == [] and len(db.unallocated_fills("AAPL")) == 1
    assert row(db, tid)["exit_qty"] == 0 and row(db, tid)["status"] == "OPEN" and "AAPL" in engine._discrepancies
    entry = own_fill(engine, g["parent_order_id"], 100, 100, "entry-last")
    entry.execution.side = "BOT"
    entry.execution.time = NOW - timedelta(minutes=1)
    engine._on_fill(None, entry)  # custo de entrada comprovado: a saída pendente é contabilizada com ele
    r = row(db, tid)
    assert r["filled_qty"] == r["exit_qty"] == 100 and r["status"] == "CLOSED"
    assert r["entry_price"] == 100 and r["gross_pnl"] == pytest.approx(-200)
    assert db.unallocated_fills("AAPL") == [] and "AAPL" not in engine._discrepancies


# ---------------------------------------------------------------- AC04
def test_ac04_migration_backfills_the_reason_of_old_allocations(tmp_path):
    engine, db, s = make_engine()
    did, tid = entered_decision(db, NOW, entry_price=100)
    engine._on_fill(None, own_fill(engine, 2, 40, 104, "old-tp"))
    db._execute("ALTER TABLE fill_allocations DROP COLUMN reason")  # esquema anterior a 1.0.11
    path = tmp_path / "legacy.sqlite3"
    db.copy_to(path)
    upgraded = Database(path)
    engine.db = upgraded
    assert upgraded.allocations_for_fill("old-tp")[0]["reason"] == "TP"  # pela identidade histórica da ordem
    sl = own_fill(engine, 3, 60, 98, "new-sl")
    sl.execution.time = NOW + timedelta(minutes=1)
    engine._on_fill(None, sl)
    r = row(upgraded, tid)
    assert r["exit_qty"] == 100 and r["gross_pnl"] == 40
    assert r["exit_reason"] == "TP+SL" and r["exit_price"] == pytest.approx(100.4)
    Settler(s, upgraded, lambda *_: 100, bars_between=lambda *_: []).run(NOW + timedelta(hours=2))
    assert upgraded._query("SELECT correct,label_source FROM decisions WHERE id=?", (did,))[0] == dict(correct=1, label_source="ledger:TP+SL")


def test_ac04_entry_allocations_keep_a_null_reason_after_migration(tmp_path):
    engine, db, s = make_engine()
    did, tid = entered_decision(db, NOW, entry_price=100)
    db._execute("UPDATE trades SET filled_qty=0, entry_price=NULL WHERE id=?", (tid,))
    entry = own_fill(engine, 1, 100, 100, "entry")
    entry.execution.side = "BOT"
    engine._on_fill(None, entry)
    db._execute("ALTER TABLE fill_allocations DROP COLUMN reason")
    path = tmp_path / "legacy.sqlite3"
    db.copy_to(path)
    upgraded = Database(path)
    assert upgraded.allocations_for_fill("entry")[0]["reason"] is None


# ---------------------------------------------------------------- AC05
@pytest.mark.parametrize("reverse", [False, True])
def test_ac05_equal_execution_timestamps_give_an_indeterminate_censored_label(reverse):
    engine, db, s = make_engine()
    did, tid = entered_decision(db, NOW, entry_price=100)
    tp = own_fill(engine, 2, 40, 104, "tp40")
    sl = own_fill(engine, 3, 60, 98, "sl60")
    assert tp.execution.time == sl.execution.time
    for event in ([sl, tp] if reverse else [tp, sl]):
        engine._on_fill(None, event)
    Settler(s, db, lambda *_: 100, bars_between=lambda *_: []).run(NOW + timedelta(hours=2))
    r = row(db, tid)
    assert r["exit_reason"] == "SL|TP" and r["gross_pnl"] == 40 and r["status"] == "CLOSED"  # sequência não comprovada
    d = db._query("SELECT correct,label_source,label_final FROM decisions WHERE id=?", (did,))[0]
    assert d == dict(correct=None, label_source="ledger:SL|TP", label_final=1)


# ---------------------------------------------------------------- AC06
def test_ac06_mixed_exits_count_once_for_stops_and_once_for_take_profits():
    engine, db, s = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 100)
    g = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    sl = own_fill(engine, g["sl_order_id"], 80, 98, "sl80")
    tp = own_fill(engine, g["tp_order_id"], 20, 104, "tp20")
    tp.execution.time = NOW + timedelta(minutes=1)
    engine._on_fill(None, sl)
    engine._on_fill(None, tp)
    assert row(db, tid)["exit_reason"] == "SL+TP" and row(db, tid)["gross_pnl"] == -80
    assert db.recent_stop_count(NOW - timedelta(hours=1)) == 1
    summary = db.pnl_summary(NOW - timedelta(hours=1))
    assert summary["stop_hits"] == 1 and summary["tp_hits"] == 1 and summary["closed_trades"] == 1


def test_ac06_pure_and_indeterminate_exits_are_counted_by_their_components():
    engine, db, s = make_engine()
    g1, t1 = own_position(engine, db, "AAPL", 10)
    g2, t2 = own_position(engine, db, "AAPL", 10)
    a = db._query("SELECT * FROM order_groups WHERE id=?", (g1,))[0]
    b = db._query("SELECT * FROM order_groups WHERE id=?", (g2,))[0]
    engine._on_fill(None, own_fill(engine, a["tp_order_id"], 10, 104, "tp-only"))
    engine._on_fill(None, own_fill(engine, b["sl_order_id"], 5, 98, "half-sl"))
    engine._on_fill(None, own_fill(engine, b["tp_order_id"], 5, 104, "half-tp"))  # mesmo instante: SL|TP
    assert row(db, t1)["exit_reason"] == "TP" and row(db, t2)["exit_reason"] == "SL|TP"
    assert db.recent_stop_count(NOW - timedelta(hours=1)) == 1
    summary = db.pnl_summary(NOW - timedelta(hours=1))
    assert summary["stop_hits"] == 1 and summary["tp_hits"] == 2
