"""Persistência em SQLite: decisões, ordens, execuções, P&L e prompts.

Thread-safe (um ``Lock`` por ligação) porque é usada tanto pela thread do
``asyncio`` (motor de trading) como por callbacks do ``ib_async``.
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

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    key TEXT UNIQUE NOT NULL,       -- ex.: rsi_regime:overbought:BUY
    symbol TEXT,                    -- NULL = global
    text TEXT NOT NULL,
    support INTEGER NOT NULL,       -- nº de decisões que suportam a lição
    effect REAL NOT NULL,           -- desvio face à taxa base (z-score)
    importance REAL NOT NULL,
    last_confirmed TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS decisions_cache (
    prompt_hash TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    ts TEXT NOT NULL,
    response_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,             -- prompt | model | threshold | config
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS protection_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    name TEXT NOT NULL,
    symbol TEXT,
    until TEXT,
    reason TEXT
);

CREATE TABLE IF NOT EXISTS event_cache (
    key TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    report_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fill_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exec_id TEXT NOT NULL,
    trade_id INTEGER NOT NULL,
    shares REAL NOT NULL,
    commission REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alloc_exec ON fill_allocations(exec_id);

-- Histórico de TODAS as ordens que já pertenceram a um grupo (pai, TP, SL), incluindo as
-- substituídas por uma reparação: um fill tardio de um filho antigo continua a encontrar o
-- seu grupo/trade (N07).
CREATE TABLE IF NOT EXISTS order_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    leg TEXT NOT NULL,             -- PARENT | TP | SL
    order_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    replaced_ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_order_history_order ON order_history(order_id);
"""

# Colunas acrescentadas à tabela decisions depois da v1.0 (migração idempotente).
DECISION_COLUMNS = {
    "verbal_conf": "REAL",
    "agree_frac": "REAL",
    "action_logprob": "REAL",
    "calibrated_prob": "REAL",
    "threshold_used": "REAL",
    "n_samples": "INTEGER",
    "samples_json": "TEXT",
    "prompt_hash": "TEXT",
    "dynamics_json": "TEXT",
    "atr": "REAL",
    "sentiment": "REAL",
    "vol_forecast": "REAL",
    "review": "INTEGER NOT NULL DEFAULT 0",
    "horizon_min": "INTEGER",
    "settled_ts": "TEXT",
    "settled_price": "REAL",
    "settled_return": "REAL",
    "bench_return": "REAL",
    "alpha": "REAL",
    "correct": "INTEGER",
    "hour_ny": "INTEGER",
    "regime": "TEXT",
    "cost_pct": "REAL",
    "market_ts": "TEXT",
    "stop_pct": "REAL",
    "tp_pct": "REAL",
    "sentiment_n": "INTEGER",
    "generation": "INTEGER",
    "account": "TEXT",
}
TRADE_COLUMNS = {"decision_action": "TEXT", "stop_price": "REAL", "tp_price": "REAL", "risk_amount": "REAL",
                 "commission": "REAL NOT NULL DEFAULT 0", "gross_pnl": "REAL NOT NULL DEFAULT 0", "account": "TEXT"}
