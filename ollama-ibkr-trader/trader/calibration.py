"""Calibração da confiança do LLM (Platt scaling) e limiar de execução.

A confiança verbalizada por modelos pequenos está "perto do teto" e tem pouca
informação. Em vez dela, usa-se um vetor de sinais (confiança verbal, fração
de acordo entre N amostras, margem entre 1.º e 2.º voto, logprob do token de
ação) e ajusta-se uma regressão logística aos resultados *settled* guardados
no SQLite. Sem amostras suficientes, cai num score heurístico conservador.

Implementação em Python puro (Newton-Raphson com regularização L2) para não
arrastar scikit-learn para o executável; o resultado é equivalente ao
``LogisticRegression`` com ``C`` moderado.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from .config import Settings
from .database import Database

log = logging.getLogger("trader.calibration")

FEATURES = ("verbal_conf", "agree_frac", "margin", "action_logprob")


@dataclass
class ConfidenceSignals:
    verbal_conf: float
    agree_frac: float
    margin: float  # (votos do 1.º - votos do 2.º) / N
    action_logprob: Optional[float] = None  # ln p(token da ação); None se indisponível

    def vector(self) -> list[float]:
        lp = self.action_logprob if self.action_logprob is not None else math.log(0.5)
        return [self.verbal_conf, self.agree_frac, self.margin, max(-10.0, lp)]

    def heuristic(self) -> float:
        """Score conservador usado enquanto não há calibração."""
        p = self.agree_frac * (0.35 + 0.65 * self.verbal_conf)
        if self.action_logprob is not None:
            p = 0.7 * p + 0.3 * math.exp(max(-10.0, self.action_logprob))
        return max(0.0, min(1.0, p))


@dataclass
class PlattModel:
    weights: list[float]
    bias: float
    n_samples: int
    fitted_at: str
    brier_train: float
    base_rate: float
    features: list[str] = field(default_factory=lambda: list(FEATURES))

    def predict(self, signals: ConfidenceSignals) -> float:
        z = self.bias + sum(w * x for w, x in zip(self.weights, signals.vector()))
        z = max(-30.0, min(30.0, z))
        return 1.0 / (1.0 + math.exp(-z))

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "PlattModel":
        data = json.loads(text)
        return cls(**data)


def fit_logistic(X: Sequence[Sequence[float]], y: Sequence[int], *, l2: float = 1.0,
                 iterations: int = 50) -> tuple[list[float], float]:
    """Regressão logística por Newton-Raphson com L2 (sem regularizar o bias)."""
    n = len(X)
    if n == 0:
        raise ValueError("sem amostras")
    d = len(X[0])
    # Normalização simples das features para estabilidade numérica.
    w = [0.0] * d
    b = math.log(max(1e-6, sum(y) / n) / max(1e-6, 1 - sum(y) / n)) if 0 < sum(y) < n else 0.0
    for _ in range(iterations):
        grad = [0.0] * (d + 1)
        hess = [[0.0] * (d + 1) for _ in range(d + 1)]
        for xi, yi in zip(X, y):
            z = b + sum(wj * xj for wj, xj in zip(w, xi))
            z = max(-30.0, min(30.0, z))
            p = 1.0 / (1.0 + math.exp(-z))
            err = p - yi
            ext = list(xi) + [1.0]
            s = p * (1 - p)
            for j in range(d + 1):
                grad[j] += err * ext[j]
                for k in range(d + 1):
                    hess[j][k] += s * ext[j] * ext[k]
        for j in range(d):
            grad[j] += l2 * w[j]
            hess[j][j] += l2
        hess[d][d] += 1e-6
        step = _solve(hess, grad)
        if step is None:
            break
        for j in range(d):
            w[j] -= step[j]
        b -= step[d]
        if max(abs(v) for v in step) < 1e-6:
            break
    return w, b


def _solve(a: list[list[float]], bvec: list[float]) -> Optional[list[float]]:
    n = len(bvec)
    m = [row[:] + [bvec[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def brier(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    if not probs:
        return float("nan")
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)


class Calibrator:
    """Um ajuste de Platt por modelo de linguagem: trocar de modelo nunca herda a calibração de outro."""

    FEATURE_VERSION = "v2"

    def __init__(self, settings: Settings, db: Database, model_name: Optional[str] = None,
                 prompt_version: Optional[int] = None) -> None:
        self.s = settings
        self.db = db
        self.model_name = model_name or settings.ollama_model
        self.prompt_version = prompt_version  # None = sem partição por prompt (replay/testes)
        self.model: Optional[PlattModel] = None
        self.reload()

    @property
    def experiment(self) -> str:
        """Identificador da experiência calibrada: modelo + versão do prompt (N11)."""
        return f"{self.model_name}" + (f":p{self.prompt_version}" if self.prompt_version is not None else "")

    @property
    def kv_key(self) -> str:
        return f"platt_model:{self.FEATURE_VERSION}:{self.experiment}"

    def set_model_name(self, model_name: str) -> None:
        if model_name != self.model_name:
            self.model_name = model_name
            self.reload()

    def set_prompt_version(self, prompt_version: Optional[int]) -> None:
        if prompt_version != self.prompt_version:
            self.prompt_version = prompt_version
            self.reload()

    def reload(self) -> None:
        self.model = None
        stored = self.db.get_kv(self.kv_key)
        if stored:
            try:
                self.model = PlattModel.from_json(stored)
            except (ValueError, TypeError):
                self.model = None
        if self.model is not None and self._stale_by_labels(self.model):
            # Modelo ajustado ANTES de rótulos terem sido corrigidos/finalizados: inválido também no arranque (Z06).
            log.warning("Calibração %s: modelo guardado é anterior à última correção de rótulos; descartado (heurística até reajustar).",
                        self.experiment)
            self.invalidate()

    def _stale_by_labels(self, model: PlattModel) -> bool:
        changed = self.db.get_kv("labels_changed_at")
        if not changed:
            return False
        try:
            return datetime.fromisoformat(changed) > datetime.fromisoformat(model.fitted_at)
        except (TypeError, ValueError):
            return True

    def invalidate(self) -> None:
        """Retira o modelo em memória E do armazenamento: estado não calibrado (heurística) até um reajuste válido (Z06)."""
        self.model = None
        if self.db.get_kv(self.kv_key):
            self.db.set_kv(self.kv_key, "")

    @property
    def is_fitted(self) -> bool:
        return self.model is not None

    def probability(self, signals: ConfidenceSignals) -> float:
        if self.model is not None:
            return self.model.predict(signals)
        return signals.heuristic()

    def needs_refit(self) -> bool:
        if self.model is None:
            return True
        fitted = datetime.fromisoformat(self.model.fitted_at)
        changed = self.db.get_kv("labels_changed_at")
        if changed and datetime.fromisoformat(changed) > fitted:
            return True  # rótulos finalizados/corrigidos depois do ajuste invalidam o modelo (Y05)
        return datetime.now(timezone.utc) - fitted > timedelta(hours=self.s.calibration_refit_every_hours)

    def fit_from_db(self) -> Optional[PlattModel]:
        rows = self.db.settled_decisions(directional_only=True)
        rows = [r for r in rows if r.get("correct") is not None and r.get("agree_frac") is not None
                and (r.get("model") or "") == self.model_name
                and (self.prompt_version is None or r.get("prompt_version") == self.prompt_version)]
        if len(rows) < self.s.calibration_min_samples:
            log.info("Calibração: %d/%d decisões settled; a usar heurística.", len(rows), self.s.calibration_min_samples)
            self._drop_if_stale()
            return None
        X, y = [], []
        for r in rows:
            sig = ConfidenceSignals(
                verbal_conf=float(r.get("verbal_conf") or r["confidence"] or 0.0),
                agree_frac=float(r["agree_frac"]),
                margin=float(_margin_from_samples(r.get("samples_json"))),
                action_logprob=r.get("action_logprob"),
            )
            X.append(sig.vector())
            y.append(int(r["correct"]))
        if len(set(y)) < 2:
            log.info("Calibração: todos os resultados iguais; sem ajuste.")
            self._drop_if_stale()
            return None
        w, b = fit_logistic(X, y)
        model = PlattModel(weights=w, bias=b, n_samples=len(y), fitted_at=datetime.now(timezone.utc).isoformat(),
                           brier_train=0.0, base_rate=sum(y) / len(y))
        probs = [model.predict(ConfidenceSignals(*x[:3], x[3])) for x in X]
        model.brier_train = brier(probs, y)
        self.model = model
        self.db.set_kv(self.kv_key, model.to_json())
        log.warning("Calibração Platt ajustada para %s com %d decisões (Brier treino %.3f, taxa base %.2f)",
                    self.model_name, len(y), model.brier_train, model.base_rate)
        return model


    def _drop_if_stale(self) -> None:
        """Um reajuste necessário que FALHA (amostras insuficientes ou uma só classe) não pode deixar o modelo
        anterior em uso: se está invalidado (rótulos corrigidos ou prazo expirado), volta-se à heurística (Z06)."""
        if self.model is not None and self.needs_refit():
            log.warning("Calibração %s: reajuste necessário sem dados válidos; modelo anterior retirado (heurística).", self.experiment)
            self.invalidate()


def _margin_from_samples(samples_json: Any) -> float:  # noqa: E302
    try:
        samples = json.loads(samples_json) if isinstance(samples_json, str) else (samples_json or [])
    except json.JSONDecodeError:
        return 0.0
    votes: dict[str, int] = {}
    for s in samples:
        votes[s.get("acao", "HOLD")] = votes.get(s.get("acao", "HOLD"), 0) + 1
    if not votes:
        return 0.0
    ordered = sorted(votes.values(), reverse=True)
    second = ordered[1] if len(ordered) > 1 else 0
    return (ordered[0] - second) / max(1, len(samples))


def execution_threshold(settings: Settings, *, reward_risk_ratio: float, cost: float, risk_amount: float,
                        calibrated: bool) -> float:
    """Limiar de probabilidade: break-even do bracket + margem; piso enquanto não calibrado."""
    from .risk import break_even_probability

    p_star = break_even_probability(reward_risk_ratio, cost, risk_amount)
    threshold = p_star + settings.edge_margin
    if not calibrated:
        threshold = max(threshold, settings.min_confidence)
    return min(0.95, threshold)
