from datetime import timedelta

from trader.indicators import Bar, aggregate_bars, atr, atr_series, compute_dynamics, ewma_volatility, percentile
from tests.helpers import make_bars, NY_OPEN_UTC


def test_aggregate_bars_aligns_to_clock():
    bars = make_bars(23)
    agg = aggregate_bars(bars, 5)
    assert len(agg) == 5  # 09:30-09:34, ..., 09:50-09:52
    assert agg[0].time == NY_OPEN_UTC and agg[0].high >= max(b.high for b in bars[:5]) - 1e-9
    assert agg[0].close == bars[4].close and agg[0].volume == sum(b.volume for b in bars[:5])
    assert aggregate_bars(bars, 1) == bars


def test_atr_percentile_ewma_dynamics():
    bars = make_bars(200, vol=0.3)
    a = atr(bars, 14)
    series = atr_series(bars, 14)
    assert a is not None and a > 0 and abs(series[-1] - a) < 1e-9
    assert percentile([1, 2, 3, 4, 5], 50) == 3 and percentile([], 50) is None
    assert ewma_volatility([b.close for b in bars]) > 0
    dyn = compute_dynamics([b.close for b in bars], bars)
    assert dyn is not None and dyn.atr_pct is not None and dyn.price_vs_fast in ("above", "below")
    # cruzamento forçado: série a cair e depois a subir abruptamente
    closes = [100 - i * 0.1 for i in range(80)] + [92 + i * 0.5 for i in range(6)]
    dyn2 = compute_dynamics(closes, None)
    assert dyn2.ema_cross_sma_fast == "up" and dyn2.rsi_slope > 0
