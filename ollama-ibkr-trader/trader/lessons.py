"""Lições calculadas em código a partir das decisões *settled*.

Em vez de pedir ao LLM que escreva a sua própria retrospetiva (padrão de
falha documentado em "Honest Lying"), extraem-se sinais de falha
programáticos: taxa de acerto por regime de RSI, por hora do dia, por ativo,
por ação e por bin de confiança, comparada com a taxa base. Só vira lição um
desvio com suporte suficiente; cada lição tem score = |efeito| × sqrt(suporte)
× decaimento temporal, e só as top-K relevantes entram no prompt.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .config import Settings
from .database import Database

log = logging.getLogger("trader.lessons")

MIN_SUPPORT = 12
MIN_Z = 1.64  # ~90% unilateral
DECAY_HALF_LIFE_DAYS = 10.0


def rsi_regime(rsi: Optional[float]) -> str:
    if rsi is None:
        return "desconhecido"
    if rsi >= 70:
        return "sobrecompra"
    if rsi <= 30:
        return "sobrevenda"
    return "neutro"


def hour_bucket(hour_ny: Optional[int]) -> str:
    if hour_ny is None:
        return "desconhecida"
    if hour_ny < 10:
        return "abertura"
    if hour_ny < 12:
        return "manhã"
    if hour_ny < 14:
        return "almoço"
    return "tarde"


def conf_bin(conf: Optional[float]) -> str:
    if conf is None:
        return "n/d"
    if conf >= 0.85:
        return ">=0.85"
    if conf >= 0.7:
        return "0.70-0.85"
    return "<0.70"


def _z(hits: int, n: int, base: float) -> float:
    if n == 0 or base <= 0 or base >= 1:
        return 0.0
    return (hits / n - base) / math.sqrt(base * (1 - base) / n)


class LessonEngine:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.s = settings
        self.db = db

    def rebuild(self, since_days: int = 30, now: Optional[datetime] = None) -> list[dict[str, Any]]:
        now = now or datetime.now(timezone.utc)
        rows = [r for r in self.db.settled_decisions(since=now - timedelta(days=since_days))
                if r.get("correct") is not None]
        directional = [r for r in rows if r["action"] in ("BUY", "SELL")]
        if len(directional) < MIN_SUPPORT:
            log.info("Lições: apenas %d decisões direcionais settled; nada a extrair.", len(directional))
            return [dict(l) for l in self.db.active_lessons()]
        base = sum(int(r["correct"]) for r in directional) / len(directional)

        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in directional:
            a = r["action"]
            groups[f"rsi:{rsi_regime(r.get('rsi'))}:{a}"].append(r)
            groups[f"hora:{hour_bucket(r.get('hour_ny'))}:{a}"].append(r)
            groups[f"ativo:{r['symbol']}:{a}"].append(r)
            groups[f"conf:{conf_bin(r.get('verbal_conf') or r.get('confidence'))}:{a}"].append(r)
            groups[f"regime:{r.get('regime') or 'n/d'}:{a}"].append(r)

        found: list[dict[str, Any]] = []
        for key, items in groups.items():
            n = len(items)
            if n < MIN_SUPPORT:
                continue
            hits = sum(int(r["correct"]) for r in items)
            z = _z(hits, n, base)
            if abs(z) < MIN_Z:
                continue
            last_ts = max(datetime.fromisoformat(r["ts"]) for r in items)
            age_days = (now - last_ts).total_seconds() / 86400
            decay = 0.5 ** (age_days / DECAY_HALF_LIFE_DAYS)
            importance = abs(z) * math.sqrt(n) * decay
            text = self._render(key, hits, n, base, z, last_ts)
            symbol = key.split(":")[1] if key.startswith("ativo:") else None
            found.append({"key": key, "symbol": symbol, "text": text, "support": n, "effect": round(z, 2),
                          "importance": round(importance, 3)})

        # Lições de calibração/sobre-confiança (sinais globais).
        high = [r for r in directional if (r.get("verbal_conf") or r.get("confidence") or 0) >= 0.85]
        if len(high) >= MIN_SUPPORT:
            hits = sum(int(r["correct"]) for r in high)
            z = _z(hits, len(high), base)
            if z < -MIN_Z:
                found.append({"key": "calib:overconfident", "symbol": None,
                              "text": (f"Decisões com confiança >= 0.85 acertaram {hits}/{len(high)} "
                                       f"({hits / len(high):.0%}) contra uma base de {base:.0%}: a confiança alta "
                                       "não está a corresponder a acerto. Reporta confiança alta só com confluência."),
                              "support": len(high), "effect": round(z, 2),
                              "importance": round(abs(z) * math.sqrt(len(high)), 3)})

        found.sort(key=lambda l: l["importance"], reverse=True)
        found = found[: self.s.retro_max_lessons]
        for l in found:
            self.db.upsert_lesson(key=l["key"], symbol=l["symbol"], text=l["text"], support=l["support"],
                                  effect=l["effect"], importance=l["importance"])
        # Lições iniciais (importadas) mantêm-se ativas: têm importância fixa baixa e são
        # ultrapassadas pelas lições medidas assim que estas existem.
        seeds = [l["key"] for l in self.db.active_lessons() if l["key"].startswith("seed:")]
        self.db.deactivate_lessons_except([l["key"] for l in found] + seeds)
        log.info("Lições recalculadas: %d ativas (base %.0f%% em %d decisões).", len(found), base * 100, len(directional))
        return found

    @staticmethod
    def _render(key: str, hits: int, n: int, base: float, z: float, last_ts: datetime) -> str:
        kind, value, action = key.split(":", 2)
        rate = hits / n
        label = {"rsi": f"com RSI em {value}", "hora": f"no período '{value}' (NY)", "ativo": f"em {value}",
                 "conf": f"com confiança {value}", "regime": f"em tendência {value}"}[kind]
        verb = "acertou" if z > 0 else "falhou"
        when = last_ts.strftime("%Y-%m-%d")
        if z < 0:
            return (f"{action} {label} {verb}: {hits}/{n} acertos ({rate:.0%}) vs base {base:.0%} "
                    f"[suporte {n}, até {when}]. Evita {action} nesta condição salvo confluência extra.")
        return (f"{action} {label} tem acertado acima da base: {hits}/{n} ({rate:.0%}) vs {base:.0%} "
                f"[suporte {n}, até {when}]. Condição favorável, mantém a disciplina de risco.")

    def for_prompt(self, symbol: str, rsi: Optional[float] = None, hour_ny: Optional[int] = None,
                   regime: Optional[str] = None, k: Optional[int] = None) -> list[str]:
        k = k or self.s.lessons_in_prompt
        lessons = self.db.active_lessons()
        scored = []
        for l in lessons:
            relevance = 1.0
            key = l["key"]
            if l["symbol"] and l["symbol"] != symbol:
                relevance *= 0.3
            if key.startswith("rsi:") and rsi is not None and rsi_regime(rsi) not in key:
                relevance *= 0.5
            if key.startswith("hora:") and hour_ny is not None and hour_bucket(hour_ny) not in key:
                relevance *= 0.5
            if key.startswith("regime:") and regime and regime not in key:
                relevance *= 0.5
            scored.append((l["importance"] * relevance, l["text"]))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [t for _, t in scored[:k]]


SEED_IMPORTANCE = 0.5  # abaixo de qualquer lição medida (|z| >= 1.64 com suporte >= 12 dá >= 5.7)


def import_seed_lessons(db: Database, path: str) -> int:
    """Importa lições iniciais de um JSON: [{"key", "text", "symbol"?, "source"?}, ...].

    Cada lição fica com a chave ``seed:<key>``, suporte 0 e importância fixa baixa,
    e inclui a fonte no texto para o LLM e para auditoria. Idempotente.
    """
    import json
    from pathlib import Path

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data.get("lessons", data) if isinstance(data, dict) else data
    count = 0
    for item in items:
        key = str(item.get("key") or "").strip()
        text = str(item.get("text") or "").strip()
        if not key or not text:
            continue
        source = str(item.get("source") or "").strip()
        full = f"{text} [fonte: {source}]" if source else text
        db.upsert_lesson(key=f"seed:{key}", symbol=item.get("symbol"), text=full, support=int(item.get("support") or 0),
                         effect=0.0, importance=float(item.get("importance") or SEED_IMPORTANCE))
        count += 1
    log.info("Lições iniciais importadas de %s: %d", path, count)
    return count
