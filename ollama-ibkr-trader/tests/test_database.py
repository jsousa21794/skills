from datetime import datetime, timedelta, timezone

from trader.database import Database


def _snapshot(price=100.0):
    return {"price": price, "rsi": 50, "sma_fast": 99, "sma_slow": 98, "ema": 99.5,
            "change_5m_pct": 0.1, "change_30m_pct": 0.5}


def test_trade_lifecycle_with_bracket_fills():
    db = Database(":memory:")
    did = db.insert_decision(symbol="AAPL", model="m", action="BUY", confidence=0.8, reason="r",
                             snapshot=_snapshot(), position_qty=0, net_liq=10000, prompt_version=0,
                             parse_ok=True, raw_response="{}")
    gid = db.insert_order_group(symbol="AAPL", decision_id=did, role="ENTRY", direction=1, qty=10,
                                parent_order_id=1, tp_order_id=2, sl_order_id=3, ref_price=100,
                                tp_price=105, sl_price=98)
    tid = db.open_trade(symbol="AAPL", decision_id=did, group_id=gid, direction=1, qty=10)
    now = datetime.now(timezone.utc)

    assert db.group_for_order(3)["id"] == gid
    db.record_entry_fill(tid, 4, 100.0, now)
    db.record_entry_fill(tid, 6, 101.0, now)
    trade = db.trade_for_group(gid)
    assert trade["filled_qty"] == 10
    assert abs(trade["entry_price"] - 100.6) < 1e-9

    partial = db.record_exit_fill(tid, 5, 105.0, now, "TP")
    assert partial["status"] == "OPEN"
    closed = db.record_exit_fill(tid, 5, 105.0, now, "TP")
    assert closed["status"] == "CLOSED"
    assert abs(closed["pnl"] - (105.0 - 100.6) * 10) < 1e-6

    summary = db.pnl_summary(now - timedelta(hours=1))
    assert summary["closed_trades"] == 1 and summary["wins"] == 1 and summary["tp_hits"] == 1


def test_short_trade_pnl_sign():
    db = Database(":memory:")
    gid = db.insert_order_group(symbol="TSLA", decision_id=None, role="ENTRY", direction=-1, qty=2,
                                parent_order_id=10, tp_order_id=11, sl_order_id=12, ref_price=200,
                                tp_price=190, sl_price=204)
    tid = db.open_trade(symbol="TSLA", decision_id=None, group_id=gid, direction=-1, qty=2)
    now = datetime.now(timezone.utc)
    db.record_entry_fill(tid, 2, 200.0, now)
    closed = db.record_exit_fill(tid, 2, 204.0, now, "SL")
    assert closed["status"] == "CLOSED"
    assert closed["pnl"] == -8.0


def test_fill_dedup_and_prompt_versions():
    db = Database(":memory:")
    now = datetime.now(timezone.utc)
    assert db.insert_fill(exec_id="e1", order_id=1, symbol="AAPL", side="BOT", shares=1, price=1, ts=now)
    assert not db.insert_fill(exec_id="e1", order_id=1, symbol="AAPL", side="BOT", shares=1, price=1, ts=now)
    assert db.latest_prompt_version() is None
    v = db.save_prompt_version("texto", ["lição 1"], {"a": 1})
    latest = db.latest_prompt_version()
    assert latest["id"] == v and latest["lessons"] == ["lição 1"]


def test_kv_and_first_net_liq():
    db = Database(":memory:")
    db.set_kv("k", "v")
    db.set_kv("k", "w")
    assert db.get_kv("k") == "w"
    db.snapshot_pnl(net_liq=1000, cash=1, unrealized=0, realized=0)
    db.snapshot_pnl(net_liq=990, cash=1, unrealized=0, realized=0)
    assert db.first_net_liq_on(datetime.now(timezone.utc)) == 1000
