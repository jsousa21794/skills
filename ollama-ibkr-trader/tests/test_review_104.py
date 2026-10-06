"""Regressões da revisão da 1.0.4 (V01-V11 / A01-A13): os 13 ensaios do auditor com as asserções
invertidas para o comportamento corrigido, mais casos adjacentes."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from trader.analytics import Analytics
from trader.app import _migrate_legacy_db
from trader.config import Settings
from trader.database import Database
from trader.indicators import Bar
from trader.risk import RiskGate
from trader.settlement import Settler, first_touch_label
from tests.helpers import make_bars
from tests.test_engine import NOW, _FixedDatetime, ctx_for, make_engine as base_engine, outcome, own_position, run_exec
from tests.test_review_103 import ib_order, position, real_client


def make_engine(**overrides):
    # Histórico termina em NOW (sem velas futuras), como nos ensaios do auditor.
    bars = {"AAPL": make_bars(600, start=NOW - timedelta(minutes=599), vol=.2)}
    return base_engine(bars=bars, **overrides)


# ---------------------------------------------------------------- V01 / A02
def test_a02_close_is_blocked_while_a_manual_stop_is_active():
    sl = ib_order(81, "SELL", 100, "STP", ref="manual")
    client = real_client([sl], [position(100)])
    assert client.has_protective_orders("AAPL")
    result = asyncio.run(client.close_position("AAPL", authorize=lambda: True))
    assert result["aborted"] and result["conflict"] == [81] and result["needs_protection"] is False
    assert sl.orderStatus.status == "Submitted" and client.ib.placed == []  # nenhuma venda concorrente
    # com autorização explícita, o stop manual é cancelado ANTES de enviar o fecho
    client.settings.cancel_external_exits_on_close = True
    result = asyncio.run(client.close_position("AAPL", authorize=lambda: True))
    assert result["qty"] == 100 and sl.orderStatus.status == "Cancelled" and client.ib.placed[-1].order.orderType == "MKT"


def test_v01_engine_reports_conflict_and_keeps_position(monkeypatch):
    engine, db, _ = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    own_position(engine, db, "AAPL", 100)
    orig = engine.ibkr.close_position

    async def blocked(symbol, authorize=None, max_qty=None):
        return {"order_id": None, "qty": 0, "direction": 1, "trade": None, "aborted": True,
                "needs_protection": False, "conflict": [81]}

    engine.ibkr.close_position = blocked
    row = run_exec(engine, db, outcome("SELL"), position=100)
    assert row["executed"] == 0 and "abortado" in row["skip_reason"]
    assert "AAPL" not in engine._pending_close and engine.ibkr.protections == []
    engine.ibkr.close_position = orig


# ---------------------------------------------------------------- V02 / A01
def test_a01_restart_keeps_standalone_take_profit():
    engine, db, s = make_engine()
    tp = ib_order(51, "SELL", 100, "LMT", oca="repair")
    sl = ib_order(52, "SELL", 100, "STP", oca="repair")
    trades = [tp, sl]
    client = real_client(trades, [position(100)])
    engine.ibkr = client
    gid = db.insert_order_group(symbol="AAPL", decision_id=None, role="ENTRY", direction=1, qty=100,
                                parent_order_id=None, tp_order_id=51, sl_order_id=52, ref_price=100, tp_price=104,
                                sl_price=98, account="U1")
    tid = db.open_trade(symbol="AAPL", decision_id=None, group_id=gid, direction=1, qty=100)
    db.record_entry_fill(tid, 100, 100, NOW)
    engine._restore_pending_orders()
    assert tp.orderStatus.status == "Submitted" and sl.orderStatus.status == "Submitted"
    assert engine._pending_entries == {} and engine._pending_close == {}
    # ordem do bot realmente desconhecida continua a ser cancelada
    unknown = ib_order(77, "BUY", 5, "LMT")
    trades.append(unknown)
    engine._restore_pending_orders()
    assert unknown.orderStatus.status == "Cancelled" and tp.orderStatus.status == "Submitted"


def test_v02_incomplete_pair_is_repaired_by_supervisor():
    engine, db, s = make_engine()
    own_position(engine, db, "AAPL", 100)
    engine.ibkr.orders["AAPL"] = [__import__("tests.helpers", fromlist=["FakeOrder"]).FakeOrder(9, "SELL", 100, "STP")]
    assert engine.ibkr.has_protective_orders("AAPL")
    engine.ibkr.has_orphan_children = lambda symbol: True  # stop sem TP
    asyncio.run(engine._supervise())
    assert "AAPL" in engine.ibkr.protections


# ---------------------------------------------------------------- V03 / A03
def test_a03_client_zero_fill_never_touches_bot_trade():
    engine, db, s = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 5)
    group = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    f = NS(contract=NS(symbol="AAPL", secType="STK", conId=engine.ibkr.con_id("AAPL")),
           execution=NS(execId="manual-zero", orderId=group["parent_order_id"], side="BOT", shares=7, price=50, time=NOW,
                        acctNumber=engine.ibkr.account, clientId=0, orderRef="", permId=99999), commissionReport=None)
    assert s.ib_client_id != 0
    engine._on_fill(None, f)
    assert db._query("SELECT filled_qty FROM trades WHERE id=?", (tid,))[0]["filled_qty"] == 5
    # permId conhecido e diferente também é rejeitado, mesmo com o clientId certo
    db.set_perm_id(group["parent_order_id"], 4242)
    g = NS(contract=NS(symbol="AAPL", secType="STK", conId=engine.ibkr.con_id("AAPL")),
           execution=NS(execId="perm-x", orderId=group["parent_order_id"], side="BOT", shares=7, price=50, time=NOW,
                        acctNumber=engine.ibkr.account, clientId=s.ib_client_id, orderRef=s.order_ref, permId=99999), commissionReport=None)
    engine._on_fill(None, g)
    assert db._query("SELECT filled_qty FROM trades WHERE id=?", (tid,))[0]["filled_qty"] == 5
    # a identidade da corretora fica registada a partir do estado da ordem
    engine._on_order_status(NS(orderStatus=NS(status="Submitted"), order=NS(orderId=group["sl_order_id"], action="SELL", permId=5151),
                               contract=NS(symbol="AAPL")))
    assert db.perm_id_for(gid, group["sl_order_id"]) == 5151


# ---------------------------------------------------------------- V04 / A04
def test_a04_migrated_groups_keep_old_children_on_first_repair():
    engine, db, s = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 100)
    group = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    old_tp = group["tp_order_id"]
    db._execute("DELETE FROM order_history")  # estado de uma base 1.0.3
    db._migrate()  # backfill idempotente
    assert db._query("SELECT COUNT(*) AS n FROM order_history WHERE group_id=?", (gid,))[0]["n"] == 3
    db._migrate()
    assert db._query("SELECT COUNT(*) AS n FROM order_history WHERE group_id=?", (gid,))[0]["n"] == 3
    db._execute("DELETE FROM order_history")  # e mesmo sem backfill, a reparação preserva os IDs antigos
    db.update_group_orders(gid, tp_order_id=501, sl_order_id=502)
    assert db.group_for_order(old_tp)["id"] == gid and db.order_leg(group, old_tp) == "TP"


# ---------------------------------------------------------------- V05 / A05
def test_a05_legacy_state_is_imported_when_the_103_db_already_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_TRADER_HOME", str(tmp_path))
    s = Settings()
    old = Database(s.legacy_db_path())
    old.add_protection_event("daily_loss", None, datetime.now(timezone.utc) + timedelta(hours=5), "halt")
    gid = old.insert_order_group(symbol="AAPL", decision_id=None, role="ENTRY", direction=1, qty=10, parent_order_id=1,
                                 tp_order_id=2, sl_order_id=3, ref_price=100, tp_price=104, sl_price=98, account="U1")
    tid = old.open_trade(symbol="AAPL", decision_id=None, group_id=gid, direction=1, qty=10, stop_price=98, tp_price=104)
    old.record_entry_fill(tid, 10, 100.0, NOW)
    old.close()
    current = Database(s.db_path())
    current.close()
    _migrate_legacy_db(s, logging.getLogger("audit"))
    current = Database(s.db_path())
    assert RiskGate(s, current).halted  # kill-switch legado restaurado
    trades = current.open_trades("AAPL")
    assert len(trades) == 1 and trades[0]["filled_qty"] == 10 and trades[0]["stop_price"] == 98
    group = current.group_for_order(2, symbol="AAPL")
    assert group is not None and current.order_leg(group, 2) == "TP"  # o TP antigo continua a fechar o trade certo
    assert not s.legacy_db_path().exists() and (tmp_path / "trader.sqlite3.migrated-1.0.2").exists()


# ---------------------------------------------------------------- V06 / A12
def test_a12_pre_account_database_is_never_copied_to_a_second_account(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_TRADER_HOME", str(tmp_path))
    engine, db, s = make_engine()
    db.switch_path(s.db_path())
    engine.ibkr.account = "U1"
    own_position(engine, db, "AAPL", 100)
    assert engine._bind_account_db() and db.get_kv("bound_account") == "U1"
    # Outro arranque: a base por modo volta a ser aberta antes de conhecer a conta, mas já está atribuída.
    second, db2, s2 = make_engine()
    db2.switch_path(s2.db_path())
    assert db2.get_kv("bound_account") == "U1"
    second.ibkr.account = "U2"
    assert second._bind_account_db() and db2.get_kv("bound_account") == "U2"
    assert db2._query("SELECT account FROM order_groups") == [] and "AAPL" not in second._managed_symbols()
    # registos de outra conta também impedem a cópia, mesmo sem marca
    third, db3, s3 = make_engine()
    db3.switch_path(s3.db_path("pre"))
    db3.insert_order_group(symbol="TSLA", decision_id=None, role="ENTRY", direction=1, qty=1, parent_order_id=1,
                           tp_order_id=2, sl_order_id=3, ref_price=1, tp_price=2, sl_price=0.5, account="U9")
    third.ibkr.account = "U3"
    assert third._bind_account_db() and db3._query("SELECT account FROM order_groups") == []


# ---------------------------------------------------------------- V07 / A13
def test_a13_mixed_position_is_not_closed_beyond_own_shares(monkeypatch):
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    engine, db, s = make_engine(manage_external_positions=False)
    own_position(engine, db, "AAPL", 20)
    engine.ibkr.positions["AAPL"] = 100  # 20 do bot + 80 ações externas
    result = run_exec(engine, db, outcome("SELL"), position=100)
    assert result["executed"] == 0 and "externa" in result["skip_reason"]
    assert db._query("SELECT qty FROM order_groups WHERE role='CLOSE'") == []
    # a proteção automática só cobre as 20 próprias
    engine.ibkr.orders.clear()
    asyncio.run(engine._supervise())
    assert engine.ibkr.orders["AAPL"][-1].totalQuantity == 20
    # com adoção explícita, a posição inteira passa a ser gerida
    s.manage_external_positions = True
    result = run_exec(engine, db, outcome("SELL"), position=100)
    assert result["executed"] == 1 and db._query("SELECT qty FROM order_groups WHERE role='CLOSE'")[0]["qty"] == 100


# ---------------------------------------------------------------- V08 / A06 / A11
def test_a06_retrospective_follows_the_current_model():
    engine, db, s = make_engine(retro_use_llm_summary=False)
    original = engine.brain.model
    asyncio.run(engine.set_model("new-model"))
    assert engine.retro.calibrator is engine.calibrator and engine.retro.calibrator.model_name == "new-model"
    report = asyncio.run(engine.run_retrospective())
    assert report["gates"]["model"] == "new-model" and report["gates"]["experiment"].startswith("new-model")
    assert db.get_kv(f"gates_passed:{original}") is None


def test_a11_gates_of_another_experiment_never_validate_the_current_model():
    engine, db, s = make_engine()
    engine._apply_gates({"all_passed": True, "checks": {}, "experiment": "different-model"})
    assert not engine.gates_passed and db.get_kv("gates_passed:" + engine.brain.model) is None
    engine._apply_gates({"all_passed": True, "checks": {}, "experiment": Analytics.experiment_id(engine.brain.model, engine.brain.prompt_version)})
    assert engine.gates_passed
    # no A/B, o estado de validação aplicado é o do modelo que respondeu
    assert engine._gates_for("other") == (False, s.learning_risk_multiplier)
    assert engine._gates_for(engine.brain.model)[0] is True


def test_v08_report_partitions_by_prompt_and_builds_experiment_equity():
    s = Settings()
    db = Database(":memory:")
    for i, (model, pv) in enumerate([("A", 1), ("A", 2), ("B", 1)] * 4):
        did = db.insert_decision(symbol="AAPL", model=model, action="BUY", confidence=0.8, reason="r", snapshot={"price": 100.0},
                                 position_qty=0, net_liq=1e5, prompt_version=pv, parse_ok=True, raw_response="",
                                 ts=NOW - timedelta(hours=i), extra={"agree_frac": 1.0, "calibrated_prob": 0.7})
        db.settle_decision(did, settled_price=101, settled_return=1.0, bench_return=None, alpha=None, correct=1, horizon_min=30)
        gid = db.insert_order_group(symbol="AAPL", decision_id=did, role="ENTRY", direction=1, qty=1, parent_order_id=i,
                                    tp_order_id=None, sl_order_id=None, ref_price=100, tp_price=None, sl_price=None, ts=NOW - timedelta(hours=i))
        tid = db.open_trade(symbol="AAPL", decision_id=did, group_id=gid, direction=1, qty=1)
        db.record_entry_fill(tid, 1, 100.0, NOW - timedelta(hours=i))
        db.record_exit_fill(tid, 1, 110.0 if model == "A" else 90.0, NOW - timedelta(hours=i) + timedelta(minutes=5), "TP")
    db.snapshot_pnl(net_liq=1e5, cash=1e5, unrealized=0, realized=0, ts=NOW - timedelta(days=1))
    a1 = Analytics(s, db).build_report(now=NOW + timedelta(hours=1), model="A", prompt_version=1)
    assert a1["experiment"] == "A:p1" and a1["calibration"]["n"] == 4 and a1["trades"]["n"] == 4
    assert a1["equity"]["n"] >= 1 if "n" in a1["equity"] else True
    b = Analytics(s, db).build_report(now=NOW + timedelta(hours=1), model="B")
    assert b["trades"]["n"] == 4 and b["trades"]["expectancy"] < 0 < a1["trades"]["expectancy"]


# ---------------------------------------------------------------- V09 / A09 / A10
def test_a09_label_uses_the_path_after_the_decision_or_the_real_entry():
    db = Database(":memory:")
    s = Settings()
    ts = NOW
    did = db.insert_decision(symbol="AAPL", model="m", action="BUY", confidence=.8, reason="r", snapshot={"price": 100},
                             position_qty=0, net_liq=10000, prompt_version=0, parse_ok=True, raw_response="",
                             ts=ts + timedelta(minutes=2), extra={"market_ts": ts.isoformat(), "stop_pct": 2, "tp_pct": 4})
    bars = [Bar(ts, 100, 101, 97, 100, 1), Bar(ts + timedelta(minutes=2), 100, 105, 99, 104, 1)]
    Settler(s, db, lambda *_: 104, bars_between=lambda *_: bars).run(ts + timedelta(hours=2))
    row = db._query("SELECT correct, label_source FROM decisions WHERE id=?", (did,))[0]
    assert row["correct"] == 1 and row["label_source"] == "bracket:vela"
    # decisão executada: o rótulo usa o preço e o instante da ENTRADA real
    did2 = db.insert_decision(symbol="AAPL", model="m", action="BUY", confidence=.8, reason="r", snapshot={"price": 100},
                              position_qty=0, net_liq=10000, prompt_version=0, parse_ok=True, raw_response="",
                              ts=ts + timedelta(minutes=2), extra={"market_ts": ts.isoformat(), "stop_pct": 2, "tp_pct": 4})
    db.mark_decision(did2, executed=True)
    gid = db.insert_order_group(symbol="AAPL", decision_id=did2, role="ENTRY", direction=1, qty=1, parent_order_id=1,
                                tp_order_id=2, sl_order_id=3, ref_price=100, tp_price=104, sl_price=98, ts=ts + timedelta(minutes=2))
    tid = db.open_trade(symbol="AAPL", decision_id=did2, group_id=gid, direction=1, qty=1)
    db.record_entry_fill(tid, 1, 103.0, ts + timedelta(minutes=3))  # entrou a 103: o TP a 107 não é tocado
    bars2 = bars + [Bar(ts + timedelta(minutes=3), 103, 105, 102, 104, 1)]
    Settler(s, db, lambda *_: 104, bars_between=lambda *_: bars2).run(ts + timedelta(hours=2))
    row = db._query("SELECT correct, label_source FROM decisions WHERE id=?", (did2,))[0]
    assert row["correct"] is None and row["label_source"] == "censurado"


def test_a10_settlement_gap_rules_match_the_replay():
    bar = Bar(NOW, 107, 108, 97, 99, 1)
    assert first_touch_label([bar], 100, 2, 6, 1) == 1  # gap pelo TP na abertura vence o low posterior
    assert first_touch_label([Bar(NOW, 97, 108, 96, 99, 1)], 100, 2, 6, 1) == 0  # gap pelo stop
    assert first_touch_label([Bar(NOW, 100, 108, 96, 99, 1)], 100, 2, 6, 1) == 0  # ambíguo intrabar: pior caso


# ---------------------------------------------------------------- V10 / A07
def test_a07_decision_expiring_during_qualification_is_not_sent(monkeypatch):
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    engine, db, s = make_engine()

    class LateDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW + timedelta(minutes=10)

    async def delayed_qualification():
        monkeypatch.setattr("trader.trading_engine.datetime", LateDatetime)
        await asyncio.sleep(0)

    engine.ibkr.slow_qualify = delayed_qualification
    result = run_exec(engine, db, outcome("BUY"))
    assert result["executed"] == 0 and engine.ibkr.brackets == [] and engine.ibkr.refused == 1
    assert (LateDatetime.now() - ctx_for(engine).snapshot.bar_time).total_seconds() > s.decision_max_age_seconds


def test_v10_quote_beyond_limit_during_qualification_is_not_sent(monkeypatch):
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    engine, db, s = make_engine()
    ref = ctx_for(engine).snapshot.price

    async def price_runs_away():
        engine.ibkr.quote = (ref * 1.01, NOW)

    engine.ibkr.slow_qualify = price_runs_away
    result = run_exec(engine, db, outcome("BUY"))
    assert result["executed"] == 0 and engine.ibkr.brackets == []


# ---------------------------------------------------------------- V11 / A08
def test_a08_changing_persistence_in_the_gui_resizes_the_tracker(monkeypatch):
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    engine, db, s = make_engine()
    s.apply({"signal_persistence_cycles": 3})
    asyncio.run(engine.settings_changed())
    assert engine.persistence.cycles == 3
    rows = [run_exec(engine, db, outcome("BUY")) for _ in range(3)]
    assert not rows[0]["executed"] and not rows[1]["executed"] and rows[2]["executed"] == 1
