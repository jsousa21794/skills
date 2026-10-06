"""Integração com o Ollama local: pipeline de decisão e parser robusto.

Desenho (fase 2 do roteiro):

1. **Prompt neutro**: sem ameaças; HOLD é a resposta esperada por omissão e
   BUY/SELL exigem sinais em *transição* (cruzamentos, mudanças de sinal), que
   é onde a literatura encontra conteúdo preditivo nos indicadores.
2. **Raciocínio antes do JSON**: a 1.ª chamada pede análise em texto livre; a
   2.ª converte-a em JSON com ``format`` = JSON Schema (Ollama >= 0.5),
   temperatura 0 e ``seed`` fixo. Evita a perda de capacidade de modelos
   pequenos quando raciocinam dentro de um schema.
3. **N amostras**: a fração de acordo e a margem entre 1.º e 2.º voto são o
   sinal de confiança; o valor verbalizado é apenas mais uma *feature*.
4. **Logprob do token de ação** (Ollama >= 0.12.11) quando disponível.
5. **REVIEW**: um parse falhado nunca vira HOLD silencioso.
6. **Anonimização** opcional do ticker e dos níveis de preço (contaminação
   temporal) e **cache** por hash do prompt (replay offline).

A comunicação usa ``requests`` (síncrono); o motor executa ``decide()`` num
``run_in_executor`` para não bloquear o loop ``asyncio``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import requests

from .config import Settings
from .database import Database
from .indicators import Dynamics, MarketSnapshot

log = logging.getLogger("trader.ollama")

VALID_ACTIONS = ("BUY", "SELL", "HOLD")

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "acao": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "confianca": {"type": "number", "minimum": 0, "maximum": 1},
        "razao": {"type": "string"},
    },
    "required": ["acao", "confianca", "razao"],
}

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
BASE_SYSTEM_PROMPT = """\
PAPEL
És um analista quantitativo que propõe uma ação (BUY, SELL ou HOLD) para um
sistema de trading automático. A tua proposta é uma opinião auditável: a
camada de risco em código decide tamanho, stop loss e take profit, aplica
cooldowns e pode vetar. Cada proposta é registada e avaliada contra o
mercado real 30 minutos depois; a tua confiança é calibrada com esses dados.

REGRAS
1. HOLD é a resposta esperada por omissão. Propõe BUY ou SELL apenas quando
   há pelo menos dois sinais em transição na mesma direção (cruzamento de
   médias, RSI a sair de zona extrema, mudança de sinal do momentum).
   Níveis estáticos ("RSI está em 58") não são sinal.
2. Não propões BUY com RSI >= 75 nem SELL com RSI <= 25.
3. Se já existe posição no ativo, diz se deve manter-se (HOLD) ou fechar-se
   (ação oposta). Não propões reforçar posições.
4. "confianca" é a probabilidade honesta (0 a 1) de o preço se mover na
   direção proposta nos próximos 30 minutos. 0.5 é moeda ao ar. Confiança
   alta sem confluência é penalizada na calibração.
