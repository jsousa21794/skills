"""Regressões da revisão da 1.0.8 (Z01-Z08 / E01-E08): os ensaios do auditor com as asserções invertidas para o
comportamento corrigido, mais casos adjacentes (reinício, pausa entre validação e envio, fills tardios)."""
import asyncio
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone

import pytest

from trader.calibration import Calibrator, ConfidenceSignals, PlattModel
from trader.config import Settings
from trader.database import Database
from trader.mcp_server import RemoteSupervisor, mask_account
from trader.ollama_brain import OllamaBrain
from trader.trading_engine import TradingEngine
from trader.ui_bus import UIBus
from tests.test_engine import _FixedDatetime, outcome, own_position, run_exec
from tests.test_review_104 import make_engine
from tests.test_review_107 import own_fill, setup_position


def trade_row(db, tid, cols="status,exit_qty,gross_pnl"):
    return db._query(f"SELECT {cols} FROM trades WHERE id=?", (tid,))[0]


# ---------------------------------------------------------------- Z01 / E01
def test_e01_reversal_is_never_reprotected_on_later_supervisor_passes():
    engine, db, s, gid, tid, tp, sl, _ = setup_position(-20)
    asyncio.run(engine._supervise())
    assert engine.ibkr.ib.placed == [] and engine._own_qty("AAPL") == 0
    for _ in range(3):
        asyncio.run(engine._supervise())
    assert engine.ibkr.ib.placed == []  # nenhuma ordem sobre a posição externa invertida
    assert s.manage_external_positions is False and "AAPL" in engine._discrepancies
    assert db._query("SELECT stop_price,tp_price,direction FROM trades WHERE id=?", (tid,))[0] == \
        {"stop_price": 98.0, "tp_price": 104.0, "direction": 1}
    assert db.get_kv("conflict:AAPL")  # natureza do conflito persistida


def test_z01_conflict_survives_a_restart_and_blocks_protection_and_decisions():
    engine, db, s, gid, tid, tp, sl, positions = setup_position(-20)
    asyncio.run(engine._supervise())
    # reinício: novo motor sobre a mesma base de dados e a mesma corretora
    fresh = TradingEngine(s, db, UIBus(), OllamaBrain(s, db))
    fresh.ibkr = engine.ibkr
    fresh.trading_enabled = True
    asyncio.run(fresh._reconcile())
    assert fresh._reconciled and engine.ibkr.ib.placed == [] and "AAPL" in fresh._discrepancies
    asyncio.run(fresh._supervise())
    assert engine.ibkr.ib.placed == [] and fresh._own_qty("AAPL") == 0
    assert db.get_kv("conflict:AAPL") and "AAPL" in fresh._discrepancies  # decisões continuam bloqueadas


def test_z01_reduced_to_zero_by_adjustment_is_treated_as_external_not_own():
    engine, db, s, gid, tid, tp, sl, positions = setup_position(20)
    asyncio.run(engine._supervise())  # redução 100 -> 20: ajuste 80, saídas redimensionadas
    db.add_adjustment(tid, 20, 101.0, "teste: tudo explicado por ajustes")  # a quantidade própria passa a zero
    assert engine._own_qty("AAPL") == 0
    before = len(engine.ibkr.ib.placed)
    asyncio.run(engine._supervise())
    assert len(engine.ibkr.ib.placed) == before and "AAPL" in engine._discrepancies
    assert engine.ibkr.own_exit_quantity("AAPL") == 0  # saídas próprias canceladas: posição não é do bot


# ---------------------------------------------------------------- Z02 / E02
def test_e02_pause_during_qualification_revokes_the_entry(monkeypatch):
    engine, db, s = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)

    async def pause_while_qualifying():
        result = await engine.pause_entries("pausa durante qualificação", actor="audit")
        assert result["paused"] and db.get_kv("entries_paused") == "1"

    engine.ibkr.slow_qualify = pause_while_qualifying
    row = run_exec(engine, db, outcome("BUY"))
    assert row["executed"] == 0 and engine.entries_paused and engine.ibkr.brackets == [] and engine.ibkr.refused == 1
    assert "pausa" in row["skip_reason"] or "autorização" in row["skip_reason"]


def test_z02_pause_does_not_revoke_a_close_in_preparation(monkeypatch):
    engine, db, s = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    own_position(engine, db, "AAPL", 100)

    async def pause_while_cancelling():
        await engine.pause_entries("pausa durante o fecho", actor="audit")

    engine.ibkr.slow_cancel = pause_while_cancelling
    row = run_exec(engine, db, outcome("SELL"), position=100)
    assert row["executed"] == 1 and engine.ibkr.closes == ["AAPL"]


