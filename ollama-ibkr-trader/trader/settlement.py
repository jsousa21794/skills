"""Settlement: cada decisão só é avaliada quando o horizonte expira.

Adaptado do ``memory/settlement.py`` do TradingAgents (Apache-2.0) a um
horizonte de minutos: para cada decisão sem ``settled_ts`` cujo ``ts`` já
tenha ``horizon`` minutos, procura-se o preço do ativo e do benchmark nesse
instante, calcula-se o retorno, o alpha e um rótulo ``correct``.

Rótulo: para BUY/SELL com níveis de stop/TP conhecidos, o rótulo é o do evento
negociado no percurso posterior à vela da decisão (TP tocado antes do stop = 1,
stop antes = 0); sem toque em nenhum dentro do horizonte, a decisão fica
censurada (``correct`` NULL) e não entra na calibração. Só sem níveis (ou para
HOLD) se usa o rótulo direcional: BUY correta se retorno >= +limiar; SELL
correta se <= -limiar; HOLD correta se |retorno| < limiar de oportunidade
perdida. O limiar é metade da
distância do stop em % quando há ATR, com um mínimo de 0,1%, e nunca abaixo
do custo estimado ida+volta em % (``cost_pct``): um movimento que não paga a
comissão é prejuízo, não acerto.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from .config import Settings
from .database import Database
from .indicators import Bar

log = logging.getLogger("trader.settlement")

PriceAt = Callable[[str, datetime], Optional[float]]
BarsBetween = Callable[[str, datetime, datetime], list[Bar]]


def first_touch_label(bars: list[Bar], entry: float, stop_pct: float, tp_pct: float, direction: int) -> Optional[int]:
    """1 se o TP é tocado antes do stop, 0 se o stop é tocado antes, None se nenhum (trajetória).

    Regras idênticas às do replay (V09/A10): primeiro os eventos observáveis na ABERTURA de cada vela
    (gap pelo stop → 0, gap pelo TP → 1) e só depois a ambiguidade intrabar, em que o pior caso prevalece.
    """
    if not bars or entry <= 0 or stop_pct <= 0 or tp_pct <= 0:
        return None
    stop = entry * (1 - direction * stop_pct / 100.0)
    tp = entry * (1 + direction * tp_pct / 100.0)
    for b in bars:
        if direction > 0:
            gap_stop, gap_tp, hit_stop, hit_tp = b.open <= stop, b.open >= tp, b.low <= stop, b.high >= tp
        else:
            gap_stop, gap_tp, hit_stop, hit_tp = b.open >= stop, b.open <= tp, b.high >= stop, b.low <= tp
        if gap_stop:
            return 0
        if gap_tp:
            return 1
        if hit_stop:
            return 0  # inclui o caso ambíguo na mesma vela: assume-se o pior caso
        if hit_tp:
            return 1
    return None


def first_touch_absolute(bars: list[Bar], stop: float, tp: float, direction: int) -> Optional[int]:
    """Como ``first_touch_label`` mas com níveis ABSOLUTOS (os da ordem realmente colocada) (W06)."""
    if not bars or stop <= 0 or tp <= 0:
        return None
    for b in bars:
        if direction > 0:
            gap_stop, gap_tp, hit_stop, hit_tp = b.open <= stop, b.open >= tp, b.low <= stop, b.high >= tp
        else:
            gap_stop, gap_tp, hit_stop, hit_tp = b.open >= stop, b.open <= tp, b.high >= stop, b.low <= tp
        if gap_stop:
            return 0
        if gap_tp:
            return 1
        if hit_stop:
            return 0
        if hit_tp:
            return 1
    return None


def label(action: str, ret_pct: float, threshold_pct: float, missed_pct: float) -> Optional[int]:
    if action == "BUY":
        if ret_pct >= threshold_pct:
            return 1
        if ret_pct <= -threshold_pct:
            return 0
        return None  # neutro: não conta para calibração direcional
    if action == "SELL":
        if ret_pct <= -threshold_pct:
            return 1
        if ret_pct >= threshold_pct:
            return 0
        return None
    return 0 if abs(ret_pct) >= missed_pct else 1


class Settler:
    def __init__(self, settings: Settings, db: Database, price_at: PriceAt,
                 bars_between: Optional[BarsBetween] = None) -> None:
        self.s = settings
        self.db = db
        self.price_at = price_at
        self.bars_between = bars_between

    @staticmethod
    def _ledger_label(trade: dict[str, Any], now: datetime) -> tuple[Optional[int], Optional[str]]:
        """Rótulo de uma operação pelo que REALMENTE aconteceu: saída por TP = 1, por stop = 0; outras saídas
        (sinal contrário, fim de dia, reconciliação) não respondem à pergunta "TP antes do stop" e ficam
        censuradas com a razão explícita; ainda aberta = (None, None) para avaliar pelos níveis absolutos."""
        if trade.get("status") != "CLOSED":
            return None, None
        reason = (trade.get("exit_reason") or "").upper()
        # Saída MISTA (ex.: "TP+SL", razões na ordem cronológica das execuções): o PRIMEIRO toque responde à pergunta
        # "TP antes do stop", tal como no rótulo por trajetória de preço; a fonte guarda a sequência completa (AB06).
        first = reason.split("+")[0]
        if first == "TP":
            return 1, f"ledger:{reason}"
        if first == "SL":
            return 0, f"ledger:{reason}"
        return None, f"ledger:{reason or 'OUTRO'}"

    def finalize_provisional(self) -> int:
        """Rótulos PROVISÓRIOS (operação ainda aberta no horizonte) passam a FINAIS quando o trade fecha, pelo
        ledger: o resultado é o mesmo qualquer que seja a cadência do avaliador (X06)."""
        finalized = 0
        for d in self.db.provisional_decisions():
            trade = self.db.trade_for_decision(int(d["id"]))
            if not trade or trade.get("status") != "CLOSED":
                continue
            label, source = self._ledger_label(trade, datetime.now(timezone.utc))
            self.db.finalize_label(int(d["id"]), correct=label, label_source=source or "ledger:OUTRO")
            finalized += 1
        if finalized:
            log.info("Settlement: %d rótulos provisórios finalizados pelo ledger.", finalized)
        return finalized

    def run(self, now: Optional[datetime] = None) -> int:
        now = now or datetime.now(timezone.utc)
        horizon = timedelta(minutes=self.s.settlement_horizon_minutes)
        self.finalize_provisional()
        pending = self.db.unsettled_decisions(now - horizon)
        settled = 0
        for d in pending:
            # Instante de OBSERVAÇÃO (fecho da vela, market_ts) vs instante de DECISÃO (fim da inferência, ts)
            # vs instante de EXECUÇÃO (entrada real do trade). O rótulo do bracket usa o mais tardio
            # conhecido: a entrada real quando a decisão foi executada, senão o fim da inferência (V09).
            observed = datetime.fromisoformat(d["market_ts"] or d["ts"])
            decided = datetime.fromisoformat(d["ts"])
            if decided.tzinfo is None:
                decided = decided.replace(tzinfo=timezone.utc)
            ts = observed
            target = ts + horizon
            path_start, entry_price, label_source = max(observed, decided), float(d["price"]), "bracket:vela"
            trade = self.db.trade_for_decision(int(d["id"])) if d.get("executed") else None
            ledger_label: Optional[int] = None
            ledger_source: Optional[str] = None
            final = True
            if trade and trade.get("entry_ts") and trade.get("entry_price"):
                # Operação EXECUTADA: o rótulo vem do ledger (saída real por TP/SL) e, enquanto aberta, dos
                # níveis ABSOLUTOS persistidos na ordem, no intervalo em que a posição esteve aberta (W06).
                ledger_label, ledger_source = self._ledger_label(trade, now)
                if ledger_label is None and ledger_source is None and trade["status"] == "OPEN":
                    path_start = datetime.fromisoformat(trade["entry_ts"]).replace(second=0, microsecond=0) + timedelta(minutes=1)
                    entry_price = float(trade["entry_price"])
                    label_source = "bracket:entrada"
                if trade["status"] == "OPEN" and datetime.fromisoformat(trade["entry_ts"]) + horizon > now:
                    continue  # ainda dentro do horizonte da operação: espera pelo desfecho real
                if trade["status"] == "OPEN":
                    final = False  # rótulo PROVISÓRIO: será finalizado pelo ledger quando o trade fechar (X06)
            price = self.price_at(d["symbol"], target)
            if price is None:
                if now - ts > timedelta(hours=36):
                    # Sem dados nunca mais: fecha como neutra para não bloquear a fila.
                    self.db.settle_decision(d["id"], settled_price=float(d["price"]), settled_return=0.0,
                                            bench_return=None, alpha=None, correct=None,
                                            horizon_min=self.s.settlement_horizon_minutes)
                    settled += 1
                continue
            entry = float(d["price"])
            ret = (price - entry) / entry * 100.0
            bench_ret = None
            alpha = None
            if self.s.benchmark_symbol:
                b0 = self.price_at(self.s.benchmark_symbol, ts)
                b1 = self.price_at(self.s.benchmark_symbol, target)
                if b0 and b1:
                    bench_ret = (b1 - b0) / b0 * 100.0
                    direction = 1 if d["action"] == "BUY" else (-1 if d["action"] == "SELL" else 0)
                    alpha = direction * ret - direction * bench_ret if direction else None
            atr = d.get("atr")
            thr = max(0.1, (atr / entry * 100.0) * self.s.atr_stop_multiple / 2) if atr else 0.2
            # Um movimento que não cobre o custo ida+volta não é um acerto.
            cost_pct = float(d.get("cost_pct") or 0.0)
            thr = max(thr, cost_pct)
            missed = max(thr * 2, 0.5)
            correct: Optional[int]
            # Rótulo do evento realmente negociado: o bracket (primeiro toque em TP ou stop) no percurso
            # POSTERIOR à vela da decisão (market_ts é o fecho dessa vela). Sem toque em nenhuma barreira
            # dentro do horizonte, a decisão fica CENSURADA (correct=None): a probabilidade calibrada
            # significa sempre "TP antes do stop" e nunca mistura o rótulo direcional (N10/F29).
            if ledger_source is not None:
                correct, label_source = ledger_label, ledger_source
            elif trade and trade.get("stop_price") and trade.get("tp_price") and self.bars_between is not None:
                direction = 1 if d["action"] == "BUY" else -1
                end = max(target, datetime.fromisoformat(trade["entry_ts"]) + horizon)
                path = [b for b in self.bars_between(d["symbol"], path_start, end) if b.time >= path_start]
                correct = first_touch_absolute(path, float(trade["stop_price"]), float(trade["tp_price"]), direction)
                if correct is None:
                    label_source = "censurado"
            elif d["action"] in ("BUY", "SELL") and self.bars_between is not None and d.get("stop_pct") and d.get("tp_pct"):
                path = [b for b in self.bars_between(d["symbol"], path_start, target) if b.time >= path_start]
                direction = 1 if d["action"] == "BUY" else -1
                correct = first_touch_label(path, entry_price, float(d["stop_pct"]), float(d["tp_pct"]), direction)
                if correct is None:
                    label_source = "censurado"
            else:
                correct = label(d["action"], ret, thr, missed)
                label_source = "direcional"
            self.db.settle_decision(d["id"], settled_price=price, settled_return=round(ret, 4),
                                    bench_return=round(bench_ret, 4) if bench_ret is not None else None,
                                    alpha=round(alpha, 4) if alpha is not None else None,
                                    correct=correct, horizon_min=self.s.settlement_horizon_minutes,
                                    label_source=label_source, final=final)
            settled += 1
        if settled:
            log.info("Settlement: %d decisões avaliadas a %d min.", settled, self.s.settlement_horizon_minutes)
        return settled
