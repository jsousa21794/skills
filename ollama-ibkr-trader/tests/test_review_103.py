"""Regressões da revisão da 1.0.3 (N01-N18 / R01-R21): cada teste reproduz o cenário defeituoso e
afirma o comportamento corrigido."""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from trader.analytics import Analytics
from trader.calibration import Calibrator
from trader.config import Settings
from trader.database import Database
from trader.ibkr_client import IBKRClient
from trader.indicators import Bar, aggregate_bars, bucket_is_complete
from trader.ollama_brain import OllamaBrain
from trader.policy import decide_execution
from trader.risk import GateResult, RiskGate
from trader.settlement import Settler
from trader.trading_engine import build_decision_context
from tests.helpers import FakeIBKR, FakeOrder, make_bars, NY_OPEN_UTC
from tests.test_engine import NOW, _FixedDatetime, fill, make_engine, outcome, own_position, run_exec


# ------------------------------------------------------------------ fakes do ib_async
def ib_order(order_id, action, qty, order_type, *, account="U1", ref="OllamaIBKRTrader", oca="", parent_id=0,
             status="Submitted", filled=0.0):
    order = SimpleNamespace(orderId=order_id, action=action, totalQuantity=qty, orderType=order_type, account=account,
                            orderRef=ref, ocaGroup=oca, parentId=parent_id)
    st = SimpleNamespace(status=status, remaining=qty - filled, filled=filled)
    trade = SimpleNamespace(order=order, orderStatus=st, contract=SimpleNamespace(secType="STK", conId=0, symbol="AAPL"))
    trade.isDone = lambda: trade.orderStatus.status in ("Cancelled", "Filled", "ApiCancelled", "Inactive")
    return trade


def fake_ib(trades, positions):
    placed = []

    def place(contract, order):
        t = ib_order(order.orderId, order.action, order.totalQuantity, order.orderType, account=order.account,
                     ref=order.orderRef, oca=getattr(order, "ocaGroup", ""))
        placed.append(t)
        trades.append(t)
        return t

    def cancel(order):
        for t in trades:
            if t.order.orderId == order.orderId:
                t.orderStatus.status = "Cancelled"

    ib = SimpleNamespace(isConnected=lambda: True, openTrades=lambda: list(trades), positions=lambda: list(positions),
                         placeOrder=place, cancelOrder=cancel, disconnect=lambda: None, client=SimpleNamespace(getReqId=lambda: 900 + len(placed) + len(trades)),
                         accountValues=lambda: [], placed=placed)
    return ib


def real_client(trades, positions, account="U1"):
    client = IBKRClient(Settings())
    client.ib = fake_ib(trades, positions)
    client.account = account

    async def qualify(symbol):
        return SimpleNamespace(conId=0, symbol=symbol)

    client.qualify = qualify
    return client


def position(qty, account="U1", symbol="AAPL"):
    return SimpleNamespace(account=account, contract=SimpleNamespace(secType="STK", conId=0, symbol=symbol), position=qty)


# ---------------------------------------------------------------- N01 / R01 / R21
def test_r01_stop_during_qualification_does_not_send_bracket(monkeypatch):
    engine, db, _ = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)

    async def stop_now():
        engine.generation += 1  # Parar durante a qualificação assíncrona

    engine.ibkr.slow_qualify = stop_now
    row = run_exec(engine, db, outcome("BUY"))
    assert row["executed"] == 0 and engine.ibkr.brackets == [] and engine.ibkr.refused == 1
    assert "autorização" in row["skip_reason"] or "falha" in row["skip_reason"]


def test_r21_stop_during_close_cancellations_does_not_send_close(monkeypatch):
    engine, db, _ = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    own_position(engine, db, "AAPL", 100)

    async def stop_now():
        engine.trading_enabled = False  # Parar enquanto se esperam os cancelamentos

    engine.ibkr.slow_cancel = stop_now
    row = run_exec(engine, db, outcome("SELL"), position=100)
    assert row["executed"] == 0 and engine.ibkr.closes == [] and engine.ibkr.refused == 1
    # a cobertura cancelada é reposta (redução de risco continua permitida depois de Parar)
    assert "AAPL" in engine.ibkr.protections and "AAPL" not in engine._pending_close