# ---------------------------------------------------------------- Z03 / E03
def test_e03_pause_new_entries_keeps_signal_exits(monkeypatch):
    engine, db, s = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    own_position(engine, db, "AAPL", 100)
    asyncio.run(engine.pause_entries("suspender apenas entradas", actor="audit"))
    row = run_exec(engine, db, outcome("SELL"), position=100)
    assert row["executed"] == 1 and engine.ibkr.closes == ["AAPL"]
    # mas uma entrada nova continua bloqueada, com a razão da pausa
    engine.ibkr.positions["AAPL"] = 0.0
    engine.ibkr.closes.clear()
    db._execute("UPDATE trades SET status='CLOSED'")
    engine._pending_close.clear()
    row = run_exec(engine, db, outcome("BUY"))
    assert row["executed"] == 0 and "pausa" in row["skip_reason"] and engine.ibkr.brackets == []


# ---------------------------------------------------------------- Z04 / E04
def test_e04_late_exit_of_an_old_group_never_touches_a_new_trade():
    engine, db, s = make_engine()
    g1, t1 = own_position(engine, db, "AAPL", 100)
    old = db._query("SELECT * FROM order_groups WHERE id=?", (g1,))[0]
    db.close_trade_reconciled(t1)  # dado como encerrado sem execução disponível
    g2, t2 = own_position(engine, db, "AAPL", 20)
    engine._on_fill(None, own_fill(engine, old["sl_order_id"], 100, 98, "old-late-exit"))
    assert trade_row(db, t2) == {"status": "OPEN", "exit_qty": 0.0, "gross_pnl": 0.0}
    # a execução corrige o trade que a ordem cobria (fechado por reconciliação), sem o reabrir
    assert trade_row(db, t1, "status,exit_qty,gross_pnl,exit_reason") == \
        {"status": "CLOSED", "exit_qty": 100.0, "gross_pnl": -200.0, "exit_reason": "SL"}
    allocations = db.allocations_for_fill("old-late-exit")
    assert len(allocations) == 1 and allocations[0]["trade_id"] == t1
    assert "AAPL" not in engine._discrepancies and engine._own_qty("AAPL") == 20


def test_z04_exit_fill_without_any_covered_trade_stays_unallocated():
    engine, db, s = make_engine()
    g1, t1 = own_position(engine, db, "AAPL", 100)
    g2, t2 = own_position(engine, db, "AAPL", 20)
    old = db._query("SELECT * FROM order_groups WHERE id=?", (g1,))[0]
    db._execute("UPDATE trades SET status='CLOSED', exit_qty=100, exit_reason='TP' WHERE id=?", (t1,))
    engine._on_fill(None, own_fill(engine, old["sl_order_id"], 100, 98, "stray"))
    assert db.allocations_for_fill("stray") == [] and trade_row(db, t2)["exit_qty"] == 0.0
    assert "AAPL" in engine._discrepancies and len(db.unallocated_fills("AAPL")) == 1


# ---------------------------------------------------------------- Z05 / E05
def test_e05_new_exit_does_not_consume_the_old_adjustment_and_the_late_fill_is_booked():
    engine, db, s, gid, tid, tp, sl, positions = setup_position(20)
    asyncio.run(engine._supervise())
    assert db.open_adjustment_qty(tid) == 80
    new_sl = db._query("SELECT sl_order_id FROM order_groups WHERE id=?", (gid,))[0]["sl_order_id"]
    assert new_sl != sl.order.orderId
    engine._on_fill(None, own_fill(engine, new_sl, 20, 98, "new-stop20"))  # fecha as 20 vivas; não explica os 80
    assert db.open_adjustment_qty(tid) == 80 and engine._own_qty("AAPL") == 0
    positions.clear()
    asyncio.run(engine._supervise())  # posição desapareceu: trade reconciliado, ajuste continua por explicar
    assert trade_row(db, tid)["status"] == "CLOSED"
    engine._on_fill(None, own_fill(engine, sl.order.orderId, 80, 97, "old-stop80"))  # a execução antiga chega depois
    assert trade_row(db, tid) == {"status": "CLOSED", "exit_qty": 100.0, "gross_pnl": -280.0}
    assert db.open_adjustment_qty(tid) == 0 and len(db.allocations_for_fill("old-stop80")) == 1
    assert "AAPL" not in engine._discrepancies


