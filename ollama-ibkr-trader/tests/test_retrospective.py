import asyncio
from datetime import datetime, timedelta, timezone

from trader.analytics import Analytics
from trader.calibration import Calibrator
from trader.config import Settings
from trader.database import Database
from trader.lessons import LessonEngine
from trader.ollama_brain import OllamaBrain
from trader.retrospective import Retrospective
from trader.settlement import Settler


def _seed(db, now, action, rsi, conf, minutes_ago):
    did = db.insert_decision(symbol="TSLA", model="m", action=action, confidence=conf, reason="r",
                             snapshot={"price": 100.0, "rsi": rsi, "sma_fast": 100, "sma_slow": 100}, position_qty=0,
                             net_liq=1e5, prompt_version=0, parse_ok=True, raw_response="",
                             extra={"verbal_conf": conf, "agree_frac": 0.8, "atr": 0.3, "hour_ny": 11, "regime": "alta"})
    db._execute("UPDATE decisions SET ts=? WHERE id=?", ((now - timedelta(minutes=minutes_ago)).isoformat(), did))
    return did


def test_retrospective_settles_builds_lessons_and_reports():
    s = Settings()
    s.calibration_min_samples = 10_000  # não ajusta aqui
    db = Database(":memory:")
    brain = OllamaBrain(s, db)
    now = datetime.now(timezone.utc)
    ids = []
    for i in range(16):
        ids.append(_seed(db, now, "BUY", 80, 0.9, 120 + i * 5))  # sobrecompra, preço cai -> erradas
    for i in range(16):
        ids.append(_seed(db, now, "BUY", 50, 0.7, 400 + i * 5))  # neutras, preço sobe -> certas

    def price_at(symbol, when):
        # decisões antigas (>= 400 min) sobem; recentes caem
        age = (now - when).total_seconds() / 60
        return 101.0 if age > 300 else 99.0

    settler = Settler(s, db, price_at)
    retro = Retrospective(s, db, brain, settler, LessonEngine(s, db), Calibrator(s, db), Analytics(s, db))
    report = asyncio.run(retro.run(now))
    assert not report["skipped"]
    assert report["stats"]["settled_now"] == 32
    assert any("sobrecompra" in l for l in report["new_lessons"])
    assert "terminad" not in " ".join(report["new_lessons"]).lower()
    assert brain.prompt_version == report["prompt_version"]
    assert "PADRÕES MEDIDOS" in brain.system_prompt()
    assert report["gates"]["all_passed"] is False and report["report_markdown"].startswith("# Relatório")
    assert db.latest_prompt_version()["lessons"] == report["active_lessons"]


def test_retrospective_without_data_is_graceful():
    s = Settings()
    db = Database(":memory:")
    brain = OllamaBrain(s, db)
    retro = Retrospective(s, db, brain, Settler(s, db, lambda *_: None), LessonEngine(s, db), Calibrator(s, db), Analytics(s, db))
    report = asyncio.run(retro.run(full_report=False))
    assert report["stats"]["decisions_total"] == 0 and report["new_lessons"] == []
