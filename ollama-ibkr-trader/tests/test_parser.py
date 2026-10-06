import pytest

from trader.ollama_brain import Decision, normalize_confidence, parse_decision


@pytest.mark.parametrize(
    "raw, action, conf",
    [
        ('{"acao": "BUY", "confianca": 0.82, "razao": "tendência de alta"}', "BUY", 0.82),
        ('```json\n{"acao": "SELL", "confianca": 0.7, "razao": "RSI 78"}\n```', "SELL", 0.7),
        ('Aqui está a decisão: {"acao":"HOLD","confianca":0.4,"razao":"lateral"} espero que ajude', "HOLD", 0.4),
        ("{'acao': 'BUY', 'confianca': 0.9, 'razao': 'ok'}", "BUY", 0.9),
        ('{"acao": "BUY", "confianca": "85%", "razao": "x",}', "BUY", 0.85),
        ('{"action": "sell", "confidence": 72, "reason": "momentum"}', "SELL", 0.72),
        ('{"ação": "COMPRAR", "confiança": 0.66, "razão": "cruzamento"}', "BUY", 0.66),
        ('{acao: "HOLD", confianca: 0.5, razao: "sem sinal"}', "HOLD", 0.5),
        ('{"acao": "MANTER", "confianca": 1.4, "razao": "x"}', "HOLD", 0.014),  # >1 lê-se como % (conservador)
        ('acao: BUY\nconfianca: 0,75\nrazao: breakout', "BUY", 0.75),
        ('{"razao": "aninhado {chaveta}", "acao": "SELL", "confianca": 0.8}', "SELL", 0.8),
    ],
)
def test_parse_valid_variants(raw, action, conf):
    d = parse_decision(raw)
    assert d.parse_ok, d
    assert d.acao == action
    assert d.confianca == pytest.approx(conf)


@pytest.mark.parametrize("raw", ["", None, "   ", "não sei o que fazer", "{broken json", '{"acao": "DANCE"}'])
def test_parse_failures_default_to_hold(raw):
    d = parse_decision(raw)
    assert d.acao == "HOLD"
    assert d.confianca == 0.0
    assert d.parse_ok is False
    assert d.error


def test_missing_confidence_is_zero():
    d = parse_decision('{"acao": "BUY", "razao": "sem confiança"}')
    assert d.parse_ok
    assert d.confianca == 0.0  # nunca executado: abaixo de qualquer limiar


@pytest.mark.parametrize("value, expected", [(0.5, 0.5), ("0.9", 0.9), ("80%", 0.8), (120, 1.0), (-3, 0.0)])
def test_normalize_confidence(value, expected):
    assert normalize_confidence(value) == pytest.approx(expected)


@pytest.mark.parametrize("value", [None, True, "alta", float("nan"), float("inf")])
def test_normalize_confidence_invalid_is_none(value):
    assert normalize_confidence(value) is None


def test_hold_factory():
    d = Decision.hold("x", error="timeout")
    assert d.acao == "HOLD" and not d.parse_ok and d.error == "timeout"
