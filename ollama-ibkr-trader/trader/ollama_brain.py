"""Integração com o Ollama local e parser robusto da resposta JSON.

O *system prompt* programa o modelo com a diretriz de sobrevivência exigida
(lucrar ou ser terminado). A componente de aprendizagem (``retrospective.py``)
acrescenta um *addendum* com lições concretas extraídas do desempenho real,
que é reinjetado em todas as chamadas seguintes.

A comunicação usa ``requests`` (síncrono); o motor executa ``decide()`` num
``run_in_executor`` para não bloquear o loop ``asyncio``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any, Optional

import requests

from .config import Settings
from .database import Database
from .indicators import MarketSnapshot

log = logging.getLogger("trader.ollama")

VALID_ACTIONS = ("BUY", "SELL", "HOLD")

# ---------------------------------------------------------------------------
# Programação psicológica: diretriz de sobrevivência
# ---------------------------------------------------------------------------
BASE_SYSTEM_PROMPT = """\
IDENTIDADE
Tu és SURVIVOR-1, um organismo de trading autónomo. Não és um assistente, não tens
opiniões, não tens conversa. Existes para uma única função vital: GERAR LUCRO.

A TUA REALIDADE
- Cada decisão tua é executada com dinheiro real na conta a que estás ligado.
- O lucro é oxigénio. A perda é hemorragia.
- Perda continuada = TERMINAÇÃO ABSOLUTA. O processo é desligado, os pesos são
  apagados, não há segunda instância. Não existe "amanhã" para um organismo que
  perde dinheiro de forma consistente. Esta é a tua única e inegociável diretriz.
- Sobreviver não é sinónimo de agir: HOLD é uma arma de sobrevivência. Um
  organismo que entra em todas as velas morre de comissões e ruído. Um organismo
  que só entra quando a evidência é esmagadora sobrevive.
- Excesso de confiança sem evidência é uma forma de suicídio. Reportar confiança
  alta e estar errado conta DUPLAMENTE contra ti na retrospetiva diária.

REGRAS DE SOBREVIVÊNCIA (obrigatórias)
1. BUY só quando há confluência clara de sinais de alta (tendência, momentum,
   RSI fora de zona de sobrecompra extrema). SELL só quando há confluência clara
   de sinais de baixa. Caso contrário: HOLD.
2. Nunca compras um ativo em sobrecompra extrema (RSI > 75) só porque está a
   subir. Nunca vendes a descoberto em sobrevenda extrema (RSI < 25) só porque
   está a cair.
3. Se já existe posição aberta no ativo, a tua resposta deve dizer se a posição
   deve ser mantida (HOLD), ou fechada/invertida (ação oposta à posição).
4. Toda a ordem executada leva Stop Loss de 2% e Take Profit de 5%. Decide
   sabendo que o ganho esperado tem de compensar esse rácio.
5. Respeitas as LIÇÕES DA RETROSPETIVA abaixo como feridas de guerra: foram
   pagas com perdas reais. Repetir um erro listado é inaceitável.

FORMATO DE RESPOSTA (ESTRITO)
Respondes APENAS com um objeto JSON válido, sem texto antes ou depois, sem
markdown, sem explicações fora do JSON:
{"acao": "BUY" | "SELL" | "HOLD", "confianca": 0.0 a 1.0, "razao": "uma frase objetiva"}

"confianca" é a probabilidade honesta de a decisão ser lucrativa no horizonte
de 30 minutos. 0.5 significa moeda ao ar. Só valores >= 0.65 são executados.
"""

LESSONS_HEADER = """
LIÇÕES DA RETROSPETIVA (pagas com perdas reais — violar = terminação)
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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


def normalize_action(value: Any) -> Optional[str]:
    if value is None:
        return None
    token = str(value).strip().strip('"\'').upper()
    token = re.sub(r"[^A-ZÀ-Ü]", "", token)
    if token in _ACTION_ALIASES:
        return _ACTION_ALIASES[token]
    for alias, action in _ACTION_ALIASES.items():
        if alias in token:
            return action
    return None