# ---------------------------------------------------------------- N02 / R18 / R21
def test_r21_reprotection_does_not_race_intentional_close(monkeypatch):
    from trader.calibration import ConfidenceSignals

    engine, db, _ = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    gid, _tid = own_position(engine, db, "AAPL", 100)
    group = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    seen = {}
    out = outcome("SELL")
    ctx = build_decision_context(engine.settings, "AAPL", engine.ibkr.bars_as_list("AAPL"))
    did = db.insert_decision(symbol="AAPL", model="m", action="SELL", confidence=0.8, reason="r", snapshot=ctx.snapshot.to_dict(),
                             position_qty=100, net_liq=1e5, prompt_version=0, parse_ok=True, raw_response="",
                             extra={"agree_frac": 1.0, "market_ts": ctx.snapshot.bar_time.isoformat()})

    async def scenario():
        tasks = []

        async def during_cancel():
            # o cancelamento intencional do SL chega como orderStatus; a reproteção automática arranca em paralelo
            seen["closing"] = engine._pending_close.get("AAPL", {}).get("state")
            engine.ibkr.intentional = {group["sl_order_id"]}
            engine._on_order_status(SimpleNamespace(orderStatus=SimpleNamespace(status="Cancelled"),
                                                    order=SimpleNamespace(orderId=group["sl_order_id"], action="SELL"),
                                                    contract=SimpleNamespace(symbol="AAPL")))
            tasks.append(asyncio.create_task(engine._reprotect("AAPL")))
            await asyncio.sleep(1.2)  # a reproteção já passou o seu sleep(1 s) e espera pelo lock do ativo

        engine.ibkr.slow_cancel = during_cancel
        cal = engine.calibrator.probability(ConfidenceSignals(0.8, 1.0, 1.0, -0.2))
        await engine._execute(out, cal, ctx, did, engine.generation, (None, 0), None)
        await asyncio.gather(*tasks)

    asyncio.run(scenario())
    row = db._query("SELECT * FROM decisions WHERE id=?", (did,))[0]
    assert seen["closing"] == "CLOSING"  # o bloqueio existe ANTES de cancelar os filhos
    assert row["executed"] == 1 and engine.ibkr.closes == ["AAPL"]
    assert engine.ibkr.protections == []  # nenhum novo TP/SL coexiste com a venda de fecho
    assert engine._pending_close["AAPL"]["state"] == "SENT"


def test_r18_partial_cancel_timeout_reports_missing_coverage():
    sl = ib_order(2, "SELL", 100, "STP", oca="g1")
    tp = ib_order(3, "SELL", 100, "LMT", oca="g1")
    client = real_client([sl, tp], [position(100)])

    async def never_done(trades, timeout=5.0):
        sl.orderStatus.status = "Cancelled"  # só o stop confirmou o cancelamento
        return False

    client.wait_done = never_done
    result = asyncio.run(client.close_position("AAPL"))
    assert result["aborted"] and result["needs_protection"] is True and result["order_id"] is None


# ---------------------------------------------------------------- N03 / R03 / R09
def test_r03_repair_places_only_the_residual_quantity():
    sl = ib_order(2, "SELL", 40, "STP", oca="g1")
    tp = ib_order(3, "SELL", 40, "LMT", oca="g1")
    client = real_client([sl, tp], [position(100)])
    assert client.protective_coverage("AAPL") == 40 and not client.has_protective_orders("AAPL")
    result = asyncio.run(client.ensure_protection("AAPL", stop_price=95.0, tp_price=110.0))
    assert result["qty"] == 60 and result["replaced_order_ids"] == []
    assert client.protective_coverage("AAPL") == 100 and client.has_protective_orders("AAPL")
    # segunda chamada: nada a repor
    assert asyncio.run(client.ensure_protection("AAPL", stop_price=95.0, tp_price=110.0)) is None


