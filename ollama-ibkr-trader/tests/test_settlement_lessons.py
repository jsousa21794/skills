from datetime import datetime, timedelta, timezone

from trader.config import Settings
from trader.database import Database
from trader.lessons import LessonEngine, rsi_regime
from trader.settlement import Settler, label


def test_label_rules():
    assert label("BUY", 0.5, 0.2, 0.5) == 1
    assert label("BUY", -0.5, 0.2, 0.5) == 0
    assert label("BUY", 0.05, 0.2, 0.5) is None
    assert label("SELL", -0.3, 0.2, 0.5) == 1
    assert label("HOLD", 1.0, 0.2, 0.5) == 0
    assert label("HOLD", 0.1, 0.2, 0.5) == 1


def _seed(db, now, symbol, action, conf, price, rsi, minutes_ago, hour_ny=11, atr=0.4):
    did = db.insert_decision(symbol=symbol, model="m", action=action, confidence=conf, reason="r",
                             snapshot={"price": price, "rsi": rsi, "sma_fast": price, "sma_slow": price},
                             position_qty=0, net_liq=1, prompt_version=0, parse_ok=True, raw_response="",
                             extra={"verbal_conf": conf, "agree_frac": 0.8, "atr": atr, "hour_ny": hour_ny, "regime": "alta"})
    db._execute("UPDATE decisions SET ts=? WHERE id=?", ((now - timedelta(minutes=minutes_ago)).isoformat(), did))
    return did


def test_settler_computes_return_alpha_and_label():
    s = Settings()
    s.settlement_horizon_minutes = 30
    db = Database(":memory:")
    now = datetime.now(timezone.utc)
    prices = {"AAPL": {0: 100.0, 30: 101.0}, "SPY": {0: 500.0, 30: 501.0}}

    def price_at(symbol, when):
        minutes = round((when - (now - timedelta(minutes=60))).total_seconds() / 60)
        table = prices[symbol]
        return table.get(min(table, key=lambda k: abs(k - minutes)))

    did = _seed(db, now, "AAPL", "BUY", 0.8, 100.0, 55, 60)
    pending = _seed(db, now, "AAPL", "BUY", 0.8, 100.0, 55, 10)  # ainda não venceu
    settled = Settler(s, db, price_at).run(now)
    assert settled == 1
    row = db._query("SELECT * FROM decisions WHERE id=?", (did,))[0]
    assert row["settled_return"] == 1.0 and row["correct"] == 1
    assert abs(row["alpha"] - (1.0 - 0.2)) < 1e-6
    assert db._query("SELECT settled_ts FROM decisions WHERE id=?", (pending,))[0]["settled_ts"] is None


def test_lessons_extract_failure_pattern_programmatically():
    s = Settings()
    db = Database(":memory:")
    now = datetime.now(timezone.utc)
    # 20 BUYs em sobrecompra: 3 certos; 20 BUYs neutros: 15 certos
    for i in range(20):
        did = _seed(db, now, "TSLA", "BUY", 0.9, 100, 78, 1000 + i)
        db.settle_decision(did, settled_price=99, settled_return=-1.0, bench_return=None, alpha=None,
                           correct=1 if i < 3 else 0, horizon_min=30)
    for i in range(20):
        did = _seed(db, now, "TSLA", "BUY", 0.7, 100, 50, 2000 + i)
        db.settle_decision(did, settled_price=101, settled_return=1.0, bench_return=None, alpha=None,
                           correct=1 if i < 15 else 0, horizon_min=30)
    engine = LessonEngine(s, db)
    lessons = engine.rebuild(now=now)
    keys = [l["key"] for l in lessons]
    assert "rsi:sobrecompra:BUY" in keys
    assert any("sobrecompra" in l["text"] and "3/20" in l["text"] for l in lessons)
    assert "calib:overconfident" in keys  # confiança 0.9 associada a erros
    top = engine.for_prompt("TSLA", rsi=80, hour_ny=11, regime="alta", k=2)
    assert len(top) == 2 and "sobrecompra" in top[0] + top[1]
    assert rsi_regime(80) == "sobrecompra" and rsi_regime(20) == "sobrevenda"
    assert len(db.active_lessons()) == len(lessons)


def test_settlement_counts_moves_below_cost_as_wrong():
    s = Settings()
    s.settlement_horizon_minutes = 30
    db = Database(":memory:")
    now = datetime.now(timezone.utc)
    did = _seed(db, now, "AAPL", "BUY", 0.8, 100.0, 55, 60)
    db.update_decision(did, cost_pct=0.6)  # custo ida+volta de 0,6% do notional
    Settler(s, db, lambda symbol, when: 100.3).run(now)  # +0,3% não cobre o custo
    row = db._query("SELECT settled_return, correct FROM decisions WHERE id=?", (did,))[0]
    assert row["settled_return"] == 0.3 and row["correct"] is None  # neutro: nem acerto nem erro
    did2 = _seed(db, now, "AAPL", "BUY", 0.8, 100.0, 55, 61)
    db.update_decision(did2, cost_pct=0.6)
    Settler(s, db, lambda symbol, when: 101.0).run(now)
    assert db._query("SELECT correct FROM decisions WHERE id=?", (did2,))[0]["correct"] == 1


def test_seed_lessons_import_is_idempotent_and_survives_rebuild(tmp_path):
    import json
    from trader.lessons import import_seed_lessons
    s = Settings()
    db = Database(":memory:")
    path = tmp_path / "seed.json"
    path.write_text(json.dumps({"lessons": [
        {"key": "open_window", "text": "Não abrir posições nos primeiros 15 minutos.", "source": "arXiv 1009.4785"},
        {"key": "rsi_extreme", "text": "Não comprar com RSI acima de 75.", "symbol": None},
        {"key": "", "text": "ignorada"},
    ]}), encoding="utf-8")
    assert import_seed_lessons(db, str(path)) == 2
    assert import_seed_lessons(db, str(path)) == 2
    active = db.active_lessons()
    assert len(active) == 2 and all(l["key"].startswith("seed:") for l in active)
    assert any("[fonte: arXiv 1009.4785]" in l["text"] for l in active)
    LessonEngine(s, db).rebuild()  # sem decisões: seeds continuam ativas
    assert len(db.active_lessons()) == 2
    assert LessonEngine(s, db).for_prompt("AAPL", k=1)


def test_seed_lessons_file_imports_and_context_relevance_applies():
    from pathlib import Path
    from trader.lessons import import_seed_lessons
    s = Settings()
    db = Database(":memory:")
    path = Path(__file__).resolve().parent.parent / "data" / "seed_lessons.json"
    n = import_seed_lessons(db, str(path))
    assert n >= 50
    engine = LessonEngine(s, db)
    at_open = engine.for_prompt("AAPL", rsi=50, hour_ny=9, regime="lateral", k=3)
    assert any("abertura" in t.lower() for t in at_open)
    downtrend = engine.for_prompt("AAPL", rsi=25, hour_ny=11, regime="baixa", k=3)
    assert any("facas" in t.lower() for t in downtrend)
    # o prompt nunca recebe mais do que k lições
    assert len(engine.for_prompt("AAPL", k=3)) == 3