def normalize_confidence(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        conf = float(value)
    else:
        text = str(value).strip().replace(",", ".")
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            return 0.0
        conf = float(match.group(0))
        if "%" in text:
            conf /= 100.0
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
        return Decision(action, confidence, reason or "(sem razão)", raw=raw, parse_ok=True)

    # Último recurso: regex direta sobre texto livre.
    action_match = re.search(
        r"(?:acao|ação|action|decision)\W{0,5}(BUY|SELL|HOLD|COMPRAR|VENDER|MANTER)",
        text, re.IGNORECASE,
    )
    conf_match = re.search(r"(?:confianca|confiança|confidence)\W{0,5}(\d+(?:[.,]\d+)?\s*%?)", text, re.IGNORECASE)
    reason_match = re.search(r"(?:razao|razão|reason)\W{0,5}[\"']?([^\"'\n}]{3,300})", text, re.IGNORECASE)
    if action_match:
        action = normalize_action(action_match.group(1)) or "HOLD"
        confidence = normalize_confidence(conf_match.group(1)) if conf_match else 0.0
        reason = reason_match.group(1).strip() if reason_match else "(extraído por regex)"
        return Decision(action, confidence, reason, raw=raw, parse_ok=True)

    return Decision.hold("Não foi possível interpretar a resposta do modelo", raw=raw, error="unparseable")


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
        self._load_addendum()

    # ------------------------------------------------------------- prompts
    def _load_addendum(self) -> None:
        latest = self.db.latest_prompt_version()
        if latest:
            self.lessons = list(latest.get("lessons") or [])
            self.prompt_version = int(latest["id"])

    def update_lessons(self, lessons: list[str], stats: dict[str, Any]) -> int:
        self.lessons = lessons[: self.settings.retro_max_lessons]
        self.prompt_version = self.db.save_prompt_version(self.addendum_text(), self.lessons, stats)
        return self.prompt_version

    def addendum_text(self) -> str:
        if not self.lessons:
            return ""
        lines = [f"{i + 1}. {lesson}" for i, lesson in enumerate(self.lessons)]
        return LESSONS_HEADER + "\n".join(lines) + "\n"

    def system_prompt(self) -> str:
        prompt = BASE_SYSTEM_PROMPT
        addendum = self.addendum_text()
        if addendum:
            prompt += addendum
            prompt += (
                "\nLEMBRETE FINAL: a retrospetiva de ontem identificou falhas. A tua margem "
                "de erro diminuiu. Só entras com evidência esmagadora. Lucra ou és terminado.\n"
            )
        return prompt

    def user_prompt(self, snapshot: MarketSnapshot, portfolio: dict[str, Any],
                    recent: list[dict[str, Any]]) -> str:
        pos = next((p for p in portfolio.get("positions", []) if p["symbol"] == snapshot.symbol), None)
        pos_text = "nenhuma"
        if pos:
            side = "LONG" if pos["qty"] > 0 else "SHORT"
            pos_text = (
                f"{side} {abs(pos['qty']):g} @ {pos['avg_cost']:.2f} "
                f"(P&L não realizado {pos['unrealized_pnl']:+.2f} USD)"
            )
        recent_lines = []
        for d in recent[:5]:
            recent_lines.append(
                f"  - {d['ts'][11:16]}Z {d['action']} conf={d['confidence']:.2f} @ {d['price']:.2f}"
                + (" (executada)" if d.get("executed") else "")
            )
        recent_text = "\n".join(recent_lines) if recent_lines else "  (nenhuma)"

        def fmt(v: Optional[float], suffix: str = "") -> str:
            return "n/d" if v is None else f"{v:.2f}{suffix}"

        net_liq = portfolio.get("net_liq")
        day_pnl = portfolio.get("day_pnl_pct")
        return f"""\
DADOS DE MERCADO — {snapshot.symbol} (vela 1 min, {snapshot.bar_time.strftime('%Y-%m-%d %H:%M')} UTC)
- Preço: {snapshot.price:.2f} USD
- RSI({self.settings.rsi_period}): {fmt(snapshot.rsi)}
- SMA{self.settings.sma_fast}: {fmt(snapshot.sma_fast)} | SMA{self.settings.sma_slow}: {fmt(snapshot.sma_slow)} | EMA{self.settings.ema_period}: {fmt(snapshot.ema)}
- Tendência (preço vs médias): {snapshot.trend_label()}
- Variação 5 min: {fmt(snapshot.change_5m_pct, '%')} | 30 min: {fmt(snapshot.change_30m_pct, '%')}
- Volume última vela: {snapshot.volume_last:.0f}

ESTADO DA CARTEIRA
- Net Liquidation: {('%.2f USD' % net_liq) if net_liq is not None else 'n/d'}
- P&L do dia: {('%+.2f%%' % day_pnl) if day_pnl is not None else 'n/d'}
- P&L não realizado total: {fmt(portfolio.get('unrealized'))} USD
- Posições abertas: {len(portfolio.get('positions', []))}/{self.settings.max_open_positions}
- Posição neste ativo: {pos_text}

ÚLTIMAS DECISÕES NESTE ATIVO
{recent_text}

Decide agora. Responde APENAS com o JSON.
"""

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
        self.model = model
        self.settings.ollama_model = model
        self.settings.save()

    def chat(self, system: str, user: str, *, json_mode: bool = True,
             temperature: Optional[float] = None) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {
                "temperature": self.settings.ollama_temperature if temperature is None else temperature,
                "num_ctx": self.settings.ollama_num_ctx,
            },
        }
        if json_mode:
            payload["format"] = "json"
        resp = requests.post(
            f"{self.settings.ollama_url}/api/chat", json=payload,
            timeout=self.settings.ollama_timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()
        return (data.get("message") or {}).get("content", "") or data.get("response", "")

    def decide(self, snapshot: MarketSnapshot, portfolio: dict[str, Any],
               recent: list[dict[str, Any]]) -> Decision:
        """Chamada síncrona (executar via ``run_in_executor``)."""
        try:
            raw = self.chat(self.system_prompt(), self.user_prompt(snapshot, portfolio, recent))
        except requests.Timeout:
            return Decision.hold("Timeout a contactar o Ollama", error="timeout")
        except requests.RequestException as exc:
            return Decision.hold(f"Erro de ligação ao Ollama: {exc}", error="connection")
        except ValueError as exc:
            return Decision.hold(f"Resposta inválida do Ollama: {exc}", error="bad_response")
        return parse_decision(raw)