def test_r09_fractional_remainder_does_not_grow_every_cycle(caplog):
    sl = ib_order(2, "SELL", 10, "STP", oca="g1")
    tp = ib_order(3, "SELL", 10, "LMT", oca="g1")
    client = real_client([sl, tp], [position(10.75)])
    assert client.has_protective_orders("AAPL")  # a parte inteira está coberta
    with caplog.at_level(logging.ERROR):
        assert asyncio.run(client.ensure_protection("AAPL", stop_price=95.0, tp_price=110.0)) is None
        assert asyncio.run(client.ensure_protection("AAPL", stop_price=95.0, tp_price=110.0)) is None
    assert client.protective_coverage("AAPL") == 10 and len(client.ib.placed) == 0


def test_n03_orphan_tp_is_replaced_not_duplicated():
    tp = ib_order(3, "SELL", 100, "LMT", oca="g1")  # TP sobreviveu, stop desapareceu
    client = real_client([tp], [position(100)])
    result = asyncio.run(client.ensure_protection("AAPL", stop_price=95.0, tp_price=110.0))
    assert result["qty"] == 100 and result["replaced_order_ids"] == [3]
    assert tp.orderStatus.status == "Cancelled" and client.is_intentional_cancel(3)
    active_tp = [t for t in client.ib.openTrades() if t.order.orderType == "LMT" and t.orderStatus.status == "Submitted"]
    assert sum(t.order.totalQuantity for t in active_tp) == 100  # nunca 200


# ---------------------------------------------------------------- N04 / R02
def test_r02_stop_from_other_account_is_not_coverage():
    other = ib_order(2, "SELL", 100, "STP", account="U2")
    client = real_client([other], [position(100, account="U1"), position(100, account="U2")], account="U1")
    assert client.protective_coverage("AAPL") == 0 and not client.has_protective_orders("AAPL")
    assert client.open_trades_for("AAPL", ours_only=False) == []
    mine = ib_order(5, "SELL", 100, "STP", account="U1", ref="manual")
    client.ib.openTrades = lambda: [other, mine]
    assert client.protective_coverage("AAPL") == 100  # stop manual da MESMA conta conta como cobertura


# ---------------------------------------------------------------- N05 / R05
def test_r05_limit_entry_is_recognized_and_restored_after_restart(monkeypatch):
    client = real_client([ib_order(7, "BUY", 10, "LMT")], [])
    assert client.has_pending_entry("AAPL")
    engine, db, s = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    gid = db.insert_order_group(symbol="AAPL", decision_id=None, role="ENTRY", direction=1, qty=10, parent_order_id=7,
                                tp_order_id=8, sl_order_id=9, ref_price=100.0, tp_price=104, sl_price=98,
                                ts=NOW - timedelta(seconds=30))
    db.open_trade(symbol="AAPL", decision_id=None, group_id=gid, direction=1, qty=10)
    engine.ibkr.open_orders_all = [ib_order(7, "BUY", 10, "LMT")]
    engine._reconciled = False
    asyncio.run(engine._reconcile())
    assert 7 in engine._pending_entries and engine._pending_entries[7]["notional"] == 1000.0
    assert engine._pending_entries[7]["deadline"] is not None and engine._reconciled
    row = run_exec(engine, db, outcome("BUY"))
    assert "pendente" in row["skip_reason"]  # nenhuma nova entrada coexiste com a LMT antiga


def test_n05_no_decision_before_reconciliation(monkeypatch):
    engine, db, _ = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    engine._reconciled = False
    row = run_exec(engine, db, outcome("BUY"))
    assert row["executed"] == 0 and "reconciliar" in row["skip_reason"]


# ---------------------------------------------------------------- N06 / R06
def test_r06_fill_of_other_contract_or_client_does_not_touch_trade():
    engine, db, _ = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 5)
    group = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    before = db._query("SELECT * FROM trades WHERE id=?", (tid,))[0]
    # mesmo orderId, outro contrato (MSFT): só registado, trade AAPL intacto
    fill(engine, group["parent_order_id"], "MSFT", "BOT", 7, 400.0, "msft-1")
    after = db._query("SELECT * FROM trades WHERE id=?", (tid,))[0]
    assert after["filled_qty"] == before["filled_qty"] == 5 and after["entry_price"] == 100.0
    # mesmo orderId e símbolo mas de outro clientId: ignorado
    f = SimpleNamespace(contract=SimpleNamespace(symbol="AAPL", secType="STK", conId=engine.ibkr.con_id("AAPL")),
                        execution=SimpleNamespace(execId="x-cli", orderId=group["parent_order_id"], side="BOT", shares=7,
                                                  price=50.0, time=NOW, acctNumber=engine.ibkr.account, clientId=0))
    f.execution.clientId = engine.settings.ib_client_id + 1
    engine._on_fill(SimpleNamespace(), f)
    assert db._query("SELECT * FROM trades WHERE id=?", (tid,))[0]["filled_qty"] == 5
    assert db.fill_by_exec("x-cli") is None


