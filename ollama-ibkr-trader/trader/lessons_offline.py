"""Lições calculadas offline a partir de histórico de velas de 1 minuto.

A pesquisa (research_notes/) não encontrou nenhum corpus de "lições" pronto a
importar no GitHub ou no Hugging Face. O substituto honesto é calculá-las a
partir de dados: este módulo lê OHLCV de 1 minuto (CSV, ou Parquet dos
datasets ``Rrishab/OHLCV-1m`` / ``ggaddam/OHLCV-1m`` se ``pandas``/``pyarrow``
estiverem instalados), agrega a 5 minutos, calcula os mesmos indicadores do
bot, define sinais em *transição* (os únicos com conteúdo preditivo) e mede o
hit-rate de cada sinal por regime de RSI, hora do dia e tendência contra o
retorno a 30 minutos, descontando o custo ida+volta.

Saída: um JSON no formato de ``data/seed_lessons.json`` (chaves ``seed:``),
com suporte e z-score, pronto a ser apontado por ``seed_lessons_file``.

Uso::

    python -m trader.lessons_offline --csv AAPL_1m.csv --symbol AAPL --out lessons_AAPL.json
    python -m trader.lessons_offline --parquet ohlcv_2025-09.parquet --symbol TSLA --out lessons.json

Aviso: lições de dados históricos descrevem o passado desse ativo; as lições
medidas nas decisões reais do bot sobrepõem-se a estas assim que existirem.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from .backtest import load_csv
from .config import Settings
from .indicators import Bar, aggregate_bars, true_ranges
from .lessons import MIN_SUPPORT, MIN_Z, hour_bucket, rsi_regime
from .risk import NY, round_trip_cost

log = logging.getLogger("trader.lessons_offline")


# --------------------------------------------------------------- séries O(n)
def rsi_series(closes: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_g, avg_l = gains / period, losses / period
    out[period] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0.0)) / period
        out[i] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return out


def sma_series(values: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema_series(values: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    cur = sum(values[:period]) / period
    out[period - 1] = cur
    for i in range(period, len(values)):
        cur = values[i] * k + cur * (1 - k)
        out[i] = cur
    return out


def atr_series_full(bars: list[Bar], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(bars)
    trs = true_ranges(bars)
    if len(bars) < period + 1:
        return out
    cur = sum(trs[1 : period + 1]) / period
    out[period] = cur
    for i in range(period + 1, len(bars)):
        cur = (cur * (period - 1) + trs[i]) / period
        out[i] = cur
    return out


# ------------------------------------------------------------------ análise
def load_parquet(path: str, symbol: str) -> list[Bar]:
    import pandas as pd  # type: ignore

    df = pd.read_parquet(path)
    cols = {c.lower(): c for c in df.columns}
    if "ticker" in cols or "symbol" in cols:
        col = cols.get("ticker") or cols["symbol"]
        df = df[df[col].astype(str).str.upper() == symbol.upper()]
    tcol = cols.get("timestamp") or cols.get("time") or cols.get("datetime") or cols.get("date")
    df = df.sort_values(tcol)
    bars = []
    for row in df.itertuples(index=False):
        r = row._asdict()
        ts = r[tcol]
        ts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else datetime.fromisoformat(str(ts))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        bars.append(Bar(ts, float(r[cols["open"]]), float(r[cols["high"]]), float(r[cols["low"]]),
                        float(r[cols["close"]]), float(r.get(cols.get("volume", ""), 0) or 0)))
    return bars


def compute_lessons(symbol: str, bars_1m: list[Bar], settings: Settings, *, horizon_min: Optional[int] = None,
                    equity: float = 100_000.0) -> dict[str, Any]:
    s = settings
    horizon_min = horizon_min or s.settlement_horizon_minutes
    agg = aggregate_bars(bars_1m, s.decision_bar_minutes)
    n = len(agg)
    closes = [b.close for b in agg]
    rsi = rsi_series(closes, s.rsi_period)
    fast = sma_series(closes, s.sma_fast)
    slow = sma_series(closes, s.sma_slow)
    ema = ema_series(closes, s.ema_period)
    atr = atr_series_full(agg, s.atr_period)
    steps = max(1, horizon_min // s.decision_bar_minutes)

    samples: list[dict[str, Any]] = []
    for i in range(max(s.sma_slow, s.rsi_period + 3), n - steps):
        if any(v is None for v in (rsi[i], rsi[i - 1], rsi[i - 2], fast[i], slow[i], ema[i], ema[i - 1], fast[i - 1], atr[i])):
            continue
        price = closes[i]
        signals = []
        if rsi[i - 1] < 30 <= rsi[i] or rsi[i - 2] < 30 <= rsi[i]:
            signals.append(("BUY", "rsi_sai_sobrevenda"))
        if rsi[i - 1] > 70 >= rsi[i] or rsi[i - 2] > 70 >= rsi[i]:
            signals.append(("SELL", "rsi_sai_sobrecompra"))
        if ema[i - 1] <= fast[i - 1] and ema[i] > fast[i]:
            signals.append(("BUY", "ema_cruza_sma_cima"))
        if ema[i - 1] >= fast[i - 1] and ema[i] < fast[i]:
            signals.append(("SELL", "ema_cruza_sma_baixo"))
        if not signals:
            continue
        # custo ida+volta para a quantidade que o sizer daria (risco% / (k×ATR))
        stop = s.atr_stop_multiple * atr[i]
        qty = max(1, math.floor(equity * s.risk_per_trade_pct / stop))
        cost_pct = round_trip_cost(qty, price, s) / (qty * price) * 100
        future = closes[i + steps]
        ret = (future - price) / price * 100
        thr = max(0.1, stop / price * 100 / 2, cost_pct)
        trend = "alta" if price > fast[i] > slow[i] else "baixa" if price < fast[i] < slow[i] else "lateral"
        hour = agg[i].time.astimezone(NY).hour
        for action, name in signals:
            correct = 1 if (ret >= thr if action == "BUY" else ret <= -thr) else 0 if (
                ret <= -thr if action == "BUY" else ret >= thr) else None
            if correct is None:
                continue
            samples.append({"action": action, "signal": name, "rsi": rsi[i], "hour": hour, "trend": trend,
                            "correct": correct, "ret": ret})

    if len(samples) < MIN_SUPPORT:
        return {"symbol": symbol, "samples": len(samples), "lessons": [], "groups": {}}
    base = sum(x["correct"] for x in samples) / len(samples)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for x in samples:
        a = x["action"]
        groups[f"sinal:{x['signal']}:{a}"].append(x)
        groups[f"rsi:{rsi_regime(x['rsi'])}:{a}"].append(x)
        groups[f"hora:{hour_bucket(x['hour'])}:{a}"].append(x)
        groups[f"regime:{x['trend']}:{a}"].append(x)
        groups[f"sinal_regime:{x['signal']}:{x['trend']}:{a}"].append(x)

    stats: dict[str, dict[str, Any]] = {}
    lessons: list[dict[str, Any]] = []
    for key, items in groups.items():
        k = len(items)
        hits = sum(x["correct"] for x in items)
        rate = hits / k
        z = (rate - base) / math.sqrt(base * (1 - base) / k) if 0 < base < 1 else 0.0
        avg_ret = sum(x["ret"] for x in items) / k
        stats[key] = {"n": k, "hits": hits, "rate": round(rate, 3), "z": round(z, 2), "avg_ret_pct": round(avg_ret, 3)}
        if k < MIN_SUPPORT or abs(z) < MIN_Z:
            continue
        parts = key.split(":")
        action = parts[-1]
        where = {"sinal": f"com o sinal '{parts[1]}'", "rsi": f"com RSI em {parts[1]}", "hora": f"no período '{parts[1]}' (NY)",
                 "regime": f"em tendência {parts[1]}", "sinal_regime": f"com o sinal '{parts[1]}' em tendência {parts[2]}"}[parts[0]]
        if z < 0:
            text = (f"Em {symbol} (histórico, {s.decision_bar_minutes} min), {action} {where} acertou {hits}/{k} ({rate:.0%}) "
                    f"vs base {base:.0%} a {horizon_min} min líquidos de custos. Evitar {action} nesta condição.")
        else:
            text = (f"Em {symbol} (histórico, {s.decision_bar_minutes} min), {action} {where} acertou {hits}/{k} ({rate:.0%}) "
                    f"vs base {base:.0%} a {horizon_min} min líquidos de custos. Condição favorável a {action}.")
        lessons.append({"key": f"hist_{symbol}_{key.replace(':', '_')}", "symbol": symbol, "text": text, "support": k,
                        "importance": round(min(0.6, 0.3 + abs(z) * 0.05), 3),
                        "source": f"cálculo offline em {len(bars_1m)} velas de 1 min, horizonte {horizon_min} min"})
    lessons.sort(key=lambda l: l["importance"], reverse=True)
    return {"symbol": symbol, "samples": len(samples), "base_rate": round(base, 3), "lessons": lessons[: s.retro_max_lessons],
            "groups": stats}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Calcula lições iniciais a partir de velas de 1 min")
    parser.add_argument("--csv", help="CSV time,open,high,low,close,volume")
    parser.add_argument("--parquet", help="Parquet OHLCV-1m (Hugging Face Rrishab/OHLCV-1m, ggaddam/OHLCV-1m)")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--out", required=True, help="JSON de saída (formato de data/seed_lessons.json)")
    parser.add_argument("--horizon", type=int, help="horizonte em minutos (defeito: settlement_horizon_minutes)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = Settings.load()
    if args.csv:
        bars = load_csv(args.csv)
    elif args.parquet:
        bars = load_parquet(args.parquet, args.symbol)
    else:
        parser.error("indique --csv ou --parquet")
        return 2
    result = compute_lessons(args.symbol, bars, settings, horizon_min=args.horizon)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"_sobre": f"Lições calculadas offline para {args.symbol}", "lessons": result["lessons"]}, fh,
                  ensure_ascii=False, indent=2)
    print(f"{result['samples']} sinais avaliados, taxa base {result.get('base_rate')}, {len(result['lessons'])} lições -> {args.out}")
    for key, st in sorted(result["groups"].items(), key=lambda kv: -abs(kv[1]["z"]))[:12]:
        print(f"  {key:45s} n={st['n']:4d} acerto={st['rate']:.0%} z={st['z']:+.2f} ret médio={st['avg_ret_pct']:+.3f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