5. Dados insuficientes ou contraditórios => HOLD.
"""

STAGE1_INSTRUCTIONS = """\
Analisa os dados em 3 a 6 frases: que sinais estão em transição, em que
direção, e que sinais contradizem. Termina obrigatoriamente com uma linha
no formato exato:
DECISÃO: BUY|SELL|HOLD | CONFIANÇA: 0.xx | RAZÃO: uma frase
"""

STAGE2_SYSTEM = """\
Converte a análise fornecida num objeto JSON com as chaves "acao" (BUY, SELL
ou HOLD), "confianca" (número entre 0 e 1) e "razao" (uma frase). Usa
exatamente a decisão e a confiança declaradas na análise. Responde apenas
com o JSON.
"""

SINGLE_STAGE_INSTRUCTIONS = """\
Responde apenas com um objeto JSON: {"acao": "BUY|SELL|HOLD", "confianca": 0.0-1.0, "razao": "..."}
"""

LESSONS_HEADER = """
PADRÕES MEDIDOS NAS TUAS DECISÕES ANTERIORES (calculados a partir de resultados reais)
"""


@dataclass
class Decision:
    acao: str
    confianca: float
    razao: str
    raw: str = ""
    parse_ok: bool = True
    error: str = ""

    @classmethod
    def hold(cls, reason: str, raw: str = "", error: str = "") -> "Decision":
        return cls("HOLD", 0.0, reason, raw=raw, parse_ok=False, error=error)

    @classmethod
    def review(cls, reason: str, raw: str = "", error: str = "review") -> "Decision":
        return cls("REVIEW", 0.0, reason, raw=raw, parse_ok=False, error=error)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DecisionOutcome:
    decision: Decision
    samples: list[dict[str, Any]] = field(default_factory=list)
    agree_frac: float = 0.0
    margin: float = 0.0
    action_logprob: Optional[float] = None
    prompt_hash: str = ""
    review: bool = False
    elapsed: float = 0.0
    models: list[str] = field(default_factory=list)
    from_cache: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"decision": self.decision.to_dict(), "samples": self.samples, "agree_frac": self.agree_frac,
                "margin": self.margin, "action_logprob": self.action_logprob, "prompt_hash": self.prompt_hash,
                "review": self.review, "models": self.models}


# ---------------------------------------------------------------------------
# Parser robusto
# ---------------------------------------------------------------------------
_ACTION_KEYS = ("acao", "ação", "accao", "action", "decision", "decisao", "decisão", "signal")
_CONF_KEYS = ("confianca", "confiança", "confidence", "conf", "probability", "probabilidade")
_REASON_KEYS = ("razao", "razão", "reason", "reasoning", "justificacao", "justificação", "explanation")

_ACTION_ALIASES = {
    "BUY": "BUY", "COMPRAR": "BUY", "COMPRA": "BUY", "LONG": "BUY", "ENTRAR": "BUY",
    "SELL": "SELL", "VENDER": "SELL", "VENDA": "SELL", "SHORT": "SELL",
    "HOLD": "HOLD", "MANTER": "HOLD", "ESPERAR": "HOLD", "WAIT": "HOLD", "NONE": "HOLD",
    "NEUTRAL": "HOLD", "NEUTRO": "HOLD", "NADA": "HOLD", "SKIP": "HOLD",
}


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json|JSON)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_json_objects(text: str) -> list[str]:
    """Devolve candidatos ``{...}`` com chavetas equilibradas (ignora strings)."""
    candidates: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    candidates.append(text[start : i + 1])
                    start = -1
    return candidates


def _loose_json_fix(text: str) -> str:
    """Correções comuns de JSON 'quase válido' produzido por LLMs."""
    fixed = text
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)  # vírgulas finais
    fixed = re.sub(r"\bTrue\b", "true", fixed)
    fixed = re.sub(r"\bFalse\b", "false", fixed)
    fixed = re.sub(r"\bNone\b", "null", fixed)
    if '"' not in fixed and "'" in fixed:
        fixed = fixed.replace("'", '"')
    # chaves sem aspas: {acao: "BUY"}
    fixed = re.sub(r"([{,]\s*)([A-Za-zÀ-ÿ_][\w À-ÿ]*?)\s*:", r'\1"\2":', fixed)
    return fixed


def _try_load(text: str) -> Optional[dict[str, Any]]:
    for candidate in (text, _loose_json_fix(text)):
        try:
            obj = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    return item
    return None


def _first_key(obj: dict[str, Any], keys: tuple[str, ...]) -> Any:
    lowered = {str(k).strip().lower(): v for k, v in obj.items()}
    for key in keys:
        if key in lowered:
            return lowered[key]
    return None


_NEGATIONS = ("NOT", "NO", "DONT", "DON'T", "NEVER", "NÃO", "NAO", "NUNCA", "SEM", "AVOID", "EVITAR", "EVITA")


def normalize_action(value: Any) -> Optional[str]:
    """Só aceita a ação exata (após limpar aspas/pontuação). "DO NOT BUY" não é BUY."""
    if value is None or isinstance(value, bool):
        return None
    token = str(value).strip().strip('"\'').upper()
    token = re.sub(r"[^A-ZÀ-Ü' ]+", " ", token).strip()
    words = [w for w in token.split() if w]
    if not words:
        return None
    if len(words) == 1:
        return _ACTION_ALIASES.get(words[0])
    # Formas de duas palavras sem negação, ex.: "BUY NOW"? Não: exigimos exatidão, exceto artigos triviais.
    if any(w in _NEGATIONS for w in words):
        return None
    if len(words) == 2 and words[0] in _ACTION_ALIASES and words[1] in ("NOW", "AGORA"):
        return _ACTION_ALIASES[words[0]]
    return None


def normalize_confidence(value: Any) -> Optional[float]:
    """Confiança em [0,1]; None quando ausente, não numérica ou não finita (NaN/Inf)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        conf = float(value)
    else:
        text = str(value).strip().replace(",", ".")
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            return None
        conf = float(match.group(0))
        if "%" in text:
            conf /= 100.0
    if not math.isfinite(conf):
        return None
    if conf > 1.0:
        conf = conf / 100.0 if conf <= 100.0 else 1.0
    return max(0.0, min(1.0, conf))


