"""Retrospetiva diária: orquestra o ciclo fechado de aprendizagem.

Já não pede ao LLM que escreva as suas próprias lições. Em vez disso:

1. ``Settler`` avalia todas as decisões cujo horizonte expirou (retorno real,
   alpha vs benchmark, rótulo correto/errado);
2. ``LessonEngine`` extrai padrões de falha em código (por regime de RSI,
   hora, ativo, confiança) e guarda as top-K lições com decaimento;
3. ``Calibrator`` reajusta o Platt scaling quando há amostras suficientes;
4. ``Analytics`` produz o relatório estatístico e os *gates* que determinam
   o multiplicador de risco.

Opcionalmente (``retro_use_llm_summary``), o modelo pode sugerir 1-2 lições
adicionais a partir da tabela de falhas; por defeito está desligado.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .analytics import Analytics
from .calibration import Calibrator
from .config import Settings
from .database import Database
from .lessons import LessonEngine
from .settlement import Settler

log = logging.getLogger("trader.retro")


class Retrospective:
    def __init__(self, settings: Settings, db: Database, brain: Any, settler: Settler,
                 lessons: LessonEngine, calibrator: Calibrator, analytics: Analytics) -> None:
        self.settings = settings
        self.db = db
        self.brain = brain
        self.settler = settler
        self.lessons = lessons
        self.calibrator = calibrator
        self.analytics = analytics

    async def run(self, now: Optional[datetime] = None, *, full_report: bool = True) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        settled_now = self.settler.run(now)
        since = now - timedelta(hours=24)
        decisions = self.db.decisions_since(since)
        settled = [d for d in self.db.settled_decisions(since=since) if d.get("correct") is not None]
        pnl = self.db.pnl_summary(since)

        lessons = self.lessons.rebuild(now=now)
        texts = [l["text"] for l in lessons]
        if self.settings.retro_use_llm_summary:
            texts.extend(await self._llm_lessons(settled))
        stats = {
            "window_start": since.isoformat(),
            "decisions_total": len(decisions),
            "settled_24h": len(settled),
            "settled_now": settled_now,
            "accuracy_24h": round(sum(int(d["correct"]) for d in settled) / len(settled), 3) if settled else None,
            "review_count": sum(1 for d in decisions if d.get("review")),
            "parse_failures": sum(1 for d in decisions if not d.get("parse_ok", 1)),
            "pnl": pnl,
            "lessons_active": len(lessons),
        }
        version = self.brain.update_lessons(texts, stats)
        self.calibrator.set_prompt_version(version)  # a calibração pertence a (modelo, prompt) (N11)

        model = None
        if self.calibrator.needs_refit():
            model = self.calibrator.fit_from_db()
        report = None
        if full_report:
            report = self.analytics.build_report(now, model=self.calibrator.model_name,
                                                 prompt_version=self.calibrator.prompt_version)  # gates da experiência atual

        result = {
            "ts": now.isoformat(),
            "skipped": False,
            "stats": stats,
            "new_lessons": texts,
            "active_lessons": texts,
            "prompt_version": version,
            "calibration": {"fitted": self.calibrator.is_fitted,
                            "n_samples": model.n_samples if model else (self.calibrator.model.n_samples if self.calibrator.model else 0)},
            "gates": report["gates"] if report else None,
            "report_markdown": Analytics.render_markdown(report) if report else None,
        }
        self.db.save_retrospective(result)
        log.warning(
            "Retrospetiva: %d decisões/24h, %d settled (acerto %s), P&L %.2f USD, %d lições, calibração %s, gates %s",
            stats["decisions_total"], stats["settled_24h"],
            f"{stats['accuracy_24h']:.0%}" if stats["accuracy_24h"] is not None else "n/d",
            pnl.get("total_pnl") or 0.0, len(lessons), "ajustada" if self.calibrator.is_fitted else "heurística",
            ("PASSAM" if report and report["gates"]["all_passed"] else "não passam") if report else "n/a",
        )
        return result

    async def _llm_lessons(self, settled: list[dict[str, Any]]) -> list[str]:
        wrong = [d for d in settled if d.get("correct") == 0 and d["action"] in ("BUY", "SELL")]
        if not wrong:
            return []
        rows = "\n".join(
            f"- {d['symbol']} {d['action']} conf={float(d['confidence']):.2f} RSI={d.get('rsi')} "
            f"retorno_30m={float(d['settled_return']):+.2f}%" for d in wrong[:25])
        system = ("És um auditor de risco. A partir das decisões erradas abaixo, devolve APENAS um array JSON com no "
                  "máximo 2 regras concretas e verificáveis, em português, que evitem repetir estes erros.")
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            raw = await loop.run_in_executor(None, lambda: self.brain.chat(system, rows, json_mode=True, temperature=0.3))
        except Exception as exc:  # noqa: BLE001
            log.warning("Resumo LLM da retrospetiva falhou: %s", exc)
            return []
        return _extract_string_list(raw)[:2]


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
