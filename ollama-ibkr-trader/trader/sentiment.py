"""Sentimento de notícias com FinBERT (fase 3, opcional).

Usa ``ProsusAI/finbert`` (ou outro id configurável) via ``transformers`` se
estiver instalado; caso contrário devolve ``None`` e o bot segue sem a
feature. O score é a média de (p_pos − p_neg) nas manchetes das últimas
``window_hours`` horas; o veto a BUY/SELL é aplicado pela camada de risco.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("trader.sentiment")


class SentimentScorer:
    def __init__(self, model_name: str = "ProsusAI/finbert") -> None:
        self.model_name = model_name
        self._pipe: Any = None
        self._available: Optional[bool] = None
        self._cache: dict[str, float] = {}

    def available(self) -> bool:
        if self._available is None:
            try:
                from transformers import pipeline  # type: ignore

                self._pipe = pipeline("text-classification", model=self.model_name, top_k=None, truncation=True)
                self._available = True
                log.info("FinBERT carregado (%s).", self.model_name)
            except Exception as exc:  # noqa: BLE001
                self._available = False
                log.info("Sentimento desativado (transformers/modelo indisponível): %s", exc)
        return bool(self._available)

    def score_headline(self, text: str) -> Optional[float]:
        if not text or not self.available():
            return None
        if text in self._cache:
            return self._cache[text]
        try:
            result = self._pipe(text[:512])
            scores = result[0] if isinstance(result[0], list) else result
            probs = {r["label"].lower(): float(r["score"]) for r in scores}
            value = probs.get("positive", 0.0) - probs.get("negative", 0.0)
        except Exception as exc:  # noqa: BLE001
            log.warning("FinBERT falhou numa manchete: %s", exc)
            return None
        self._cache[text] = value
        return value

    def score_news(self, headlines: list[str]) -> tuple[Optional[float], int]:
        values = [v for v in (self.score_headline(h) for h in headlines) if v is not None]
        if not values:
            return None, 0
        return round(sum(values) / len(values), 3), len(values)
