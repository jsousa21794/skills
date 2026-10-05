"""Replay offline das decisões sobre velas de 1 minuto, com cache e custos.

Não é um framework completo: é o "caminho mais leve" do roteiro. Reutiliza
exactamente o mesmo pipeline do bot (indicadores → dinâmica → LLM com cache por
hash do prompt → camada de risco → bracket) sobre um ficheiro CSV ou sobre
barras da Alpaca (Basic, gratuito, IEX), simula fills nos filhos TP/SL pelos
máximos/mínimos das velas seguintes, aplica comissão e slippage e devolve o
mesmo relatório estatístico do modo live.

Uso: ``python -m trader.backtest --csv dados.csv --symbol AAPL [--model llama3]``
CSV esperado: ``time,open,high,low,close,volume`` com ``time`` ISO-8601 (UTC).

Avisos (do roteiro): o LLM pode conhecer o período testado através dos pesos
(contaminação temporal); use ``anonymize_prompt=True`` e trate como evidência
só o período posterior ao *cutoff* de treino do modelo e o paper forward.
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
from .config import Settings
from .database import Database
from .indicators import Bar
from .risk import PositionSizer, round_trip_cost

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
    """Alpaca Market Data v2 (plano Basic: IEX, sem os últimos 15 min)."""
    import requests

    bars: list[Bar] = []
    url = "https://data.alpaca.markets/v2/stocks/bars"
    params: dict[str, Any] = {"symbols": symbol, "timeframe": "1Min", "start": start.isoformat(),
                              "end": end.isoformat(), "limit": 10000, "feed": "iex", "adjustment": "raw"}
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
class SimTrade:
    symbol: str
    direction: int
    qty: int
    entry_ts: datetime
    entry_price: float
    stop: float
    tp: float
    decision_id: int
    exit_ts: Optional[datetime] = None
    exit_price: Optional[float] = None
    reason: str = ""
    pnl: float = 0.0


class Replayer:
    def __init__(self, settings: Settings, brain: Any, db: Optional[Database] = None, equity: float = 100_000.0) -> None:
        self.s = settings
        self.brain = brain
        self.db = db or Database(":memory:")
        self.equity = equity
        self.start_equity = equity
        self.sizer = PositionSizer(settings)
        self.trades: list[SimTrade] = []
        self.open: Optional[SimTrade] = None

    def run(self, symbol: str, bars: list[Bar], bench: Optional[list[Bar]] = None) -> dict[str, Any]:
        from .trading_engine import build_decision_context  # evita import circular no arranque

        interval = timedelta(minutes=self.s.llm_interval_minutes)
        last_llm: Optional[datetime] = None
        persistence: list[str] = []
        price_index = {b.time: b.close for b in bars}
        bench_index = {b.time: b.close for b in (bench or [])}

        for i in range(self.s.sma_slow + 5, len(bars)):
            bar = bars[i]
            history = bars[: i + 1]
            # 1) gere a posição aberta com os extremos da vela corrente
            if self.open:
                self._check_exit(bar)
            # 2) consulta o LLM na cadência definida
            if last_llm and bar.time - last_llm < interval:
                continue
            if not self._in_window(bar.time):
                continue
            ctx = build_decision_context(self.s, symbol, history, equity=self.equity)
            if ctx is None:
                continue
            last_llm = bar.time
            portfolio = {"net_liq": self.equity, "positions": [], "unrealized": 0.0, "day_pnl_pct": 0.0}
            if self.open:
                portfolio["positions"] = [{"symbol": symbol, "qty": self.open.qty * self.open.direction,
                                           "avg_cost": self.open.entry_price,
                                           "unrealized_pnl": (bar.close - self.open.entry_price) * self.open.qty * self.open.direction}]
            outcome = self.brain.decide(ctx.snapshot, portfolio, [], dynamics=ctx.dynamics, lessons=[], cache=self.db)
            decision_id = self.db.insert_decision(
                symbol=symbol, model=self.brain.model, action=outcome.decision.acao, confidence=outcome.decision.confianca,
                reason=outcome.decision.razao, snapshot=ctx.snapshot.to_dict(), position_qty=0.0, net_liq=self.equity,
                prompt_version=0, parse_ok=outcome.decision.parse_ok, raw_response=outcome.decision.raw,
                extra={"verbal_conf": outcome.decision.confianca, "agree_frac": outcome.agree_frac,
                       "action_logprob": outcome.action_logprob, "atr": ctx.atr, "hour_ny": ctx.hour_ny,
                       "regime": ctx.snapshot.trend_label(), "prompt_hash": outcome.prompt_hash},
            )
            # Settlement imediato a partir das barras futuras.
            target = bar.time + timedelta(minutes=self.s.settlement_horizon_minutes)
            future = next((b.close for b in bars[i:] if b.time >= target), None)
            if future is not None:
                ret = (future - bar.close) / bar.close * 100
                b0, b1 = bench_index.get(bar.time), bench_index.get(target)
                from .settlement import label
                thr = max(0.1, (ctx.atr / bar.close * 100) * self.s.atr_stop_multiple / 2) if ctx.atr else 0.2
                self.db.settle_decision(decision_id, settled_price=future, settled_return=round(ret, 4),
                                        bench_return=((b1 - b0) / b0 * 100) if b0 and b1 else None, alpha=None,
                                        correct=label(outcome.decision.acao, ret, thr, max(thr * 2, 0.5)),
                                        horizon_min=self.s.settlement_horizon_minutes)
            action = outcome.decision.acao
            persistence.append(action)
            persistence = persistence[-self.s.signal_persistence_cycles:]
            if action in ("BUY", "SELL") and len(persistence) == self.s.signal_persistence_cycles \
                    and all(a == action for a in persistence) and self.open is None \
                    and outcome.agree_frac >= self.s.llm_min_agreement \
                    and outcome.decision.confianca >= self.s.min_confidence:
                self._enter(symbol, action, bar, history, decision_id)
                self.db.mark_decision(decision_id, executed=True)
        if self.open:
            self._close(self.open, bars[-1].time, bars[-1].close, "EOD")
        return self.summary()

    def _in_window(self, ts: datetime) -> bool:
        from .risk import NY
        local = ts.astimezone(NY)
        m = local.hour * 60 + local.minute
        return local.weekday() < 5 and (9 * 60 + 30 + self.s.skip_open_minutes) <= m < (16 * 60 - self.s.skip_close_minutes)

    def _enter(self, symbol: str, action: str, bar: Bar, history: list[Bar], decision_id: int) -> None:
        from .indicators import aggregate_bars
        sizing = self.sizer.size(action=action, price=bar.close, equity=self.equity,
                                 bars=aggregate_bars(history, self.s.decision_bar_minutes))
        if not sizing or sizing.qty < 1:
            return
        cost = round_trip_cost(sizing.qty, bar.close, self.s)
        direction = 1 if action == "BUY" else -1
        slip = self.s.slippage_ticks * self.s.tick_size * direction
        self.open = SimTrade(symbol, direction, sizing.qty, bar.time, bar.close + slip, sizing.stop_price,
                             sizing.tp_price, decision_id)
        self.open.pnl -= cost
        gid = self.db.insert_order_group(symbol=symbol, decision_id=decision_id, role="ENTRY", direction=direction,
                                         qty=sizing.qty, parent_order_id=None, tp_order_id=None, sl_order_id=None,
                                         ref_price=bar.close, tp_price=sizing.tp_price, sl_price=sizing.stop_price)
        tid = self.db.open_trade(symbol=symbol, decision_id=decision_id, group_id=gid, direction=direction,
                                 qty=sizing.qty, stop_price=sizing.stop_price, tp_price=sizing.tp_price,
                                 risk_amount=sizing.risk_amount)
        self.db.record_entry_fill(tid, sizing.qty, self.open.entry_price, bar.time)
        self.open.decision_id = tid  # reutiliza o campo para o id do trade na DB

    def _check_exit(self, bar: Bar) -> None:
        t = self.open
        assert t is not None
        if t.direction == 1:
            if bar.low <= t.stop:
                return self._close(t, bar.time, t.stop, "SL")
            if bar.high >= t.tp:
                return self._close(t, bar.time, t.tp, "TP")
        else:
            if bar.high >= t.stop:
                return self._close(t, bar.time, t.stop, "SL")
            if bar.low <= t.tp:
                return self._close(t, bar.time, t.tp, "TP")

    def _close(self, t: SimTrade, ts: datetime, price: float, reason: str) -> None:
        t.exit_ts, t.exit_price, t.reason = ts, price, reason
        t.pnl += (price - t.entry_price) * t.qty * t.direction
        self.equity += t.pnl
        self.trades.append(t)
        self.db.record_exit_fill(t.decision_id, t.qty, price, ts, reason)
        self.db.snapshot_pnl(net_liq=self.equity, cash=self.equity, unrealized=0.0, realized=0.0)
        self.open = None

    def summary(self) -> dict[str, Any]:
        report = Analytics(self.s, self.db).build_report()
        report["simulation"] = {"start_equity": self.start_equity, "end_equity": round(self.equity, 2),
                                "trades": len(self.trades), "return_pct": round((self.equity / self.start_equity - 1) * 100, 3)}
        return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Replay offline do bot sobre velas de 1 min")
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
