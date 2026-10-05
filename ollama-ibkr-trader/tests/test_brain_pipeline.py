"""Pipeline do LLM com Ollama simulado (sem rede)."""
from datetime import datetime, timezone

from trader.config import Settings
from trader.database import Database
from trader.indicators import MarketSnapshot, compute_dynamics
from trader.ollama_brain import DECISION_SCHEMA, OllamaBrain
from tests.helpers import make_bars


class FakeOllama(OllamaBrain):
    """Responde de forma determinística em função da temperatura/seed."""

    def __init__(self, settings, db, actions=None, fail_stage1=False, broken_json=False):
        super().__init__(settings, db)
        self.calls = []
        self.actions = actions or ["BUY"]
        self.fail_stage1 = fail_stage1
        self.broken_json = broken_json

    def chat_raw(self, system, user, *, model=None, json_mode=False, schema=None, temperature=None, seed=None, logprobs=False):
        self.calls.append({"model": model, "schema": schema is not None, "temperature": temperature, "seed": seed,
                           "logprobs": logprobs, "system": system, "user": user})
        if schema is None:  # etapa 1
            action = self.actions[(seed or 0) % len(self.actions)]
            text = "A EMA cruzou a SMA rápida para cima e o RSI saiu de sobrevenda.\n"
            if not self.fail_stage1:
                text += f"DECISÃO: {action} | CONFIANÇA: 0.72 | RAZÃO: cruzamento com confirmação"
            return {"message": {"content": text}}
        # etapa 2
        if self.broken_json:
            return {"message": {"content": "não sei"}}
        import re
        m = re.search(r"DECISÃO: (\w+)", user)
        action = m.group(1) if m else self.actions[0]
        return {"message": {"content": f'{{"acao": "{action}", "confianca": 0.7, "razao": "fmt"}}'},
                "logprobs": [{"token": '{"', "logprob": -0.01}, {"token": "acao", "logprob": -0.01},
                             {"token": '":"', "logprob": -0.01}, {"token": action, "logprob": -0.22}]}


def _snapshot():
    return MarketSnapshot("AAPL", 100.0, datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc), 42.0, 99.5, 99.0, 99.8, 0.2, 0.5, 1000, 120)


def test_two_stage_pipeline_votes_and_logprob():
    s = Settings()
    s.llm_samples = 5
    s.anonymize_prompt = True
    db = Database(":memory:")
    brain = FakeOllama(s, db, actions=["BUY", "BUY", "BUY", "HOLD", "BUY"])
    out = brain.decide(_snapshot(), {"net_liq": 1e5, "positions": []}, [], lessons=["lição A"])
    assert out.decision.acao == "BUY" and out.decision.parse_ok
    assert out.agree_frac == 0.8 and out.margin == 0.6
    assert out.action_logprob == -0.22
    assert len(out.samples) == 5 and not out.review
    stage1 = [c for c in brain.calls if not c["schema"]]
    stage2 = [c for c in brain.calls if c["schema"]]
    assert len(stage1) == 5 and len(stage2) == 5
    assert all(c["temperature"] == 0.0 and c["logprobs"] for c in stage2)
    assert "AAPL" not in stage1[0]["user"] and "ATIVO-" in stage1[0]["user"]
    assert "100.00 USD" not in stage1[0]["user"]
    assert "lição A" in stage1[0]["system"]
    assert "terminad" not in stage1[0]["system"].lower()  # prompt neutro
    assert "HOLD é a resposta esperada" in stage1[0]["system"]


def test_review_sentinel_on_unparseable_majority():
    s = Settings()
    s.llm_samples = 3
    db = Database(":memory:")
    brain = FakeOllama(s, db, fail_stage1=True, broken_json=True)
    out = brain.decide(_snapshot(), {"net_liq": 1e5, "positions": []}, [])
    assert out.review and out.decision.acao == "REVIEW" and not out.decision.parse_ok


def test_ensemble_disagreement_yields_hold_and_cache_hit():
    s = Settings()
    s.llm_samples = 1
    s.ensemble_models = ["modelo-b"]
    db = Database(":memory:")

    class TwoModels(FakeOllama):
        def chat_raw(self, system, user, *, model=None, **kw):
            self.actions = ["SELL"] if model == "modelo-b" else ["BUY"]
            return super().chat_raw(system, user, model=model, **kw)

    brain = TwoModels(s, db)
    out = brain.decide(_snapshot(), {"net_liq": 1e5, "positions": []}, [], cache=db)
    assert out.decision.acao == "HOLD" and "sem acordo" in out.decision.razao
    assert out.models == [brain.model, "modelo-b"]
    n_calls = len(brain.calls)
    again = brain.decide(_snapshot(), {"net_liq": 1e5, "positions": []}, [], cache=db)
    assert again.from_cache and len(brain.calls) == n_calls


def test_single_stage_schema_and_dynamics_in_prompt():
    s = Settings()
    s.llm_two_stage = False
    s.llm_samples = 1
    s.anonymize_prompt = False
    db = Database(":memory:")
    brain = FakeOllama(s, db)
    bars = make_bars(400)
    dyn = compute_dynamics([b.close for b in bars], bars)
    out = brain.decide(_snapshot(), {"net_liq": 1e5, "positions": []}, [], dynamics=dyn)
    assert out.decision.acao == "BUY"
    call = brain.calls[0]
    assert call["schema"] and "Cruzamentos" in call["user"] and "AAPL" in call["user"]
    assert DECISION_SCHEMA["properties"]["acao"]["enum"] == ["BUY", "SELL", "HOLD"]