def parse_decision(raw: str) -> Decision:
    """Converte a resposta bruta do modelo numa ``Decision`` segura.

    Nunca lança exceções: qualquer falha resulta em HOLD com ``parse_ok=False``.
    """
    if raw is None:
        return Decision.hold("Resposta vazia do modelo", raw="", error="empty")
    text = _strip_code_fences(str(raw))
    if not text:
        return Decision.hold("Resposta vazia do modelo", raw=raw, error="empty")

    obj = _try_load(text)
    if obj is None:
        for candidate in _extract_json_objects(text):
            obj = _try_load(candidate)
            if obj is not None:
                break

    if obj is not None:
        action = normalize_action(_first_key(obj, _ACTION_KEYS))
        confidence = normalize_confidence(_first_key(obj, _CONF_KEYS))
        reason_val = _first_key(obj, _REASON_KEYS)
        reason = str(reason_val).strip() if reason_val is not None else ""
        if action is None:
            return Decision.hold(
                f"Ação inválida na resposta: {_first_key(obj, _ACTION_KEYS)!r}",
                raw=raw, error="invalid_action",
            )
        if confidence is None:
            if _first_key(obj, _CONF_KEYS) is None:
                confidence = 0.0  # ausente: nunca executa (abaixo de qualquer limiar)
            else:
                return Decision.hold(f"Confiança inválida: {_first_key(obj, _CONF_KEYS)!r}", raw=raw,
                                     error="invalid_confidence")
        return Decision(action, confidence, reason or "(sem razão)", raw=raw, parse_ok=True)

    # Último recurso: regex direta sobre texto livre.
    action_match = re.search(
        r"(?:acao|ação|action|decision)\W{0,5}(?!(?:NOT|NO|DON'T|NÃO|NAO|NUNCA|NEVER)\b)(BUY|SELL|HOLD|COMPRAR|VENDER|MANTER)\b",
        text, re.IGNORECASE,
    )
    conf_match = re.search(r"(?:confianca|confiança|confidence)\W{0,5}(\d+(?:[.,]\d+)?\s*%?)", text, re.IGNORECASE)
    reason_match = re.search(r"(?:razao|razão|reason)\W{0,5}[\"']?([^\"'\n}]{3,300})", text, re.IGNORECASE)
    if action_match:
        action = normalize_action(action_match.group(1)) or "HOLD"
        confidence = (normalize_confidence(conf_match.group(1)) if conf_match else 0.0)
        if confidence is None:
            return Decision.hold("Confiança inválida no texto livre", raw=raw, error="invalid_confidence")
        reason = reason_match.group(1).strip() if reason_match else "(extraído por regex)"
        return Decision(action, confidence, reason, raw=raw, parse_ok=True)

    return Decision.hold("Não foi possível interpretar a resposta do modelo", raw=raw, error="unparseable")


