from datetime import datetime, timezone

import pytest

from trader.indicators import build_snapshot, ema, pct_change, rsi, sma


def test_sma_and_ema_basic():
    values = [1, 2, 3, 4, 5]
    assert sma(values, 5) == 3
    assert sma(values, 6) is None
    assert ema(values, 5) == 3  # seed = SMA quando não há mais dados
    assert ema(values, 2) == pytest.approx(4.5, abs=0.2)


def test_rsi_bounds_and_direction():
    up = list(range(1, 40))
    down = list(range(40, 1, -1))
    assert rsi(up, 14) == 100.0
    assert rsi(down, 14) == pytest.approx(0.0, abs=1e-9)
    assert rsi([1, 2], 14) is None
    mixed = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00]
    value = rsi(mixed, 14)
    assert 60 < value < 80


def test_pct_change():
    assert pct_change([100, 110], 1) == pytest.approx(10.0)
    assert pct_change([100], 1) is None
    assert pct_change([0, 5], 1) is None


def test_build_snapshot_rounds_and_labels():
    closes = [100 + i * 0.1 for i in range(60)]
    snap = build_snapshot("AAPL", closes, [1000] * 60, datetime(2026, 1, 2, tzinfo=timezone.utc))
    assert snap is not None
    assert snap.price == pytest.approx(105.9)
    assert snap.rsi == 100.0
    assert snap.trend_label() == "alta"
    assert snap.to_dict()["bar_time"].startswith("2026-01-02")
    assert build_snapshot("X", [], [], datetime.now(timezone.utc)) is None
