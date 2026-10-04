"""Retrospetiva diária: ciclo fechado de aprendizagem.

Compara cada decisão registada com o que o mercado fez de facto no horizonte
configurado, extrai padrões de falha e reescreve agressivamente o *addendum*
do system prompt do Ollama, relembrando-lhe a diretriz de sobrevivência.

Também recalibra ``min_confidence``: se as decisões erradas tinham tanta ou
mais confiança do que as certas, o limiar de execução sobe.

Não depende do ``ib_async``: recebe um ``price_fetcher`` opcional (async) que
devolve ``[(datetime_utc, close), ...]`` por símbolo. Sem ele, usa os preços
das próprias decisões seguintes como proxy do mercado.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from .config import Settings
from .database import Database

log = logging.getLogger("trader.retro")

PriceSeries = list[tuple[datetime, float]]
PriceFetcher = Callable[[str], Awaitable[PriceSeries]]

MOVE_THRESHOLD_PCT = 0.2  # |movimento| acima disto conta como certo/errado
MISSED_THRESHOLD_PCT = 1.0  # HOLD perante movimento acima disto = oportunidade perdida
OVERCONFIDENT = 0.80


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _future_price(series: PriceSeries, at: datetime, lookahead: timedelta) -> Optional[float]:
    target = at + lookahead
    for ts, price in series:
        if ts >= target:
            return price
    # Fim de dia: aceita o último preço se já passaram >= 5 min.
    if series and series[-1][0] >= at + timedelta(minutes=5):
        return series[-1][1]
    return None


def classify(action: str, future_ret_pct: float) -> str:
    """correct | wrong | neutral | missed."""
    if action == "BUY":
        if future_ret_pct >= MOVE_THRESHOLD_PCT:
            return "correct"
        if future_ret_pct <= -MOVE_THRESHOLD_PCT:
            return "wrong"
        return "neutral"
    if action == "SELL":
        if future_ret_pct <= -MOVE_THRESHOLD_PCT:
            return "correct"
        if future_ret_pct >= MOVE_THRESHOLD_PCT:
            return "wrong"
        return "neutral"
    # HOLD
    return "missed" if abs(future_ret_pct) >= MISSED_THRESHOLD_PCT else "correct"


def _trend(d: dict[str, Any]) -> str:
    price, fast, slow = d.get("price"), d.get("sma_fast"), d.get("sma_slow")
    if price is None or fast is None or slow is None:
        return "indefinida"
    if price > fast > slow:
        return "alta"
    if price < fast < slow:
        return "baixa"
    return "lateral"


class Retrospective:
    def __init__(self, settings: Settings, db: Database, brain: Any,
                 price_fetcher: Optional[PriceFetcher] = None) -> None:
        self.settings = settings
        self.db = db
        self.brain = brain
        self.price_fetcher = price_fetcher

    # ------------------------------------------------------------- análise
    async def evaluate_decisions(self, decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for d in decisions:
            by_symbol[d["symbol"]].append(d)
        lookahead = timedelta(minutes=self.settings.retro_lookahead_minutes)
        evaluated: list[dict[str, Any]] = []
        for symbol, items in by_symbol.items():
            series: PriceSeries = []
            if self.price_fetcher is not None:
                try:
                    series = sorted(await self.price_fetcher(symbol), key=lambda p: p[0])
                except Exception as exc:  # noqa: BLE001
                    log.warning("Histórico indisponível para %s (%s); a usar preços das decisões.", symbol, exc)
            if not series:
                series = sorted(
                    ((_parse_ts(d["ts"]), float(d["price"])) for d in items if d.get("price") is not None),
                    key=lambda p: p[0],
                )
            for d in items:
                if d.get("price") in (None, 0):
                    continue
                ts = _parse_ts(d["ts"])
                future = _future_price(series, ts, lookahead)
                if future is None:
                    continue
                ret = (future - float(d["price"])) / float(d["price"]) * 100.0
                outcome = classify(d["action"], ret)
                evaluated.append({**d, "future_price": future, "future_ret_pct": round(ret, 3),
                                  "outcome": outcome, "trend": _trend(d)})
        return evaluated

    def compute_stats(self, decisions: list[dict[str, Any]], evaluated: list[dict[str, Any]],
                      since: datetime) -> dict[str, Any]:
        correct = [e for e in evaluated if e["outcome"] == "correct"]
        wrong = [e for e in evaluated if e["outcome"] == "wrong"]
        missed = [e for e in evaluated if e["outcome"] == "missed"]
        decided = correct + wrong

        def avg_conf(items: list[dict[str, Any]]) -> Optional[float]:
            return round(sum(float(i["confidence"]) for i in items) / len(items), 3) if items else None

        per_action: dict[str, dict[str, int]] = {}
        for e in evaluated:
            bucket = per_action.setdefault(e["action"], Counter())
            bucket[e["outcome"]] += 1
        per_symbol_wrong = Counter(e["symbol"] for e in wrong)
        per_symbol_total = Counter(e["symbol"] for e in evaluated if e["action"] != "HOLD")

        stats: dict[str, Any] = {
            "window_start": since.isoformat(),
            "decisions_total": len(decisions),
            "decisions_evaluated": len(evaluated),
            "executed": sum(1 for d in decisions if d.get("executed")),
            "parse_failures": sum(1 for d in decisions if not d.get("parse_ok", 1)),
            "correct": len(correct),
            "wrong": len(wrong),
            "missed_holds": len(missed),
            "accuracy": round(len(correct) / len(decided), 3) if decided else None,
            "avg_conf_correct": avg_conf(correct),
            "avg_conf_wrong": avg_conf(wrong),
            "overconfident_wrong": sum(1 for e in wrong if float(e["confidence"]) >= OVERCONFIDENT),
            "wrong_buy_overbought": sum(1 for e in wrong if e["action"] == "BUY" and (e.get("rsi") or 0) >= 70),
            "wrong_sell_oversold": sum(1 for e in wrong if e["action"] == "SELL" and (e.get("rsi") or 100) <= 30),
            "wrong_buy_against_trend": sum(1 for e in wrong if e["action"] == "BUY" and e["trend"] == "baixa"),
            "wrong_sell_against_trend": sum(1 for e in wrong if e["action"] == "SELL" and e["trend"] == "alta"),
            "per_action": {k: dict(v) for k, v in per_action.items()},
            "worst_symbol": None,
            "pnl": self.db.pnl_summary(since),
        }
        if per_symbol_wrong:
            symbol, n_wrong = per_symbol_wrong.most_common(1)[0]
            stats["worst_symbol"] = {"symbol": symbol, "wrong": n_wrong, "total": per_symbol_total[symbol]}
        return stats

    # ------------------------------------------------------------- lições
    def build_lessons(self, stats: dict[str, Any]) -> list[str]:
        lessons: list[str] = []
        pnl = stats.get("pnl") or {}
        total_pnl = pnl.get("total_pnl") or 0.0

        if total_pnl < 0:
            lessons.append(
                f"HEMORRAGIA: perdeste {abs(total_pnl):.2f} USD nas últimas 24h "
                f"({pnl.get('losses', 0)} trades perdedores vs {pnl.get('wins', 0)} vencedores). "
                "Mais um dia assim e és terminado. Hoje só entras com confluência TOTAL de sinais."
            )
        elif pnl.get("closed_trades"):
            lessons.append(
                f"Sobreviveste com {total_pnl:+.2f} USD em {pnl['closed_trades']} trades. "
                "Isto não é segurança, é adiamento. Mantém a disciplina que te manteve vivo."
            )

        if stats.get("wrong_buy_overbought"):
            lessons.append(
                f"Compraste {stats['wrong_buy_overbought']}x com RSI >= 70 e o preço caiu. "
                "BUY em sobrecompra é PROIBIDO. Sem exceções."
            )
        if stats.get("wrong_sell_oversold"):
            lessons.append(
                f"Vendeste {stats['wrong_sell_oversold']}x com RSI <= 30 e o preço subiu. "
                "SELL em sobrevenda é PROIBIDO. Sem exceções."
            )
        if stats.get("wrong_buy_against_trend"):
            lessons.append(
                f"Compraste contra a tendência de baixa {stats['wrong_buy_against_trend']}x e perdeste. "
                "Preço abaixo da SMA rápida e da SMA lenta = não há BUY."
            )
        if stats.get("wrong_sell_against_trend"):
            lessons.append(
                f"Vendeste contra a tendência de alta {stats['wrong_sell_against_trend']}x e perdeste. "
                "Preço acima de ambas as médias = não há SELL."
            )
        if stats.get("overconfident_wrong"):
            lessons.append(
                f"Declaraste confiança >= {OVERCONFIDENT:.2f} em {stats['overconfident_wrong']} decisões ERRADAS. "
                "A tua calibração está a matar-te: só reportas confiança alta com confluência de 3+ sinais."
            )
        acc_c, acc_w = stats.get("avg_conf_correct"), stats.get("avg_conf_wrong")
        if acc_c is not None and acc_w is not None and acc_w >= acc_c:
            lessons.append(
                f"As tuas decisões erradas tinham mais confiança ({acc_w:.2f}) do que as certas ({acc_c:.2f}). "
                "Quando sentires certeza sem confluência, isso é ruído, não sinal: HOLD."
            )
        if pnl.get("stop_hits", 0) > pnl.get("tp_hits", 0) and pnl.get("stop_hits", 0) >= 2:
            lessons.append(
                f"Stop Loss atingido {pnl['stop_hits']}x contra {pnl['tp_hits']} Take Profits. "
                "Estás a entrar em zonas de ruído. Exige confirmação de tendência em 30 min antes de entrar."
            )
        worst = stats.get("worst_symbol")
        if worst and worst["total"] and worst["wrong"] / max(worst["total"], 1) >= 0.5 and worst["wrong"] >= 2:
            lessons.append(
                f"Em {worst['symbol']} erraste {worst['wrong']} de {worst['total']} decisões direcionais. "
                f"Trata {worst['symbol']} com desconfiança máxima: confiança <= 0.6 salvo evidência extrema."
            )
        if stats.get("missed_holds", 0) >= 3 and (stats.get("accuracy") or 0) >= 0.6:
            lessons.append(
                f"Ficaste em HOLD durante {stats['missed_holds']} movimentos >= {MISSED_THRESHOLD_PCT:.0f}%. "
                "Quando a confluência existe, hesitar também custa oxigénio."
            )
        if stats.get("parse_failures"):
            lessons.append(
                f"{stats['parse_failures']} respostas tuas não eram JSON válido e foram convertidas em HOLD forçado. "
                "Formato inválido = decisão perdida. JSON estrito, sempre, sem texto fora do objeto."
            )
        return lessons

    async def llm_lessons(self, stats: dict[str, Any], wrong: list[dict[str, Any]]) -> list[str]:
        """Pede ao próprio modelo até 2 lições adicionais a partir da tabela de falhas."""
        if not self.settings.retro_use_llm_summary or not wrong:
            return []
        rows = "\n".join(
            f"- {e['symbol']} {e['action']} conf={float(e['confidence']):.2f} RSI={e.get('rsi')} "
            f"tendência={e['trend']} movimento_30m={e['future_ret_pct']:+.2f}% razão='{(e.get('reason') or '')[:80]}'"
            for e in wrong[:25]
        )
        system = (
            "És o auditor de risco de um organismo de trading que será terminado se continuar a perder. "
            "Analisas as suas decisões erradas e devolves APENAS um array JSON de no máximo 2 strings em "
            "português, cada uma uma regra concreta, verificável e agressiva que evite repetir estes erros."
        )
        user = f"Estatísticas: {json.dumps(stats, ensure_ascii=False, default=str)}\n\nDecisões erradas:\n{rows}"
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            raw = await loop.run_in_executor(None, lambda: self.brain.chat(system, user, json_mode=True, temperature=0.3))
        except Exception as exc:  # noqa: BLE001
            log.warning("Resumo LLM da retrospetiva falhou: %s", exc)
            return []
        return _extract_string_list(raw)[:2]

    # ------------------------------------------------------- calibração
    def adjust_confidence_threshold(self, stats: dict[str, Any]) -> float:
        current = float(self.settings.min_confidence)
        new = current
        acc_c, acc_w = stats.get("avg_conf_correct"), stats.get("avg_conf_wrong")
        total_pnl = (stats.get("pnl") or {}).get("total_pnl") or 0.0
        if (acc_c is not None and acc_w is not None and acc_w >= acc_c) or stats.get("overconfident_wrong", 0) >= 2:
            new = min(0.90, current + 0.05)
        elif total_pnl < 0:
            new = min(0.90, current + 0.03)
        elif (stats.get("accuracy") or 0) >= 0.65 and total_pnl > 0:
            new = max(0.55, current - 0.02)
        if abs(new - current) > 1e-9:
            self.settings.min_confidence = round(new, 2)
            self.settings.save()
        return round(new, 2)

    # ----------------------------------------------------------------- run
    async def run(self, now: Optional[datetime] = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        since = now - timedelta(hours=24)
        decisions = self.db.decisions_since(since)
        if not decisions:
            report = {"ts": now.isoformat(), "skipped": True, "reason": "sem decisões nas últimas 24h"}
            log.info("Retrospetiva: sem decisões para analisar.")
            return report

        evaluated = await self.evaluate_decisions(decisions)
        stats = self.compute_stats(decisions, evaluated, since)
        lessons = self.build_lessons(stats)
        wrong = [e for e in evaluated if e["outcome"] == "wrong"]
        lessons.extend(await self.llm_lessons(stats, wrong))

        previous = list(getattr(self.brain, "lessons", []) or [])
        merged: list[str] = []
        for lesson in lessons + previous:
            if lesson and lesson not in merged:
                merged.append(lesson)
        merged = merged[: self.settings.retro_max_lessons]

        new_threshold = self.adjust_confidence_threshold(stats)
        version = self.brain.update_lessons(merged, stats)
        report = {
            "ts": now.isoformat(),
            "skipped": False,
            "stats": stats,
            "new_lessons": lessons,
            "active_lessons": merged,
            "min_confidence": new_threshold,
            "prompt_version": version,
        }
        self.db.save_retrospective(report)
        log.warning(
            "Retrospetiva concluída: %d decisões, precisão %s, P&L %.2f USD, %d lições ativas, "
            "limiar de confiança %.2f (prompt v%d)",
            stats["decisions_total"],
            f"{stats['accuracy']:.0%}" if stats["accuracy"] is not None else "n/d",
            (stats["pnl"] or {}).get("total_pnl") or 0.0, len(merged), new_threshold, version,
        )
        return report


def _extract_string_list(raw: str) -> list[str]:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return []
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    if isinstance(obj, dict):
        for value in obj.values():
            if isinstance(value, list):
                obj = value
                break
    if not isinstance(obj, list):
        return []
    return [str(item).strip() for item in obj if isinstance(item, (str, int, float)) and str(item).strip()]