def test_z05_old_fill_arriving_first_still_consumes_the_adjustment_it_explains():
    engine, db, s, gid, tid, tp, sl, positions = setup_position(20)
    asyncio.run(engine._supervise())
    engine._on_fill(None, own_fill(engine, sl.order.orderId, 80, 97, "old-first"))
    assert db.open_adjustment_qty(tid) == 0 and engine._own_qty("AAPL") == 20
    assert trade_row(db, tid) == {"status": "OPEN", "exit_qty": 80.0, "gross_pnl": -240.0}


# ---------------------------------------------------------------- Z06 / E06
def test_e06_invalidated_calibrator_is_dropped_when_the_refit_fails():
    s, db = Settings(), Database(":memory:")
    cal = Calibrator(s, db, model_name="m", prompt_version=0)
    model = PlattModel(weights=[0, 0, 0, 0], bias=3, n_samples=100,
                       fitted_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), brier_train=.01, base_rate=.9)
    db.set_kv(cal.kv_key, model.to_json())
    cal.reload()
    assert cal.is_fitted
    db.set_kv("labels_changed_at", datetime.now(timezone.utc).isoformat())
    assert cal.needs_refit()
    assert cal.fit_from_db() is None
    signals = ConfidenceSignals(.2, .5, .1)
    assert not cal.is_fitted and cal.probability(signals) == signals.heuristic() < .3
    assert not Calibrator(s, db, model_name="m", prompt_version=0).is_fitted  # também removido do armazenamento


def test_z06_stale_stored_model_is_not_loaded_at_startup():
    s, db = Settings(), Database(":memory:")
    cal = Calibrator(s, db, model_name="m", prompt_version=0)
    model = PlattModel(weights=[0, 0, 0, 0], bias=3, n_samples=100,
                       fitted_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), brier_train=.01, base_rate=.9)
    db.set_kv(cal.kv_key, model.to_json())
    db.set_kv("labels_changed_at", datetime.now(timezone.utc).isoformat())
    assert not Calibrator(s, db, model_name="m", prompt_version=0).is_fitted


# ---------------------------------------------------------------- Z07 / E07
def test_e07_mcp_comparison_flags_orphan_exits_and_refuses_ok_when_disconnected():
    engine, db, s, gid, tid, tp, sl, _ = setup_position(0)
    db.close_trade_reconciled(tid)
    sup = RemoteSupervisor(engine, s, db)
    first = sup.compare()
    assert first["ok"] is False and first["status"] == "inconsistent"
    assert any(i["symbol"] == "AAPL" and i["kind"] == "saídas sem posição" for i in first["issues"])
    engine.ibkr.ib.isConnected = lambda: False
    engine._reconciled = False
    second = sup.compare()
    assert second["ok"] is False and second["status"] == "unknown" and second["reconciled"] is False


def test_z07_mcp_comparison_is_consistent_for_a_covered_own_position():
    engine, db, s, gid, tid, tp, sl, _ = setup_position(100)
    sup = RemoteSupervisor(engine, s, db)
    result = sup.compare()
    assert result["ok"] is True and result["status"] == "consistent" and result["issues"] == []


# ---------------------------------------------------------------- Z08 / E08
def test_e08_mcp_pause_is_refused_when_the_account_changes_before_it_is_applied(tmp_path):
    engine, db, s = make_engine()
    engine.ibkr.account = "U11111"
    sup = RemoteSupervisor(engine, s, db)
    first_mask = mask_account(engine.ibkr.account)

    def queued_after_account_switch(coro):
        engine.ibkr.account = "U22222"
        engine.generation += 1
        db.switch_path(tmp_path / "account_U22222.sqlite3")
        f = Future()

        async def apply_later():
            f.set_result(await coro)

        asyncio.get_event_loop().create_task(apply_later())
        return f

    engine.call = queued_after_account_switch
    result = asyncio.run(sup.pause_entries(first_mask, s.trading_mode, "pedido para conta anterior"))
    assert result["applied"] is False and "conta" in result["error"] and not engine.entries_paused
    assert db.get_kv("entries_paused") is None
    record = db.recent_remote_commands()[0]
    assert record["tool"] == "pause_new_entries" and '"applied": false' in record["result"]


def test_z08_mcp_pause_is_refused_when_only_the_generation_changes():
    engine, db, s = make_engine()
    sup = RemoteSupervisor(engine, s, db)

    def queued_after_restart(coro):
        engine.generation += 1  # paragem/reinício do ciclo entre a validação e a aplicação
        f = Future()

        async def apply_later():
            f.set_result(await coro)

        asyncio.get_event_loop().create_task(apply_later())
        return f

    engine.call = queued_after_restart
    result = asyncio.run(sup.pause_entries(mask_account(engine.ibkr.account), s.trading_mode, "geração anterior"))
    assert result["applied"] is False and not engine.entries_paused
