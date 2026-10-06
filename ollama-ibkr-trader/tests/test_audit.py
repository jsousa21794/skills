"""Regressões dos achados da auditoria 1.0.2 (um teste por achado corrigido)."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from trader import __version__
from trader.calibration import ConfidenceSignals
from trader.config import Settings
from trader.database import Database
from trader.ibkr_client import IBKRClient
from trader.indicators import aggregate_bars, build_snapshot
from trader.ollama_brain import Decision, DecisionOutcome, OllamaBrain, parse_decision
from trader.risk import PositionSizer, RiskGate
from trader.settlement import Settler, first_touch_label
from trader.ui_bus import UIBus
from tests.helpers import FakeIBKR, FakeOrder, make_bars, NY_OPEN_UTC
from tests.test_brain_pipeline import FakeOllama
from tests.test_engine import NOW, _FixedDatetime, fill, make_engine, outcome, own_position, run_exec


# ---------------------------------------------------------------- F01
def test_f01_stop_or_mode_change_invalidates_in_flight_decision(monkeypatch):
    engine, db, _ = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    old_generation = engine.generation
    asyncio.run(engine.stop_trading())
    assert engine.generation == old_generation + 1
    engine.trading_enabled = True  # mesmo com o ciclo ligado de novo, a decisão antiga morre
    row = run_exec(engine, db, outcome("BUY"), generation=old_generation)
    assert row["executed"] == 0 and "invalidada" in row["skip_reason"]
    assert engine.ibkr.brackets == []


# ---------------------------------------------------------------- F02
def test_f02_close_does_not_send_order_when_children_already_closed(monkeypatch):
    engine, db, _ = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    own_position(engine, db, "AAPL", 100)
    engine.ibkr.close_behaviour = "closed_by_children"
    row = run_exec(engine, db, outcome("SELL"), position=100)
    assert row["executed"] == 0 and "já fechada" in row["skip_reason"]
    assert engine.ibkr.closes == [] and "AAPL" not in engine._pending_close
    engine.ibkr.positions["AAPL"] = 100
    engine.ibkr.close_behaviour = "abort"
    row = run_exec(engine, db, outcome("SELL"), position=100)
    assert "abortado" in row["skip_reason"] and "AAPL" in engine.ibkr.protections  # cobertura reposta


def test_f02_real_client_waits_cancellations_and_requantifies():
    s = Settings()
    client = IBKRClient(s)
    client.ib = SimpleNamespace(isConnected=lambda: True, cancelOrder=lambda o: None, placeOrder=None,
                                client=SimpleNamespace(getReqId=lambda: 999))
    calls = {"qty": [100, 100, 0]}  # 100 antes (leitura + reconciliação das saídas), 0 depois (stop executou)
    client.position_qty = lambda symbol: calls["qty"].pop(0) if len(calls["qty"]) > 1 else calls["qty"][0]
    child = SimpleNamespace(order=SimpleNamespace(orderId=5, action="SELL", orderRef="OllamaIBKRTrader", account=""),
                            orderStatus=SimpleNamespace(status="Submitted", remaining=100, filled=0))
    child.isDone = lambda: child.orderStatus.status != "Submitted"  # termina quando o cancelamento é confirmado

    def cancel(order):
        child.orderStatus.status = "Cancelled"

    client.ib.cancelOrder = cancel
    client.open_trades_for = lambda symbol, ours_only=True: [child]

    async def qualify(symbol):
        return SimpleNamespace(conId=1)
    client.qualify = qualify
    result = asyncio.run(client.close_position("AAPL"))
    assert result["closed_by_children"] and result["order_id"] is None
    assert client.is_intentional_cancel(5)  # o cancelamento dos filhos fica registado como intencional (N02)


# ---------------------------------------------------------------- F03
def test_f03_cancelled_close_releases_guard():
    engine, db, _ = make_engine()
    engine._pending_close["AAPL"] = {"order_id": 500, "group_id": 1, "qty": 10.0, "filled": 0.0, "ts": NOW}
    trade = SimpleNamespace(orderStatus=SimpleNamespace(status="Cancelled"),
                            order=SimpleNamespace(orderId=500, action="SELL"), contract=SimpleNamespace(symbol="AAPL"))
    engine._on_order_status(trade)
    assert "AAPL" not in engine._pending_close


# ---------------------------------------------------------------- F04
def test_f04_protective_coverage_checks_side_quantity_and_state():
    s = Settings()
    client = IBKRClient(s)
    client.ib = SimpleNamespace(isConnected=lambda: True)
    client.position_qty = lambda symbol: 100.0
    wrong = SimpleNamespace(order=FakeOrder(1, "BUY", 1, "STP", "PendingCancel"), orderStatus=SimpleNamespace(status="PendingCancel", remaining=1, filled=0))
    client.open_trades_for = lambda symbol, ours_only=True: [wrong]
    assert not client.has_protective_orders("AAPL")
    small = SimpleNamespace(order=FakeOrder(2, "SELL", 40, "STP"), orderStatus=SimpleNamespace(status="Submitted", remaining=40, filled=0))
    client.open_trades_for = lambda symbol, ours_only=True: [small]
    assert client.protective_coverage("AAPL") == 40 and not client.has_protective_orders("AAPL")
    full = SimpleNamespace(order=FakeOrder(3, "SELL", 60, "STP"), orderStatus=SimpleNamespace(status="PreSubmitted", remaining=60, filled=0))
    client.open_trades_for = lambda symbol, ours_only=True: [small, full]
    assert client.has_protective_orders("AAPL")


# ---------------------------------------------------------------- F05
def test_f05_lost_protection_is_restored():
    engine, db, _ = make_engine()
    engine.ibkr.positions["AAPL"] = 50
    asyncio.run(engine._reprotect("AAPL"))
    assert "AAPL" in engine.ibkr.protections
    # supervisor por ciclo também repõe
    engine.ibkr.protections.clear()
    engine.ibkr.orders.clear()
    asyncio.run(engine._supervise())
    assert "AAPL" in engine.ibkr.protections


# ---------------------------------------------------------------- F06 / F07 / F09 cobertos em test_engine (reconcile)
# ---------------------------------------------------------------- F10
def test_f10_fractional_positions_are_not_truncated_to_zero():
    assert IBKRClient._order_qty(0.75) is None
    assert IBKRClient._order_qty(10.75) == 10
    assert IBKRClient._order_qty(-3.0) == 3


# ---------------------------------------------------------------- F11
def test_f11_pending_entry_reserves_funds_in_same_cycle(monkeypatch):
    engine, db, s = make_engine(max_open_positions=5)
    engine.ibkr.available_funds = 6_000.0
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    first = run_exec(engine, db, outcome("BUY"))
    assert first["executed"] == 1
    reserved = sum(e["notional"] for e in engine._pending_entries.values())
    assert reserved > 0
    second = run_exec(engine, db, outcome("BUY"), symbol="TSLA")
    assert second["executed"] == 0 and ("fundos" in second["skip_reason"] or "quantidade" in second["skip_reason"])


# ---------------------------------------------------------------- F12
def test_f12_stale_decision_is_not_executed(monkeypatch):
    engine, db, s = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    # velas terminam 3 horas antes de "agora": decisão expirada
    engine.ibkr._bars["AAPL"] = make_bars(600, start=NY_OPEN_UTC - timedelta(hours=14), vol=0.2)
    row = run_exec(engine, db, outcome("BUY"))
    assert "expirada" in row["skip_reason"]


# ---------------------------------------------------------------- F14
def test_f14_negative_or_unknown_funds_block_entries(monkeypatch):
    engine, db, _ = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    engine.ibkr.available_funds = -100.0
    assert "fundos" in run_exec(engine, db, outcome("BUY"))["skip_reason"]
    engine.ibkr.available_funds = None
    assert "desconhecidos" in run_exec(engine, db, outcome("BUY"))["skip_reason"]


# ---------------------------------------------------------------- F15
def test_f15_eur_account_values_and_usd_conversion():
    s = Settings()
    client = IBKRClient(s)
    AV = lambda tag, cur, val: SimpleNamespace(tag=tag, currency=cur, value=str(val), account="U1")
    client.ib = SimpleNamespace(isConnected=lambda: True, accountValues=lambda: [
        AV("NetLiquidation", "EUR", 10000), AV("NetLiquidation", "BASE", 10000), AV("ExchangeRate", "USD", 0.9),
        AV("AvailableFunds", "EUR", 5000), AV("ExchangeRate", "EUR", 1.0)])
    client.account = "U1"
    assert client._detect_base_currency() == "EUR"
    assert client._account_value("NetLiquidation") == 10000
    assert client._to_usd(9000) == pytest.approx(10000)  # 9000 EUR = 10000 USD a 0,9 EUR/USD
    client2 = IBKRClient(s)
    client2.ib = SimpleNamespace(isConnected=lambda: True, accountValues=lambda: [AV("NetLiquidation", "EUR", 1234)])
    assert client2._account_value("NetLiquidation") == 1234  # sem linha BASE: usa a moeda base


# ---------------------------------------------------------------- F16
def test_f16_paper_and_live_use_separate_databases(tmp_path):
    s = Settings()
    assert s.db_path().name == "trader_live.sqlite3"
    s.trading_mode = "paper"
    assert s.db_path().name == "trader_paper.sqlite3"
    db = Database(tmp_path / "a.sqlite3")
    db.set_kv("k", "a")
    db.switch_path(tmp_path / "b.sqlite3")
    assert db.get_kv("k") is None
    db.set_kv("k", "b")
    db.switch_path(tmp_path / "a.sqlite3")
    assert db.get_kv("k") == "a"


# ---------------------------------------------------------------- F17
def test_f17_intraday_drawdown_is_detected():
    s = Settings()
    db = Database(":memory:")
    gate = RiskGate(s, db)
    now = datetime.now(timezone.utc)
    for i, eq in enumerate([100_000, 100_500, 90_000, 90_000]):
        db.snapshot_pnl(net_liq=eq, cash=0, unrealized=0, realized=0, ts=now - timedelta(minutes=60 - i * 10))
    assert gate.recent_drawdown_pct(now) == pytest.approx(10.45, abs=0.05)


# ---------------------------------------------------------------- F18
def test_f18_pauses_and_kill_switch_survive_restart():
    s = Settings()
    db = Database(":memory:")
    now = datetime.now(timezone.utc)
    gate = RiskGate(s, db)
    gate.pause("stoploss_guard", now + timedelta(hours=1), "3 stops")
    gate.update_day_baseline(100_000.0, now)
    s.daily_loss_limit_pct = 0.03
    assert not gate.check_global(equity=96_000.0, now=now).allowed and gate.halted
    fresh = RiskGate(s, db)  # "reinício"
    assert "stoploss_guard" in fresh.active_pauses(now) and fresh.halted
    fresh.update_day_baseline(100_000.0, now)
    assert not fresh.check_global(equity=100_000.0, now=now).allowed  # equity recuperou, bloqueio mantém-se


# ---------------------------------------------------------------- F19
def test_f19_consecutive_losses_do_not_reblock_after_pause():
    s = Settings()
    s.consecutive_loss_halt = 3
    s.stoploss_guard_count = 99  # isolar o travão de perdas seguidas
    db = Database(":memory:")
    gate = RiskGate(s, db)
    t0 = NY_OPEN_UTC + timedelta(hours=1)
    gate.update_day_baseline(100_000.0, t0)
    from tests.test_risk import _closed_trade
    for i in range(3):
        _closed_trade(db, -1.0, t0 - timedelta(minutes=10 * (i + 1)), "SL")
    assert not gate.check_global(equity=99_000.0, now=t0).allowed
    next_day = t0 + timedelta(days=1)
    gate2 = RiskGate(s, db)
    gate2.update_day_baseline(99_000.0, next_day)
    assert gate2.check_global(equity=99_000.0, now=next_day).allowed  # as mesmas perdas não voltam a bloquear


# ---------------------------------------------------------------- F20
def test_f20_profit_releases_stoploss_pause():
    s = Settings()
    db = Database(":memory:")
    now = NY_OPEN_UTC + timedelta(hours=1)
    gate = RiskGate(s, db)
    gate.update_day_baseline(100_000.0, now)
    gate.pause("stoploss_guard", now + timedelta(hours=1), "x")
    assert not gate.check_global(equity=99_500.0, now=now).allowed
    assert gate.check_global(equity=101_000.0, now=now).allowed


# ---------------------------------------------------------------- F21
def test_f21_config_validation():
    s = Settings()
    w = s.apply({"live_confirmed": "false", "stop_loss_pct": 1.5, "risk_per_trade_pct": -0.1, "llm_samples": "7",
                 "cycle_seconds": float("nan"), "symbols": "AAPL, MSFT"})
    assert s.live_confirmed is False and s.stop_loss_pct == 0.02 and s.risk_per_trade_pct == 0.10
    assert s.llm_samples == 7 and s.cycle_seconds == 60 and s.symbols == ["AAPL", "MSFT"]
    assert len(w) == 3


# ---------------------------------------------------------------- F22
def test_f22_failed_samples_do_not_inflate_consensus():
    import requests
    s = Settings()
    s.llm_samples = 5
    db = Database(":memory:")
    brain = FakeOllama(s, db, actions=["BUY"])
    calls = {"n": 0}
    original = brain.chat_raw

    def flaky(system, user, **kw):
        calls["n"] += 1
        if kw.get("schema") is None and (kw.get("seed") or 0) % 5 != 0:
            raise requests.Timeout()
        return original(system, user, **kw)
    brain.chat_raw = flaky
    snap = build_snapshot("AAPL", [100 + i * 0.1 for i in range(60)], [1] * 60, NOW)
    out = brain.decide(snap, {"net_liq": 1e5, "positions": []}, [])
    assert out.review and out.decision.acao == "REVIEW"


def test_f22_missing_ensemble_model_blocks():
    import requests
    s = Settings()
    s.llm_samples = 1
    s.ensemble_models = ["modelo-b"]
    db = Database(":memory:")
    brain = FakeOllama(s, db)
    original = brain.chat_raw

    def down(system, user, *, model=None, **kw):
        if model == "modelo-b":
            raise requests.ConnectionError()
        return original(system, user, model=model, **kw)
    brain.chat_raw = down
    snap = build_snapshot("AAPL", [100 + i * 0.1 for i in range(60)], [1] * 60, NOW)
    assert brain.decide(snap, {"net_liq": 1e5, "positions": []}, []).review


# ---------------------------------------------------------------- F23
def test_f23_parser_rejects_negations_and_nan():
    d = parse_decision('{"acao": "DO NOT BUY", "confianca": 0.9, "razao": "x"}')
    assert d.acao == "HOLD" and not d.parse_ok
    d = parse_decision('{"acao": "BUY", "confianca": NaN, "razao": "x"}')
    assert not d.parse_ok and d.error == "invalid_confidence"
    d = parse_decision("acao: not BUY, confianca: 0.9")
    assert not d.parse_ok


# ---------------------------------------------------------------- F24
def test_f24_stage2_cannot_reverse_stage1():
    s = Settings()
    s.llm_samples = 1
    db = Database(":memory:")
    brain = FakeOllama(s, db, actions=["HOLD"])
    original = brain.chat_raw

    def flip(system, user, **kw):
        data = original(system, user, **kw)
        if kw.get("schema") is not None:
            data["message"]["content"] = '{"acao": "BUY", "confianca": 0.9, "razao": "invertido"}'
        return data
    brain.chat_raw = flip
    snap = build_snapshot("AAPL", [100 + i * 0.1 for i in range(60)], [1] * 60, NOW)
    out = brain.decide(snap, {"net_liq": 1e5, "positions": []}, [])
    assert out.review and out.samples[0]["error"] == "stage_mismatch"


# ---------------------------------------------------------------- F25
def test_f25_logprob_comes_from_winning_action():
    s = Settings()
    s.llm_samples = 5
    db = Database(":memory:")
    brain = FakeOllama(s, db, actions=["SELL", "BUY", "BUY", "BUY", "BUY"])
    original = brain.chat_raw

    def tagged(system, user, **kw):
        data = original(system, user, **kw)
        if kw.get("schema") is not None:
            action = json.loads(data["message"]["content"])["acao"]
            data["logprobs"][-1]["logprob"] = -0.05 if action == "SELL" else -0.6
        return data
    brain.chat_raw = tagged
    snap = build_snapshot("AAPL", [100 + i * 0.1 for i in range(60)], [1] * 60, NOW)
    out = brain.decide(snap, {"net_liq": 1e5, "positions": []}, [])
    assert out.decision.acao == "BUY" and out.action_logprob == pytest.approx(-0.6)


# ---------------------------------------------------------------- F26 / F27
def test_f26_f27_horizons_in_bars_and_incomplete_bucket_dropped():
    closes = [100.0] * 50 + [101.0]
    snap = build_snapshot("A", closes, [1] * 51, NOW, bar_minutes=5)
    assert snap.change_5m_pct == pytest.approx(1.0)  # 1 vela de 5 min atrás
    bars = make_bars(23)
    assert len(aggregate_bars(bars, 5, drop_incomplete=True)) == 4


# ---------------------------------------------------------------- F28 / F29
def test_f28_settlement_respects_gap_and_market_ts_and_f29_first_touch():
    s = Settings()
    db = Database(":memory:")
    bars = make_bars(120, vol=0.0)
    for i, b in enumerate(bars):  # sobe 0,5% nas primeiras 10 velas, depois cai 3%, termina +1%
        b.close = b.open = 100.0 * (1 + (0.005 if i < 10 else -0.03 if i < 40 else 0.01))
        b.high, b.low = b.close + 0.05, b.close - 0.05
    index = {b.time: b for b in bars}
    def price_at(symbol, when):
        for b in bars:
            if b.time >= when:
                return b.close if (b.time - when).total_seconds() <= 600 else None
        return None
    def bars_between(symbol, start, end):
        return [b for b in bars if start <= b.time <= end]
    market_ts = bars[0].time
    did = db.insert_decision(symbol="AAPL", model="m", action="BUY", confidence=0.8, reason="r", snapshot={"price": 100.0},
                             position_qty=0, net_liq=1, prompt_version=0, parse_ok=True, raw_response="",
                             ts=market_ts + timedelta(minutes=2),  # decisão registada 2 min depois da vela
                             extra={"market_ts": market_ts.isoformat(), "stop_pct": 2.0, "tp_pct": 4.0, "atr": 0.5})
    Settler(s, db, price_at, bars_between).run(market_ts + timedelta(hours=1))
    row = db._query("SELECT * FROM decisions WHERE id=?", (did,))[0]
    assert row["correct"] == 0  # tocou o stop (-3%) antes do TP, apesar de terminar +1%
    # lacuna: sem vela a menos de 10 min do alvo -> não avaliada
    did2 = db.insert_decision(symbol="AAPL", model="m", action="BUY", confidence=0.8, reason="r", snapshot={"price": 100.0},
                              position_qty=0, net_liq=1, prompt_version=0, parse_ok=True, raw_response="",
                              ts=bars[-1].time + timedelta(minutes=1), extra={"market_ts": (bars[-1].time + timedelta(minutes=1)).isoformat()})
    Settler(s, db, price_at, bars_between).run(bars[-1].time + timedelta(hours=1))
    assert db._query("SELECT settled_ts FROM decisions WHERE id=?", (did2,))[0]["settled_ts"] is None
    assert first_touch_label(bars[:60], 100.0, 2.0, 4.0, 1) == 0


# ---------------------------------------------------------------- F31
def test_f31_learning_risk_multiplier_is_applied(monkeypatch):
    engine, db, s = make_engine(learning_risk_multiplier=0.5)
    engine.risk_multiplier = 0.5
    engine.gates_passed = False
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    row = run_exec(engine, db, outcome("BUY"))
    assert row["executed"] == 1
    _, _, qty, price, stop, _, _ = engine.ibkr.brackets[0]
    assert qty * (price - stop) <= 100_000 * s.risk_per_trade_pct * 0.5 * 1.05


# ---------------------------------------------------------------- F33 / F34 / F35
def test_f33_f34_f35_backtest_ledger_gaps_and_clock():
    from trader.backtest import Replayer
    s = Settings()
    s.llm_samples = 1
    s.signal_persistence_cycles = 1
    s.skip_open_minutes = 0
    s.min_confidence = 0.6
    s.llm_min_agreement = 0.5
    db = Database(":memory:")
    bars = make_bars(390 * 2, vol=0.15, drift=0.01)
    # gap: a vela 600 abre 5% abaixo
    for b in bars[600:]:
        b.open -= 5.0; b.high -= 5.0; b.low -= 5.0; b.close -= 5.0
    rep = Replayer(s, FakeOllama(s, db, actions=["BUY"]), db, equity=50_000.0)
    report = rep.run("AAPL", bars)
    sim = report["simulation"]
    assert abs(sim["end_equity"] - sim["start_equity"] - sim["ledger_pnl"]) < 1e-6
    # decisões datadas com o relógio da simulação, não com o relógio real
    ts = db._query("SELECT ts FROM decisions ORDER BY id LIMIT 1")[0]["ts"]
    assert ts.startswith("2026-10-05")
    # qualquer stop atingido num gap executa à abertura (pior do que o nível do stop)
    gapped = [t for t in rep.closed if t["exit_reason"] == "SL" and t["exit_ts"] >= bars[600].time.isoformat()]
    for t in gapped:
        assert float(t["exit_price"]) <= float(t["stop_price"]) + 1e-9
    assert report["gates"] is not None


# ---------------------------------------------------------------- F37
def test_f37_sentiment_count_is_real():
    engine, db, s = make_engine()
    s.sentiment_veto_min_news = 3
    assert not engine.gate.check_sentiment_veto("BUY", -0.9, 1).allowed is False or engine.gate.check_sentiment_veto("BUY", -0.9, 1).allowed
    assert not engine.gate.check_sentiment_veto("BUY", -0.9, 3).allowed


# ---------------------------------------------------------------- F38
def test_f38_close_commission_is_split_over_the_trades_it_closed(monkeypatch):
    engine, db, _ = make_engine()
    monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
    # dois trades abertos no mesmo ativo (um reconstruído), fechados por uma única ordem CLOSE
    for i in range(2):
        gid = db.insert_order_group(symbol="AAPL", decision_id=None, role="ENTRY", direction=1, qty=10, parent_order_id=10 + i,
                                    tp_order_id=20 + i, sl_order_id=30 + i, ref_price=100, tp_price=104, sl_price=98)
        tid = db.open_trade(symbol="AAPL", decision_id=None, group_id=gid, direction=1, qty=10)
        db.record_entry_fill(tid, 10, 100.0, NOW, 1.0)
    db.insert_order_group(symbol="AAPL", decision_id=None, role="CLOSE", direction=1, qty=20, parent_order_id=77,
                          tp_order_id=None, sl_order_id=None, ref_price=102, tp_price=None, sl_price=None)
    fill(engine, 77, "AAPL", "SLD", 20, 102.0, "c1")
    before = [t["commission"] for t in db.trades_since(datetime(2000, 1, 1, tzinfo=timezone.utc))]
    f = SimpleNamespace(execution=SimpleNamespace(execId="c1", orderId=77), contract=SimpleNamespace(symbol="AAPL"))
    engine._on_commission(SimpleNamespace(), f, SimpleNamespace(commission=3.0))  # real 3,0 vs estimada 1,0 -> +2,0 repartido
    after = [t["commission"] for t in db.trades_since(datetime(2000, 1, 1, tzinfo=timezone.utc))]
    assert all(a - b == pytest.approx(1.0) for a, b in zip(after, before))


# ---------------------------------------------------------------- F40
def test_f40_version_matches_release_line():
    assert __version__ == "1.0.9"
