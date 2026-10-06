"""Regressões da revisão da 1.0.7 (Y01-Y07 / D01-D07): os ensaios do auditor com as asserções invertidas para
o comportamento corrigido, mais casos adjacentes."""
import asyncio
import time
from datetime import timedelta
from types import SimpleNamespace as NS

from trader.calibration import Calibrator
from trader.config import Settings
from trader.database import Database
from trader.indicators import Bar
from trader.settlement import Settler
from tests.test_engine import NOW, own_position
from tests.test_review_103 import ib_order, position, real_client
from tests.test_review_104 import make_engine
from tests.test_review_105 import entered_decision


def setup_position(broker_qty=20):
    engine, db, s = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 100)
    g = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    acct = engine.ibkr.account
    tp = ib_order(g["tp_order_id"], "SELL", 100, "LMT", account=acct, oca="pair")
    sl = ib_order(g["sl_order_id"], "SELL", 100, "STP", account=acct, oca="pair")
    positions = [position(broker_qty, account=acct)] if broker_qty else []
    client = real_client([tp, sl], positions, account=acct)
    client.ib.fills = lambda: []
    client.portfolio_state = lambda: {"positions": [dict(symbol="AAPL", qty=p.position, avg_cost=100, market_price=101,
                                                        sec_type="STK") for p in positions]}
    engine.ibkr = client
    return engine, db, s, gid, tid, tp, sl, positions


def own_fill(engine, oid, qty, price, eid, perm_id=None):
    return NS(contract=NS(symbol="AAPL", secType="STK", conId=engine.ibkr.con_id("AAPL")),
              execution=NS(execId=eid, orderId=oid, side="SLD", shares=qty, price=price, time=NOW,
                           acctNumber=engine.ibkr.account, clientId=engine.settings.ib_client_id,
                           orderRef=engine.settings.order_ref, permId=perm_id),
              commissionReport=NS(commission=1.0))


# ---------------------------------------------------------------- Y01 / D01
def test_d01_restart_cancels_orphan_exits_before_closing_the_ledger():
    engine, db, s, gid, tid, tp, sl, _ = setup_position(0)
    asyncio.run(engine._reconcile())
    assert tp.orderStatus.status == sl.orderStatus.status == "Cancelled"  # saídas confirmadas ANTES de fechar o vínculo
    assert db._query("SELECT status, exit_reason FROM trades WHERE id=?", (tid,))[0] == {"status": "CLOSED", "exit_reason": "RECONCILED"}
    assert engine._reconciled and "AAPL" not in engine._discrepancies


def test_y01_live_own_orders_keep_the_symbol_supervised_without_open_trade():
    engine, db, s, gid, tid, tp, sl, _ = setup_position(0)
    db.close_trade_reconciled(tid)  # estado herdado: trade já fechado, saídas ainda vivas
    assert "AAPL" in engine._managed_symbols()
    asyncio.run(engine._supervise())
    assert tp.orderStatus.status == sl.orderStatus.status == "Cancelled"


def test_y01_unconfirmed_cancellation_keeps_the_conflict_open():
    engine, db, s, gid, tid, tp, sl, _ = setup_position(0)

    async def never(children, timeout=5):
        return False

    engine.ibkr.wait_done = never
    asyncio.run(engine._reconcile())
    assert db._query("SELECT status FROM trades WHERE id=?", (tid,))[0]["status"] == "OPEN"
    assert "AAPL" in engine._discrepancies and "AAPL" in engine._managed_symbols()


# ---------------------------------------------------------------- Y02 / D02
def test_d02_supervisor_keeps_an_unfilled_bracket_until_its_deadline():
    engine, db, s = make_engine()
    acct = engine.ibkr.account
    parent = ib_order(100, "BUY", 100, "LMT", account=acct)
    tp = ib_order(101, "SELL", 100, "LMT", parent_id=100, account=acct)
    sl = ib_order(102, "SELL", 100, "STP", parent_id=100, account=acct)
    client = real_client([parent, tp, sl], [], account=acct)
    client.portfolio_state = lambda: {"positions": []}
    engine.ibkr = client
    gid = db.insert_order_group(symbol="AAPL", decision_id=None, role="ENTRY", direction=1, qty=100, parent_order_id=100,
                                tp_order_id=101, sl_order_id=102, ref_price=100, tp_price=104, sl_price=98, account=acct)
    tid = db.open_trade(symbol="AAPL", decision_id=None, group_id=gid, direction=1, qty=100)
    engine._restore_pending_orders()
    engine._pending_entries[100]["deadline"] = time.monotonic() + 120
    asyncio.run(engine._supervise())
    assert [t.orderStatus.status for t in (parent, tp, sl)] == ["Submitted"] * 3
    assert 100 in engine._pending_entries and "AAPL" not in engine._discrepancies
    # o prazo continua a mandar: expirado, só o pai é cancelado pelo caminho do timeout
    engine._pending_entries[100]["deadline"] = time.monotonic() - 1
    asyncio.run(engine._supervise())
    assert parent.orderStatus.status == "Cancelled" and engine._pending_entries[100]["cancel_sent"]