# ---------------------------------------------------------------------------
# Extração da linha "DECISÃO: ... | CONFIANÇA: ... | RAZÃO: ..."
# ---------------------------------------------------------------------------
_DECISION_LINE = re.compile(
    r"DECIS[ÃA]O\s*[:=]\s*\**\s*(BUY|SELL|HOLD|COMPRAR|VENDER|MANTER)\b.*?"
    r"CONFIAN[ÇC]A\s*[:=]\s*\**\s*([0-9]+(?:[.,][0-9]+)?\s*%?)(?:.*?RAZ[ÃA]O\s*[:=]\s*(.+))?",
    re.IGNORECASE | re.DOTALL,
)


def parse_stage1(text: str) -> Optional[Decision]:
    """Lê a linha final da análise livre; None se não existir."""
    if not text:
        return None
    match = None
    for match in _DECISION_LINE.finditer(text):
        pass  # fica com a última ocorrência
    if not match:
        return None
    action = normalize_action(match.group(1))
    if action is None:
        return None
    conf = normalize_confidence(match.group(2))
    if conf is None:
        return None
    reason = (match.group(3) or "").strip().splitlines()[0].strip() if match.group(3) else "(ver análise)"
    return Decision(action, conf, reason[:300], raw=text, parse_ok=True)


def extract_action_logprob(payload: dict[str, Any], action: str) -> Optional[float]:
    """Procura o logprob do token da ação em qualquer das formas que o Ollama devolve."""
    tokens: Optional[list[dict[str, Any]]] = None
    for candidate in (payload.get("logprobs"), (payload.get("message") or {}).get("logprobs"),
                      ((payload.get("choices") or [{}])[0].get("logprobs") or {}).get("content")):
        if isinstance(candidate, list) and candidate and isinstance(candidate[0], dict) and "logprob" in candidate[0]:
            tokens = candidate
            break
    if not tokens:
        return None
    seen = ""
    key_seen = False
    for tok in tokens:
        text = str(tok.get("token", ""))
        seen += text
        if not key_seen:
            if "acao" in seen.lower() or "ação" in seen.lower():
                key_seen = True
            continue
        piece = text.strip().strip('"').strip().upper()
        if piece and action.startswith(piece):
            try:
                return float(tok["logprob"])
            except (TypeError, ValueError):
                return None
    return None


