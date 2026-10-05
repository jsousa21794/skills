"""Motor com cliente IBKR falso: camada de risco, execução, fills, reconciliação."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from trader.calibration import ConfidenceSignals
from trader.config import Settings
from trader.database import Database
from trader.ollama_brain import Decision, DecisionOutcome, OllamaBrain
from trader.risk import GateResult
from trader.trading_engine import TradingEngine, build_decision_context, in_regular_hours
from trader.ui_bus import UIBus
from tests.helpers import FakeIBKR, make_bars, NY_OPEN_UTC


def make_engine(bars=None):
    settings = Settings()
    settings.min_confidence = 0.65
    settings.signal_persistence_cycles = 1
    settings.llm_min_agreement = 0.6
    settings.skip_open_minutes = 0
    db = Database(":memory:")
    engine = TradingEngine(settings, db, UIBus(), OllamaBrain(settings, db))
    engine.ibkr = FakeIBKR(bars or {"AAPL": make_bars(600, vol=0.2)})
    engine.gate.events = None
    return engine, db, settings


def ctx_for(engine, symbol="AAPL"):
    ctx = build_decision_context(engine.settings, symbol, engine.ibkr.bars_as_list(symbol))
    assert ctx is not None
    return ctx


def outcome(action, conf=0.8, agree=1.0):
    return DecisionOutcome(Decision(action, conf, "ok"), [{"acao": action}] * 5, agree, agree, -0.2, "h")


def run_exec(engine, db, out, position=0.0, symbol="AAPL", state=None, gate=None):
    ctx = ctx_for(engine, symbol)
    state = state or engine.ibkr.portfolio_state()
    cal = engine.calibrator.probability(ConfidenceSignals(out.decision.confianca, out.agree_frac, out.margin, out.action_logprob)) \
        if out.decision.acao in ("BUY", "SELL") else None
    did = db.insert_decision(symbol=symbol, model="m", action=out.decision.acao, confidence=out.decision.confianca,
                             reason="r", snapshot=ctx.snapshot.to_dict(), position_qty=position, net_liq=state["net_liq"],
                             prompt_version=0, parse_ok=out.decision.parse_ok, raw_response="",
                             extra={"agree_frac": out.agree_frac, "atr": ctx.atr})
    gate = gate or GateResult(True)
    asyncio.run(engine._execute(out, cal, ctx, state, did, position, gate))
    return db._query("SELECT * FROM decisions WHERE id=?", (did,))[0]


def fill(engine, order_id, symbol, side, shares, price, exec_id):
    f = SimpleNamespace(contract=SimpleNamespace(symbol=symbol),
                        execution=SimpleNamespace(execId=exec_id, orderId=order_id, side=side, shares=shares,
                                                  price=price, time=datetime.now(timezone.utc)))
    engine._on_fill(SimpleNamespace(), f)


def test_context_uses_aggregated_bars_and_dynamics():
    engine, _, s = make_engine()
    ctx = ctx_for(engine)
    assert ctx.dynamics is not None and ctx.atr is not None and ctx.atr > 0
    assert len(ctx.agg_bars) < 600 / s.decision_bar_minutes + 2
    assert ctx.snapshot.bars_available == len(ctx.agg_bars)


def test_buy_sizes_by_atr_and_places_bracket(monkeypatch):
    engine, db, s = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    row = run_exec(engine, db, outcome("BUY", 0.9, 1.0))
    assert row["executed"] == 1, row["skip_reason"]
    symbol, action, qty, price, stop, tp = engine.ibkr.brackets[0]
    assert action == "BUY" and stop < price < tp
    risk = qty * (price - stop)
    assert risk <= 100_000 * s.risk_per_trade_pct * 1.05
    trade = db.open_trades("AAPL")[0]
    assert trade["stop_price"] == stop and trade["risk_amount"] == pytest.approx(risk, rel=1e-2)
    assert row["threshold_used"] >= s.min_confidence


def test_low_agreement_low_confidence_hold_review_skipped(monkeypatch):
    engine, db, _ = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    assert "acordo" in run_exec(engine, db, outcome("BUY", 0.9, 0.4))["skip_reason"]
    assert "limiar" in run_exec(engine, db, outcome("BUY", 0.2, 1.0))["skip_reason"]
    assert run_exec(engine, db, outcome("HOLD"))["skip_reason"] == "HOLD"
    assert engine.ibkr.brackets == []


def test_persistence_requires_consecutive_signals(monkeypatch):
    engine, db, s = make_engine()
    s.signal_persistence_cycles = 2
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    assert "persistente" in run_exec(engine, db, outcome("BUY"))["skip_reason"]
    assert run_exec(engine, db, outcome("BUY"))["executed"] == 1
    assert len(engine.ibkr.brackets) == 1


def test_global_and_symbol_gates_block(monkeypatch):
    engine, db, _ = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    blocked = GateResult(False, "kill-switch diário")
    assert run_exec(engine, db, outcome("BUY"), gate=blocked)["skip_reason"] == "kill-switch diário"
    engine.ibkr.pending_entry = True
    assert "pendente" in run_exec(engine, db, outcome("BUY"))["skip_reason"]


def test_opposite_signal_closes_and_fills_settle_pnl(monkeypatch):
    engine, db, _ = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    run_exec(engine, db, outcome("BUY"))
    qty = engine.ibkr.brackets[0][2]
    fill(engine, 100, "AAPL", "BOT", qty, 100.0, "e1")
    engine.ibkr.positions["AAPL"] = qty
    row = run_exec(engine, db, outcome("SELL", 0.9, 1.0), position=qty)
    assert row["executed"] == 1 and engine.ibkr.closes == ["AAPL"]
    close_group = db._query("SELECT * FROM order_groups WHERE role='CLOSE'")[0]
    fill(engine, close_group["parent_order_id"], "AAPL", "SLD", qty, 102.0, "e2")
    closed = db.trades_since(datetime(2000, 1, 1, tzinfo=timezone.utc))[0]
    assert closed["status"] == "CLOSED" and closed["exit_reason"] == "SIGNAL"
    assert closed["gross_pnl"] == pytest.approx(2.0 * qty)
    assert closed["commission"] > 0
    assert closed["pnl"] == pytest.approx(2.0 * qty - closed["commission"])


def test_commission_report_replaces_estimate_and_adjusts_net_pnl(monkeypatch):
    engine, db, _ = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    run_exec(engine, db, outcome("BUY"))
    qty = engine.ibkr.brackets[0][2]
    fill(engine, 100, "AAPL", "BOT", qty, 100.0, "e1")
    trade = db.open_trades("AAPL")[0]
    estimated = trade["commission"]
    assert estimated == pytest.approx(max(1.0, qty * 0.005))
    report = SimpleNamespace(commission=estimated + 0.75)
    engine._on_commission(SimpleNamespace(), SimpleNamespace(execution=SimpleNamespace(execId="e1", orderId=100),
                                                            contract=SimpleNamespace(symbol="AAPL")), report)
    trade = db.open_trades("AAPL")[0]
    assert trade["commission"] == pytest.approx(estimated + 0.75)
    assert trade["pnl"] == pytest.approx(-(estimated + 0.75))
    assert db.fill_by_exec("e1")["commission_estimated"] == 0
    # repetição do mesmo relatório não duplica
    engine._on_commission(SimpleNamespace(), SimpleNamespace(execution=SimpleNamespace(execId="e1", orderId=100),
                                                            contract=SimpleNamespace(symbol="AAPL")), report)
    assert db.open_trades("AAPL")[0]["commission"] == pytest.approx(estimated + 0.75)


def test_reconcile_closes_orphans_and_protects_naked_positions():
    engine, db, _ = make_engine()
    gid = db.insert_order_group(symbol="AAPL", decision_id=None, role="ENTRY", direction=1, qty=5, parent_order_id=1,
                                tp_order_id=2, sl_order_id=3, ref_price=100, tp_price=104, sl_price=98)
    tid = db.open_trade(symbol="AAPL", decision_id=None, group_id=gid, direction=1, qty=5)
    db.record_entry_fill(tid, 5, 100.0, datetime.now(timezone.utc))
    engine.ibkr.positions["TSLA"] = 10  # posição sem proteção e sem registo
    asyncio.run(engine._reconcile())
    assert db._query("SELECT status, exit_reason FROM trades WHERE id=?", (tid,))[0]["status"] == "CLOSED"
    assert "TSLA" in engine.ibkr.protections


def test_regular_hours():
    assert in_regular_hours(NY_OPEN_UTC + timedelta(minutes=30))
    assert not in_regular_hours(NY_OPEN_UTC - timedelta(hours=1))
    assert not in_regular_hours(datetime(2026, 10, 3, 14, 0, tzinfo=timezone.utc))


class _FixedDatetime(datetime):
    """datetime.now() fixo a meio da sessão de NY para os gates de horário."""

    @classmethod
    def now(cls, tz=None):
        fixed = NY_OPEN_UTC + timedelta(hours=2)
        return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)
