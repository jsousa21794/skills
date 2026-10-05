"""Protocolo estatístico: decide quando há evidência para escalar risco.

Tudo em Python puro sobre o SQLite (sem framework de backtesting):
- calibração das decisões: Brier vs climatologia, ECE, diagrama de fiabilidade;
- hit-rate direcional vs teste de permutação (política aleatória com a mesma
  proporção BUY/SELL);
- métricas de trades: expectancy, profit factor, Kelly, drawdown, Monte Carlo
  de ruína;
- métricas de equity: Sharpe/Sortino, PSR, DSR com N honesto de experiências,
  MinBTL, WFE aproximado;
- *gates* que devolvem um multiplicador de risco (0.5 em aprendizagem, 1.0
  quando tudo passa).

``quantstats`` (opcional) gera um relatório HTML adicional se estiver instalado.
"""

from __future__ import annotations

import logging
import math
import random
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from .config import Settings
from .database import Database

log = logging.getLogger("trader.analytics")
EULER_GAMMA = 0.5772156649015329


# ---------------------------------------------------------------- utilidades
def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """Inversa da normal padrão (aproximação de Acklam, erro < 1e-9)."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def _skew_kurt(xs: Sequence[float]) -> tuple[float, float]:
    n = len(xs)
    if n < 3:
        return 0.0, 3.0
    m = statistics.fmean(xs)
    sd = statistics.pstdev(xs)
    if sd == 0:
        return 0.0, 3.0
    skew = sum(((x - m) / sd) ** 3 for x in xs) / n
    kurt = sum(((x - m) / sd) ** 4 for x in xs) / n
    return skew, kurt


# ------------------------------------------------------------- calibração
def calibration_metrics(probs: Sequence[float], outcomes: Sequence[int], bins: int = 10) -> dict[str, Any]:
    n = len(probs)
    if n == 0:
        return {"n": 0}
    base = sum(outcomes) / n
    brier = sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / n
    brier_clim = sum((base - o) ** 2 for o in outcomes) / n
    table = []
    ece = 0.0
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        idx = [k for k, p in enumerate(probs) if lo <= p < hi or (i == bins - 1 and p == 1.0)]
        if not idx:
            continue
        conf = sum(probs[k] for k in idx) / len(idx)
        acc = sum(outcomes[k] for k in idx) / len(idx)
        ece += len(idx) / n * abs(conf - acc)
        table.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(idx), "confidence": round(conf, 3), "accuracy": round(acc, 3)})
    return {"n": n, "base_rate": round(base, 4), "brier": round(brier, 4), "brier_climatology": round(brier_clim, 4),
            "brier_skill": round(1 - brier / brier_clim, 4) if brier_clim else None, "ece": round(ece, 4),
            "reliability": table}


def permutation_hit_rate(rows: Sequence[dict[str, Any]], iterations: int = 2000, seed: int = 1) -> dict[str, Any]:
    """Hit-rate observado vs política aleatória com a mesma proporção de BUY/SELL."""
    rows = [r for r in rows if r.get("settled_return") is not None and r["action"] in ("BUY", "SELL")]
    n = len(rows)
    if n == 0:
        return {"n": 0}
    rets = [float(r["settled_return"]) for r in rows]
    actions = [r["action"] for r in rows]
    observed = sum(1 for a, r in zip(actions, rets) if (r > 0) == (a == "BUY") and r != 0) / n
    rng = random.Random(seed)
    sims = []
    for _ in range(iterations):
        shuffled = actions[:]
        rng.shuffle(shuffled)
        sims.append(sum(1 for a, r in zip(shuffled, rets) if (r > 0) == (a == "BUY") and r != 0) / n)
    sims.sort()
    p95 = sims[int(0.95 * (len(sims) - 1))]
    p_value = sum(1 for s in sims if s >= observed) / len(sims)
    return {"n": n, "hit_rate": round(observed, 4), "random_p95": round(p95, 4), "p_value": round(p_value, 4),
            "beats_random": observed > p95}


# ------------------------------------------------------------------ trades
def trade_metrics(pnls: Sequence[float]) -> dict[str, Any]:
    n = len(pnls)
    if n == 0:
        return {"n": 0}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    win_rate = len(wins) / n
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    expectancy = sum(pnls) / n
    ratio = (avg_win / avg_loss) if avg_loss else None
    kelly = (win_rate - (1 - win_rate) / ratio) if ratio else None
    cum, peak, max_dd = 0.0, 0.0, 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return {"n": n, "win_rate": round(win_rate, 4), "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
            "expectancy": round(expectancy, 2), "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
            "total_pnl": round(sum(pnls), 2), "max_drawdown_abs": round(max_dd, 2),
            "kelly": round(kelly, 4) if kelly is not None else None, "payoff_ratio": round(ratio, 3) if ratio else None}


def monte_carlo_ruin(pnls: Sequence[float], equity: float, ruin_drawdown_pct: float = 0.20,
                     iterations: int = 2000, seed: int = 2) -> dict[str, Any]:
    if len(pnls) < 10 or equity <= 0:
        return {"n": len(pnls)}
    rng = random.Random(seed)
    horizon = max(len(pnls), 100)
    ruined = 0
    worst_dd = []
    for _ in range(iterations):
        cum, peak, dd = 0.0, 0.0, 0.0
        hit = False
        for _ in range(horizon):
            cum += rng.choice(pnls)
            peak = max(peak, cum)
            dd = max(dd, (peak - cum) / equity)
            if dd >= ruin_drawdown_pct:
                hit = True
                break
        ruined += hit
        worst_dd.append(dd)
    worst_dd.sort()
    return {"n": len(pnls), "iterations": iterations, "horizon_trades": horizon,
            "ruin_probability": round(ruined / iterations, 4), "ruin_threshold_pct": ruin_drawdown_pct * 100,
            "median_max_dd_pct": round(worst_dd[len(worst_dd) // 2] * 100, 2),
            "p95_max_dd_pct": round(worst_dd[int(0.95 * (iterations - 1))] * 100, 2)}


# ------------------------------------------------------------------ equity
def equity_metrics(daily_equity: Sequence[float], n_trials: int = 1, periods_per_year: int = 252) -> dict[str, Any]:
    if len(daily_equity) < 3:
        return {"n_days": len(daily_equity)}
    rets = [(daily_equity[i] - daily_equity[i - 1]) / daily_equity[i - 1] for i in range(1, len(daily_equity))
            if daily_equity[i - 1]]
    t = len(rets)
    mean = statistics.fmean(rets)
    sd = statistics.pstdev(rets)
    if sd == 0:
        return {"n_days": t, "sharpe": 0.0}
    sr = mean / sd
    downside = [r for r in rets if r < 0]
    dsd = math.sqrt(sum(r * r for r in downside) / t) if downside else 0.0
    sortino = mean / dsd if dsd else None
    skew, kurt = _skew_kurt(rets)
    denom = math.sqrt(max(1e-9, 1 - skew * sr + (kurt - 1) / 4 * sr * sr))
    psr = norm_cdf((sr - 0.0) * math.sqrt(t - 1) / denom)
    # DSR: SR* esperado do máximo de N tentativas sem skill (aprox. V = variância do estimador de SR).
    var_sr = (1 - skew * sr + (kurt - 1) / 4 * sr * sr) / max(t - 1, 1)
    if n_trials > 1:
        sr_star = math.sqrt(var_sr) * ((1 - EULER_GAMMA) * norm_ppf(1 - 1 / n_trials)
                                       + EULER_GAMMA * norm_ppf(1 - 1 / (n_trials * math.e)))
    else:
        sr_star = 0.0
    dsr = norm_cdf((sr - sr_star) * math.sqrt(t - 1) / denom)
    sr_annual = sr * math.sqrt(periods_per_year)
    min_btl_years = (2 * math.log(n_trials) / (sr_annual ** 2)) if n_trials > 1 and sr_annual > 0 else None
    cum, peak, max_dd = 1.0, 1.0, 0.0
    for r in rets:
        cum *= 1 + r
        peak = max(peak, cum)
        max_dd = max(max_dd, (peak - cum) / peak)
    return {"n_days": t, "sharpe_daily": round(sr, 4), "sharpe_annual": round(sr_annual, 3),
            "sortino_daily": round(sortino, 4) if sortino is not None else None, "skew": round(skew, 3),
            "kurtosis": round(kurt, 3), "psr": round(psr, 4), "dsr": round(dsr, 4), "n_trials": n_trials,
            "sr_star": round(sr_star, 4), "min_btl_years": round(min_btl_years, 2) if min_btl_years else None,
            "max_drawdown_pct": round(max_dd * 100, 2), "total_return_pct": round((cum - 1) * 100, 2)}


def walk_forward_efficiency(pnls_in_order: Sequence[float]) -> Optional[float]:
    """Aproximação: expectancy da 2.ª metade / expectancy da 1.ª metade (Pardo: >0,5 aceitável)."""
    n = len(pnls_in_order)
    if n < 20:
        return None
    half = n // 2
    is_exp = sum(pnls_in_order[:half]) / half
    oos_exp = sum(pnls_in_order[half:]) / (n - half)
    if is_exp <= 0:
        return None
    return round(oos_exp / is_exp, 3)


# ------------------------------------------------------------------ gates
class Analytics:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.s = settings
        self.db = db

    def build_report(self, now: Optional[datetime] = None, since_days: int = 90) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        since = now - timedelta(days=since_days)
        settled = [r for r in self.db.settled_decisions(since=since, directional_only=True)
                   if r.get("correct") is not None]
        probs = [float(r["calibrated_prob"] if r.get("calibrated_prob") is not None else r["confidence"]) for r in settled]
        outcomes = [int(r["correct"]) for r in settled]
        closed = self.db.closed_trades_between(since)
        pnls = [float(t["pnl"]) for t in closed]  # líquidos de comissões
        commissions = sum(float(t.get("commission") or 0) for t in closed)
        gross = sum(float(t.get("gross_pnl") or 0) for t in closed)
        equity = [v for _, v in self.db.daily_equity(since)]
        current_equity = equity[-1] if equity else 0.0
        n_trials = self.db.experiment_count()

        by_model: dict[str, dict[str, Any]] = {}
        for model in sorted({r.get("model") or "?" for r in settled}):
            rows = [r for r in settled if (r.get("model") or "?") == model]
            by_model[model] = {"n": len(rows), "hit_rate": round(sum(int(r["correct"]) for r in rows) / len(rows), 4)}

        report = {
            "ts": now.isoformat(), "window_days": since_days,
            "calibration": calibration_metrics(probs, outcomes),
            "permutation": permutation_hit_rate(settled),
            "trades": {**trade_metrics(pnls), "commissions": round(commissions, 2), "gross_pnl": round(gross, 2),
                       "cost_share_of_gross": round(commissions / gross, 3) if gross > 0 else None},
            "monte_carlo": monte_carlo_ruin(pnls, current_equity, self.s.max_drawdown_pct * 3),
            "equity": equity_metrics(equity, n_trials),
            "wfe": walk_forward_efficiency(pnls),
            "by_model": by_model,
            "n_trials": n_trials,
        }
        report["gates"] = self.gates(report)
        self.db.save_report("weekly", report)
        return report

    def gates(self, report: dict[str, Any]) -> dict[str, Any]:
        cal, perm, trades = report["calibration"], report["permutation"], report["trades"]
        eq, mc = report["equity"], report["monte_carlo"]
        checks = {
            "trades_minimos": trades.get("n", 0) >= self.s.gate_min_closed_trades,
            "brier_abaixo_climatologia": bool(cal.get("n")) and cal.get("brier", 1) < cal.get("brier_climatology", 0),
            "ece_ok": bool(cal.get("n")) and cal.get("ece", 1) <= self.s.gate_max_ece,
            "hit_rate_bate_aleatorio": bool(perm.get("beats_random")),
            "expectancy_positiva": trades.get("expectancy", 0) > 0 if trades.get("n") else False,
            "psr_ok": (eq.get("psr") or 0) >= self.s.gate_min_psr,
            "dsr_ok": (eq.get("dsr") or 0) >= self.s.gate_min_psr,
            "wfe_ok": (report.get("wfe") or 0) >= self.s.gate_min_wfe,
            "ruina_ok": bool(mc.get("n", 0) >= 10) and mc.get("ruin_probability", 1) <= self.s.gate_max_ruin_prob,
        }
        passed = all(checks.values())
        return {"checks": checks, "all_passed": passed, "risk_multiplier": 1.0 if passed else 0.5,
                "risk_per_trade": self.s.risk_per_trade_pct_validated if passed else self.s.risk_per_trade_pct}

    @staticmethod
    def render_markdown(report: dict[str, Any]) -> str:
        cal, perm, tr, eq, mc, g = (report["calibration"], report["permutation"], report["trades"],
                                    report["equity"], report["monte_carlo"], report["gates"])
        lines = [f"# Relatório estatístico ({report['ts'][:16]} UTC, janela {report['window_days']} dias)", ""]
        lines.append(f"**Gates:** {'TODOS PASSAM' if g['all_passed'] else 'NÃO PASSA'} → risco por trade "
                     f"{g['risk_per_trade']:.2%} (multiplicador {g['risk_multiplier']})")
        lines.append("")
        for k, v in g["checks"].items():
            lines.append(f"- {'✅' if v else '❌'} {k}")
        lines += ["", "## Calibração das decisões direcionais",
                  f"- n = {cal.get('n', 0)}, taxa base {cal.get('base_rate')}, Brier {cal.get('brier')} vs climatologia "
                  f"{cal.get('brier_climatology')} (skill {cal.get('brier_skill')}), ECE {cal.get('ece')}"]
        for row in cal.get("reliability", []):
            lines.append(f"  - bin {row['bin']}: n={row['n']} conf={row['confidence']} acerto={row['accuracy']}")
        lines += ["", "## Hit-rate vs permutação",
                  f"- n = {perm.get('n', 0)}, hit-rate {perm.get('hit_rate')}, p95 aleatório {perm.get('random_p95')}, "
                  f"p-value {perm.get('p_value')}"]
        lines += ["", "## Trades fechados",
                  f"- n = {tr.get('n', 0)}, win-rate {tr.get('win_rate')}, expectancy {tr.get('expectancy')} USD, "
                  f"profit factor {tr.get('profit_factor')}, Kelly {tr.get('kelly')}, P&L líquido {tr.get('total_pnl')} USD "
                  f"(bruto {tr.get('gross_pnl')}, comissões {tr.get('commissions')} USD = "
                  f"{tr.get('cost_share_of_gross') if tr.get('cost_share_of_gross') is not None else 'n/a'} do bruto), "
                  f"max DD {tr.get('max_drawdown_abs')} USD",
                  f"- Monte Carlo: ruína (DD ≥ {mc.get('ruin_threshold_pct')}%) = {mc.get('ruin_probability')}, "
                  f"DD mediano {mc.get('median_max_dd_pct')}%, p95 {mc.get('p95_max_dd_pct')}%"]
        lines += ["", "## Equity",
                  f"- dias = {eq.get('n_days')}, Sharpe anual {eq.get('sharpe_annual')}, Sortino {eq.get('sortino_daily')}, "
                  f"PSR {eq.get('psr')}, DSR {eq.get('dsr')} (N experiências = {report['n_trials']}, SR* {eq.get('sr_star')}), "
                  f"MinBTL {eq.get('min_btl_years')} anos, max DD {eq.get('max_drawdown_pct')}%",
                  f"- WFE aproximada: {report.get('wfe')}"]
        if report.get("by_model"):
            lines += ["", "## Por modelo (A/B)"]
            for m, v in report["by_model"].items():
                lines.append(f"- {m}: n={v['n']} hit-rate={v['hit_rate']}")
        return "\n".join(lines)

    def try_quantstats_html(self, path: str) -> bool:
        try:
            import pandas as pd  # type: ignore
            import quantstats as qs  # type: ignore
        except ImportError:
            return False
        equity = self.db.daily_equity(datetime.now(timezone.utc) - timedelta(days=365))
        if len(equity) < 5:
            return False
        series = pd.Series([v for _, v in equity], index=pd.to_datetime([d for d, _ in equity])).pct_change().dropna()
        qs.reports.html(series, output=path, title="Ollama IBKR Trader")
        return True
