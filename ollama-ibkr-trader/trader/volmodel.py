"""Previsão de intervalo/volatilidade (fase 3): Chronos-Bolt com fallback EWMA.

Só se usa para largura de intervalo (p90 − p10), nunca para direção. Se
``chronos-forecasting`` não estiver instalado, a largura vem de uma
volatilidade EWMA escalada pela raiz do horizonte, o que mantém o gate de
"não negociar em expansão de vol" sempre operacional.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Optional, Sequence

from .indicators import ewma_volatility

log = logging.getLogger("trader.volmodel")


class VolatilityForecaster:
    def __init__(self, model_name: str = "amazon/chronos-bolt-small", enabled: bool = False) -> None:
        self.model_name = model_name
        self.enabled = enabled
        self._pipe: Any = None
        self._loaded: Optional[bool] = None

    def _load(self) -> bool:
        if not self.enabled:
            return False
        if self._loaded is None:
            try:
                import torch  # type: ignore
                from chronos import BaseChronosPipeline  # type: ignore

                self._pipe = BaseChronosPipeline.from_pretrained(self.model_name, device_map="cpu", torch_dtype=torch.float32)
                self._loaded = True
                log.info("Chronos carregado (%s).", self.model_name)
            except Exception as exc:  # noqa: BLE001
                self._loaded = False
                log.info("Previsor de volatilidade em fallback EWMA (%s).", exc)
        return bool(self._loaded)

    def interval_width_pct(self, closes: Sequence[float], horizon: int) -> tuple[Optional[float], str]:
        """Largura p90−p10 prevista a ``horizon`` velas, em % do último preço."""
        if len(closes) < 20 or closes[-1] <= 0:
            return None, "n/d"
        if self._load():
            try:
                import torch  # type: ignore

                context = torch.tensor(list(closes[-512:]), dtype=torch.float32)
                quantiles, _ = self._pipe.predict_quantiles(context=context, prediction_length=horizon,
                                                            quantile_levels=[0.1, 0.5, 0.9])
                q = quantiles[0]
                width = float(q[-1, 2] - q[-1, 0])
                return round(width / closes[-1] * 100.0, 3), "chronos"
            except Exception as exc:  # noqa: BLE001
                log.warning("Chronos falhou; fallback EWMA: %s", exc)
        sigma = ewma_volatility(closes)
        if sigma is None:
            return None, "n/d"
        width = 2 * 1.2816 * sigma * math.sqrt(horizon)
        return round(width * 100.0, 3), "ewma"
