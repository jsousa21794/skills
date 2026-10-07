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

-- Ajustes PROVISÓRIOS de quantidade (posição reduzida fora do bot sem execução comprovada): reduzem a
-- quantidade própria sem fabricar uma saída definitiva; são consumidos quando chega a execução real (Y03).
CREATE TABLE IF NOT EXISTS ledger_adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    qty REAL NOT NULL,
    consumed_qty REAL NOT NULL DEFAULT 0,
    price_hint REAL,
    reason TEXT
);

-- Comandos remotos (MCP) auditados: autor, pedido, instante, conta, modo e resultado.
CREATE TABLE IF NOT EXISTS remote_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT,
    tool TEXT NOT NULL,
    args_json TEXT,
    account TEXT,
    mode TEXT,
    result TEXT
);

-- Histórico de TODAS as ordens que já pertenceram a um grupo (pai, TP, SL), incluindo as
-- substituídas por uma reparação: um fill tardio de um filho antigo continua a encontrar o
-- seu grupo/trade (N07).
CREATE TABLE IF NOT EXISTS exit_coverage (
    group_id INTEGER NOT NULL,     -- grupo cuja(s) ordem(ns) de saída cobre(m) o trade (Z04)
    trade_id INTEGER NOT NULL,
    PRIMARY KEY (group_id, trade_id)
);

CREATE TABLE IF NOT EXISTS order_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    leg TEXT NOT NULL,             -- PARENT | TP | SL
    order_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    replaced_ts TEXT,
    perm_id INTEGER                -- identidade durável da corretora, quando conhecida (V03)
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
    "label_source": "TEXT",  # "bracket:entrada" | "bracket:vela" | "direcional" | "censurado" | "ledger:*" (V09/W06)
    "label_final": "INTEGER NOT NULL DEFAULT 1",  # 0 = rótulo provisório (operação ainda aberta no horizonte) (X06)
    "provisional_correct": "INTEGER",  # rótulo provisório guardado quando o final o substitui (X06)
}
HISTORY_COLUMNS = {"perm_id": "INTEGER"}
ADJUSTMENT_COLUMNS = {"order_ids": "TEXT"}
ALLOCATION_COLUMNS = {"reason": "TEXT"}  # TP | SL | SIGNAL para saídas; NULL para entradas (AB06)  # saídas vivas no instante do ajuste: só as suas execuções o explicam (Z05)
TRADE_COLUMNS = {"decision_action": "TEXT", "stop_price": "REAL", "tp_price": "REAL", "risk_amount": "REAL",
                 "commission": "REAL NOT NULL DEFAULT 0", "gross_pnl": "REAL NOT NULL DEFAULT 0", "account": "TEXT",
                 "reconciled_ts": "TEXT"}  # instante da reconciliação, separado da data real de saída (AA06)
