import pytest

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
    s.llm_min_agreement = 0.5
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
    # ledger único: equity da simulação == soma dos P&L líquidos dos trades; comissões nos trades
    assert abs(sim["end_equity"] - sim["start_equity"] - sim["ledger_pnl"]) < 1e-6
    assert sim["commissions"] > 0
    assert all(float(t["commission"]) > 0 and float(t["pnl"]) == pytest.approx(float(t["gross_pnl"]) - float(t["commission"]))
               for t in rep.closed)