# ---------------------------------------------------------------- N07 / R04
def test_r04_replaced_children_keep_identity_and_close_the_trade():
    engine, db, _ = make_engine()
    gid, tid = own_position(engine, db, "AAPL", 100)
    group = db._query("SELECT * FROM order_groups WHERE id=?", (gid,))[0]
    old_tp = group["tp_order_id"]
    db.update_group_orders(gid, tp_order_id=501, sl_order_id=502)
    assert db.group_for_order(old_tp, symbol="AAPL")["id"] == gid
    assert db.order_leg(db.group_for_order(old_tp), old_tp) == "TP"
    assert db.order_leg(group, 501) == "TP" and db.order_leg(group, 502) == "SL"
    fill(engine, old_tp, "AAPL", "SLD", 100, 104.0, "old-tp")  # o TP antigo executou
    row = db._query("SELECT status, exit_reason FROM trades WHERE id=?", (tid,))[0]
    assert row["status"] == "CLOSED" and row["exit_reason"] == "TP"
    hist = db._query("SELECT leg, order_id, replaced_ts FROM order_history WHERE group_id=? ORDER BY id", (gid,))
    assert any(h["order_id"] == old_tp and h["replaced_ts"] for h in hist)


# ---------------------------------------------------------------- N08 / R07
def test_r07_legacy_database_is_migrated_once(tmp_path, monkeypatch):
    from trader.app import _migrate_legacy_db

    monkeypatch.setenv("OLLAMA_TRADER_HOME", str(tmp_path))
    s = Settings()
    legacy = Database(s.legacy_db_path())
    legacy.add_protection_event("daily_loss", None, datetime.now(timezone.utc) + timedelta(hours=5), "-21%")
    legacy.close()
    log = logging.getLogger("test")
    _migrate_legacy_db(s, log)
    assert s.db_path().exists() and not s.legacy_db_path().exists()
    assert (tmp_path / "trader.sqlite3.migrated-1.0.2").exists()
    db = Database(s.db_path())
    gate = RiskGate(s, db)
    assert gate.halted  # o kill-switch da base antiga é visto pela nova sessão
    _migrate_legacy_db(s, log)  # idempotente


# ---------------------------------------------------------------- N09 / R08 / R17
def test_r08_currency_and_account_metadata_reset_on_disconnect():
    client = real_client([], [])
    client.base_currency = "EUR"
    client.accounts = ["U1"]
    client.data_delayed = True
    asyncio.run(client.disconnect())
    assert client.base_currency == "" and client.account == "" and client.accounts == [] and client.data_delayed is None


def test_r17_manual_position_in_configured_symbol_is_not_managed(monkeypatch):
    engine, db, _ = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    engine.ibkr.positions["AAPL"] = 100  # posição manual: sem trade do bot
    row = run_exec(engine, db, outcome("SELL"), position=100)
    assert row["executed"] == 0 and "externa" in row["skip_reason"] and engine.ibkr.closes == []
    row = run_exec(engine, db, outcome("BUY"), position=100)
    assert row["executed"] == 0 and "externa" in row["skip_reason"]