FILL_COLUMNS = {"commission": "REAL", "commission_estimated": "INTEGER NOT NULL DEFAULT 1", "account": "TEXT",
                "client_id": "INTEGER", "perm_id": "INTEGER", "order_ref": "TEXT", "con_id": "INTEGER"}  # identidade original (X02)
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
                                   ("order_groups", GROUP_COLUMNS), ("order_history", HISTORY_COLUMNS),
                                   ("ledger_adjustments", ADJUSTMENT_COLUMNS), ("fill_allocations", ALLOCATION_COLUMNS)):
                existing = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}
                for name, decl in columns.items():
                    if name not in existing:
                        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            # Backfill idempotente (AB05): fechos por reconciliação anteriores à coluna ``reconciled_ts`` guardam a data da
            # reconciliação à parte; se já tinham correções parciais, a data de fecho é reconstruída pelas execuções.
            self._conn.execute("""UPDATE trades SET reconciled_ts=exit_ts WHERE reconciled_ts IS NULL AND status='CLOSED'
                                  AND exit_ts IS NOT NULL AND (exit_reason='RECONCILED' OR exit_qty < COALESCE(filled_qty, qty) - 1e-9)""")
            for tid, in self._conn.execute(
                    """SELECT t.id FROM trades t WHERE t.reconciled_ts IS NOT NULL AND t.exit_ts = t.reconciled_ts
                       AND EXISTS (SELECT 1 FROM fill_allocations a WHERE a.trade_id = t.id)""").fetchall():
                last = self._conn.execute(
                    """SELECT MAX(f.ts) FROM fill_allocations a JOIN fills f ON f.exec_id = a.exec_id
                       WHERE a.trade_id=? AND f.side != (SELECT CASE WHEN direction > 0 THEN 'BOT' ELSE 'SLD' END FROM trades WHERE id=?)""",
                    (tid, tid)).fetchone()[0]
                if last:
                    self._conn.execute("UPDATE trades SET exit_ts=? WHERE id=?", (last, tid))
            # Backfill idempotente (AC04): alocações de SAÍDA anteriores à coluna ``reason`` recebem a razão pela identidade
            # histórica da ordem (perna TP/SL do grupo, fecho por sinal) e pela direção do trade; entradas ficam NULL.
            pending = self._conn.execute(
                """SELECT a.id, f.order_id, f.side, f.symbol, t.direction, t.group_id FROM fill_allocations a
                   JOIN fills f ON f.exec_id = a.exec_id JOIN trades t ON t.id = a.trade_id WHERE a.reason IS NULL""").fetchall()
            for alloc_id, order_id, side, symbol, direction, trade_group in pending:
                entry_side = "BOT" if int(direction) > 0 else "SLD"
                if (side or "").upper() == entry_side:
                    continue  # entrada: sem razão de saída
                reason = "OUTRO"
                groups = self._conn.execute(
                    """SELECT id, role, parent_order_id, tp_order_id, sl_order_id FROM order_groups WHERE symbol=? AND (parent_order_id=?
                       OR tp_order_id=? OR sl_order_id=? OR id IN (SELECT group_id FROM order_history WHERE order_id=?))
                       ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END, id DESC""",
                    (symbol, order_id, order_id, order_id, order_id, trade_group)).fetchall()
                for gid, role, parent, tp, sl in groups:
                    if role == "CLOSE":
                        reason = "SIGNAL"
                    elif order_id == tp:
                        reason = "TP"
                    elif order_id == sl:
                        reason = "SL"
                    else:
                        leg = self._conn.execute("SELECT leg FROM order_history WHERE group_id=? AND order_id=? ORDER BY id DESC LIMIT 1",
                                                 (gid, order_id)).fetchone()
                        reason = leg[0] if leg and leg[0] in ("TP", "SL") else reason
                    if reason != "OUTRO":
                        break
                self._conn.execute("UPDATE fill_allocations SET reason=? WHERE id=?", (reason, alloc_id))
            # Backfill idempotente (V04): grupos anteriores à tabela order_history ficam com as suas pernas
            # registadas, para que uma reparação posterior arquive em vez de perder os IDs antigos.
            rows = self._conn.execute(
                """SELECT g.id, g.ts, g.parent_order_id, g.tp_order_id, g.sl_order_id FROM order_groups g
                   WHERE NOT EXISTS (SELECT 1 FROM order_history h WHERE h.group_id = g.id)""").fetchall()
            for gid, ts, parent, tp, sl in rows:
                for leg, oid in (("PARENT", parent), ("TP", tp), ("SL", sl)):
                    if oid is not None:
                        self._conn.execute("INSERT INTO order_history (group_id, leg, order_id, ts) VALUES (?,?,?,?)",
                                           (gid, leg, int(oid), ts))
            self._conn.commit()

    def accounts_in_records(self) -> set[str]:
        """Contas presentes nos registos (grupos, trades, fills, decisões): proveniência dos dados (V06)."""
        out: set[str] = set()
        for table in ("order_groups", "trades", "fills", "decisions"):
            for r in self._query(f"SELECT DISTINCT account FROM {table} WHERE account IS NOT NULL AND account != ''"):
                out.add(str(r["account"]))
        return out

    def known_order_keys(self) -> set[tuple]:
        """Identidade COMPLETA das ordens conhecidas: (símbolo, conta, conId, orderId). Um número de ordem
        sozinho não é identidade global (sessões/clientes diferentes reutilizam-no) (X04)."""
        keys: set[tuple] = set()
        rows = self._query(
            """SELECT g.symbol, g.account, g.con_id, h.order_id, h.perm_id FROM order_history h JOIN order_groups g ON g.id = h.group_id
               UNION SELECT symbol, account, con_id, parent_order_id, NULL FROM order_groups WHERE parent_order_id IS NOT NULL
               UNION SELECT symbol, account, con_id, tp_order_id, NULL FROM order_groups WHERE tp_order_id IS NOT NULL
               UNION SELECT symbol, account, con_id, sl_order_id, NULL FROM order_groups WHERE sl_order_id IS NOT NULL""")
        for r in rows:
            keys.add((r["symbol"], r["account"] or None, r["con_id"] or None, int(r["order_id"]), r.get("perm_id") or None))
        return keys

    @staticmethod
    def _same_order(key: tuple, known: set[tuple]) -> Optional[bool]:
        """True = a mesma ordem (identidade durável comprovada); False = ordem distinta; None = coincidência
        parcial (identidade permanente desconhecida de um dos lados): conflito a assinalar, nunca descarte (Y04)."""
        symbol, account, con_id, oid, perm = key
        verdict: Optional[bool] = False
        for k_symbol, k_account, k_con, k_oid, k_perm in known:
            if k_oid != oid or k_symbol != symbol:
                continue
            if account and k_account and account != k_account:
                continue
            if con_id and k_con and int(con_id) != int(k_con):
                continue
            if perm and k_perm:
                if int(perm) == int(k_perm):
                    return True
                continue  # mesmo número local, permId diferente: ordens distintas
            verdict = None
        return verdict

    def merge_open_state_from(self, other: "Database") -> dict[str, int]:
        """Importa de outra base (legado que coexiste com esta) só o estado OPERACIONAL: proteções ainda
        ativas e trades abertos com os seus grupos e pernas (IDs remapeados), numa ÚNICA transação e com
        DEDUPLICAÇÃO pela identidade das ordens e da entrada (W04). O histórico fechado fica no ficheiro
        de origem; paper e live nunca são fundidos cegamente (V05)."""
        counts = {"protections": 0, "trades": 0, "duplicates": 0, "conflicts": 0}
        now = utc_now()
        known_ids = self.known_order_keys()
        existing = {(t["symbol"], t.get("account") or None, int(t["direction"]), t.get("entry_ts"), round(float(t.get("filled_qty") or 0), 6))
                    for t in self.open_trades()}
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._merge_open_state(other, now, known_ids, existing, counts)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return counts

    def _merge_open_state(self, other: "Database", now: datetime, known_ids: set[int], existing: set, counts: dict[str, int]) -> None:
        for ev in other.active_protections(now):
            if self._conn.execute("SELECT 1 FROM protection_events WHERE name=? AND until=? AND symbol IS ?",
                                  (ev["name"], ev["until"], ev["symbol"])).fetchone():
                continue
            self._conn.execute("INSERT INTO protection_events (ts, name, symbol, until, reason) VALUES (?,?,?,?,?)",
                               (ev["ts"], ev["name"], ev["symbol"], ev["until"], f"{ev.get('reason') or ''} [importado]"))
            counts["protections"] += 1
        for t in other.open_trades():
            group = other._query("SELECT * FROM order_groups WHERE id=?", (t["group_id"],))
            g = group[0] if group else None
            keys = set()
            if g is not None:
                for leg, v in (("PARENT", g.get("parent_order_id")), ("TP", g.get("tp_order_id")), ("SL", g.get("sl_order_id"))):
                    if v is not None:
                        perm = other.perm_id_for(int(g["id"]), int(v))
                        keys.add((g["symbol"], g.get("account") or None, g.get("con_id") or None, int(v), perm))
            key = (t["symbol"], t.get("account") or None, int(t["direction"]), t.get("entry_ts"), round(float(t.get("filled_qty") or 0), 6))
            verdicts = [self._same_order(k, known_ids) for k in keys]
            if any(v is True for v in verdicts) or key in existing:
                counts["duplicates"] += 1
                continue
            if any(v is None for v in verdicts):
                counts["conflicts"] += 1  # importado na mesma, mas assinalado: identidade permanente por confirmar
            gid = None
            if g is not None:
                gid = self._insert_group_raw(
                    symbol=g["symbol"], decision_id=None, role=g["role"], direction=int(g["direction"]), qty=float(g["qty"]),
                    parent_order_id=g.get("parent_order_id"), tp_order_id=g.get("tp_order_id"), sl_order_id=g.get("sl_order_id"),
                    ref_price=g.get("ref_price"), tp_price=g.get("tp_price"), sl_price=g.get("sl_price"),
                    account=g.get("account"), con_id=g.get("con_id"), ts=datetime.fromisoformat(g["ts"]))
                for h in other._query("SELECT * FROM order_history WHERE group_id=? AND replaced_ts IS NOT NULL", (g["id"],)):
                    self._conn.execute("INSERT INTO order_history (group_id, leg, order_id, ts, replaced_ts, perm_id) VALUES (?,?,?,?,?,?)",
                                       (gid, h["leg"], h["order_id"], h["ts"], h["replaced_ts"], h.get("perm_id")))
                for k_symbol, k_account, k_con, oid, perm in keys:  # identidade permanente das pernas ativas (Y04)
                    if perm:
                        self._conn.execute("UPDATE order_history SET perm_id=? WHERE group_id=? AND order_id=? AND perm_id IS NULL",
                                           (int(perm), gid, int(oid)))
                known_ids |= keys
            self._conn.execute(
                """INSERT INTO trades (symbol, decision_id, group_id, direction, qty, filled_qty, entry_ts, entry_price,
                   exit_qty, exit_reason, pnl, status, stop_price, tp_price, risk_amount, commission, gross_pnl, account)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,'OPEN',?,?,?,?,?,?)""",
                (t["symbol"], None, gid, int(t["direction"]), float(t["qty"]), float(t["filled_qty"] or 0), t.get("entry_ts"),
                 t.get("entry_price"), float(t.get("exit_qty") or 0), t.get("exit_reason"), float(t.get("pnl") or 0),
                 t.get("stop_price"), t.get("tp_price"), t.get("risk_amount"), float(t.get("commission") or 0),
                 float(t.get("gross_pnl") or 0), t.get("account")))
            existing.add(key)
            counts["trades"] += 1

    def _insert_group_raw(self, *, symbol, decision_id, role, direction, qty, parent_order_id, tp_order_id, sl_order_id,
                          ref_price, tp_price, sl_price, account=None, con_id=None, ts=None) -> int:
        """Versão sem commit de ``insert_order_group`` para uso dentro de uma transação."""
        when = iso(ts or utc_now())
        cur = self._conn.execute(
            """INSERT INTO order_groups (ts, symbol, decision_id, role, direction, qty,
               parent_order_id, tp_order_id, sl_order_id, ref_price, tp_price, sl_price, account, con_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (when, symbol, decision_id, role, direction, qty, parent_order_id, tp_order_id, sl_order_id, ref_price, tp_price,
             sl_price, account, con_id))
        group_id = int(cur.lastrowid)
        for leg, oid in (("PARENT", parent_order_id), ("TP", tp_order_id), ("SL", sl_order_id)):
            if oid is not None:
                self._conn.execute("INSERT INTO order_history (group_id, leg, order_id, ts) VALUES (?,?,?,?)",
                                   (group_id, leg, int(oid), when))
        return group_id

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

    def provisional_decisions(self) -> list[dict[str, Any]]:
        """Decisões já avaliadas com rótulo PROVISÓRIO (operação ainda aberta na altura) (X06)."""
        return self._query("SELECT * FROM decisions WHERE settled_ts IS NOT NULL AND label_final=0 ORDER BY ts")

    def finalize_label(self, decision_id: int, *, correct: Optional[int], label_source: str) -> None:
        self._execute(
            """UPDATE decisions SET provisional_correct=correct, correct=?, label_source=?, label_final=1 WHERE id=?""",
            (correct, label_source, decision_id))
        self.set_kv("labels_changed_at", iso(utc_now()))  # modelos ajustados antes disto têm de ser reajustados (Y05)

    def settle_decision(self, decision_id: int, *, settled_price: float, settled_return: float,
                        bench_return: Optional[float], alpha: Optional[float], correct: Optional[int],
                        horizon_min: int, label_source: Optional[str] = None, final: bool = True) -> None:
        self._execute(
            """UPDATE decisions SET settled_ts=?, settled_price=?, settled_return=?, bench_return=?, alpha=?,
               correct=?, horizon_min=?, label_source=COALESCE(?, label_source), label_final=? WHERE id=?""",
            (iso(utc_now()), settled_price, settled_return, bench_return, alpha, correct, horizon_min, label_source,
             1 if final else 0, decision_id),
        )

    def settled_decisions(self, since: Optional[datetime] = None, symbol: Optional[str] = None,
                          directional_only: bool = False, final_only: bool = True) -> list[dict[str, Any]]:
        """Decisões avaliadas. Por defeito SÓ rótulos finais: os provisórios (operação ainda aberta) nunca
        entram em calibração, lições ou métricas finais (Y05)."""
        sql = "SELECT * FROM decisions WHERE settled_ts IS NOT NULL"
        params: list[Any] = []
        if final_only:
            sql += " AND label_final=1"
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
                              model: Optional[str] = None, prompt_version: Optional[int] = None) -> list[dict[str, Any]]:
        """Trades fechados; com ``model``/``prompt_version`` só os originados por decisões dessa experiência (N11)."""
        sql = "SELECT t.* FROM trades t"
        params: list[Any] = []
        if model is not None or prompt_version is not None:
            sql += " JOIN decisions d ON d.id = t.decision_id"
        sql += " WHERE t.status='CLOSED' AND t.exit_ts >= ?"
        params.append(iso(since))
        if until:
            sql += " AND t.exit_ts <= ?"
            params.append(iso(until))
        if model is not None:
            sql += " AND d.model = ?"
            params.append(model)
        if prompt_version is not None:
            sql += " AND d.prompt_version = ?"
            params.append(prompt_version)
        return self._query(sql + " ORDER BY t.exit_ts", params)

    def recent_stop_count(self, since: datetime) -> int:
        # Um stop numa saída MISTA (SL+TP, SL|TP) conta como evento de stop uma só vez por trade (AC06).
        rows = self._query(
            f"SELECT COUNT(*) AS n FROM trades WHERE status='CLOSED' AND {self._component_sql('exit_reason', 'SL')} AND exit_ts >= ?",
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
        """Substitui os filhos ativos do grupo, ARQUIVANDO os anteriores em ``order_history`` (N07).

        Se o ID antigo ainda não estiver no histórico (grupo herdado), é inserido primeiro: nunca se perde
        a identidade de um filho substituído (V04)."""
        when = iso(ts or utc_now())
        current = self._query("SELECT ts, tp_order_id, sl_order_id FROM order_groups WHERE id=?", (group_id,))
        for leg, oid in (("TP", tp_order_id), ("SL", sl_order_id)):
            if oid is None:
                continue
            if current:
                old_id = current[0]["tp_order_id" if leg == "TP" else "sl_order_id"]
                if old_id is not None and not self._query(
                        "SELECT 1 FROM order_history WHERE group_id=? AND leg=? AND order_id=?", (group_id, leg, int(old_id))):
                    self._execute("INSERT INTO order_history (group_id, leg, order_id, ts) VALUES (?,?,?,?)",
                                  (group_id, leg, int(old_id), current[0]["ts"]))
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

    def set_perm_id(self, order_id: int, perm_id: Optional[int], *, group_id: Optional[int] = None,
                    overwrite: bool = False) -> None:
        """Associa o ``permId`` da corretora a UMA ordem de UM grupo (nunca a todos os grupos com o mesmo
        orderId); com ``overwrite`` corrige uma associação anterior (W03)."""
        if not perm_id:
            return
        sql = "UPDATE order_history SET perm_id=? WHERE order_id=?" + ("" if overwrite else " AND perm_id IS NULL")
        params: list[Any] = [int(perm_id), int(order_id)]
        if group_id is not None:
            sql += " AND group_id=?"
            params.append(int(group_id))
        self._execute(sql, params)

    def unallocated_fills(self, symbol: Optional[str] = None, order_id: Optional[int] = None) -> list[dict[str, Any]]:
        """Execuções registadas com saldo POR ALOCAR: ``shares`` menos a soma das alocações já feitas (AA03). Inclui as
        parcialmente alocadas (ex.: saída recebida antes do resto da entrada); ``remaining`` é o saldo recuperável."""
        sql = """SELECT f.*, f.shares - COALESCE((SELECT SUM(a.shares) FROM fill_allocations a WHERE a.exec_id = f.exec_id), 0) AS remaining
                 FROM fills f WHERE f.shares - COALESCE((SELECT SUM(a.shares) FROM fill_allocations a WHERE a.exec_id = f.exec_id), 0) > 1e-9"""
        params: list[Any] = []
        if symbol:
            sql += " AND f.symbol=?"
            params.append(symbol)
        if order_id is not None:
            sql += " AND f.order_id=?"
            params.append(int(order_id))
        return self._query(sql + " ORDER BY f.id", params)

    def perm_id_for(self, group_id: int, order_id: int) -> Optional[int]:
        rows = self._query("SELECT perm_id FROM order_history WHERE group_id=? AND order_id=? AND perm_id IS NOT NULL LIMIT 1",
                           (int(group_id), int(order_id)))
        return int(rows[0]["perm_id"]) if rows else None

    def trade_for_decision(self, decision_id: int) -> Optional[dict[str, Any]]:
        rows = self._query("SELECT * FROM trades WHERE decision_id=? ORDER BY id DESC LIMIT 1", (decision_id,))
        return rows[0] if rows else None

    def group_for_order(self, order_id: int, *, symbol: Optional[str] = None, con_id: Optional[int] = None,
                        account: Optional[str] = None, perm_id: Optional[int] = None) -> Optional[dict[str, Any]]:
        """Grupo a que a ordem pertence (ativa ou arquivada), validando símbolo, contrato, conta e, quando
        ambos são conhecidos, o ``permId`` (N06/V03).

        Um grupo gravado sem ``con_id``/``account`` (versões antigas) só é aceite se o símbolo coincidir.
        """
        rows = self._query(
            """SELECT g.* FROM order_groups g
               WHERE g.parent_order_id=? OR g.tp_order_id=? OR g.sl_order_id=?
                  OR g.id IN (SELECT group_id FROM order_history WHERE order_id=?)
               ORDER BY g.id DESC""",
            (order_id, order_id, order_id, order_id),
        )
        candidates = []
        for row in rows:
            if symbol is not None and row["symbol"] != symbol:
                continue
            if con_id and row.get("con_id") and int(row["con_id"]) != int(con_id):
                continue
            if account and row.get("account") and row["account"] != account:
                continue
            if perm_id:
                known = self.perm_id_for(int(row["id"]), int(order_id))
                if known is not None and known != int(perm_id):
                    continue
                if known is not None:
                    return row  # correspondência permanente EXATA prevalece sobre identidades desconhecidas (AB01)
            candidates.append(row)
        if not candidates:
            return None
        if perm_id and len(candidates) > 1:
            return None  # identidade AMBÍGUA (vários grupos sem permId conhecido): a execução fica pendente (AB01)
        return candidates[0]

    def groups_for_order(self, order_id: int, symbol: Optional[str] = None) -> list[dict[str, Any]]:
        """Todos os grupos (do símbolo) em que a ordem é ou foi perna, do mais recente ao mais antigo."""
        rows = self._query(
            """SELECT g.* FROM order_groups g WHERE g.parent_order_id=? OR g.tp_order_id=? OR g.sl_order_id=?
               OR g.id IN (SELECT group_id FROM order_history WHERE order_id=?) ORDER BY g.id DESC""",
            (order_id, order_id, order_id, order_id))
        return [r for r in rows if symbol is None or r["symbol"] == symbol]

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
        now = iso(utc_now())
        self._execute("UPDATE trades SET status='CLOSED', exit_ts=?, reconciled_ts=?, exit_reason=? WHERE id=? AND status='OPEN'",
                      (now, now, reason, trade_id))

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
                          commission: float = 0.0) -> dict[str, Any]:
        """Atualiza preço médio de entrada com execuções (possivelmente parciais); a comissão
        entra logo no P&L líquido (``pnl``), ficando ``gross_pnl`` sem custos."""
        trade = self._query("SELECT * FROM trades WHERE id=?", (trade_id,))[0]
        filled = float(trade["filled_qty"] or 0)
        prev_price = float(trade["entry_price"] or 0)
        new_filled = filled + shares
        avg = (prev_price * filled + price * shares) / new_filled if new_filled else price
        # Saídas já contabilizadas usaram o custo médio antigo: o lucro bruto é corrigido para o custo médio novo, para
        # que as mesmas execuções deem o mesmo resultado em qualquer ordem de chegada (AB03).
        exit_qty = float(trade["exit_qty"] or 0)
        reprice = -(avg - prev_price) * exit_qty * int(trade["direction"]) if (exit_qty > 0 and filled > 0) else 0.0
        # Uma entrada tardia que excede as saídas REABRE um trade dado como fechado: o saldo volta a ser próprio (AB04).
        reopened = trade["status"] == "CLOSED" and new_filled > exit_qty + 1e-9
        self._execute(
            """UPDATE trades SET filled_qty=?, entry_price=?, entry_ts=COALESCE(entry_ts, ?),
               commission=commission+?, pnl=pnl-?+?, gross_pnl=gross_pnl+?, status=? WHERE id=?""",
            (new_filled, avg, iso(ts), commission, commission, reprice, reprice, "OPEN" if reopened else trade["status"], trade_id),
        )
        return {"reopened": reopened, "trade": self._query("SELECT * FROM trades WHERE id=?", (trade_id,))[0]}

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

    def add_adjustment(self, trade_id: int, qty: float, price_hint: Optional[float], reason: str,
                       ts: Optional[datetime] = None, order_ids: Optional[Iterable[int]] = None) -> None:
        """Ajuste PROVISÓRIO de quantidade ligado à evidência que o justifica (Z05): as saídas vivas do trade no
        instante da discrepância (``order_ids``; por defeito, as pernas ativas/arquivadas dos grupos que o cobrem).
        Só uma execução dessas ordens o explica; saídas colocadas DEPOIS fecham a quantidade viva e nunca o consomem."""
        if order_ids is None:
            order_ids = self.exit_order_ids_for_trade(int(trade_id))
        self._execute("INSERT INTO ledger_adjustments (trade_id, ts, qty, price_hint, reason, order_ids) VALUES (?,?,?,?,?,?)",
                      (int(trade_id), iso(ts or utc_now()), float(qty), price_hint, reason,
                       json.dumps(sorted({int(o) for o in order_ids}))))

    def exit_order_ids_for_trade(self, trade_id: int) -> set[int]:
        """Ordens de saída (TP/SL ativas e arquivadas, fechos por sinal) de todos os grupos que cobrem o trade."""
        ids: set[int] = set()
        for g in self._query(
                """SELECT g.* FROM order_groups g WHERE g.id IN (SELECT group_id FROM trades WHERE id=?)
                   OR g.id IN (SELECT group_id FROM exit_coverage WHERE trade_id=?)""", (int(trade_id), int(trade_id))):
            for col in ("tp_order_id", "sl_order_id"):
                if g.get(col) is not None:
                    ids.add(int(g[col]))
            if g["role"] == "CLOSE" and g.get("parent_order_id") is not None:
                ids.add(int(g["parent_order_id"]))
            for h in self._query("SELECT order_id FROM order_history WHERE group_id=? AND leg IN ('TP','SL')", (int(g["id"]),)):
                ids.add(int(h["order_id"]))
        return ids

    def open_adjustment_qty(self, trade_id: int) -> float:
        rows = self._query("SELECT COALESCE(SUM(qty - consumed_qty), 0) AS q FROM ledger_adjustments WHERE trade_id=?", (int(trade_id),))
        return float(rows[0]["q"] or 0.0)

    def open_adjustments_for_symbol(self, symbol: str, include_closed: bool = False) -> float:
        status = "" if include_closed else " AND t.status='OPEN'"
        rows = self._query(
            f"""SELECT COALESCE(SUM(a.qty - a.consumed_qty), 0) AS q FROM ledger_adjustments a JOIN trades t ON t.id = a.trade_id
               WHERE t.symbol=?{status}""", (symbol,))
        return float(rows[0]["q"] or 0.0)

    def consume_adjustment(self, trade_id: int, qty: float, order_id: Optional[int] = None) -> float:
        """Uma execução REAL substitui o ajuste provisório na mesma quantidade (nunca se soma duas vezes) (Y03) — mas
        só se a EXPLICA (Z05): a ordem da execução tem de ser uma das saídas vivas quando o ajuste foi registado
        (ajustes antigos sem essa lista aceitam qualquer ordem anterior ao ajuste). Devolve a quantidade consumida."""
        remaining = float(qty)
        consumed = 0.0
        for row in self._query("SELECT id, ts, qty, consumed_qty, order_ids FROM ledger_adjustments WHERE trade_id=? "
                               "AND qty - consumed_qty > 1e-9 ORDER BY id", (int(trade_id),)):
            if remaining <= 1e-9:
                break
            if order_id is not None and not self._explains_adjustment(row, int(order_id)):
                continue
            portion = min(remaining, float(row["qty"]) - float(row["consumed_qty"]))
            self._execute("UPDATE ledger_adjustments SET consumed_qty=consumed_qty+? WHERE id=?", (portion, row["id"]))
            remaining -= portion
            consumed += portion
        return consumed

    def _explains_adjustment(self, adjustment: dict[str, Any], order_id: int) -> bool:
        raw = adjustment.get("order_ids")
        if raw:
            try:
                return int(order_id) in {int(x) for x in json.loads(raw)}
            except (ValueError, TypeError):
                return False
        placed = self._query("SELECT MIN(ts) AS ts FROM order_history WHERE order_id=?", (int(order_id),))
        return not placed or placed[0]["ts"] is None or placed[0]["ts"] <= adjustment["ts"]

    def record_remote_command(self, *, actor: str, tool: str, args: Any, account: Optional[str], mode: str, result: str) -> int:
        cur = self._execute(
            "INSERT INTO remote_commands (ts, actor, tool, args_json, account, mode, result) VALUES (?,?,?,?,?,?,?)",
            (iso(utc_now()), actor, tool, json.dumps(args, ensure_ascii=False, default=str), account, mode, result))
        return int(cur.lastrowid)

    def recent_remote_commands(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM remote_commands ORDER BY id DESC LIMIT ?", (limit,))

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
                    commission_estimated: bool = True, account: Optional[str] = None, client_id: Optional[int] = None,
                    perm_id: Optional[int] = None, order_ref: Optional[str] = None, con_id: Optional[int] = None) -> bool:
        """Devolve False se a execução já estava registada (ib_async pode repetir eventos). A identidade
        ORIGINAL (conta, clientId, permId, orderRef, conId) fica persistida para qualquer reprocessamento (X02)."""
        try:
            self._execute(
                """INSERT INTO fills (ts, exec_id, order_id, symbol, side, shares, price, commission, commission_estimated,
                   account, client_id, perm_id, order_ref, con_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (iso(ts), exec_id, order_id, symbol, side, shares, price, commission, int(commission_estimated),
                 account or None, client_id, perm_id, order_ref, con_id),
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

    def allocate_fill(self, exec_id: str, trade_id: int, shares: float, commission: float, reason: Optional[str] = None) -> None:
        self._execute("INSERT INTO fill_allocations (exec_id, trade_id, shares, commission, reason) VALUES (?,?,?,?,?)",
                      (exec_id, trade_id, shares, commission, reason))

    def set_allocation_commission(self, allocation_id: int, commission: float) -> None:
        self._execute("UPDATE fill_allocations SET commission=? WHERE id=?", (float(commission), int(allocation_id)))

    def refresh_exit_summary(self, trade_id: int) -> Optional[dict[str, Any]]:
        """Razão, data e preço de saída derivados do conjunto CRONOLÓGICO das execuções de saída alocadas ao trade
        (AB06): ``exit_reason`` = razões distintas por ordem de execução unidas por ``+`` (ex.: ``TP+SL``),
        ``exit_ts`` = execução mais recente, ``exit_price`` = média ponderada. Independente da ordem de chegada."""
        rows = self._query(
            """SELECT a.shares, a.reason, f.ts, f.price FROM fill_allocations a JOIN fills f ON f.exec_id = a.exec_id
               WHERE a.trade_id=? AND a.reason IS NOT NULL ORDER BY f.ts, a.id""", (int(trade_id),))
        if not rows:
            return None
        # Sequência só é comprovada quando os instantes de execução a ordenam: razões DIFERENTES no mesmo instante
        # tornam o primeiro toque indeterminado -> ``SL|TP`` (ordem alfabética, censurado no treino) (AC05).
        by_ts: dict[str, set[str]] = {}
        for r in rows:
            by_ts.setdefault(r["ts"], set()).add(r["reason"])
        indeterminate = any(len(v) > 1 for v in by_ts.values())
        reasons: list[str] = []
        for r in rows:
            if r["reason"] not in reasons:
                reasons.append(r["reason"])
        label = "|".join(sorted(reasons)) if indeterminate else "+".join(reasons)
        trade = self._query("SELECT exit_qty FROM trades WHERE id=?", (int(trade_id),))[0]
        covered = sum(float(r["shares"]) for r in rows)
        if covered + 1e-9 < float(trade["exit_qty"] or 0):
            label += "+?"  # saídas sem razão conhecida: resumo INCOMPLETO, nunca apresentado como conclusão (AC04)
        total = covered or 1.0
        price = sum(float(r["shares"]) * float(r["price"]) for r in rows) / total
        last_ts = max(r["ts"] for r in rows)
        self._execute("UPDATE trades SET exit_reason=?, exit_ts=?, exit_price=? WHERE id=?",
                      (label, last_ts, price, int(trade_id)))
        return self._query("SELECT * FROM trades WHERE id=?", (int(trade_id),))[0]

    @staticmethod
    def exit_components(reason: Optional[str]) -> set[str]:
        """Razões elementares de uma saída (``TP``, ``SL``, ``SIGNAL``...) a partir de ``TP+SL``, ``SL|TP`` ou ``TP+?`` (AC06)."""
        return {p for p in (reason or "").replace("|", "+").split("+") if p and p != "?"}

    @staticmethod
    def _component_sql(column: str, component: str) -> str:
        """Condição SQL: ``component`` é uma das razões elementares de ``column`` (AC06)."""
        return f"('+' || REPLACE(COALESCE({column}, ''), '|', '+') || '+') LIKE '%+{component}+%'"

    def unfinalize_label(self, decision_id: int) -> None:
        """A decisão volta a rótulo PROVISÓRIO (o trade reabriu): o avaliador finaliza de novo pelo ledger (AB04)."""
        self._execute("UPDATE decisions SET label_final=0 WHERE id=? AND settled_ts IS NOT NULL AND label_final=1", (int(decision_id),))
        self.set_kv("labels_changed_at", iso(utc_now()))

    def add_exit_coverage(self, group_id: int, trade_ids: Iterable[int]) -> None:
        """Relação EXPLÍCITA entre uma ordem de saída (grupo) e os trades que ela cobre (Z04)."""
        for tid in trade_ids:
            self._execute("INSERT OR IGNORE INTO exit_coverage (group_id, trade_id) VALUES (?,?)", (int(group_id), int(tid)))

    def trades_for_order(self, order_id: int, symbol: Optional[str] = None, perm_id: Optional[int] = None,
                         group_id: Optional[int] = None) -> list[dict[str, Any]]:
        """Trades que uma ordem de saída cobre: os dos grupos onde a ordem é/foi perna (ativa ou arquivada) e os
        ligados explicitamente em ``exit_coverage``. Nunca inclui outros trades do mesmo ativo (Z04). A identidade
        PERMANENTE acompanha a consulta (AA02): um grupo cujo ``permId`` conhecido para esta ordem difere do da
        execução fica excluído (orderId reutilizado entre sessões); ``group_id`` é o grupo já validado no callback."""
        groups = [int(r["id"]) for r in self._query(
            """SELECT id FROM order_groups WHERE parent_order_id=? OR tp_order_id=? OR sl_order_id=?
               OR id IN (SELECT group_id FROM order_history WHERE order_id=?)""", (order_id, order_id, order_id, order_id))]
        if symbol:
            groups = [g for g in groups if self._query("SELECT 1 FROM order_groups WHERE id=? AND symbol=?", (g, symbol))]
        if perm_id:
            exact, unknown = [], []
            for g in groups:
                known = self.perm_id_for(g, int(order_id))
                if known is None:
                    unknown.append(g)
                elif int(known) == int(perm_id):
                    exact.append(g)
            groups = exact or unknown  # a correspondência exata prevalece sobre identidades desconhecidas (AB01)
        if group_id is not None and int(group_id) not in groups:
            groups.append(int(group_id))
        if not groups:
            return []
        marks = ",".join("?" * len(groups))
        where_symbol = "AND symbol=?" if symbol else ""
        rows = self._query(
            f"""SELECT * FROM trades WHERE (group_id IN ({marks}) OR id IN (SELECT trade_id FROM exit_coverage WHERE group_id IN ({marks})))
                {where_symbol} ORDER BY id""", (*groups, *groups, *([symbol] if symbol else [])))
        if not rows:
            # Fecho por sinal gravado por uma versão anterior (sem ``exit_coverage``): cobre, por CRONOLOGIA, os trades do
            # ativo abertos ANTES da ordem de fecho — nunca os abertos depois (Z04).
            for g in self._query(f"SELECT * FROM order_groups WHERE id IN ({marks}) AND role='CLOSE'", groups):
                rows += self._query(
                    """SELECT * FROM trades WHERE symbol=? AND filled_qty > 0 AND COALESCE(entry_ts, '') <= ?
                       AND id NOT IN (SELECT trade_id FROM exit_coverage) ORDER BY id""", (g["symbol"], g["ts"]))
        return rows

    def record_late_exit_fill(self, trade_id: int, shares: float, price: float, ts: datetime, reason: str,
                              commission: float = 0.0) -> dict[str, Any]:
        """Execução que chega DEPOIS do trade ter sido fechado por reconciliação (sem execução disponível): corrige a
        quantidade saída e o P&L desse trade sem o reabrir (Z04/Z05)."""
        trade = self._query("SELECT * FROM trades WHERE id=?", (trade_id,))[0]
        direction = int(trade["direction"])
        entry = float(trade["entry_price"] or price)
        gross_increment = (price - entry) * shares * direction
        exit_qty = float(trade["exit_qty"] or 0) + shares
        # Data de fecho = execução comprovada mais recente que completa a saída; a data da reconciliação fica à parte (AA06).
        when = iso(ts)
        current = trade.get("exit_ts")
        if current is None or current == trade.get("reconciled_ts") or when > current:
            exit_ts = when
        else:
            exit_ts = current
        self._execute(
            """UPDATE trades SET exit_qty=?, exit_price=?, exit_ts=?, exit_reason=?, gross_pnl=gross_pnl+?, pnl=pnl+?-?,
               commission=commission+? WHERE id=?""",
            (exit_qty, price, exit_ts, reason, gross_increment, gross_increment, commission, commission, trade_id))
        return self._query("SELECT * FROM trades WHERE id=?", (trade_id,))[0]

    def allocations_for_fill(self, exec_id: str) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM fill_allocations WHERE exec_id=? ORDER BY id", (exec_id,))

    def allocated_shares(self, exec_id: str) -> float:
        rows = self._query("SELECT COALESCE(SUM(shares), 0) AS q FROM fill_allocations WHERE exec_id=?", (exec_id,))
        return float(rows[0]["q"] or 0.0)

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
            "stop_hits": sum(1 for t in trades if "SL" in self.exit_components(t["exit_reason"])),
            "tp_hits": sum(1 for t in trades if "TP" in self.exit_components(t["exit_reason"])),
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

    def kv_with_prefix(self, prefix: str) -> dict[str, str]:
        rows = self._query("SELECT key, value FROM kv WHERE key LIKE ? AND value IS NOT NULL AND value != ''", (prefix + "%",))
        return {r["key"][len(prefix):]: r["value"] for r in rows}

    def set_kv(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO kv (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