FILL_COLUMNS = {"commission": "REAL", "commission_estimated": "INTEGER NOT NULL DEFAULT 1", "account": "TEXT"}
GROUP_COLUMNS = {"account": "TEXT", "con_id": "INTEGER", "order_ref": "TEXT"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path | str = ":memory:") -> None:
        self._lock = threading.RLock()
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        self._migrate()

    def switch_path(self, path: Path | str) -> None:
        """Fecha a ligação atual e abre outro ficheiro (ex.: paper -> real). Mesmo objeto, outro estado."""
        with self._lock:
            self._conn.close()
            self.path = str(path)
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        self._migrate()

    def copy_to(self, target: Path | str) -> None:
        """Cópia consistente do ficheiro atual (API de backup do SQLite) para ``target``."""
        with self._lock:
            dest = sqlite3.connect(str(target))
            try:
                self._conn.backup(dest)
            finally:
                dest.close()

    def _migrate(self) -> None:
        with self._lock:
            for table, columns in (("decisions", DECISION_COLUMNS), ("trades", TRADE_COLUMNS), ("fills", FILL_COLUMNS),
                                   ("order_groups", GROUP_COLUMNS)):
                existing = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}
                for name, decl in columns.items():
                    if name not in existing:
                        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
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
        extra: Optional[dict[str, Any]] = None,
        ts: Optional[datetime] = None,
    ) -> int:
        cur = self._execute(
            """INSERT INTO decisions (ts, symbol, model, action, confidence, reason, price, rsi,
               sma_fast, sma_slow, ema, change_5m_pct, change_30m_pct, position_qty, net_liq,
               prompt_version, parse_ok, raw_response)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                iso(ts or utc_now()), symbol, model, action, confidence, reason,
                snapshot.get("price"), snapshot.get("rsi"), snapshot.get("sma_fast"),
                snapshot.get("sma_slow"), snapshot.get("ema"), snapshot.get("change_5m_pct"),
                snapshot.get("change_30m_pct"), position_qty, net_liq, prompt_version,
                int(parse_ok), raw_response[:4000],
            ),
        )
        decision_id = int(cur.lastrowid)
        if extra:
            self.update_decision(decision_id, **extra)
        return decision_id

    def update_decision(self, decision_id: int, **fields: Any) -> None:
        cols = [k for k in fields if k in DECISION_COLUMNS or k in ("executed", "skip_reason")]
        if not cols:
            return
        assignments = ", ".join(f"{c}=?" for c in cols)
        values = [json.dumps(fields[c], ensure_ascii=False, default=str) if isinstance(fields[c], (dict, list)) else fields[c]
                  for c in cols]
        self._execute(f"UPDATE decisions SET {assignments} WHERE id=?", (*values, decision_id))

    # ------------------------------------------------------- settlement
    def unsettled_decisions(self, before: datetime) -> list[dict[str, Any]]:
        return self._query(
            "SELECT * FROM decisions WHERE settled_ts IS NULL AND review=0 AND price IS NOT NULL AND ts <= ? ORDER BY ts",
            (iso(before),),
        )

    def settle_decision(self, decision_id: int, *, settled_price: float, settled_return: float,
                        bench_return: Optional[float], alpha: Optional[float], correct: Optional[int],
                        horizon_min: int) -> None:
        self._execute(
            """UPDATE decisions SET settled_ts=?, settled_price=?, settled_return=?, bench_return=?, alpha=?,
               correct=?, horizon_min=? WHERE id=?""",
            (iso(utc_now()), settled_price, settled_return, bench_return, alpha, correct, horizon_min, decision_id),
        )

    def settled_decisions(self, since: Optional[datetime] = None, symbol: Optional[str] = None,
                          directional_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM decisions WHERE settled_ts IS NOT NULL"
        params: list[Any] = []
        if since:
            sql += " AND ts >= ?"
            params.append(iso(since))
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        if directional_only:
            sql += " AND action IN ('BUY','SELL')"
        return self._query(sql + " ORDER BY ts", params)

    def count_settled(self) -> int:
        return int(self._query("SELECT COUNT(*) AS n FROM decisions WHERE settled_ts IS NOT NULL")[0]["n"])

    # -------------------------------------------------------- protections
    def closed_trades_between(self, since: datetime, until: Optional[datetime] = None,
                              model: Optional[str] = None) -> list[dict[str, Any]]:
        """Trades fechados; com ``model`` só os originados por decisões desse modelo (N11)."""
        sql = "SELECT t.* FROM trades t"
        params: list[Any] = []
        if model is not None:
            sql += " JOIN decisions d ON d.id = t.decision_id"
        sql += " WHERE t.status='CLOSED' AND t.exit_ts >= ?"
        params.append(iso(since))
        if until:
            sql += " AND t.exit_ts <= ?"
            params.append(iso(until))
        if model is not None:
            sql += " AND d.model = ?"
            params.append(model)
        return self._query(sql + " ORDER BY t.exit_ts", params)

    def recent_stop_count(self, since: datetime) -> int:
        rows = self._query(
            "SELECT COUNT(*) AS n FROM trades WHERE status='CLOSED' AND exit_reason='SL' AND exit_ts >= ?",
            (iso(since),),
        )
        return int(rows[0]["n"])

    def last_exit_ts(self, symbol: str) -> Optional[datetime]:
        rows = self._query(
            "SELECT exit_ts FROM trades WHERE symbol=? AND exit_ts IS NOT NULL ORDER BY exit_ts DESC LIMIT 1",
            (symbol,),
        )
        return datetime.fromisoformat(rows[0]["exit_ts"]) if rows else None

    def last_exit(self, symbol: str) -> Optional[tuple[datetime, float]]:
        rows = self._query(
            "SELECT exit_ts, pnl FROM trades WHERE symbol=? AND exit_ts IS NOT NULL ORDER BY exit_ts DESC LIMIT 1",
            (symbol,),
        )
        return (datetime.fromisoformat(rows[0]["exit_ts"]), float(rows[0]["pnl"] or 0.0)) if rows else None

    def day_trades_since(self, since: datetime) -> int:
        """Round-trips abertos e fechados no mesmo dia (data NY) desde ``since`` (regra PDT)."""
        from zoneinfo import ZoneInfo

        ny = ZoneInfo("America/New_York")
        rows = self._query(
            "SELECT entry_ts, exit_ts FROM trades WHERE entry_ts IS NOT NULL AND exit_ts IS NOT NULL AND exit_ts >= ?",
            (iso(since),),
        )
        n = 0
        for r in rows:
            e = datetime.fromisoformat(r["entry_ts"]).astimezone(ny).date()
            x = datetime.fromisoformat(r["exit_ts"]).astimezone(ny).date()
            if e == x:
                n += 1
        return n

    def consecutive_losses(self, since: Optional[datetime] = None) -> int:
        """Perdas seguidas mais recentes; ``since`` limita a contagem (ex.: desde a última pausa)."""
        if since is not None:
            rows = self._query("SELECT pnl FROM trades WHERE status='CLOSED' AND exit_ts > ? ORDER BY exit_ts DESC LIMIT 50",
                               (iso(since),))
        else:
            rows = self._query("SELECT pnl FROM trades WHERE status='CLOSED' ORDER BY exit_ts DESC LIMIT 50")
        n = 0
        for r in rows:
            if float(r["pnl"]) < 0:
                n += 1
            else:
                break
        return n

    def entries_today(self, day_start: datetime) -> int:
        rows = self._query("SELECT COUNT(*) AS n FROM order_groups WHERE role='ENTRY' AND ts >= ?", (iso(day_start),))
        return int(rows[0]["n"])

    def equity_series(self, since: datetime) -> list[tuple[datetime, float]]:
        rows = self._query(
            "SELECT ts, net_liq FROM pnl_snapshots WHERE ts >= ? AND net_liq IS NOT NULL ORDER BY ts", (iso(since),)
        )
        return [(datetime.fromisoformat(r["ts"]), float(r["net_liq"])) for r in rows]

    def daily_equity(self, since: datetime) -> list[tuple[str, float]]:
        """Último NetLiq de cada dia (UTC) desde ``since``."""
        rows = self._query(
            """SELECT substr(ts, 1, 10) AS day, net_liq FROM pnl_snapshots
               WHERE ts >= ? AND net_liq IS NOT NULL AND id IN (
                   SELECT MAX(id) FROM pnl_snapshots WHERE ts >= ? GROUP BY substr(ts, 1, 10))
               ORDER BY day""",
            (iso(since), iso(since)),
        )
        return [(r["day"], float(r["net_liq"])) for r in rows]

    def add_protection_event(self, name: str, symbol: Optional[str], until: Optional[datetime], reason: str,
                             ts: Optional[datetime] = None) -> None:
        """``ts`` é o relógio de quem chama (histórico no replay, atual no live) (N18/F35)."""
        self._execute(
            "INSERT INTO protection_events (ts, name, symbol, until, reason) VALUES (?,?,?,?,?)",
            (iso(ts or utc_now()), name, symbol, iso(until) if until else None, reason),
        )

    def active_protections(self, now: datetime) -> list[dict[str, Any]]:
        return self._query(
            "SELECT * FROM protection_events WHERE until IS NOT NULL AND until > ? ORDER BY until DESC", (iso(now),)
        )

    def last_protection_ts(self, name: str) -> Optional[datetime]:
        rows = self._query("SELECT ts FROM protection_events WHERE name=? ORDER BY id DESC LIMIT 1", (name,))
        return datetime.fromisoformat(rows[0]["ts"]) if rows else None

    # ------------------------------------------------------------- lessons
    def upsert_lesson(self, *, key: str, symbol: Optional[str], text: str, support: int, effect: float,
                      importance: float) -> None:
        now = iso(utc_now())
        self._execute(
            """INSERT INTO lessons (ts, key, symbol, text, support, effect, importance, last_confirmed, active)
               VALUES (?,?,?,?,?,?,?,?,1)
               ON CONFLICT(key) DO UPDATE SET text=excluded.text, support=excluded.support, effect=excluded.effect,
               importance=excluded.importance, last_confirmed=excluded.last_confirmed, active=1""",
            (now, key, symbol, text, support, effect, importance, now),
        )

    def deactivate_lessons_except(self, keys: list[str]) -> None:
        if keys:
            placeholders = ",".join("?" for _ in keys)
            self._execute(f"UPDATE lessons SET active=0 WHERE key NOT IN ({placeholders})", keys)
        else:
            self._execute("UPDATE lessons SET active=0")

    def active_lessons(self) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM lessons WHERE active=1 ORDER BY importance DESC")

    # --------------------------------------------------------------- cache
    def cache_get(self, prompt_hash: str) -> Optional[dict[str, Any]]:
        rows = self._query("SELECT response_json FROM decisions_cache WHERE prompt_hash=?", (prompt_hash,))
        return json.loads(rows[0]["response_json"]) if rows else None

    def cache_put(self, prompt_hash: str, model: str, response: dict[str, Any]) -> None:
        self._execute(
            "INSERT OR REPLACE INTO decisions_cache (prompt_hash, model, ts, response_json) VALUES (?,?,?,?)",
            (prompt_hash, model, iso(utc_now()), json.dumps(response, ensure_ascii=False, default=str)),
        )

    # --------------------------------------------------------- experiments
    def record_experiment(self, kind: str, description: str) -> int:
        rows = self._query("SELECT description FROM experiments WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,))
        if rows and rows[0]["description"] == description:
            return self.experiment_count()
        self._execute("INSERT INTO experiments (ts, kind, description) VALUES (?,?,?)", (iso(utc_now()), kind, description))
        return self.experiment_count()

    def experiment_count(self) -> int:
        return max(1, int(self._query("SELECT COUNT(*) AS n FROM experiments")[0]["n"]))

    def event_cache_get(self, key: str, max_age_hours: float) -> Optional[Any]:
        rows = self._query("SELECT ts, value_json FROM event_cache WHERE key=?", (key,))
        if not rows:
            return None
        age = (utc_now() - datetime.fromisoformat(rows[0]["ts"])).total_seconds() / 3600
        if age > max_age_hours:
            return None
        return json.loads(rows[0]["value_json"])

    def event_cache_put(self, key: str, value: Any) -> None:
        self._execute(
            "INSERT OR REPLACE INTO event_cache (key, ts, value_json) VALUES (?,?,?)",
            (key, iso(utc_now()), json.dumps(value, ensure_ascii=False, default=str)),
        )

    def save_report(self, kind: str, report: dict[str, Any]) -> int:
        cur = self._execute("INSERT INTO reports (ts, kind, report_json) VALUES (?,?,?)",
                            (iso(utc_now()), kind, json.dumps(report, ensure_ascii=False, default=str)))
        return int(cur.lastrowid)

    def latest_report(self, kind: str) -> Optional[dict[str, Any]]:
        rows = self._query("SELECT report_json FROM reports WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,))
        return json.loads(rows[0]["report_json"]) if rows else None

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
        account: Optional[str] = None,
        con_id: Optional[int] = None,
        ts: Optional[datetime] = None,
    ) -> int:
        when = iso(ts or utc_now())
        cur = self._execute(
            """INSERT INTO order_groups (ts, symbol, decision_id, role, direction, qty,
               parent_order_id, tp_order_id, sl_order_id, ref_price, tp_price, sl_price, account, con_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                when, symbol, decision_id, role, direction, qty, parent_order_id,
                tp_order_id, sl_order_id, ref_price, tp_price, sl_price, account, con_id,
            ),
        )
        group_id = int(cur.lastrowid)
        for leg, oid in (("PARENT", parent_order_id), ("TP", tp_order_id), ("SL", sl_order_id)):
            if oid is not None:
                self._execute("INSERT INTO order_history (group_id, leg, order_id, ts) VALUES (?,?,?,?)",
                              (group_id, leg, int(oid), when))
        return group_id

    def update_group_orders(self, group_id: int, *, tp_order_id: Optional[int] = None,
                            sl_order_id: Optional[int] = None, ts: Optional[datetime] = None) -> None:
        """Substitui os filhos ativos do grupo, ARQUIVANDO os anteriores em ``order_history`` (N07)."""
        when = iso(ts or utc_now())
        for leg, oid in (("TP", tp_order_id), ("SL", sl_order_id)):
            if oid is None:
                continue
            self._execute("UPDATE order_history SET replaced_ts=? WHERE group_id=? AND leg=? AND replaced_ts IS NULL",
                          (when, group_id, leg))
            self._execute("INSERT INTO order_history (group_id, leg, order_id, ts) VALUES (?,?,?,?)",
                          (group_id, leg, int(oid), when))
        self._execute("UPDATE order_groups SET tp_order_id=COALESCE(?, tp_order_id), sl_order_id=COALESCE(?, sl_order_id) WHERE id=?",
                      (tp_order_id, sl_order_id, group_id))

    def order_leg(self, group: dict[str, Any], order_id: int) -> Optional[str]:
        """PARENT | TP | SL para uma ordem do grupo (ativa ou já substituída)."""
        if order_id == group.get("parent_order_id"):
            return "PARENT"
        if order_id == group.get("tp_order_id"):
            return "TP"
        if order_id == group.get("sl_order_id"):
            return "SL"
        rows = self._query("SELECT leg FROM order_history WHERE group_id=? AND order_id=? ORDER BY id DESC LIMIT 1",
                           (int(group["id"]), int(order_id)))
        return rows[0]["leg"] if rows else None

    def group_for_order(self, order_id: int, *, symbol: Optional[str] = None, con_id: Optional[int] = None,
                        account: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Grupo a que a ordem pertence (ativa ou arquivada), validando símbolo, contrato e conta (N06).

        Um grupo gravado sem ``con_id``/``account`` (versões antigas) só é aceite se o símbolo coincidir.
        """
        rows = self._query(
            """SELECT g.* FROM order_groups g
               WHERE g.parent_order_id=? OR g.tp_order_id=? OR g.sl_order_id=?
                  OR g.id IN (SELECT group_id FROM order_history WHERE order_id=?)
               ORDER BY g.id DESC""",
            (order_id, order_id, order_id, order_id),
        )
        for row in rows:
            if symbol is not None and row["symbol"] != symbol:
                continue
            if con_id and row.get("con_id") and int(row["con_id"]) != int(con_id):
                continue
            if account and row.get("account") and row["account"] != account:
                continue
            return row
        return None

    def group_by_parent(self, parent_order_id: int, role: Optional[str] = None) -> Optional[dict[str, Any]]:
        if role:
            rows = self._query("SELECT * FROM order_groups WHERE parent_order_id=? AND role=? ORDER BY id DESC LIMIT 1",
                               (parent_order_id, role))
        else:
            rows = self._query("SELECT * FROM order_groups WHERE parent_order_id=? ORDER BY id DESC LIMIT 1",
                               (parent_order_id,))
        return rows[0] if rows else None

    def open_trade(self, *, symbol: str, decision_id: Optional[int], group_id: int,
                   direction: int, qty: float, stop_price: Optional[float] = None,
                   tp_price: Optional[float] = None, risk_amount: Optional[float] = None) -> int:
        cur = self._execute(
            """INSERT INTO trades (symbol, decision_id, group_id, direction, qty, status, stop_price, tp_price, risk_amount)
               VALUES (?,?,?,?,?,'OPEN',?,?,?)""",
            (symbol, decision_id, group_id, direction, qty, stop_price, tp_price, risk_amount),
        )
        return int(cur.lastrowid)

    def close_trade_reconciled(self, trade_id: int, reason: str = "RECONCILED") -> None:
        self._execute("UPDATE trades SET status='CLOSED', exit_ts=?, exit_reason=? WHERE id=? AND status='OPEN'",
                      (iso(utc_now()), reason, trade_id))

    def trade_for_group(self, group_id: int) -> Optional[dict[str, Any]]:
        rows = self._query("SELECT * FROM trades WHERE group_id=? ORDER BY id DESC LIMIT 1", (group_id,))
        return rows[0] if rows else None

    def open_trades(self, symbol: Optional[str] = None) -> list[dict[str, Any]]:
        if symbol:
            return self._query(
                "SELECT * FROM trades WHERE status='OPEN' AND symbol=? ORDER BY id", (symbol,)
            )
        return self._query("SELECT * FROM trades WHERE status='OPEN' ORDER BY id")

    def record_entry_fill(self, trade_id: int, shares: float, price: float, ts: datetime,
                          commission: float = 0.0) -> None:
        """Atualiza preço médio de entrada com execuções (possivelmente parciais); a comissão
        entra logo no P&L líquido (``pnl``), ficando ``gross_pnl`` sem custos."""
        trade = self._query("SELECT * FROM trades WHERE id=?", (trade_id,))[0]
        filled = float(trade["filled_qty"] or 0)
        prev_price = float(trade["entry_price"] or 0)
        new_filled = filled + shares
        avg = (prev_price * filled + price * shares) / new_filled if new_filled else price
        self._execute(
            """UPDATE trades SET filled_qty=?, entry_price=?, entry_ts=COALESCE(entry_ts, ?),
               commission=commission+?, pnl=pnl-? WHERE id=?""",
            (new_filled, avg, iso(ts), commission, commission, trade_id),
        )

    def record_exit_fill(self, trade_id: int, shares: float, price: float, ts: datetime,
                         reason: str, commission: float = 0.0) -> dict[str, Any]:
        trade = self._query("SELECT * FROM trades WHERE id=?", (trade_id,))[0]
        direction = int(trade["direction"])
        entry = float(trade["entry_price"] or price)
        gross_increment = (price - entry) * shares * direction
        exit_qty = float(trade["exit_qty"] or 0) + shares
        status = "CLOSED" if exit_qty >= float(trade["filled_qty"] or trade["qty"]) - 1e-9 else "OPEN"
        self._execute(
            """UPDATE trades SET exit_qty=?, exit_price=?, exit_ts=?, exit_reason=?, gross_pnl=gross_pnl+?,
               pnl=pnl+?-?, commission=commission+?, status=? WHERE id=?""",
            (exit_qty, price, iso(ts), reason, gross_increment, gross_increment, commission, commission, status, trade_id),
        )
        return self._query("SELECT * FROM trades WHERE id=?", (trade_id,))[0]

    def apply_commission_delta(self, trade_id: int, delta: float) -> dict[str, Any]:
        """Corrige a comissão de um trade quando chega o CommissionReport real da IBKR."""
        self._execute("UPDATE trades SET commission=commission+?, pnl=pnl-? WHERE id=?", (delta, delta, trade_id))
        return self._query("SELECT * FROM trades WHERE id=?", (trade_id,))[0]

    def trades_since(self, since: datetime) -> list[dict[str, Any]]:
        return self._query(
            "SELECT * FROM trades WHERE COALESCE(exit_ts, entry_ts) >= ? ORDER BY id", (iso(since),)
        )

    def insert_fill(self, *, exec_id: str, order_id: int, symbol: str, side: str,
                    shares: float, price: float, ts: datetime, commission: float = 0.0,
                    commission_estimated: bool = True) -> bool:
        """Devolve False se a execução já estava registada (ib_async pode repetir eventos)."""
        try:
            self._execute(
                """INSERT INTO fills (ts, exec_id, order_id, symbol, side, shares, price, commission, commission_estimated)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (iso(ts), exec_id, order_id, symbol, side, shares, price, commission, int(commission_estimated)),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def fill_by_exec(self, exec_id: str) -> Optional[dict[str, Any]]:
        rows = self._query("SELECT * FROM fills WHERE exec_id=?", (exec_id,))
        return rows[0] if rows else None

    def set_fill_commission(self, exec_id: str, commission: float) -> Optional[float]:
        """Substitui a comissão estimada pela real; devolve a diferença (real − estimada) ou None."""
        row = self.fill_by_exec(exec_id)
        if row is None:
            return None
        if not row["commission_estimated"] and abs(float(row["commission"] or 0) - commission) < 1e-9:
            return 0.0
        delta = commission - float(row["commission"] or 0.0)
        self._execute("UPDATE fills SET commission=?, commission_estimated=0 WHERE exec_id=?", (commission, exec_id))
        return delta

    def allocate_fill(self, exec_id: str, trade_id: int, shares: float, commission: float) -> None:
        self._execute("INSERT INTO fill_allocations (exec_id, trade_id, shares, commission) VALUES (?,?,?,?)",
                      (exec_id, trade_id, shares, commission))

    def allocations_for_fill(self, exec_id: str) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM fill_allocations WHERE exec_id=? ORDER BY id", (exec_id,))

    def commissions_since(self, since: datetime) -> float:
        rows = self._query("SELECT COALESCE(SUM(commission), 0) AS c FROM fills WHERE ts >= ?", (iso(since),))
        return float(rows[0]["c"] or 0.0)

    # ---------------------------------------------------------------- P&L
    def snapshot_pnl(self, *, net_liq: Optional[float], cash: Optional[float],
                     unrealized: Optional[float], realized: Optional[float], ts: Optional[datetime] = None) -> None:
        self._execute(
            "INSERT INTO pnl_snapshots (ts, net_liq, cash, unrealized, realized) VALUES (?,?,?,?,?)",
            (iso(ts or utc_now()), net_liq, cash, unrealized, realized),
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
            "gross_pnl": round(sum(float(t.get("gross_pnl") or 0) for t in trades), 2),
            "commissions": round(sum(float(t.get("commission") or 0) for t in trades), 2),
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
