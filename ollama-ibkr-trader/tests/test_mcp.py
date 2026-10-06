"""Integração ChatGPT via MCP: camada de domínio (RemoteSupervisor), autenticação (TokenGate) e ferramentas."""
import asyncio
import json

import pytest

from tests.test_engine import NOW, _FixedDatetime, make_engine, outcome, own_position, run_exec
from trader.mcp_server import RemoteSupervisor, TokenGate, ensure_token, mask_account

mcp = pytest.importorskip("mcp", reason="dependência opcional mcp>=2 não instalada")


def make_supervisor():
    from tests.helpers import FakeOrder

    engine, db, s = make_engine()
    own_position(engine, db, "AAPL", 10)
    engine.ibkr.orders["AAPL"] = [FakeOrder(9, "SELL", 10, "STP")]  # posição coberta
    return RemoteSupervisor(engine, s, db), engine, db, s


def payload(result):
    data = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    return data if data else json.loads(result.content[0].text)


def test_status_positions_orders_and_diagnostics_are_read_only_and_audited():
    sup, engine, db, s = make_supervisor()
    status = sup.status()
    assert status["mode"] == "live" and status["account"] == mask_account(engine.ibkr.account) and "***" in status["account"]
    assert status["entries_paused"] is False and status["symbols"] == ["AAPL", "TSLA"]
    pos = sup.positions()["positions"]
    assert pos[0]["symbol"] == "AAPL" and pos[0]["own_qty"] == 10 and pos[0]["broker_qty"] == 10
    orders = sup.open_orders()
    assert orders["pending_entries"] == [] and isinstance(orders["orders"], list)
    diag = sup.diagnostics()
    assert diag["sqlite_quick_check"] == "ok" and diag["no_orders_sent"] is True and diag["mode"] == "live"
    cmp_ = sup.compare()
    assert cmp_["ok"] is True
    assert engine.ibkr.brackets == [] and engine.ibkr.closes == []  # nada foi enviado
    tools = [c["tool"] for c in db.recent_remote_commands()]
    assert {"get_status", "get_positions", "get_open_orders", "run_diagnostics", "compare_ledger_with_broker"} <= set(tools)


def test_pause_requires_matching_account_and_mode_and_is_idempotent_and_persistent(monkeypatch):
    sup, engine, db, s = make_supervisor()
    engine.start()
    try:
        monkeypatch.setattr("trader.trading_engine.datetime", _FixedDatetime)
        masked = mask_account(engine.ibkr.account)
        ctx = sup.status()["context_id"]
        assert asyncio.run(sup.pause_entries("XX", "live", "teste", context_id=ctx))["applied"] is False
        assert asyncio.run(sup.pause_entries(masked, "paper", "teste", context_id=ctx))["applied"] is False
        assert asyncio.run(sup.pause_entries(masked, "live", "", context_id=ctx))["applied"] is False
        assert db.get_kv("entries_paused") is None
        first = asyncio.run(sup.pause_entries(masked, "live", "supervisão: dados desatualizados", context_id=ctx))
        assert first["applied"] is True and first["persisted"] is True and first["already_paused"] is False
        second = asyncio.run(sup.pause_entries(masked, "live", "repetido", context_id=ctx))
        assert second["applied"] is True and second["already_paused"] is True
        assert db.get_kv("entries_paused") == "1" and engine.entries_paused
        # o motor recusa novas entradas enquanto a pausa persistir; proteções e supervisão mantêm-se
        row = run_exec(engine, db, outcome("BUY"), symbol="TSLA")
        assert row["executed"] == 0 and "pausa" in row["skip_reason"]
        engine.ibkr.positions["TSLA"] = 5
        own_position(engine, db, "TSLA", 5)
        engine.ibkr.orders.clear()
        asyncio.run(engine._supervise())
        assert "TSLA" in engine.ibkr.protections
        audit = [c for c in db.recent_remote_commands() if c["tool"] == "pause_new_entries"]
        assert len(audit) == 5 and audit[0]["actor"] == "chatgpt-mcp" and audit[0]["mode"] == "live"
        # retomar só existe localmente
        asyncio.run(engine.resume_entries(actor="gui"))
        assert not engine.entries_paused
    finally:
        engine.shutdown()


def test_token_gate_accepts_path_token_or_bearer_and_rejects_the_rest():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    gate = TokenGate(inner, "s3cret")

    async def call(path, headers=()):
        out = []

        async def send(msg):
            out.append(msg)

        await gate({"type": "http", "path": path, "headers": list(headers)}, None, send)
        return out[0]["status"]

    assert asyncio.run(call("/t/s3cret/mcp")) == 200 and seen[-1] == "/mcp"
    assert asyncio.run(call("/mcp", [(b"authorization", b"Bearer s3cret")])) == 200
    assert asyncio.run(call("/mcp")) == 401
    assert asyncio.run(call("/t/wrong/mcp")) == 401
    assert asyncio.run(call("/mcp", [(b"authorization", b"Bearer nope")])) == 401


def test_mcp_tools_are_registered_and_limited(tmp_path, monkeypatch):
    from trader.mcp_server import build_server

    sup, engine, db, s = make_supervisor()
    server = build_server(sup)
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {"get_status", "get_positions", "get_open_orders", "get_recent_log", "get_recent_decisions",
                     "compare_ledger_with_broker", "run_diagnostics", "get_remote_commands", "pause_new_entries"}
    assert not any(n in names for n in ("resume", "place", "cancel", "close", "set_risk"))
    result = asyncio.run(server.call_tool("get_status", {}))
    assert payload(result)["mode"] == "live"
    monkeypatch.setenv("OLLAMA_TRADER_HOME", str(tmp_path))
    s.mcp_token = ""
    assert len(ensure_token(s)) >= 24 and s.mcp_token == ensure_token(s)
