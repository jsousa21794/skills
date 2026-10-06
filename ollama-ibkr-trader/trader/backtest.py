"""Replay offline das decisões sobre velas de 1 minuto, com a MESMA política do live.

Reutiliza ``policy.decide_execution`` (persistência, acordo, gates, sizing por
ATR, limiar com custos, EV, cooldown, kill-switch, fecho por sinal contrário,
short) sobre um relógio de simulação. Todos os fills, comissões e slippage
passam pelo mesmo ledger (tabelas ``trades``/``fills``) que alimenta o
relatório, pelo que a equity e as métricas derivam dos mesmos números.

Fills: entrada em limit marketable ao fecho da vela de decisão (± slippage);
stop executado ao primeiro preço disponível (abertura da vela se abrir já
além do stop, senão o stop) com slippage adverso; TP ao nível ou à abertura
se abrir melhor.

Uso: ``python -m trader.backtest --csv dados.csv --symbol AAPL [--model llama3]``
CSV esperado: ``time,open,high,low,close,volume`` com ``time`` ISO-8601 (UTC).

Avisos: o LLM pode conhecer o período (contaminação temporal); mantém
``anonymize_prompt=True`` e trata como evidência só o período posterior ao
cutoff do modelo e o paper/real forward.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .analytics import Analytics
from .calibration import Calibrator, ConfidenceSignals
from .config import Settings
from .database import Database
from .indicators import Bar
from .policy import ExecutionPlan, PersistenceTracker, Skip, decide_execution
from .risk import GateResult, PositionSizer, RiskGate, commission as estimate_commission
from .settlement import Settler

log = logging.getLogger("trader.backtest")


def load_csv(path: str) -> list[Bar]:
    bars: list[Bar] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ts = datetime.fromisoformat(row["time"].replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            bars.append(Bar(ts, float(row["open"]), float(row["high"]), float(row["low"]),
                            float(row["close"]), float(row.get("volume") or 0)))
    bars.sort(key=lambda b: b.time)
    return bars


def load_alpaca(symbol: str, start: datetime, end: datetime, key: str, secret: str) -> list[Bar]:
    """Alpaca Market Data v2 (plano Basic: SIP com ``feed=sip``, sem os últimos 15 min)."""
    import requests

    bars: list[Bar] = []
    url = "https://data.alpaca.markets/v2/stocks/bars"
    params: dict[str, Any] = {"symbols": symbol, "timeframe": "1Min", "start": start.isoformat(),
                              "end": end.isoformat(), "limit": 10000, "feed": "sip", "adjustment": "raw"}
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    while True:
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for b in data.get("bars", {}).get(symbol, []):
            bars.append(Bar(datetime.fromisoformat(b["t"].replace("Z", "+00:00")), b["o"], b["h"], b["l"], b["c"], b["v"]))
        token = data.get("next_page_token")
        if not token:
            break
        params["page_token"] = token
    return bars


@dataclass
class SimPosition:
    trade_id: int
    direction: int
    qty: int
    entry_price: float
    stop: float
    tp: float
    opened: datetime


class Replayer:
    def __init__(self, settings: Settings, brain: Any, db: Optional[Database] = None, equity: float = 100_000.0) -> None:
        self.s = settings
        self.brain = brain
        self.db = db or Database(":memory:")
        self.start_equity = equity
        self.realized = 0.0
        self.sizer = PositionSizer(settings)
        self.gate = RiskGate(settings, self.db, events=None)
        self.calibrator = Calibrator(settings, self.db, brain.model)
        self.persistence = PersistenceTracker(settings.signal_persistence_cycles)
        self.position: Optional[SimPosition] = None
        self.closed: list[dict[str, Any]] = []
        self._bars: list[Bar] = []
        self._index: dict[datetime, int] = {}
        self._bench: dict[datetime, float] = {}
        self._next_order_id = 1
        self._mark: Optional[float] = None  # último preço conhecido, para marcar a posição a mercado (N12)
        self.settler = Settler(settings, self.db, self._price_at, bars_between=self._bars_between)

    # ------------------------------------------------------------- dados
    def _price_at(self, symbol: str, when: datetime) -> Optional[float]:
        gap = timedelta(minutes=self.s.settlement_max_gap_minutes)
        if symbol == self.s.benchmark_symbol and self._bench:
            for t in sorted(self._bench):
                if t >= when:
                    return self._bench[t] if t - when <= gap else None
            return None
        for b in self._bars:
            if b.time >= when:
                return b.close if b.time - when <= gap else None
        return None

    def _bars_between(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        return [b for b in self._bars if start <= b.time <= end]

    @property
    def unrealized(self) -> float:
        if self.position is None or self._mark is None:
            return 0.0
        p = self.position
        return (self._mark - p.entry_price) * p.qty * p.direction

    @property
    def equity(self) -> float:
        """Equity MARCADA A MERCADO (realizado + não realizado), como o NetLiquidation da corretora (N12/F32)."""
        return self.start_equity + self.realized + self.unrealized

    # --------------------------------------------------------------- run
    def run(self, symbol: str, bars: list[Bar], bench: Optional[list[Bar]] = None) -> dict[str, Any]:
        from .trading_engine import build_decision_context

        self._bars = bars
        self._bench = {b.time: b.close for b in (bench or [])}
        interval = timedelta(minutes=self.s.llm_interval_minutes)
        last_llm: Optional[datetime] = None
        last_day = None

        for i in range(self.s.sma_slow + 5, len(bars)):
            bar = bars[i]
            now = bar.time
            if self.position:
                self._check_exit(bar)
            self._mark = bar.close
            if last_day != now.date():
                last_day = now.date()
                self.db.snapshot_pnl(net_liq=self.equity, cash=self.start_equity + self.realized,
                                     unrealized=self.unrealized, realized=self.realized, ts=now)
            self.settler.run(now)
            if last_llm and now - last_llm < interval:
                continue
            ctx = build_decision_context(self.s, symbol, bars[: i + 1], equity=self.equity)
            if ctx is None:
                continue
            last_llm = now
            self.gate.update_day_baseline(self.equity, now)
            global_gate = self.gate.check_global(equity=self.equity, now=now)
            if self.gate.halted:
                continue
            position_qty = self.position.qty * self.position.direction if self.position else 0.0
            portfolio = {"net_liq": self.equity, "positions": [], "unrealized": self.unrealized,
                         "day_pnl_pct": self.gate.day_pnl_pct(self.equity)}
            if self.position:
                portfolio["positions"] = [{"symbol": symbol, "qty": position_qty, "avg_cost": self.position.entry_price,
                                           "unrealized_pnl": self.unrealized}]
            recent = self.db.recent_decisions(5, symbol)
            lessons = []
            outcome = self.brain.decide(ctx.snapshot, portfolio, recent, dynamics=ctx.dynamics, lessons=lessons, cache=self.db)
            decision = outcome.decision
            signals = ConfidenceSignals(decision.confianca, outcome.agree_frac, outcome.margin, outcome.action_logprob)
            calibrated = self.calibrator.probability(signals) if decision.acao in ("BUY", "SELL") else None
            est = self.sizer.size(action="BUY", price=bar.close, equity=self.equity, bars=ctx.agg_bars)
            stop_pct = (est.stop_distance / bar.close * 100.0) if est else None
            decision_id = self.db.insert_decision(
                symbol=symbol, model=self.brain.model, action=decision.acao, confidence=decision.confianca,
                reason=decision.razao, snapshot=ctx.snapshot.to_dict(), position_qty=position_qty, net_liq=self.equity,
                prompt_version=0, parse_ok=decision.parse_ok, raw_response=decision.raw, ts=now,
                extra={"verbal_conf": decision.confianca, "agree_frac": outcome.agree_frac,
                       "action_logprob": outcome.action_logprob, "calibrated_prob": calibrated, "atr": ctx.atr,
                       "hour_ny": ctx.hour_ny, "regime": ctx.snapshot.trend_label(), "prompt_hash": outcome.prompt_hash,
                       "market_ts": ctx.snapshot.bar_time.isoformat(), "stop_pct": stop_pct,
                       "tp_pct": (stop_pct * self.s.reward_risk_ratio) if stop_pct else None,
                       "cost_pct": (2 * estimate_commission(max(est.qty, 1), bar.close, self.s) / (max(est.qty, 1) * bar.close) * 100.0) if est else None,
                       "review": int(outcome.review)})
            persistence = self.persistence.push(symbol, decision.acao)
            open_notional = self.position.qty * bar.close if self.position else 0.0
            plan = decide_execution(
                settings=self.s, gate=self.gate, sizer=self.sizer, calibrator=self.calibrator, outcome=outcome,
                calibrated=calibrated, snapshot=ctx.snapshot, agg_bars=ctx.agg_bars, now=now, position_qty=position_qty,
                open_positions=1 if self.position else 0, pending_entries=0, equity_usd=self.equity,
                available_funds_usd=max(self.equity - open_notional, 0.0), reserved_notional=0.0,
                persistence=persistence, pending_close=False, pending_entry=False, global_gate=global_gate,
                # O mesmo estado de risco do motor sem gates validados: risco base × multiplicador de aprendizagem (N12).
                risk_multiplier=self.s.learning_risk_multiplier, gates_passed=False)
            if isinstance(plan, Skip):
                self.db.mark_decision(decision_id, executed=False, skip_reason=plan.reason)
                continue
            if plan.kind == "CLOSE" and self.position:
                self._close(bar, bar.close, "SIGNAL", adverse_slippage=True)
                self.db.mark_decision(decision_id, executed=True)
                self.persistence.reset(symbol)
                continue
            if plan.kind == "ENTRY" and self.position is None and self._enter(symbol, plan, bar, decision_id):
                self.db.mark_decision(decision_id, executed=True)
                self.persistence.reset(symbol)
            else:
                self.db.mark_decision(decision_id, executed=False, skip_reason="entrada não aberta na simulação")
        if self.position:
            self._close(bars[-1], bars[-1].close, "EOD", adverse_slippage=False)
        self.settler.run(bars[-1].time + timedelta(days=2))
        return self.summary(bars)

    # ------------------------------------------------------------ fills
    def _slip(self, price: float, direction: int, adverse: bool) -> float:
        tick = self.s.slippage_ticks * self.s.tick_size
        return price + (direction * tick if adverse else 0.0)

    def _enter(self, symbol: str, plan: ExecutionPlan, bar: Bar, decision_id: int) -> bool:
        sizing = plan.sizing
        assert sizing is not None
        direction = 1 if plan.action == "BUY" else -1
        fill = self._slip(bar.close, direction, adverse=True)
        if plan.limit_price is not None and ((direction > 0 and fill > plan.limit_price) or (direction < 0 and fill < plan.limit_price)):
            return False  # limit não executável
        commission = estimate_commission(sizing.qty, fill, self.s)
        oid = self._next_order_id
        self._next_order_id += 3
        gid = self.db.insert_order_group(symbol=symbol, decision_id=decision_id, role="ENTRY", direction=direction,
                                         qty=sizing.qty, parent_order_id=oid, tp_order_id=oid + 1, sl_order_id=oid + 2,
                                         ref_price=bar.close, tp_price=sizing.tp_price, sl_price=sizing.stop_price, ts=bar.time)
        tid = self.db.open_trade(symbol=symbol, decision_id=decision_id, group_id=gid, direction=direction,
                                 qty=sizing.qty, stop_price=sizing.stop_price, tp_price=sizing.tp_price,
                                 risk_amount=sizing.risk_amount)
        self.db.record_entry_fill(tid, sizing.qty, fill, bar.time, commission)
        self.db.insert_fill(exec_id=f"sim-{oid}", order_id=oid, symbol=symbol, side="BOT" if direction > 0 else "SLD",
                            shares=sizing.qty, price=fill, ts=bar.time, commission=commission, commission_estimated=True)
        self.position = SimPosition(tid, direction, sizing.qty, fill, sizing.stop_price, sizing.tp_price, bar.time)
        return True

    def _check_exit(self, bar: Bar) -> None:
        """Ordem de resolução (N12/F34): 1) eventos observáveis na ABERTURA (gap através do stop ou do TP
        executa à abertura); 2) só depois a ambiguidade intrabar, em que o pior caso (stop) prevalece."""
        p = self.position
        assert p is not None
        if p.direction == 1:
            gap_stop, gap_tp = bar.open <= p.stop, bar.open >= p.tp
            hit_stop, hit_tp = bar.low <= p.stop, bar.high >= p.tp
        else:
            gap_stop, gap_tp = bar.open >= p.stop, bar.open <= p.tp
            hit_stop, hit_tp = bar.high >= p.stop, bar.low <= p.tp
        if gap_stop:
            return self._close(bar, bar.open, "SL", adverse_slippage=True)
        if gap_tp:
            return self._close(bar, bar.open, "TP", adverse_slippage=False)
        if hit_stop:  # ambíguo na mesma vela: assume-se o pior caso
            return self._close(bar, p.stop, "SL", adverse_slippage=True)
        if hit_tp:
            return self._close(bar, p.tp, "TP", adverse_slippage=False)

    def _close(self, bar: Bar, price: float, reason: str, *, adverse_slippage: bool) -> None:
        p = self.position
        assert p is not None
        fill = self._slip(price, -p.direction, adverse=adverse_slippage)
        commission = estimate_commission(p.qty, fill, self.s)
        row = self.db.record_exit_fill(p.trade_id, p.qty, fill, bar.time, reason, commission)
        oid = self._next_order_id
        self._next_order_id += 1
        self.db.insert_fill(exec_id=f"sim-x-{oid}", order_id=oid, symbol=row["symbol"], side="SLD" if p.direction > 0 else "BOT",
                            shares=p.qty, price=fill, ts=bar.time, commission=commission, commission_estimated=True)
        # pnl do ledger já inclui ambas as comissões; realized é a soma dos pnl dos trades fechados
        self.realized = sum(float(t["pnl"]) for t in self.db.closed_trades_between(datetime(2000, 1, 1, tzinfo=timezone.utc)))
        self.closed.append(dict(row))
        self.position = None
        self.db.snapshot_pnl(net_liq=self.equity, cash=self.equity, unrealized=0.0, realized=self.realized, ts=bar.time)

    def summary(self, bars: list[Bar]) -> dict[str, Any]:
        span_days = max(1, (bars[-1].time - bars[0].time).days + 2)
        report = Analytics(self.s, self.db).build_report(now=bars[-1].time + timedelta(days=1), since_days=span_days)
        report["simulation"] = {"start_equity": self.start_equity, "end_equity": round(self.equity, 2),
                                "trades": len(self.closed), "return_pct": round((self.equity / self.start_equity - 1) * 100, 3),
                                "ledger_pnl": round(sum(float(t["pnl"]) for t in self.closed), 2),
                                "commissions": round(sum(float(t.get("commission") or 0) for t in self.closed), 2),
                                "from": bars[0].time.isoformat(), "to": bars[-1].time.isoformat()}
        return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Replay offline do bot sobre velas de 1 min (mesma política do live)")
    parser.add_argument("--csv", help="CSV time,open,high,low,close,volume")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--bench-csv", help="CSV do benchmark (SPY) opcional")
    parser.add_argument("--alpaca-days", type=int, help="Em vez de CSV: últimos N dias via Alpaca")
    parser.add_argument("--model", help="Modelo Ollama a usar")
    parser.add_argument("--equity", type=float, default=100_000.0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    settings = Settings.load()
    db = Database(":memory:")
    from .ollama_brain import OllamaBrain
    brain = OllamaBrain(settings, db)
    if args.model:
        brain.model = args.model
    if args.csv:
        bars = load_csv(args.csv)
    elif args.alpaca_days:
        end = datetime.now(timezone.utc) - timedelta(minutes=16)
        bars = load_alpaca(args.symbol, end - timedelta(days=args.alpaca_days), end,
                           settings.alpaca_api_key, settings.alpaca_api_secret)
    else:
        parser.error("indique --csv ou --alpaca-days")
        return 2
    bench = load_csv(args.bench_csv) if args.bench_csv else None
    report = Replayer(settings, brain, db, equity=args.equity).run(args.symbol, bars, bench)
    print(Analytics.render_markdown(report))
    print("\nSimulação:", report["simulation"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
