"""Persistência em SQLite: decisões, ordens, execuções, P&L e prompts.

Thread-safe (um ``Lock`` por ligação) porque é usada tanto pela thread do
``asyncio`` (motor de trading) como por callbacks do ``ib_insync``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    model TEXT,
    action TEXT NOT NULL,
    confidence REAL NOT NULL,
    reason TEXT,
    price REAL,
    rsi REAL,
    sma_fast REAL,
    sma_slow REAL,
    ema REAL,
    change_5m_pct REAL,
    change_30m_pct REAL,
    position_qty REAL,
    net_liq REAL,
    prompt_version INTEGER,
    parse_ok INTEGER NOT NULL DEFAULT 1,
    executed INTEGER NOT NULL DEFAULT 0,
    skip_reason TEXT,
    raw_response TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts);

CREATE TABLE IF NOT EXISTS order_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    decision_id INTEGER,
    role TEXT NOT NULL,            -- ENTRY | CLOSE
    direction INTEGER NOT NULL,    -- +1 long, -1 short
    qty REAL NOT NULL,
    parent_order_id INTEGER,
    tp_order_id INTEGER,
    sl_order_id INTEGER,
    ref_price REAL,
    tp_price REAL,
    sl_price REAL
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    decision_id INTEGER,
    group_id INTEGER,
    direction INTEGER NOT NULL,
    qty REAL NOT NULL,
    filled_qty REAL NOT NULL DEFAULT 0,
    entry_ts TEXT,
    entry_price REAL,
    exit_ts TEXT,
    exit_price REAL,
    exit_qty REAL NOT NULL DEFAULT 0,
    exit_reason TEXT,
    pnl REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'OPEN'   -- OPEN | CLOSED
);
CREATE INDEX IF NOT EXISTS idx_trades_symbol_status ON trades(symbol, status);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    exec_id TEXT UNIQUE,
    order_id INTEGER,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    shares REAL NOT NULL,
    price REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pnl_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    net_liq REAL,
    cash REAL,
    unrealized REAL,
    realized REAL
);

CREATE TABLE IF NOT EXISTS prompt_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    addendum TEXT NOT NULL,
    lessons_json TEXT,
    stats_json TEXT
);

CREATE TABLE IF NOT EXISTS retrospectives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    report_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path | str = ":memory:") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------ util
    def _execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------ decisions
    def insert_decision(
        self,
        *,
        symbol: str,
        model: str,
        action: str,
        confidence: float,
        reason: str,
        snapshot: dict[str, Any],
        position_qty: float,
        net_liq: Optional[float],
        prompt_version: int,
        parse_ok: bool,
        raw_response: str,
    ) -> int:
        cur = self._execute(
            """INSERT INTO decisions (ts, symbol, model, action, confidence, reason, price, rsi,
               sma_fast, sma_slow, ema, change_5m_pct, change_30m_pct, position_qty, net_liq,
               prompt_version, parse_ok, raw_response)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                iso(utc_now()), symbol, model, action, confidence, reason,
                snapshot.get("price"), snapshot.get("rsi"), snapshot.get("sma_fast"),
                snapshot.get("sma_slow"), snapshot.get("ema"), snapshot.get("change_5m_pct"),
                snapshot.get("change_30m_pct"), position_qty, net_liq, prompt_version,
                int(parse_ok), raw_response[:4000],
            ),
        )
        return int(cur.lastrowid)

    def mark_decision(self, decision_id: int, *, executed: bool, skip_reason: str = "") -> None:
        self._execute(
            "UPDATE decisions SET executed=?, skip_reason=? WHERE id=?",
            (int(executed), skip_reason or None, decision_id),
        )

    def decisions_since(self, since: datetime, symbol: Optional[str] = None) -> list[dict[str, Any]]:
        if symbol:
            return self._query(
                "SELECT * FROM decisions WHERE ts >= ? AND symbol = ? ORDER BY ts",
                (iso(since), symbol),
            )
        return self._query("SELECT * FROM decisions WHERE ts >= ? ORDER BY ts", (iso(since),))

    def recent_decisions(self, limit: int = 10, symbol: Optional[str] = None) -> list[dict[str, Any]]:
        if symbol:
            return self._query(
                "SELECT * FROM decisions WHERE symbol=? ORDER BY id DESC LIMIT ?", (symbol, limit)
            )
        return self._query("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------ orders / trades
    def insert_order_group(
        self,
        *,
        symbol: str,
        decision_id: Optional[int],
        role: str,
        direction: int,
        qty: float,
        parent_order_id: Optional[int],
        tp_order_id: Optional[int],
        sl_order_id: Optional[int],
        ref_price: Optional[float],
        tp_price: Optional[float],
        sl_price: Optional[float],
    ) -> int:
        cur = self._execute(
            """INSERT INTO order_groups (ts, symbol, decision_id, role, direction, qty,
               parent_order_id, tp_order_id, sl_order_id, ref_price, tp_price, sl_price)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                iso(utc_now()), symbol, decision_id, role, direction, qty, parent_order_id,
                tp_order_id, sl_order_id, ref_price, tp_price, sl_price,
            ),
        )
        return int(cur.lastrowid)

    def group_for_order(self, order_id: int) -> Optional[dict[str, Any]]:
        rows = self._query(
            """SELECT * FROM order_groups
               WHERE parent_order_id=? OR tp_order_id=? OR sl_order_id=?
               ORDER BY id DESC LIMIT 1""",
            (order_id, order_id, order_id),
        )
        return rows[0] if rows else None

    def open_trade(self, *, symbol: str, decision_id: Optional[int], group_id: int,
                   direction: int, qty: float) -> int:
        cur = self._execute(
            """INSERT INTO trades (symbol, decision_id, group_id, direction, qty, status)
               VALUES (?,?,?,?,?,'OPEN')""",
            (symbol, decision_id, group_id, direction, qty),
        )
        return int(cur.lastrowid)

    def trade_for_group(self, group_id: int) -> Optional[dict[str, Any]]:
        rows = self._query("SELECT * FROM trades WHERE group_id=? ORDER BY id DESC LIMIT 1", (group_id,))
        return rows[0] if rows else None

    def open_trades(self, symbol: Optional[str] = None) -> list[dict[str, Any]]:
        if symbol:
            return self._query(
                "SELECT * FROM trades WHERE status='OPEN' AND symbol=? ORDER BY id", (symbol,)
            )
        return self._query("SELECT * FROM trades WHERE status='OPEN' ORDER BY id")

    def record_entry_fill(self, trade_id: int, shares: float, price: float, ts: datetime) -> None:
        """Atualiza preço médio de entrada com execuções (possivelmente parciais)."""
        trade = self._query("SELECT * FROM trades WHERE id=?", (trade_id,))[0]
        filled = float(trade["filled_qty"] or 0)
        prev_price = float(trade["entry_price"] or 0)
        new_filled = filled + shares
        avg = (prev_price * filled + price * shares) / new_filled if new_filled else price
        self._execute(
            "UPDATE trades SET filled_qty=?, entry_price=?, entry_ts=COALESCE(entry_ts, ?) WHERE id=?",
            (new_filled, avg, iso(ts), trade_id),
        )

    def record_exit_fill(self, trade_id: int, shares: float, price: float, ts: datetime,
                         reason: str) -> dict[str, Any]:
        trade = self._query("SELECT * FROM trades WHERE id=?", (trade_id,))[0]
        direction = int(trade["direction"])
        entry = float(trade["entry_price"] or price)
        pnl_increment = (price - entry) * shares * direction
        exit_qty = float(trade["exit_qty"] or 0) + shares
        status = "CLOSED" if exit_qty >= float(trade["filled_qty"] or trade["qty"]) - 1e-9 else "OPEN"
        self._execute(
            """UPDATE trades SET exit_qty=?, exit_price=?, exit_ts=?, exit_reason=?, pnl=pnl+?, status=?
               WHERE id=?""",
            (exit_qty, price, iso(ts), reason, pnl_increment, status, trade_id),
        )
        return self._query("SELECT * FROM trades WHERE id=?", (trade_id,))[0]

    def trades_since(self, since: datetime) -> list[dict[str, Any]]:
        return self._query(
            "SELECT * FROM trades WHERE COALESCE(exit_ts, entry_ts) >= ? ORDER BY id", (iso(since),)
        )

    def insert_fill(self, *, exec_id: str, order_id: int, symbol: str, side: str,
                    shares: float, price: float, ts: datetime) -> bool:
        """Devolve False se a execução já estava registada (ib_insync pode repetir eventos)."""
        try:
            self._execute(
                "INSERT INTO fills (ts, exec_id, order_id, symbol, side, shares, price) VALUES (?,?,?,?,?,?,?)",
                (iso(ts), exec_id, order_id, symbol, side, shares, price),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    # ---------------------------------------------------------------- P&L
    def snapshot_pnl(self, *, net_liq: Optional[float], cash: Optional[float],
                     unrealized: Optional[float], realized: Optional[float]) -> None:
        self._execute(
            "INSERT INTO pnl_snapshots (ts, net_liq, cash, unrealized, realized) VALUES (?,?,?,?,?)",
            (iso(utc_now()), net_liq, cash, unrealized, realized),
        )

    def first_net_liq_on(self, day_utc: datetime) -> Optional[float]:
        start = day_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = self._query(
            "SELECT net_liq FROM pnl_snapshots WHERE ts >= ? AND ts < ? AND net_liq IS NOT NULL ORDER BY ts LIMIT 1",
            (iso(start), iso(start + timedelta(days=1))),
        )
        return float(rows[0]["net_liq"]) if rows else None

    def pnl_summary(self, since: datetime) -> dict[str, Any]:
        trades = [t for t in self.trades_since(since) if t["status"] == "CLOSED"]
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        total = sum(t["pnl"] for t in trades)
        return {
            "closed_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(trades)) if trades else None,
            "total_pnl": round(total, 2),
            "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0.0,
            "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0.0,
            "stop_hits": sum(1 for t in trades if (t["exit_reason"] or "") == "SL"),
            "tp_hits": sum(1 for t in trades if (t["exit_reason"] or "") == "TP"),
        }

    # ------------------------------------------------------------- prompts
    def save_prompt_version(self, addendum: str, lessons: list[str], stats: dict[str, Any]) -> int:
        cur = self._execute(
            "INSERT INTO prompt_versions (ts, addendum, lessons_json, stats_json) VALUES (?,?,?,?)",
            (iso(utc_now()), addendum, json.dumps(lessons, ensure_ascii=False),
             json.dumps(stats, ensure_ascii=False, default=str)),
        )
        return int(cur.lastrowid)

    def latest_prompt_version(self) -> Optional[dict[str, Any]]:
        rows = self._query("SELECT * FROM prompt_versions ORDER BY id DESC LIMIT 1")
        if not rows:
            return None
        row = rows[0]
        row["lessons"] = json.loads(row.get("lessons_json") or "[]")
        return row

    def save_retrospective(self, report: dict[str, Any]) -> int:
        cur = self._execute(
            "INSERT INTO retrospectives (ts, report_json) VALUES (?,?)",
            (iso(utc_now()), json.dumps(report, ensure_ascii=False, default=str)),
        )
        return int(cur.lastrowid)

    # ------------------------------------------------------------------ kv
    def get_kv(self, key: str, default: Optional[str] = None) -> Optional[str]:
        rows = self._query("SELECT value FROM kv WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def set_kv(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO kv (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
