import math
import random

from trader.calibration import Calibrator, ConfidenceSignals, PlattModel, brier, execution_threshold, fit_logistic
from trader.config import Settings
from trader.database import Database


def test_logistic_fit_recovers_signal():
    rng = random.Random(0)
    X, y = [], []
    for _ in range(400):
        agree = rng.choice([0.4, 0.6, 0.8, 1.0])
        verbal = rng.uniform(0.5, 0.95)
        margin = agree - 0.2
        lp = math.log(rng.uniform(0.3, 0.99))
        p = 1 / (1 + math.exp(-(4 * (agree - 0.7))))  # só o acordo tem informação
        X.append([verbal, agree, margin, lp])
        y.append(1 if rng.random() < p else 0)
    w, b = fit_logistic(X, y)
    assert w[1] > 1.0  # peso do acordo claramente positivo
    assert abs(w[0]) < abs(w[1])  # confiança verbal pesa menos
    model = PlattModel(w, b, len(y), "2026-01-01T00:00:00+00:00", 0.0, sum(y) / len(y))
    hi = model.predict(ConfidenceSignals(0.8, 1.0, 0.8, math.log(0.9)))
    lo = model.predict(ConfidenceSignals(0.8, 0.4, 0.1, math.log(0.9)))
    assert hi > lo + 0.2


def test_heuristic_when_not_fitted_and_fit_from_db():
    s = Settings()
    s.calibration_min_samples = 30
    db = Database(":memory:")
    cal = Calibrator(s, db)
    assert not cal.is_fitted
    p = cal.probability(ConfidenceSignals(0.9, 1.0, 1.0, None))
    assert 0.8 <= p <= 1.0
    assert cal.probability(ConfidenceSignals(0.9, 0.4, 0.0, None)) < p
    rng = random.Random(1)
    for i in range(60):
        agree = rng.choice([0.4, 0.6, 0.8, 1.0])
        did = db.insert_decision(symbol="AAPL", model="m", action="BUY", confidence=0.8, reason="r",
                                 snapshot={"price": 100}, position_qty=0, net_liq=1, prompt_version=0, parse_ok=True,
                                 raw_response="", extra={"verbal_conf": 0.8, "agree_frac": agree,
                                                         "samples_json": [{"acao": "BUY"}] * int(agree * 5) + [{"acao": "HOLD"}] * (5 - int(agree * 5))})
        db.settle_decision(did, settled_price=101, settled_return=1.0, bench_return=None, alpha=None,
                           correct=1 if rng.random() < agree else 0, horizon_min=30)
    model = cal.fit_from_db()
    assert model is not None and cal.is_fitted
    assert db.get_kv("platt_model")
    assert Calibrator(s, db).is_fitted  # persiste
    assert abs(brier([0.9, 0.1], [1, 0]) - 0.01) < 1e-9


def test_execution_threshold_uses_break_even_and_floor():
    s = Settings()
    s.edge_margin = 0.08
    s.min_confidence = 0.65
    # R:R 2 -> break-even 1/3 + 0.08 ≈ 0.41, mas sem calibração o piso de 0.65 manda
    assert execution_threshold(s, reward_risk_ratio=2.0, cost=0.0, risk_amount=100.0, calibrated=False) == 0.65
    assert execution_threshold(s, reward_risk_ratio=2.0, cost=0.0, risk_amount=100.0, calibrated=True) < 0.45
