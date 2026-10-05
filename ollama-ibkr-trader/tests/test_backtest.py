from trader.backtest import Replayer
from trader.config import Settings
from trader.database import Database
from tests.helpers import make_bars
from tests.test_brain_pipeline import FakeOllama


def test_replay_runs_pipeline_with_cache_and_costs():
    s = Settings()
    s.llm_samples = 1
    s.llm_interval_minutes = 15
    s.signal_persistence_cycles = 1
    s.skip_open_minutes = 0
    s.min_confidence = 0.6
    db = Database(":memory:")
    brain = FakeOllama(s, db, actions=["BUY"])
    bars = make_bars(390 * 2, vol=0.15, drift=0.01)  # ~2 sessões de 1 min a subir
    rep = Replayer(s, brain, db, equity=50_000.0)
    report = rep.run("AAPL", bars)
    sim = report["simulation"]
    assert sim["trades"] >= 1
    assert db.count_settled() >= 1
    assert db._query("SELECT COUNT(*) AS n FROM decisions_cache")[0]["n"] >= 1
    assert report["trades"]["n"] == sim["trades"]
    assert all(t.pnl != 0 or t.reason for t in rep.trades)
    # custos aplicados: um trade sem movimento líquido fica negativo
    assert any(t.pnl < (t.exit_price - t.entry_price) * t.qty * t.direction for t in rep.trades)
