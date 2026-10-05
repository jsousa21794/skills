import json

from trader.config import Settings
from trader.indicators import aggregate_bars, rsi, sma, ema
from trader.lessons_offline import compute_lessons, ema_series, rsi_series, sma_series, main
from tests.helpers import make_bars


def test_incremental_series_match_reference():
    bars = aggregate_bars(make_bars(800, vol=0.3), 5)
    closes = [b.close for b in bars]
    assert abs(rsi_series(closes, 14)[-1] - rsi(closes, 14)) < 1e-9
    assert abs(sma_series(closes, 20)[-1] - sma(closes, 20)) < 1e-9
    assert abs(ema_series(closes, 9)[-1] - ema(closes, 9)) < 1e-9


def test_compute_lessons_finds_patterns_in_trending_data(tmp_path):
    s = Settings()
    # 8 sessões de 1 min com tendência de alta: sinais BUY devem acertar acima da base
    bars = make_bars(390 * 8, vol=0.12, drift=0.004, seed=4)
    result = compute_lessons("AAPL", bars, s)
    assert result["samples"] >= 12
    assert 0 < result["base_rate"] < 1
    assert all(l["key"].startswith("hist_AAPL_") and l["source"] for l in result["lessons"])
    # CLI escreve JSON importável
    csv = tmp_path / "a.csv"
    csv.write_text("time,open,high,low,close,volume\n" + "\n".join(
        f"{b.time.isoformat()},{b.open},{b.high},{b.low},{b.close},{b.volume}" for b in bars), encoding="utf-8")
    out = tmp_path / "lessons.json"
    assert main(["--csv", str(csv), "--symbol", "AAPL", "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "lessons" in data
