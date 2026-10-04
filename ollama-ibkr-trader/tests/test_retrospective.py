import asyncio
from datetime import datetime, timedelta, timezone

from trader.config import Settings
from trader.database import Database
from trader.ollama_brain import OllamaBrain
from trader.retrospective import Retrospective, classify


class FakeBrain(OllamaBrain):
    """OllamaBrain sem rede: o resumo LLM devolve uma lição fixa."""

    def chat(self, system, user, *, json_mode=True, temperature=None):  # noqa: D401
        return '["Nunca compres TSLA nos primeiros 5 minutos após a abertura."]'


def _seed(db, now, symbol, action, conf, price, rsi, sma_fast, sma_slow, minutes_ago):
    did = db.insert_decision(
        symbol=symbol, model="m", action=action, confidence=conf, reason="r",
        snapshot={"price": price, "rsi": rsi, "sma_fast": sma_fast, "sma_slow": sma_slow, "ema": price,
                  "change_5m_pct": 0, "change_30m_pct": 0},
        position_qty=0, net_liq=10000, prompt_version=0, parse_ok=True, raw_response="{}",
    )
    ts = (now - timedelta(minutes=minutes_ago)).astimezone(timezone.utc).isoformat()
    db._execute("UPDATE decisions SET ts=? WHERE id=?", (ts, did))
    return did


def test_classify():
    assert classify("BUY", 0.5) == "correct"
    assert classify("BUY", -0.5) == "wrong"
    assert classify("BUY", 0.05) == "neutral"
    assert classify("SELL", -0.3) == "correct"
    assert classify("SELL", 0.3) == "wrong"
    assert classify("HOLD", 1.5) == "missed"
    assert classify("HOLD", 0.2) == "correct"


def test_retrospective_builds_lessons_and_raises_threshold(tmp_path):
    settings = Settings()
    settings.min_confidence = 0.65
    settings.retro_use_llm_summary = True
    db = Database(":memory:")
    brain = FakeBrain(settings, db)
    now = datetime.now(timezone.utc)

    # Série de preços sintética: TSLA cai 2% ao longo de 2h.
    series = [(now - timedelta(minutes=120 - i), 200.0 * (1 - 0.02 * i / 120)) for i in range(121)]

    # Dois BUYs errados em sobrecompra, com confiança alta, contra a tendência.
    _seed(db, now, "TSLA", "BUY", 0.9, 200.0, 78, 198, 197, 110)
    _seed(db, now, "TSLA", "BUY", 0.85, 199.0, 74, 199.5, 200.5, 90)
    # Um SELL certo com confiança menor.
    _seed(db, now, "TSLA", "SELL", 0.7, 198.5, 55, 199.5, 200.5, 70)
    # Resposta inválida registada.
    did = _seed(db, now, "TSLA", "HOLD", 0.0, 198.0, 50, 199, 200, 60)
    db._execute("UPDATE decisions SET parse_ok=0 WHERE id=?", (did,))

    async def fetcher(symbol):
        return series

    retro = Retrospective(settings, db, brain, price_fetcher=fetcher)
    report = asyncio.run(retro.run(now))

    assert not report["skipped"]
    stats = report["stats"]
    assert stats["wrong"] == 2 and stats["correct"] >= 1
    assert stats["wrong_buy_overbought"] == 2  # RSI 78 e 74, ambos >= 70 e o preço caiu
    lessons = report["active_lessons"]
    assert any("confiança" in l.lower() for l in lessons)
    assert any("JSON" in l for l in lessons)
    assert any("TSLA" in l for l in lessons)  # lição do LLM falso ou do pior ativo
    assert report["min_confidence"] > 0.65
    assert settings.min_confidence == report["min_confidence"]
    assert brain.prompt_version == report["prompt_version"]
    assert "LIÇÕES DA RETROSPETIVA" in brain.system_prompt()
    assert len(lessons) <= settings.retro_max_lessons


def test_retrospective_without_decisions_is_skipped():
    settings = Settings()
    db = Database(":memory:")
    brain = FakeBrain(settings, db)
    report = asyncio.run(Retrospective(settings, db, brain).run())
    assert report["skipped"]


def test_retrospective_falls_back_to_decision_prices():
    settings = Settings()
    settings.retro_use_llm_summary = False
    db = Database(":memory:")
    brain = FakeBrain(settings, db)
    now = datetime.now(timezone.utc)
    _seed(db, now, "AAPL", "BUY", 0.8, 100.0, 50, 99, 98, 90)
    _seed(db, now, "AAPL", "HOLD", 0.3, 101.0, 55, 99, 98, 50)  # +1% depois de 40 min -> BUY correto
    report = asyncio.run(Retrospective(settings, db, brain).run(now))
    assert report["stats"]["decisions_evaluated"] >= 1
    assert report["stats"]["correct"] == 1