# ---------------------------------------------------------------- Y03 / D03
def test_d03_late_real_fill_replaces_the_provisional_adjustment():
    engine, db, s, gid, tid, tp, sl, positions = setup_position(20)
    asyncio.run(engine._supervise())
    row = db._query("SELECT exit_qty, status FROM trades WHERE id=?", (tid,))[0]
    assert row == {"exit_qty": 0.0, "status": "OPEN"} and db.open_adjustment_qty(tid) == 80 and engine._own_qty("AAPL") == 20
    engine._on_fill(None, own_fill(engine, sl.order.orderId, 80, 98, "late-real-stop"))
    row = db._query("SELECT status, exit_qty, gross_pnl FROM trades WHERE id=?", (tid,))[0]
    assert row["exit_qty"] == 80 and row["status"] == "OPEN" and row["gross_pnl"] == -160  # só a execução real conta
    assert db.open_adjustment_qty(tid) == 0 and engine._own_qty("AAPL") == 20
    asyncio.run(engine._supervise())
    assert "AAPL" not in engine._discrepancies  # a execução real explicou a diferença: bloqueio levantado


# ---------------------------------------------------------------- Y04 / D04
def test_d04_same_contract_with_different_perm_ids_is_imported():
    dst, src = Database(":memory:"), Database(":memory:")
    for db, perm, age, closed in [(dst, 888, 10, True), (src, 999, 0, False)]:
        gid = db.insert_order_group(symbol="AAPL", decision_id=None, role="ENTRY", direction=1, qty=20, parent_order_id=100,
                                    tp_order_id=101, sl_order_id=102, ref_price=100, tp_price=104, sl_price=98, account="U1", con_id=11)
        db.set_perm_id(100, perm, group_id=gid)
        tid = db.open_trade(symbol="AAPL", decision_id=None, group_id=gid, direction=1, qty=20)
        db.record_entry_fill(tid, 20, 100, NOW - timedelta(days=age))
        if closed:
            db.record_exit_fill(tid, 20, 104, NOW - timedelta(days=9), "TP")
    counts = dst.merge_open_state_from(src)
    assert counts["trades"] == 1 and counts["duplicates"] == 0 and dst.open_trades()
    # identidade permanente igual = mesma ordem (duplicado); desconhecida = conflito assinalado, nunca descarte
    src2 = Database(":memory:")
    gid = src2.insert_order_group(symbol="AAPL", decision_id=None, role="ENTRY", direction=1, qty=20, parent_order_id=100,
                                  tp_order_id=101, sl_order_id=102, ref_price=100, tp_price=104, sl_price=98, account="U1", con_id=11)
    src2.set_perm_id(100, 999, group_id=gid)
    tid = src2.open_trade(symbol="AAPL", decision_id=None, group_id=gid, direction=1, qty=20)
    src2.record_entry_fill(tid, 20, 100, NOW - timedelta(minutes=1))
    assert dst.merge_open_state_from(src2)["duplicates"] == 1
    src3 = Database(":memory:")
    gid = src3.insert_order_group(symbol="AAPL", decision_id=None, role="ENTRY", direction=1, qty=7, parent_order_id=100,
                                  tp_order_id=101, sl_order_id=102, ref_price=100, tp_price=104, sl_price=98, account="U1", con_id=11)
    tid = src3.open_trade(symbol="AAPL", decision_id=None, group_id=gid, direction=1, qty=7)
    src3.record_entry_fill(tid, 7, 100, NOW - timedelta(minutes=2))
    counts = dst.merge_open_state_from(src3)
    assert counts["trades"] == 1 and counts["conflicts"] == 1