def test_n09_database_is_bound_to_one_account(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_TRADER_HOME", str(tmp_path))
    engine, _db, s = make_engine()
    engine.db.switch_path(s.db_path())
    engine.ibkr.account = "U1"
    assert engine._bind_account_db() and engine.db.path == str(s.db_path("U1"))
    assert engine.db.get_kv("bound_account") == "U1"
    engine.ibkr.account = "U2"
    assert engine._bind_account_db() and engine.db.path == str(s.db_path("U2"))  # outra conta, outro ficheiro
    # um ficheiro já associado a U1 nunca é usado por U2
    engine.db.switch_path(s.db_path("U1"))
    engine.db.set_kv("bound_account", "U1")
    engine.ibkr.account = "U2"
    engine.db.switch_path(s.db_path("U2"))
    engine.db.set_kv("bound_account", "U1")
    assert engine._bind_account_db() is False


# ---------------------------------------------------------------- N10 / R10 / R11
def test_r10_market_ts_is_bar_close_and_path_starts_after_decision():
    s = Settings()
    bars = make_bars(600, vol=0.2)
    ctx = build_decision_context(s, "AAPL", bars)
    assert ctx.snapshot.bar_time == ctx.agg_bars[-1].time + timedelta(minutes=s.decision_bar_minutes)
    db = Database(":memory:")
    t0 = NY_OPEN_UTC
    # velas ANTES da decisão tocam o stop; depois da decisão só o TP é tocado
    before = [Bar(t0 + timedelta(minutes=i), 100, 100.5, 95.0, 100, 1) for i in range(5)]
    after = [Bar(t0 + timedelta(minutes=5 + i), 100, 104.5, 99.5, 100, 1) for i in range(45)]
    path = before + after
    did = db.insert_decision(symbol="AAPL", model="m", action="BUY", confidence=0.8, reason="r",
                             snapshot={"price": 100.0}, position_qty=0, net_liq=1e5, prompt_version=0, parse_ok=True,
                             raw_response="", ts=t0 + timedelta(minutes=5),
                             extra={"market_ts": (t0 + timedelta(minutes=5)).isoformat(), "stop_pct": 2.0, "tp_pct": 4.0})
    settler = Settler(s, db, lambda sym, when: next((b.close for b in path if b.time >= when), None),
                      bars_between=lambda sym, a, b: [x for x in path if a <= x.time <= b])
    settler.run(t0 + timedelta(hours=2))
    assert db._query("SELECT correct FROM decisions WHERE id=?", (did,))[0]["correct"] == 1


def test_r11_no_barrier_touch_is_censored_not_directional():
    s = Settings()
    db = Database(":memory:")
    t0 = NY_OPEN_UTC
    path = [Bar(t0 + timedelta(minutes=i), 100, 101.2, 99.5, 101.0, 1) for i in range(40)]  # sobe 1%, alvo 4%
    did = db.insert_decision(symbol="AAPL", model="m", action="BUY", confidence=0.8, reason="r",
                             snapshot={"price": 100.0}, position_qty=0, net_liq=1e5, prompt_version=0, parse_ok=True,
                             raw_response="", ts=t0, extra={"market_ts": t0.isoformat(), "stop_pct": 2.0, "tp_pct": 4.0})
    Settler(s, db, lambda sym, when: 101.0, bars_between=lambda sym, a, b: path).run(t0 + timedelta(hours=2))
    row = db._query("SELECT settled_ts, correct FROM decisions WHERE id=?", (did,))[0]
    assert row["settled_ts"] is not None and row["correct"] is None


# ---------------------------------------------------------------- N11 / R12
def test_r12_report_gates_and_calibration_are_per_experiment():
    s = Settings()
    db = Database(":memory:")
    for i in range(30):
        model = "A" if i % 2 == 0 else "B"
        did = db.insert_decision(symbol="AAPL", model=model, action="BUY", confidence=0.8, reason="r",
                                 snapshot={"price": 100.0}, position_qty=0, net_liq=1e5, prompt_version=3, parse_ok=True,
                                 raw_response="", ts=NOW - timedelta(hours=i),
                                 extra={"agree_frac": 1.0, "calibrated_prob": 0.7})
        db.settle_decision(did, settled_price=101, settled_return=1.0, bench_return=None, alpha=None,
                           correct=1 if model == "A" else 0, horizon_min=30)
    rep_a = Analytics(s, db).build_report(now=NOW + timedelta(hours=1), model="A")
    rep_all = Analytics(s, db).build_report(now=NOW + timedelta(hours=1))
    assert rep_a["experiment"] == "A" and rep_a["calibration"]["n"] == 15 and rep_all["calibration"]["n"] == 30
    assert set(rep_a["by_model"]) == {"A"} and set(rep_all["by_model"]) == {"A", "B"}
    assert rep_a["gates"]["experiment"] == "A"
    # a calibração partilha a partição (modelo + versão do prompt)
    cal = Calibrator(s, db, "A", prompt_version=3)
    assert cal.kv_key.endswith("A:p3")
    cal.set_prompt_version(4)
    assert cal.kv_key.endswith("A:p4") and not cal.is_fitted


def test_n11_ab_sample_uses_its_own_model_calibrator():
    engine, db, _ = make_engine()
    other = engine._calibrator_for("qwen")
    assert other is not engine.calibrator and other.model_name == "qwen"
    assert other.prompt_version == engine.brain.prompt_version
    # a versão do prompt só avança quando o texto muda
    v1 = engine.brain.update_lessons(["lição A"], {})
    v2 = engine.brain.update_lessons(["lição A"], {})
    v3 = engine.brain.update_lessons(["lição B"], {})
    assert v1 == v2 and v3 > v2


# ---------------------------------------------------------------- N12 / R13 / R20
class _NoBrain:
    model = "m"


def _replayer():
    from trader.backtest import Replayer, SimPosition

    s = Settings()
    rep = Replayer(s, _NoBrain(), Database(":memory:"), equity=10_000.0)
    gid = rep.db.insert_order_group(symbol="AAPL", decision_id=None, role="ENTRY", direction=1, qty=10, parent_order_id=1,
                                    tp_order_id=2, sl_order_id=3, ref_price=100, tp_price=106, sl_price=98, ts=NOW)
    tid = rep.db.open_trade(symbol="AAPL", decision_id=None, group_id=gid, direction=1, qty=10, stop_price=98, tp_price=106)
    rep.db.record_entry_fill(tid, 10, 100.0, NOW, 1.0)
    rep.position = SimPosition(tid, 1, 10, 100.0, 98.0, 106.0, NOW)
    return rep


def test_r13_gap_through_tp_at_open_wins_over_later_low():
    rep = _replayer()
    rep._check_exit(Bar(NOW + timedelta(minutes=1), 107.0, 108.0, 97.0, 99.0, 1))
    assert rep.position is None and rep.closed[-1]["exit_reason"] == "TP" and rep.closed[-1]["exit_price"] == 107.0


def test_r20_replay_equity_is_marked_to_market():
    rep = _replayer()
    rep._mark = 80.0  # posição aberta 20% abaixo
    assert rep.unrealized == pytest.approx(-200.0)
    assert rep.equity == pytest.approx(10_000.0 - 200.0)  # a perda aberta entra na equity usada pelos gates


# ---------------------------------------------------------------- N13 / R15
def test_r15_zero_slippage_means_limit_at_reference_price():
    engine, db, s = make_engine()
    s.max_entry_slippage_pct = 0.0
    ctx = build_decision_context(s, "AAPL", engine.ibkr.bars_as_list("AAPL"))
    out = outcome("BUY")
    plan = decide_execution(settings=s, gate=engine.gate, sizer=engine.sizer, calibrator=engine.calibrator, outcome=out,
                            calibrated=0.95, snapshot=ctx.snapshot, agg_bars=ctx.agg_bars, now=NOW,
                            position_qty=0, open_positions=0, pending_entries=0, equity_usd=1e5, available_funds_usd=1e5,
                            reserved_notional=0, persistence=["BUY"], pending_close=False, pending_entry=False,
                            global_gate=GateResult(True))
    assert plan.limit_price == pytest.approx(ctx.snapshot.price)
    s.entry_order_type = "market"
    plan = decide_execution(settings=s, gate=engine.gate, sizer=engine.sizer, calibrator=engine.calibrator, outcome=out,
                            calibrated=0.95, snapshot=ctx.snapshot, agg_bars=ctx.agg_bars, now=NOW,
                            position_qty=0, open_positions=0, pending_entries=0, equity_usd=1e5, available_funds_usd=1e5,
                            reserved_notional=0, persistence=["BUY"], pending_close=False, pending_entry=False,
                            global_gate=GateResult(True))
    assert plan.limit_price is None
    assert Settings().apply({"entry_order_type": "yolo"}) and Settings().entry_order_type == "limit"


# ---------------------------------------------------------------- N14
def test_n14_supervisor_is_independent_and_uses_monotonic_deadline():
    import time

    engine, db, _ = make_engine()
    engine._pending_entries[77] = {"symbol": "AAPL", "qty": 1.0, "notional": 100.0, "ts": NOW, "trade_id": None,
                                   "filled": 0.0, "deadline": time.monotonic() - 1}
    asyncio.run(engine._supervise())
    assert 77 in engine.ibkr.cancelled and engine._pending_entries[77]["cancel_sent"]
    import inspect
    src = inspect.getsource(type(engine)._cycle_loop)
    assert "_supervise" not in src  # o ciclo de inferência não arrasta a supervisão
    assert hasattr(engine, "_supervisor_loop")


# ---------------------------------------------------------------- N15
def test_n15_stale_reference_price_is_rechecked_before_sending(monkeypatch):
    engine, db, _ = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    ref = build_decision_context(engine.settings, "AAPL", engine.ibkr.bars_as_list("AAPL")).snapshot.price
    engine.ibkr.quote = (ref * 1.02, NOW)  # subiu 2% desde a vela de referência
    row = run_exec(engine, db, outcome("BUY"))
    assert row["executed"] == 0 and "preço atual" in row["skip_reason"] and engine.ibkr.brackets == []
    engine.ibkr.quote = (ref * 1.0005, NOW)
    assert run_exec(engine, db, outcome("BUY"))["executed"] == 1


def test_n15_news_in_loop_never_hits_network():
    from trader.market_data import EventData

    db = Database(":memory:")
    ev = EventData(db)
    ev._fetch_news = lambda symbol: (_ for _ in ()).throw(AssertionError("rede no loop"))
    assert ev.recent_news("AAPL", 2, cached_only=True) == []
    db.event_cache_put("news:AAPL", [{"ts": datetime.now(timezone.utc).isoformat(), "title": "x"}])
    assert len(ev.recent_news("AAPL", 2, cached_only=True)) == 1


# ---------------------------------------------------------------- N16 / R16
def test_r16_non_object_config_recovers_with_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_TRADER_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text("null", encoding="utf-8")
    s = Settings.load()
    assert s.trading_mode == "live" and any("não é um objeto" in w for w in Settings.load_warnings)
    assert (tmp_path / "config.json.invalid").read_text() == "null"
    assert json.loads((tmp_path / "config.json").read_text())["trading_mode"] == "live"
    assert Settings().apply([1, 2, 3])  # lista: aviso, sem exceção


# ---------------------------------------------------------------- N17 / R14
def test_r14_incomplete_internal_buckets_are_dropped_and_duplicates_collapse():
    t0 = NY_OPEN_UTC
    bars = [Bar(t0 + timedelta(minutes=m), 1, 1, 1, 1, 1) for m in range(15) if m not in (7, 8, 9)]  # bucket 09:35 com 2 min
    agg = aggregate_bars(bars, 5, drop_incomplete=True)
    assert [b.time for b in agg] == [t0, t0 + timedelta(minutes=10)]
    assert len(aggregate_bars(bars, 5)) == 3  # sem a flag, política permissiva mantém-se
    dup = bars[:5] + [Bar(t0 + timedelta(minutes=4), 2, 2, 2, 2, 5)]
    agg = aggregate_bars(dup, 5, drop_incomplete=True)
    assert len(agg) == 1 and agg[0].close == 2 and agg[0].volume == 1 * 4 + 5
    assert not bucket_is_complete([Bar(t0 + timedelta(minutes=4), 1, 1, 1, 1, 1)], 5)


# ---------------------------------------------------------------- N18 / R19 / F35
def test_r19_report_multiplier_matches_engine_and_replay_clock_is_historical():
    s = Settings()
    s.learning_risk_multiplier = 0.5
    db = Database(":memory:")
    gates = Analytics(s, db).build_report()["gates"]
    assert gates["risk_multiplier"] == 0.5 and gates["risk_per_trade"] == pytest.approx(s.risk_per_trade_pct * 0.5)
    gate = RiskGate(s, db)
    past = datetime(2024, 3, 1, 15, 0, tzinfo=timezone.utc)
    gate.pause("stoploss_guard", past + timedelta(hours=1), "3 stops", now=past)
    assert db.last_protection_ts("stoploss_guard") == past
