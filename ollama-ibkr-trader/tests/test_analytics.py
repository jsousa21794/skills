import random
from datetime import datetime, timedelta, timezone

from trader.analytics import (Analytics, calibration_metrics, equity_metrics, monte_carlo_ruin, norm_ppf,
                              permutation_hit_rate, trade_metrics, walk_forward_efficiency)
from trader.config import Settings
from trader.database import Database


def test_norm_ppf_and_calibration():
    assert abs(norm_ppf(0.975) - 1.95996) < 1e-4
    assert abs(norm_ppf(0.5)) < 1e-9
    good = calibration_metrics([0.9] * 9 + [0.1] * 9, [1] * 8 + [0] + [0] * 8 + [1])
    assert good["ece"] < 0.05 and good["brier"] < good["brier_climatology"]
    bad = calibration_metrics([0.95] * 20, [1] * 10 + [0] * 10)
    assert bad["ece"] > 0.4 and bad["brier"] > bad["brier_climatology"]


def test_permutation_detects_skill():
    rng = random.Random(3)
    rows = []
    for i in range(200):
        ret = rng.gauss(0, 1)
        action = "BUY" if ret > 0 else "SELL"
        if rng.random() < 0.3:
            action = "SELL" if action == "BUY" else "BUY"
        rows.append({"action": action, "settled_return": ret})
    res = permutation_hit_rate(rows, iterations=500)
    assert res["beats_random"] and res["p_value"] < 0.05
    noise = [{"action": rng.choice(["BUY", "SELL"]), "settled_return": rng.gauss(0, 1)} for _ in range(200)]
    assert not permutation_hit_rate(noise, iterations=500)["beats_random"] or permutation_hit_rate(noise, iterations=500)["p_value"] > 0.01


def test_trade_equity_and_monte_carlo():
    pnls = [50, -30, 40, -30, 60, -30, 45, -30, 55, -30, 50, -30]
    tm = trade_metrics(pnls)
    assert tm["n"] == 12 and tm["win_rate"] == 0.5 and tm["profit_factor"] > 1 and tm["kelly"] > 0
    mc = monte_carlo_ruin(pnls, equity=10_000, ruin_drawdown_pct=0.05, iterations=300)
    assert 0.0 <= mc["ruin_probability"] <= 1.0
    rng = random.Random(5)
    equity = [100_000.0]
    for _ in range(120):
        equity.append(equity[-1] * (1 + rng.gauss(0.0008, 0.006)))
    em = equity_metrics(equity, n_trials=10)
    assert em["n_days"] == 120 and 0 <= em["psr"] <= 1 and em["dsr"] <= em["psr"] + 1e-9
    assert em["sr_star"] > 0 and em["min_btl_years"] is not None
    assert walk_forward_efficiency(pnls * 2) is not None
    assert walk_forward_efficiency([1, 2]) is None


def test_report_and_gates_fail_in_learning_phase():
    s = Settings()
    db = Database(":memory:")
    report = Analytics(s, db).build_report()
    assert report["gates"]["all_passed"] is False
    assert report["gates"]["risk_multiplier"] == s.learning_risk_multiplier  # o mesmo estado de risco do motor (N18)
    md = Analytics.render_markdown(report)
    assert "Gates" in md and "NÃO PASSA" in md
    assert db.latest_report("weekly") is not None