# ---------------------------------------------------------------- Y05 / D05
def test_d05_calibrator_and_analytics_ignore_provisional_labels():
    db, s = Database(":memory:"), Settings()
    s.calibration_min_samples = 2
    for j in range(2):
        did, tid = entered_decision(db, NOW, entry_price=100)
        db._execute("UPDATE decisions SET agree_frac=1 WHERE id=?", (did,))
        bars = [Bar(NOW + timedelta(minutes=i), 100, 105 if j == 0 else 101, 99 if j == 0 else 97, 100, 1) for i in range(1, 31)]
        Settler(s, db, lambda *_: 100, bars_between=lambda *_: bars).run(NOW + timedelta(minutes=31))
    assert len(db.provisional_decisions()) == 2 and db.settled_decisions() == []
    assert len(db.settled_decisions(final_only=False)) == 2
    cal = Calibrator(s, db, model_name="m", prompt_version=0)
    assert cal.fit_from_db() is None  # nada de definitivo para aprender
    # finalizar pelo ledger torna-os definitivos e invalida modelos anteriores
    for t in db.open_trades():
        db.record_exit_fill(t["id"], 100, 104, NOW + timedelta(minutes=45), "TP")
    Settler(s, db, lambda *_: 100, bars_between=lambda *_: []).run(NOW + timedelta(minutes=60))
    assert len(db.settled_decisions()) == 2 and db.get_kv("labels_changed_at")
    fitted = cal.fit_from_db()
    assert fitted is None or cal.needs_refit() is False  # resultados iguais (ambos TP): sem ajuste, mas sem provisórios


# ---------------------------------------------------------------- Y06 / D06
def test_d06_direction_reversal_cancels_old_exits_and_keeps_the_block():
    engine, db, s, gid, tid, tp, sl, _ = setup_position(-20)
    asyncio.run(engine._supervise())
    assert tp.orderStatus.status == sl.orderStatus.status == "Cancelled"
    assert engine.ibkr.ib.placed == []  # nenhuma proteção nova para uma posição que o bot não abriu
    assert "AAPL" in engine._discrepancies and engine._own_qty("AAPL") == 0
    assert engine.ibkr.wrong_side_exit_quantity("AAPL") == 0
    asyncio.run(engine._supervise())
    assert "AAPL" in engine._discrepancies  # bloqueio persistente até as execuções explicarem a diferença


# ---------------------------------------------------------------- Y07 / D07
def test_d07_aggregated_exit_is_distributed_across_open_trades():
    engine, db, s = make_engine()
    g1, t1 = own_position(engine, db, "AAPL", 50)
    g2, t2 = own_position(engine, db, "AAPL", 50)
    acct = engine.ibkr.account
    groups = db._query("SELECT * FROM order_groups ORDER BY id")
    trades = [ib_order(g[key], "SELL", 50, typ, account=acct, oca=str(g["id"]))
              for g in groups for key, typ in [("tp_order_id", "LMT"), ("sl_order_id", "STP")]]
    client = real_client(trades, [position(80, account=acct)], account=acct)
    client.portfolio_state = lambda: {"positions": [dict(symbol="AAPL", qty=80, avg_cost=100, market_price=101, sec_type="STK")]}
    engine.ibkr = client
    asyncio.run(engine._supervise())
    assert client.ib.placed[-1].order.totalQuantity == 80
    new_sl = db._query("SELECT sl_order_id FROM order_groups WHERE id=?", (g1,))[0]["sl_order_id"]
    assert db._query("SELECT sl_order_id FROM order_groups WHERE id=?", (g2,))[0]["sl_order_id"] == new_sl  # ligado a ambos
    engine._on_fill(None, own_fill(engine, new_sl, 80, 98, "combined-exit"))
    r1 = db._query("SELECT exit_qty, status FROM trades WHERE id=?", (t1,))[0]
    r2 = db._query("SELECT exit_qty, status FROM trades WHERE id=?", (t2,))[0]
    assert r1["exit_qty"] <= 50 and r2["exit_qty"] <= 50 and r1["exit_qty"] + r2["exit_qty"] == 80
    # a saída NOVA fecha as 80 vivas e não explica as 20 que saíram antes: o ajuste provisório mantém-se (Z05)
    assert db.open_adjustment_qty(t1) + db.open_adjustment_qty(t2) == 20 and engine._own_qty("AAPL") == 0
    # a posição desapareceu na corretora: as 20 restantes do ledger são reconciliadas, sem exit_qty > filled_qty;
    # o resultado fica assinalado como indeterminado até chegar a execução que as explique
    client.portfolio_state = lambda: {"positions": []}
    client.ib.positions = lambda: []
    asyncio.run(engine._supervise())
    rows = db._query("SELECT exit_qty, filled_qty, status FROM trades WHERE id IN (?, ?)", (t1, t2))
    assert all(r["exit_qty"] <= r["filled_qty"] and r["status"] == "CLOSED" for r in rows)
    assert db.get_kv("conflict:AAPL") and "AAPL" not in engine._discrepancies
