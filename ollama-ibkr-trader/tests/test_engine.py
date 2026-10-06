"""Motor com cliente IBKR falso: política partilhada, execução, fills, reconciliação."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from trader.calibration import ConfidenceSignals
from trader.config import Settings
from trader.database import Database
from trader.ollama_brain import Decision, DecisionOutcome, OllamaBrain
from trader.trading_engine import TradingEngine, build_decision_context, in_regular_hours
from trader.ui_bus import UIBus
from tests.helpers import FakeIBKR, make_bars, NY_OPEN_UTC

NOW = NY_OPEN_UTC + timedelta(hours=2)


class _FixedDatetime(datetime):
    """datetime.now() fixo a meio da sessão de NY para os gates de horário."""

    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


def make_engine(bars=None, **overrides):
    settings = Settings()
    settings.min_confidence = 0.65
    settings.signal_persistence_cycles = 1
    settings.llm_min_agreement = 0.6
    settings.skip_open_minutes = 0
    settings.max_entry_slippage_pct = 0.003
    for k, v in overrides.items():
        setattr(settings, k, v)
    db = Database(":memory:")
    engine = TradingEngine(settings, db, UIBus(), OllamaBrain(settings, db))
    # velas até "agora" para que a decisão não expire
    engine.ibkr = FakeIBKR(bars or {"AAPL": make_bars(600, vol=0.2), "TSLA": make_bars(600, vol=0.3, seed=2)})
    engine.gate.events = None
    engine.trading_enabled = True
    return engine, db, settings


def ctx_for(engine, symbol="AAPL"):
    ctx = build_decision_context(engine.settings, symbol, engine.ibkr.bars_as_list(symbol))
    assert ctx is not None
    return ctx


def outcome(action, conf=0.8, agree=1.0, review=False):
    d = Decision(action, conf, "ok") if not review else Decision.review("x")
    return DecisionOutcome(d, [{"acao": action, "parse_ok": True}] * 5, agree, agree, -0.2, "h", review=review)


def run_exec(engine, db, out, position=0.0, symbol="AAPL", generation=None):
    ctx = ctx_for(engine, symbol)
    state = engine.ibkr.portfolio_state()
    cal = engine.calibrator.probability(ConfidenceSignals(out.decision.confianca, out.agree_frac, out.margin, out.action_logprob)) \
        if out.decision.acao in ("BUY", "SELL") else None
    did = db.insert_decision(symbol=symbol, model="m", action=out.decision.acao, confidence=out.decision.confianca,
                             reason="r", snapshot=ctx.snapshot.to_dict(), position_qty=position, net_liq=state["net_liq"],
                             prompt_version=0, parse_ok=out.decision.parse_ok, raw_response="",
                             extra={"agree_frac": out.agree_frac, "atr": ctx.atr, "market_ts": ctx.snapshot.bar_time.isoformat()})
    gen = engine.generation if generation is None else generation
    asyncio.run(engine._execute(out, cal, ctx, did, gen, (None, 0), None))
    return db._query("SELECT * FROM decisions WHERE id=?", (did,))[0]


def fill(engine, order_id, symbol, side, shares, price, exec_id, commission=None):
    report = SimpleNamespace(commission=commission) if commission is not None else None
    f = SimpleNamespace(contract=SimpleNamespace(symbol=symbol, secType="STK"),
                        execution=SimpleNamespace(execId=exec_id, orderId=order_id, side=side, shares=shares,
                                                  price=price, time=NOW, acctNumber=engine.ibkr.account),
                        commissionReport=report)
    engine._on_fill(SimpleNamespace(), f)
    return f


def test_context_uses_complete_aggregated_bars_and_dynamics():
    engine, _, s = make_engine()
    ctx = ctx_for(engine)
    assert ctx.dynamics is not None and ctx.atr is not None and ctx.atr > 0
    assert ctx.snapshot.bars_available == len(ctx.agg_bars)
    # 600 velas de 1 min, última descartada (em formação) -> 599 fechadas -> 119 buckets completos de 5 min
    assert len(ctx.agg_bars) == 119


def test_buy_sizes_by_atr_places_limit_bracket_and_tracks_pending_entry(monkeypatch):
    engine, db, s = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    row = run_exec(engine, db, outcome("BUY", 0.9, 1.0))
    assert row["executed"] == 1, row["skip_reason"]
    symbol, action, qty, price, stop, tp, limit = engine.ibkr.brackets[0]
    assert action == "BUY" and stop < price < tp
    assert limit == pytest.approx(price * (1 + s.max_entry_slippage_pct))
    assert qty * (price - stop) <= 100_000 * s.risk_per_trade_pct * 1.05
    trade = db.open_trades("AAPL")[0]
    assert trade["stop_price"] == stop and row["threshold_used"] >= s.min_confidence
    assert len(engine._pending_entries) == 1


def test_low_agreement_low_confidence_hold_review_skipped(monkeypatch):
    engine, db, _ = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    assert "acordo" in run_exec(engine, db, outcome("BUY", 0.9, 0.4))["skip_reason"]
    assert "limiar" in run_exec(engine, db, outcome("BUY", 0.2, 1.0))["skip_reason"]
    assert run_exec(engine, db, outcome("HOLD"))["skip_reason"] == "HOLD"
    assert run_exec(engine, db, outcome("BUY", review=True))["executed"] == 0
    assert engine.ibkr.brackets == []


def test_persistence_requires_consecutive_signals(monkeypatch):
    engine, db, s = make_engine(signal_persistence_cycles=2)
    engine.persistence.cycles = 2
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    assert "persistente" in run_exec(engine, db, outcome("BUY"))["skip_reason"]
    assert run_exec(engine, db, outcome("BUY"))["executed"] == 1
    assert len(engine.ibkr.brackets) == 1


def test_pending_entry_and_max_positions_block(monkeypatch):
    engine, db, s = make_engine(max_open_positions=1)
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    assert run_exec(engine, db, outcome("BUY"))["executed"] == 1
    # a entrada pendente conta como posição: segundo ativo recusado no mesmo ciclo
    assert "máximo de posições" in run_exec(engine, db, outcome("BUY"), symbol="TSLA")["skip_reason"]


def test_opposite_signal_closes_with_state_machine_and_net_pnl(monkeypatch):
    engine, db, _ = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    run_exec(engine, db, outcome("BUY"))
    qty = engine.ibkr.brackets[0][2]
    fill(engine, 100, "AAPL", "BOT", qty, 100.0, "e1")
    assert engine._pending_entries == {}
    engine.ibkr.positions["AAPL"] = qty
    row = run_exec(engine, db, outcome("SELL", 0.9, 1.0), position=qty)
    assert row["executed"] == 1 and engine.ibkr.closes == ["AAPL"]
    assert "AAPL" in engine._pending_close
    close_group = db._query("SELECT * FROM order_groups WHERE role='CLOSE'")[0]
    # fill parcial mantém o bloqueio; outro sinal contrário é recusado
    fill(engine, close_group["parent_order_id"], "AAPL", "SLD", qty // 2, 102.0, "e2")
    assert "AAPL" in engine._pending_close
    assert "pendente" in run_exec(engine, db, outcome("SELL", 0.9, 1.0), position=qty - qty // 2)["skip_reason"]
    fill(engine, close_group["parent_order_id"], "AAPL", "SLD", qty - qty // 2, 102.0, "e3")
    assert "AAPL" not in engine._pending_close
    closed = db.trades_since(datetime(2000, 1, 1, tzinfo=timezone.utc))[0]
    assert closed["status"] == "CLOSED" and closed["exit_reason"] == "SIGNAL"
    assert closed["gross_pnl"] == pytest.approx(2.0 * qty)
    assert closed["pnl"] == pytest.approx(closed["gross_pnl"] - closed["commission"])


def test_commission_report_adjusts_exactly_the_allocated_trade(monkeypatch):
    engine, db, _ = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    run_exec(engine, db, outcome("BUY"))
    qty = engine.ibkr.brackets[0][2]
    fill(engine, 100, "AAPL", "BOT", qty, 100.0, "e1")
    trade = db.open_trades("AAPL")[0]
    estimated = trade["commission"]
    report = SimpleNamespace(commission=estimated + 0.75)
    f = SimpleNamespace(execution=SimpleNamespace(execId="e1", orderId=100), contract=SimpleNamespace(symbol="AAPL"))
    engine._on_commission(SimpleNamespace(), f, report)
    trade = db.open_trades("AAPL")[0]
    assert trade["commission"] == pytest.approx(estimated + 0.75)
    assert trade["pnl"] == pytest.approx(-(estimated + 0.75))
    engine._on_commission(SimpleNamespace(), f, report)  # repetição não duplica
    assert db.open_trades("AAPL")[0]["commission"] == pytest.approx(estimated + 0.75)


def test_reconcile_imports_fills_closes_orphans_and_persists_protection():
    engine, db, s = make_engine()
    s.symbols = ["AAPL", "TSLA", "SPY"]
    s.benchmark_symbol = "SPY"
    # trade órfão com posição inexistente -> RECONCILED
    gid = db.insert_order_group(symbol="AAPL", decision_id=None, role="ENTRY", direction=1, qty=5, parent_order_id=1,
                                tp_order_id=2, sl_order_id=3, ref_price=100, tp_price=104, sl_price=98)
    tid = db.open_trade(symbol="AAPL", decision_id=None, group_id=gid, direction=1, qty=5)
    db.record_entry_fill(tid, 5, 100.0, NOW)
    # posição sem registo e sem stop (TSLA) e posição no benchmark negociado (SPY): ambas protegidas e persistidas
    engine.ibkr.positions["TSLA"] = 10
    engine.ibkr.positions["SPY"] = 3
    # posição externa fora dos ativos: não gerida
    engine.ibkr.positions["NVDA"] = 7
    asyncio.run(engine._reconcile())
    assert db._query("SELECT status FROM trades WHERE id=?", (tid,))[0]["status"] == "CLOSED"
    assert set(engine.ibkr.protections) == {"TSLA", "SPY"}
    tsla = db.open_trades("TSLA")[0]
    assert tsla["filled_qty"] == 10 and tsla["stop_price"] and tsla["tp_price"]
    group = db._query("SELECT * FROM order_groups WHERE symbol='TSLA'")[0]
    assert group["sl_order_id"] and group["tp_order_id"]
    # o fill do stop reconstruído fecha o trade certo
    fill(engine, group["sl_order_id"], "TSLA", "SLD", 10, 97.0, "x1")
    assert db._query("SELECT status, exit_reason FROM trades WHERE id=?", (tsla["id"],))[0]["exit_reason"] == "SL"


def test_regular_hours():
    assert in_regular_hours(NY_OPEN_UTC + timedelta(minutes=30))
    assert not in_regular_hours(NY_OPEN_UTC - timedelta(hours=1))
    assert not in_regular_hours(datetime(2026, 10, 3, 14, 0, tzinfo=timezone.utc))