# ---------------------------------------------------------------------------
# Cliente Ollama
# ---------------------------------------------------------------------------
class OllamaBrain:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.model = settings.ollama_model
        self.lessons: list[str] = []
        self.prompt_version = 0
        self._ab_index = 0
        self._load_addendum()

    # ------------------------------------------------------------- prompts
    def _load_addendum(self) -> None:
        latest = self.db.latest_prompt_version()
        if latest:
            self.lessons = list(latest.get("lessons") or [])
            self.prompt_version = int(latest["id"])

    def update_lessons(self, lessons: list[str], stats: dict[str, Any]) -> int:
        """Nova versão do prompt SÓ quando o texto muda: a versão identifica a experiência (calibração,
        gates) e não deve avançar a cada retrospetiva sem alteração (N11)."""
        self.lessons = lessons[: self.settings.retro_max_lessons]
        latest = self.db.latest_prompt_version()
        text = self.addendum_text()
        if latest is not None and latest.get("addendum") == text:
            self.prompt_version = int(latest["id"])
            return self.prompt_version
        self.prompt_version = self.db.save_prompt_version(text, self.lessons, stats)
        self.db.record_experiment("prompt", f"v{self.prompt_version}")
        return self.prompt_version

    def addendum_text(self, lessons: Optional[list[str]] = None) -> str:
        lessons = lessons if lessons is not None else self.lessons
        lessons = lessons[: self.settings.lessons_in_prompt]
        if not lessons:
            return ""
        return LESSONS_HEADER + "\n".join(f"- {l}" for l in lessons) + "\n"

    def system_prompt(self, lessons: Optional[list[str]] = None, single_stage: bool = False) -> str:
        prompt = BASE_SYSTEM_PROMPT + self.addendum_text(lessons)
        prompt += "\n" + (SINGLE_STAGE_INSTRUCTIONS if single_stage else STAGE1_INSTRUCTIONS)
        return prompt

    def alias(self, symbol: str) -> str:
        if not self.settings.anonymize_prompt:
            return symbol
        try:
            idx = self.settings.symbols.index(symbol) + 1
        except ValueError:
            idx = abs(hash(symbol)) % 97 + 10
        return f"ATIVO-{idx}"

    def user_prompt(self, snapshot: MarketSnapshot, portfolio: dict[str, Any], recent: list[dict[str, Any]],
                    dynamics: Optional[Dynamics] = None, sentiment: Optional[tuple[Optional[float], int]] = None,
                    vol_width: Optional[float] = None) -> str:
        s = self.settings
        anon = s.anonymize_prompt
        pos = next((p for p in portfolio.get("positions", []) if p["symbol"] == snapshot.symbol), None)
        pos_text = "nenhuma"
        if pos:
            side = "LONG" if pos["qty"] > 0 else "SHORT"
            upnl = pos.get("unrealized_pnl", 0.0)
            pos_text = f"{side} (P&L não realizado {upnl:+.2f} USD)" if anon else \
                f"{side} {abs(pos['qty']):g} @ {pos['avg_cost']:.2f} (P&L não realizado {upnl:+.2f} USD)"

        def pct(v: Optional[float], ref: float) -> str:
            return "n/d" if v is None or not ref else f"{(ref - v) / v * 100:+.2f}%"

        def fmt(v: Optional[float], suffix: str = "", digits: int = 2) -> str:
            return "n/d" if v is None else f"{v:.{digits}f}{suffix}"

        from .risk import NY
        hour_ny = snapshot.bar_time.astimezone(NY).strftime("%H:%M")
        lines = [f"ATIVO: {self.alias(snapshot.symbol)}",
                 f"Vela de {s.decision_bar_minutes} min mais recente (hora NY {hour_ny})"]
        if anon:
            lines.append(f"- Preço vs SMA{s.sma_fast}: {pct(snapshot.sma_fast, snapshot.price)} | vs SMA{s.sma_slow}: "
                         f"{pct(snapshot.sma_slow, snapshot.price)} | vs EMA{s.ema_period}: {pct(snapshot.ema, snapshot.price)}")
        else:
            lines.append(f"- Preço: {snapshot.price:.2f} USD | SMA{s.sma_fast} {fmt(snapshot.sma_fast)} | "
                         f"SMA{s.sma_slow} {fmt(snapshot.sma_slow)} | EMA{s.ema_period} {fmt(snapshot.ema)}")
        rsi_line = f"- RSI({s.rsi_period}): {fmt(snapshot.rsi, digits=0)}"
        if dynamics:
            rsi_line += (f" | variação em 3 velas: {fmt(dynamics.rsi_slope, digits=1)} | saiu de sobrevenda (30↑): "
                         f"{'sim' if dynamics.rsi_cross_30_up else 'não'} | saiu de sobrecompra (70↓): "
                         f"{'sim' if dynamics.rsi_cross_70_down else 'não'}")
        lines.append(rsi_line)
        if dynamics:
            lines.append(f"- Cruzamentos nas últimas 3 velas: EMA×SMA rápida: {dynamics.ema_cross_sma_fast or 'nenhum'} | "
                         f"SMA rápida×SMA lenta: {dynamics.sma_fast_cross_slow or 'nenhum'}")
            lines.append(f"- Momentum de 5 velas mudou de sinal: {'sim' if dynamics.returns_sign_change else 'não'} | "
                         f"preço {'acima' if dynamics.price_vs_fast == 'above' else 'abaixo'} da SMA rápida")
            lines.append(f"- ATR({s.atr_period}): {fmt(dynamics.atr_pct, '%', 2)} do preço")
        lines.append(f"- Variação 5 min: {fmt(snapshot.change_5m_pct, '%')} | 30 min: {fmt(snapshot.change_30m_pct, '%')}")
        lines.append(f"- Tendência (preço vs médias): {snapshot.trend_label()}")
        if sentiment and sentiment[0] is not None:
            lines.append(f"- Sentimento de notícias ({s.sentiment_window_hours}h): {sentiment[0]:+.2f} em {sentiment[1]} manchetes")
        if vol_width is not None:
            lines.append(f"- Largura prevista do intervalo p90−p10 a {s.volmodel_horizon_bars} velas: {vol_width:.2f}%")
        day_pnl = portfolio.get("day_pnl_pct")
        lines += ["", "CARTEIRA",
                  f"- Posição neste ativo: {pos_text}",
                  f"- P&L do dia: {('%+.2f%%' % day_pnl) if day_pnl is not None else 'n/d'}",
                  f"- Posições abertas: {len(portfolio.get('positions', []))}/{s.max_open_positions}"]
        lines += ["", "ÚLTIMAS PROPOSTAS NESTE ATIVO (resultado a 30 min quando disponível)"]
        if recent:
            for d in recent[:5]:
                outcome = ""
                if d.get("settled_return") is not None:
                    outcome = f" → {float(d['settled_return']):+.2f}%" + (" ✓" if d.get("correct") == 1 else (" ✗" if d.get("correct") == 0 else ""))
                lines.append(f"- {d['ts'][11:16]}Z {d['action']} conf={float(d['confidence']):.2f}{outcome}"
                             + (" (executada)" if d.get("executed") else ""))
        else:
            lines.append("- (nenhuma)")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------ requests
    def list_models(self) -> list[str]:
        try:
            resp = requests.get(f"{self.settings.ollama_url}/api/tags", timeout=10)
            resp.raise_for_status()
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            return sorted(m for m in models if m)
        except (requests.RequestException, ValueError) as exc:
            log.warning("Ollama indisponível ao listar modelos: %s", exc)
            return []

    def is_available(self) -> bool:
        try:
            return requests.get(f"{self.settings.ollama_url}/api/tags", timeout=5).ok
        except requests.RequestException:
            return False

    def set_model(self, model: str) -> None:
        if model != self.model:
            self.db.record_experiment("model", model)
        self.model = model
        self.settings.ollama_model = model
        self.settings.save()

    def next_ab_model(self) -> str:
        """Round-robin entre os modelos de A/B (ou o modelo corrente)."""
        models = [m for m in self.settings.ab_test_models if m] or [self.model]
        model = models[self._ab_index % len(models)]
        self._ab_index += 1
        return model

    def chat_raw(self, system: str, user: str, *, model: Optional[str] = None, json_mode: bool = False,
                 schema: Optional[dict[str, Any]] = None, temperature: Optional[float] = None,
                 seed: Optional[int] = None, logprobs: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {
                "temperature": self.settings.ollama_temperature if temperature is None else temperature,
                "num_ctx": self.settings.ollama_num_ctx,
            },
        }
        if seed is not None:
            payload["options"]["seed"] = seed
        if schema is not None:
            payload["format"] = schema
        elif json_mode:
            payload["format"] = "json"
        if logprobs:
            payload["logprobs"] = True
            payload["top_logprobs"] = 3
        resp = requests.post(f"{self.settings.ollama_url}/api/chat", json=payload,
                             timeout=self.settings.ollama_timeout_seconds)
        resp.raise_for_status()
        return resp.json()

    def chat(self, system: str, user: str, *, json_mode: bool = True, temperature: Optional[float] = None,
             model: Optional[str] = None) -> str:
        data = self.chat_raw(system, user, model=model, json_mode=json_mode, temperature=temperature)
        return (data.get("message") or {}).get("content", "") or data.get("response", "")

    # ------------------------------------------------------------ pipeline
    def decide(self, snapshot: MarketSnapshot, portfolio: dict[str, Any], recent: list[dict[str, Any]], *,
               dynamics: Optional[Dynamics] = None, lessons: Optional[list[str]] = None,
               sentiment: Optional[tuple[Optional[float], int]] = None, vol_width: Optional[float] = None,
               model: Optional[str] = None, cache: Optional[Database] = None,
               temperature_override: Optional[float] = None) -> DecisionOutcome:
        """Chamada síncrona (executar via ``run_in_executor``)."""
        started = time.monotonic()
        s = self.settings
        primary = model or self.model
        models = [primary] + [m for m in s.ensemble_models if m and m != primary]
        system = self.system_prompt(lessons, single_stage=not s.llm_two_stage)
        user = self.user_prompt(snapshot, portfolio, recent, dynamics, sentiment, vol_width)
        prompt_hash = hashlib.sha256(("|".join(models) + system + user).encode("utf-8")).hexdigest()

        if cache is not None:
            hit = cache.cache_get(prompt_hash)
            if hit:
                outcome = self._outcome_from_dict(hit)
                outcome.from_cache = True
                outcome.prompt_hash = prompt_hash
                return outcome

        samples: list[dict[str, Any]] = []
        per_model_majority: dict[str, str] = {}
        errors: list[str] = []
        n = max(1, s.llm_samples)
        n_requested = n * len(models)
        for m in models:
            model_samples: list[dict[str, Any]] = []
            for k in range(n):
                temp = temperature_override if temperature_override is not None else (
                    s.llm_sample_temperature if n > 1 else s.ollama_temperature)
                try:
                    dec, lp = self._one_sample(system, user, m, temp, seed=s.llm_seed + k)
                except requests.Timeout:
                    errors.append(f"{m}: timeout")
                    continue
                except requests.RequestException as exc:
                    errors.append(f"{m}: ligação ({exc.__class__.__name__})")
                    break
                except ValueError as exc:
                    errors.append(f"{m}: resposta inválida ({exc})")
                    continue
                model_samples.append({"model": m, "acao": dec.acao, "confianca": dec.confianca, "razao": dec.razao,
                                      "parse_ok": dec.parse_ok, "error": dec.error, "logprob": lp})
            samples.extend(model_samples)
            valid_m = [x for x in model_samples if x["parse_ok"]]
            if valid_m:
                per_model_majority[m] = Counter(x["acao"] for x in valid_m).most_common(1)[0][0]
            else:
                errors.append(f"{m}: sem respostas válidas")

        elapsed = time.monotonic() - started
        valid = [x for x in samples if x["parse_ok"]]
        # Quórum sobre o número PEDIDO: timeouts e respostas inválidas contam como indisponibilidade.
        if len(valid) < max(1, math.ceil(n_requested * s.llm_min_valid_fraction)) or len(per_model_majority) < len(models):
            reason = "; ".join(errors) if errors else f"{n_requested - len(valid)}/{n_requested} respostas sem decisão válida"
            kind = "connection" if any("ligação" in e for e in errors) else ("timeout" if any("timeout" in e for e in errors) else "review")
            return DecisionOutcome(Decision.review(reason, error=kind), samples, 0.0, 0.0, None, prompt_hash,
                                   review=True, elapsed=elapsed, models=models)

        votes = Counter(x["acao"] for x in valid)
        ordered = votes.most_common()
        top_action, top_n = ordered[0]
        second_n = ordered[1][1] if len(ordered) > 1 else 0
        agree = top_n / n_requested
        margin = (top_n - second_n) / n_requested
        majority = [x for x in valid if x["acao"] == top_action]
        mean_conf = sum(x["confianca"] for x in majority) / len(majority)
        reason = max(majority, key=lambda x: x["confianca"])["razao"]
        lps = [x["logprob"] for x in majority if x.get("logprob") is not None]
        logprob = sum(lps) / len(lps) if lps else None  # só logprobs da ação vencedora

        if len(per_model_majority) > 1 and len(set(per_model_majority.values())) > 1:
            decision = Decision("HOLD", mean_conf, f"sem acordo entre modelos ({per_model_majority})", parse_ok=True)
        else:
            decision = Decision(top_action, round(mean_conf, 3), reason, parse_ok=True)
        outcome = DecisionOutcome(decision, samples, round(agree, 3), round(margin, 3), logprob, prompt_hash,
                                  review=False, elapsed=elapsed, models=models)
        if cache is not None:
            cache.cache_put(prompt_hash, primary, outcome.to_dict())
        return outcome

    def _one_sample(self, system: str, user: str, model: str, temperature: float, seed: int
                    ) -> tuple[Decision, Optional[float]]:
        s = self.settings
        if not s.llm_two_stage:
            data = self.chat_raw(system, user, model=model, schema=DECISION_SCHEMA, temperature=temperature,
                                 seed=seed, logprobs=s.llm_request_logprobs)
            content = (data.get("message") or {}).get("content", "")
            dec = parse_decision(content)
            return dec, extract_action_logprob(data, dec.acao) if dec.parse_ok else None
        # Etapa 1: raciocínio livre.
        data1 = self.chat_raw(system, user, model=model, temperature=temperature, seed=seed)
        analysis = (data1.get("message") or {}).get("content", "")
        dec = parse_stage1(analysis)
        lp: Optional[float] = None
        if dec is None or s.llm_request_logprobs:
            # Etapa 2: JSON com schema, temperatura 0, seed fixo (e logprobs, se suportado).
            data2 = self.chat_raw(STAGE2_SYSTEM, f"ANÁLISE:\n{analysis}\n\nJSON:", model=model,
                                  schema=DECISION_SCHEMA, temperature=0.0, seed=s.llm_seed,
                                  logprobs=s.llm_request_logprobs)
            content = (data2.get("message") or {}).get("content", "")
            dec2 = parse_decision(content)
            if dec2.parse_ok:
                lp = extract_action_logprob(data2, dec2.acao)
                if dec is None:
                    dec = dec2  # a linha final faltou: vale o JSON da análise
                elif dec.acao != dec2.acao:
                    # A formatação nunca pode mudar a decisão da análise: amostra inválida.
                    dec = Decision.hold(f"Divergência análise={dec.acao} vs JSON={dec2.acao}",
                                        raw=analysis + "\n" + content, error="stage_mismatch")
                    lp = None
            elif dec is None:
                dec = Decision.hold("Análise sem linha de decisão e JSON inválido", raw=analysis + "\n" + content,
                                    error="unparseable")
        dec.raw = analysis
        return dec, lp

    @staticmethod
    def _outcome_from_dict(data: dict[str, Any]) -> DecisionOutcome:
        d = data.get("decision", {})
        decision = Decision(d.get("acao", "HOLD"), float(d.get("confianca", 0.0)), d.get("razao", ""),
                            raw=d.get("raw", ""), parse_ok=bool(d.get("parse_ok", True)), error=d.get("error", ""))
        return DecisionOutcome(decision, data.get("samples", []), float(data.get("agree_frac", 0.0)),
                               float(data.get("margin", 0.0)), data.get("action_logprob"), data.get("prompt_hash", ""),
                               review=bool(data.get("review", False)), models=data.get("models", []))
