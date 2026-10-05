"""Motor de trading: loop ``asyncio`` numa thread dedicada.

Separação de responsabilidades:
- A GUI (thread principal, Tkinter) nunca chama ``ib_async`` diretamente;
  envia pedidos através de ``TradingEngine.call()`` (``run_coroutine_threadsafe``).
- O motor publica estado/logs no ``UIBus`` e nunca toca em widgets.

Ciclo (a cada ``cycle_seconds``):
  settlement de decisões vencidas -> gates globais (kill-switch, protections,
  VIX) -> por ativo: dados frescos? cadência do LLM? -> contexto (velas
  agregadas, indicadores, dinâmica, ATR, sentimento, vol prevista) -> LLM (N
  amostras, duas etapas) -> registo -> persistência do sinal -> camada de
  risco (gates por ativo, sizing por ATR, custos, vetos) -> bracket.

O LLM propõe; o código decide.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Coroutine, Optional
from zoneinfo import ZoneInfo

from .analytics import Analytics
from .calibration import Calibrator, ConfidenceSignals, execution_threshold
from .config import Settings
from .database import Database
from .ibkr_client import IBKRClient
from .indicators import Bar, MarketSnapshot, aggregate_bars, atr as atr_fn, build_snapshot, compute_dynamics, Dynamics
from .lessons import LessonEngine
from .market_data import EventData
from .ollama_brain import Decision, DecisionOutcome, OllamaBrain
from .retrospective import Retrospective
from .risk import PositionSizer, RiskGate, commission as estimate_commission, round_trip_cost
from .sentiment import SentimentScorer
from .settlement import Settler
from .ui_bus import UIBus
from .volmodel import VolatilityForecaster

log = logging.getLogger("trader.engine")
NY = ZoneInfo("America/New_York")


def in_regular_hours(now_utc: datetime) -> bool:
    local = now_utc.astimezone(NY)
    if local.weekday() >= 5:
        return False
    return dtime(9, 30) <= local.time() < dtime(16, 0)


@dataclass
class DecisionContext:
    snapshot: MarketSnapshot
    dynamics: Optional[Dynamics]
    atr: Optional[float]
    hour_ny: int
    agg_bars: list[Bar]
    closes: list[float]


def build_decision_context(settings: Settings, symbol: str, bars_1m: list[Bar], equity: float = 0.0,
                           drop_forming: bool = True) -> Optional[DecisionContext]:
    """Agrega velas, calcula indicadores e dinâmica sobre a última vela fechada."""
    if not bars_1m:
        return None
    closed = bars_1m[:-1] if (drop_forming and len(bars_1m) > 1) else bars_1m
    agg = aggregate_bars(closed, settings.decision_bar_minutes)
    if len(agg) < settings.sma_slow + 5:
        return None
    closes = [b.close for b in agg]
    volumes = [b.volume for b in agg]
    snapshot = build_snapshot(symbol, closes, volumes, agg[-1].time, rsi_period=settings.rsi_period,
                              sma_fast_period=settings.sma_fast, sma_slow_period=settings.sma_slow,
                              ema_period=settings.ema_period)
    if snapshot is None:
        return None
    dynamics = compute_dynamics(closes, agg, rsi_period=settings.rsi_period, sma_fast_period=settings.sma_fast,
                                sma_slow_period=settings.sma_slow, ema_period=settings.ema_period,
                                atr_period=settings.atr_period)
    return DecisionContext(snapshot, dynamics, atr_fn(agg, settings.atr_period),
                           agg[-1].time.astimezone(NY).hour, agg, closes)


class TradingEngine:
    def __init__(self, settings: Settings, db: Database, bus: UIBus, brain: OllamaBrain) -> None:
        self.settings = settings
        self.db = db
        self.bus = bus
        self.brain = brain
        self.ibkr = IBKRClient(settings, on_bar=self._on_bar, on_fill=self._on_fill,
                               on_order_status=self._on_order_status, on_disconnect=self._on_disconnect,
                               on_commission=self._on_commission)
        self.events = EventData(db, settings.finnhub_api_key, settings.news_source)
        self.sizer = PositionSizer(settings)
        self.gate = RiskGate(settings, db, self.events)
        self.calibrator = Calibrator(settings, db)
        self.settler = Settler(settings, db, self._price_at)
        self.lessons = LessonEngine(settings, db)
        self.analytics = Analytics(settings, db)
        self.retro = Retrospective(settings, db, brain, self.settler, self.lessons, self.calibrator, self.analytics)
        self.sentiment = SentimentScorer(settings.sentiment_model) if settings.sentiment_enabled else None
        self.volmodel = VolatilityForecaster(settings.volmodel_name, settings.volmodel_enabled)

        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._stop_event: Optional[asyncio.Event] = None
        self._decision_lock: Optional[asyncio.Lock] = None

        self.trading_enabled = False
        self.ollama_ok = False
        self.risk_multiplier = 0.5  # fase de aprendizagem até os gates passarem
        self.gates_passed = False
        self._last_llm: dict[str, datetime] = {}
        self._last_decided_bar: dict[str, datetime] = {}
        self._persistence: dict[str, list[str]] = {}
        self._pending_close: dict[str, int] = {}
        self._last_weekly_report: Optional[date] = None
        self._load_gate_state()
        self.db.record_experiment("config", json.dumps({
            "model": brain.model, "threshold": settings.min_confidence, "edge_margin": settings.edge_margin,
            "samples": settings.llm_samples, "two_stage": settings.llm_two_stage, "interval": settings.llm_interval_minutes}))

    # ------------------------------------------------------------ threading
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run_thread, name="trading-engine", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)

    def _run_thread(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
        except Exception as exc:  # noqa: BLE001
            log.exception("Motor terminou com erro: %s", exc)
        finally:
            try:
                self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            finally:
                self.loop.close()

    def call(self, coro: Coroutine[Any, Any, Any]) -> Optional["asyncio.Future[Any]"]:
        if self.loop is None or self.loop.is_closed():
            coro.close()
            return None
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def shutdown(self, timeout: float = 8.0) -> None:
        fut = self.call(self._shutdown())
        if fut is not None:
            try:
                fut.result(timeout=timeout)
            except Exception:  # noqa: BLE001
                pass
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # ----------------------------------------------------------------- main
    async def _main(self) -> None:
        self._stop_event = asyncio.Event()
        self._decision_lock = asyncio.Lock()
        self._ready.set()
        log.info("Motor iniciado: modelo %s | LLM a cada %d min, %d amostras, %s | risco/trade %.2f%% ×%.1f | "
                 "stop %s | calibração %s | prompt v%d",
                 self.brain.model, self.settings.llm_interval_minutes, self.settings.llm_samples,
                 "2 etapas" if self.settings.llm_two_stage else "1 etapa", self.settings.risk_per_trade_pct * 100,
                 self.risk_multiplier, self.settings.stop_mode, "Platt" if self.calibrator.is_fitted else "heurística",
                 self.brain.prompt_version)
        await self._refresh_models()
        self.bus.emit("equity_history", points=self.equity_history())
        tasks = [
            asyncio.create_task(self._cycle_loop(), name="cycle"),
            asyncio.create_task(self._portfolio_loop(), name="portfolio"),
            asyncio.create_task(self._connection_loop(), name="connection"),
            asyncio.create_task(self._retro_scheduler(), name="retro"),
            asyncio.create_task(self._health_loop(), name="health"),
        ]
        await self._stop_event.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.ibkr.disconnect()
        log.info("Motor de trading parado.")

    async def _shutdown(self) -> None:
        self.trading_enabled = False
        if self._stop_event:
            self._stop_event.set()

    # ------------------------------------------------------------ comandos
    async def start_trading(self) -> None:
        if self.trading_enabled:
            return
        self.trading_enabled = True
        log.info("Ciclo de trading INICIADO. Ativos: %s", ", ".join(self.settings.symbols))
        await self._ensure_connected()
        self._emit_status()

    async def stop_trading(self) -> None:
        self.trading_enabled = False
        log.info("Ciclo de trading PARADO. Posições abertas mantêm os seus Brackets (GTC) na IBKR.")
        self._emit_status()

    async def set_model(self, model: str) -> None:
        if model and model != self.brain.model:
            self.brain.set_model(model)
            log.info("Modelo Ollama alterado para %s (conta como nova experiência: N=%d)", model, self.db.experiment_count())
            self._emit_status()

    async def set_symbols(self, symbols: list[str]) -> None:
        cleaned = sorted({s.strip().upper() for s in symbols if s.strip()})
        if not cleaned:
            return
        self.settings.symbols = cleaned
        self.settings.save()
        log.info("Ativos atualizados: %s", ", ".join(cleaned))
        if self.ibkr.connected:
            for symbol in cleaned:
                await self.ibkr.subscribe_bars(symbol)

    async def set_mode(self, mode: str, confirmed: bool = False) -> bool:
        """Alterna entre paper (7497) e real (7496). Modo real exige confirmação explícita."""
        mode = "live" if mode == "live" else "paper"
        if mode == self.settings.trading_mode and self.ibkr.connected:
            return True
        if mode == "live" and not confirmed:
            log.error("Modo REAL recusado: falta confirmação explícita.")
            return False
        was_trading = self.trading_enabled
        self.trading_enabled = False
        await self.ibkr.disconnect()
        self._last_llm.clear()
        self._persistence.clear()
        self.settings.trading_mode = mode
        self.settings.live_confirmed = bool(mode == "live" and confirmed)
        self.settings.save()
        self.db.record_experiment("config", f"mode={mode}")
        if mode == "live":
            log.critical("MODO REAL ATIVADO (porta %d). Ordens com dinheiro real. Camada de risco: %.2f%%/trade, "
                         "kill-switch %.0f%%/dia, StoplossGuard %d stops.", self.settings.ib_port,
                         self.settings.risk_per_trade_pct * 100, self.settings.daily_loss_limit_pct * 100,
                         self.settings.stoploss_guard_count)
        else:
            log.warning("Modo PAPER ativado (porta %d).", self.settings.ib_port)
        ok = await self._ensure_connected()
        self.trading_enabled = was_trading and ok
        self._emit_status()
        return ok

    def equity_history(self, hours: int = 48) -> list[tuple[str, float]]:
        rows = self.db.equity_series(datetime.now(timezone.utc) - timedelta(hours=hours))
        return [(ts.isoformat(), v) for ts, v in rows]

    async def run_retrospective(self) -> dict[str, Any]:
        log.info("Retrospetiva manual iniciada…")
        report = await self.retro.run()
        self._apply_gates(report.get("gates"))
        self.bus.emit("retrospective", report=report)
        self._emit_status()
        return report

    async def run_statistical_report(self) -> dict[str, Any]:
        self.settler.run()
        report = self.analytics.build_report()
        self._apply_gates(report["gates"])
        md = Analytics.render_markdown(report)
        path = self.settings.log_path().with_name("relatorio_estatistico.md")
        try:
            path.write_text(md, encoding="utf-8")
            html = self.analytics.try_quantstats_html(str(path.with_suffix(".html")))
            log.warning("Relatório estatístico guardado em %s%s", path, " (+ HTML quantstats)" if html else "")
        except OSError as exc:
            log.error("Não foi possível guardar o relatório: %s", exc)
        for line in md.splitlines()[:16]:
            if line.strip():
                log.info(line, extra={"category": "ollama"})
        self.bus.emit("report", markdown=md, report=report)
        self._emit_status()
        return report

    async def refresh_models(self) -> None:
        await self._refresh_models()

    async def _refresh_models(self) -> None:
        loop = asyncio.get_running_loop()
        models = await loop.run_in_executor(None, self.brain.list_models)
        self.ollama_ok = bool(models)
        if models and self.brain.model not in models:
            log.warning("Modelo %s não está instalado no Ollama; a usar %s", self.brain.model, models[0])
            self.brain.set_model(models[0])
        self.bus.emit("models", models=models, current=self.brain.model)
        self._emit_status()

    # -------------------------------------------------------------- loops
    async def _ensure_connected(self) -> bool:
        was_connected = self.ibkr.connected
        if not was_connected:
            if not await self.ibkr.connect():
                return False
        for symbol in list(self.settings.symbols) + ([self.settings.benchmark_symbol] if self.settings.benchmark_symbol else []):
            await self.ibkr.subscribe_bars(symbol)
        if not was_connected:
            await self._reconcile()
        self._emit_status()
        return True

    async def _reconcile(self) -> None:
        """Após (re)ligar: SQLite vs IBKR. Fecha trades órfãos e protege posições sem stop."""
        positions = {p["symbol"]: p for p in self.ibkr.portfolio_state().get("positions", [])}
        for trade in self.db.open_trades():
            if trade["symbol"] not in positions and float(trade["filled_qty"] or 0) > 0:
                self.db.close_trade_reconciled(trade["id"])
                log.warning("Reconciliação: trade #%d %s sem posição na IBKR -> fechado como RECONCILED.",
                            trade["id"], trade["symbol"])
        for symbol, pos in positions.items():
            if symbol == self.settings.benchmark_symbol or self.ibkr.has_protective_orders(symbol):
                continue
            bars = self.ibkr.bars_as_list(symbol)
            price = pos.get("market_price") or pos.get("avg_cost") or 0.0
            action = "BUY" if pos["qty"] > 0 else "SELL"
            sizing = self.sizer.size(action=action, price=price, equity=max(price * abs(pos["qty"]), 1.0),
                                     bars=aggregate_bars(bars, self.settings.decision_bar_minutes), risk_pct=1.0)
            if sizing and price > 0:
                await self.ibkr.ensure_protection(symbol, stop_price=sizing.stop_price, tp_price=sizing.tp_price)
            else:
                sign = 1 if pos["qty"] > 0 else -1
                await self.ibkr.ensure_protection(symbol, stop_price=price * (1 - sign * self.settings.stop_loss_pct),
                                                  tp_price=price * (1 + sign * self.settings.take_profit_pct))

    async def _connection_loop(self) -> None:
        backoff = 5
        while True:
            try:
                if self.trading_enabled and not self.ibkr.connected:
                    if await self._ensure_connected():
                        backoff = 5
                    else:
                        log.info("Nova tentativa de ligação em %ds…", backoff)
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 60)
                        continue
            except Exception as exc:  # noqa: BLE001
                log.exception("Erro no loop de ligação: %s", exc)
            await asyncio.sleep(3)

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            loop = asyncio.get_running_loop()
            ok = await loop.run_in_executor(None, self.brain.is_available)
            if ok != self.ollama_ok:
                self.ollama_ok = ok
                log.log(logging.INFO if ok else logging.ERROR, "Ollama %s",
                        "disponível" if ok else "INDISPONÍVEL em " + self.settings.ollama_url)
                if ok:
                    await self._refresh_models()
            self._emit_status()

    async def _portfolio_loop(self) -> None:
        tick = 0
        while True:
            try:
                state = self.ibkr.portfolio_state()
                now = datetime.now(timezone.utc)
                open_trades = {t["symbol"]: t for t in self.db.open_trades()}
                for pos in state.get("positions", []):
                    trade = open_trades.get(pos["symbol"])
                    pos["stop"] = trade["stop_price"] if trade else None
                    pos["tp"] = trade["tp_price"] if trade else None
                self.gate.update_day_baseline(state.get("net_liq"), now)
                day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                state["commissions_today"] = round(self.db.commissions_since(day_start), 2)
                pct = self.gate.day_pnl_pct(state.get("net_liq"))
                state["day_pnl_pct"] = round(pct, 3) if pct is not None else None
                self.bus.emit("portfolio", **state)
                if state["connected"] and tick % 20 == 0:
                    self.db.snapshot_pnl(net_liq=state["net_liq"], cash=state["cash"],
                                         unrealized=state["unrealized"], realized=state["realized"])
            except Exception as exc:  # noqa: BLE001
                log.exception("Erro no loop de portefólio: %s", exc)
            tick += 1
            await asyncio.sleep(3)

    async def _cycle_loop(self) -> None:
        while True:
            try:
                if self.ibkr.connected:
                    self.settler.run()
                if self.trading_enabled and self.ibkr.connected:
                    await self._run_cycle()
            except Exception as exc:  # noqa: BLE001
                log.exception("Erro no ciclo de decisão: %s", exc)
            await asyncio.sleep(max(5, self.settings.cycle_seconds))

    async def _retro_scheduler(self) -> None:
        while True:
            delay = self._seconds_until_retro()
            log.info("Próxima retrospetiva em %.1f h", delay / 3600)
            await asyncio.sleep(delay)
            try:
                today = datetime.now().date()
                weekly = today.weekday() == self.settings.weekly_report_weekday and self._last_weekly_report != today
                report = await self.retro.run(full_report=weekly)
                if weekly:
                    self._last_weekly_report = today
                    await self.run_statistical_report()
                self._apply_gates(report.get("gates"))
                self.bus.emit("retrospective", report=report)
                self._emit_status()
            except Exception as exc:  # noqa: BLE001
                log.exception("Retrospetiva falhou: %s", exc)
            await asyncio.sleep(61)

    def _seconds_until_retro(self) -> float:
        hour, minute = (int(x) for x in self.settings.retro_time_local.split(":"))
        now = datetime.now().astimezone()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    # ----------------------------------------------------- ciclo decisão
    async def _run_cycle(self) -> None:
        assert self._decision_lock is not None
        now = datetime.now(timezone.utc)
        if self.settings.trade_only_rth and not in_regular_hours(now):
            return
        state = self.ibkr.portfolio_state()
        global_gate = self.gate.check_global(equity=state.get("net_liq"), now=now)
        state["day_pnl_pct"] = self.gate.day_pnl_pct(state.get("net_liq"))
        for symbol in list(self.settings.symbols):
            if not self.trading_enabled or not self.ibkr.connected:
                return
            async with self._decision_lock:
                await self._process_symbol(symbol, state, now, global_gate)

    async def _process_symbol(self, symbol: str, state: dict[str, Any], now: datetime, global_gate: Any) -> None:
        bars = self.ibkr.bars_as_list(symbol)
        if not bars:
            await self.ibkr.subscribe_bars(symbol)
            return
        last_time = bars[-2].time if len(bars) > 1 else bars[-1].time
        if (now - last_time).total_seconds() > self.settings.max_bar_age_seconds + 60:
            log.debug("%s: dados desatualizados (%s).", symbol, last_time.isoformat())
            return
        last_llm = self._last_llm.get(symbol)
        if last_llm and now - last_llm < timedelta(minutes=self.settings.llm_interval_minutes):
            return
        ctx = build_decision_context(self.settings, symbol, bars, state.get("net_liq") or 0.0)
        if ctx is None:
            log.debug("%s: histórico insuficiente para indicadores.", symbol)
            return
        if self._last_decided_bar.get(symbol) == ctx.snapshot.bar_time:
            return
        self._last_llm[symbol] = now
        self._last_decided_bar[symbol] = ctx.snapshot.bar_time

        # Features opcionais (fase 3) e lições relevantes.
        loop = asyncio.get_running_loop()
        sentiment: Optional[tuple[Optional[float], int]] = None
        if self.sentiment is not None:
            headlines = [n["title"] for n in await loop.run_in_executor(
                None, self.events.recent_news, symbol, self.settings.sentiment_window_hours)]
            sentiment = await loop.run_in_executor(None, self.sentiment.score_news, headlines)
        vol_width, vol_source = await loop.run_in_executor(
            None, self.volmodel.interval_width_pct, ctx.closes, self.settings.volmodel_horizon_bars)
        lessons = self.lessons.for_prompt(symbol, ctx.snapshot.rsi, ctx.hour_ny, ctx.snapshot.trend_label())
        recent = self.db.recent_decisions(5, symbol)
        model = self.brain.next_ab_model() if self.settings.ab_test_models else None

        outcome: DecisionOutcome = await loop.run_in_executor(
            None, lambda: self.brain.decide(ctx.snapshot, state, recent, dynamics=ctx.dynamics, lessons=lessons,
                                            sentiment=sentiment, vol_width=vol_width, model=model))
        if outcome.review and outcome.decision.error == "review":
            log.error("[%s] REVIEW: %s. A repetir uma vez a temperatura %.1f.", symbol, outcome.decision.razao,
                      self.settings.review_retry_temperature)
            outcome = await loop.run_in_executor(
                None, lambda: self.brain.decide(ctx.snapshot, state, recent, dynamics=ctx.dynamics, lessons=lessons,
                                                sentiment=sentiment, vol_width=vol_width, model=model,
                                                temperature_override=self.settings.review_retry_temperature))
        self.ollama_ok = outcome.decision.error not in ("timeout", "connection")

        decision = outcome.decision
        position_qty = self.ibkr.position_qty(symbol)
        cost_pct = self._estimated_cost_pct(ctx, state)
        signals = ConfidenceSignals(decision.confianca, outcome.agree_frac, outcome.margin, outcome.action_logprob)
        calibrated = self.calibrator.probability(signals) if decision.acao in ("BUY", "SELL") else None
        decision_id = self.db.insert_decision(
            symbol=symbol, model=(outcome.models[0] if outcome.models else self.brain.model), action=decision.acao,
            confidence=decision.confianca, reason=decision.razao, snapshot=ctx.snapshot.to_dict(),
            position_qty=position_qty, net_liq=state.get("net_liq"), prompt_version=self.brain.prompt_version,
            parse_ok=decision.parse_ok, raw_response=decision.raw,
            extra={"verbal_conf": decision.confianca, "agree_frac": outcome.agree_frac,
                   "action_logprob": outcome.action_logprob, "calibrated_prob": calibrated,
                   "n_samples": len(outcome.samples), "samples_json": outcome.samples, "prompt_hash": outcome.prompt_hash,
                   "dynamics_json": ctx.dynamics.to_dict() if ctx.dynamics else None, "atr": ctx.atr,
                   "sentiment": sentiment[0] if sentiment else None, "vol_forecast": vol_width,
                   "review": int(outcome.review), "hour_ny": ctx.hour_ny, "regime": ctx.snapshot.trend_label(),
                   "cost_pct": cost_pct},
        )
        level = logging.ERROR if outcome.review else (logging.INFO if decision.parse_ok else logging.WARNING)
        log.log(level, "[%s] %s verbal=%.2f acordo=%.0f%% p=%s (%.0fs, %d amostras%s) | RSI=%s ATR=%s%% | %s",
                symbol, decision.acao, decision.confianca, outcome.agree_frac * 100,
                f"{calibrated:.2f}" if calibrated is not None else "n/a", outcome.elapsed, len(outcome.samples),
                f", logprob {outcome.action_logprob:.2f}" if outcome.action_logprob is not None else "",
                f"{ctx.snapshot.rsi:.0f}" if ctx.snapshot.rsi is not None else "n/d",
                f"{ctx.dynamics.atr_pct:.2f}" if ctx.dynamics and ctx.dynamics.atr_pct is not None else "n/d",
                decision.razao, extra={"category": "ollama"})
        self.bus.emit("decision", decision_id=decision_id, symbol=symbol, decision=decision.to_dict(),
                      snapshot=ctx.snapshot.to_dict(), agree_frac=outcome.agree_frac, calibrated=calibrated,
                      model=(outcome.models[0] if outcome.models else self.brain.model))
        if outcome.review:
            self.db.mark_decision(decision_id, executed=False, skip_reason="REVIEW")
            self.bus.emit("decision_result", decision_id=decision_id, executed=False, reason="REVIEW")
            return
        await self._execute(outcome, calibrated, ctx, state, decision_id, position_qty, global_gate)

    # ------------------------------------------------------------ execução
    async def _execute(self, outcome: DecisionOutcome, calibrated: Optional[float], ctx: DecisionContext,
                       state: dict[str, Any], decision_id: int, position_qty: float, global_gate: Any) -> None:
        decision = outcome.decision
        symbol = ctx.snapshot.symbol
        action = decision.acao
        now = datetime.now(timezone.utc)

        def skip(reason: str) -> None:
            self.db.mark_decision(decision_id, executed=False, skip_reason=reason)
            self.bus.emit("decision_result", decision_id=decision_id, executed=False, reason=reason)
            if action != "HOLD":
                log.info("[%s] %s não executado: %s", symbol, action, reason)

        # Persistência do sinal entre ciclos LLM.
        hist = self._persistence.setdefault(symbol, [])
        hist.append(action)
        del hist[:-max(1, self.settings.signal_persistence_cycles)]

        if action == "HOLD":
            return skip("HOLD")
        if not decision.parse_ok:
            return skip("resposta inválida")
        if outcome.agree_frac < self.settings.llm_min_agreement:
            return skip(f"acordo {outcome.agree_frac:.0%} < {self.settings.llm_min_agreement:.0%}")
        if len(hist) < self.settings.signal_persistence_cycles or any(a != action for a in hist):
            return skip(f"sinal ainda não persistente ({'/'.join(hist)})")
        if symbol in self._pending_close:
            return skip("fecho de posição ainda pendente")

        # Ação alinhada com posição existente -> nada (sem pirâmide).
        if (action == "BUY" and position_qty > 0) or (action == "SELL" and position_qty < 0):
            return skip("já posicionado nessa direção")

        # Ação oposta -> fecha (os filhos são cancelados), não inverte no mesmo ciclo.
        if (action == "BUY" and position_qty < 0) or (action == "SELL" and position_qty > 0):
            result = await self.ibkr.close_position(symbol)
            if result:
                group_id = self.db.insert_order_group(
                    symbol=symbol, decision_id=decision_id, role="CLOSE", direction=result["direction"],
                    qty=result["qty"], parent_order_id=result["order_id"], tp_order_id=None, sl_order_id=None,
                    ref_price=ctx.snapshot.price, tp_price=None, sl_price=None)
                self._pending_close[symbol] = group_id
                self.db.mark_decision(decision_id, executed=True)
                self.bus.emit("decision_result", decision_id=decision_id, executed=True, reason="fecho de posição")
                log.warning("[%s] Sinal %s contra posição existente: posição FECHADA (ordem %d).",
                            symbol, action, result["order_id"])
            else:
                skip("falha ao fechar posição")
            return

        # ---- Camada de risco: o código decide. ----
        if not global_gate.allowed:
            return skip(global_gate.reason)
        sym_gate = self.gate.check_symbol(symbol=symbol, now=now)
        if not sym_gate.allowed:
            return skip(sym_gate.reason)
        if action == "SELL" and not self.settings.allow_short:
            return skip("short desativado na configuração")
        if self.ibkr.has_pending_entry(symbol):
            return skip("ordem de entrada ainda pendente")
        if len(state.get("positions", [])) >= self.settings.max_open_positions:
            return skip(f"máximo de posições ({self.settings.max_open_positions}) atingido")
        sentiment_gate = self.gate.check_sentiment_veto(action, *(self._last_sentiment(decision_id)))
        if not sentiment_gate.allowed:
            return skip(sentiment_gate.reason)
        vol_gate = self.gate.check_vol_forecast(self._last_vol(decision_id))
        if not vol_gate.allowed:
            return skip(vol_gate.reason)

        equity = state.get("net_liq")
        if not equity or ctx.snapshot.price <= 0:
            return skip("NetLiq desconhecido")
        risk_pct = self.settings.risk_per_trade_pct_validated if self.gates_passed else self.settings.risk_per_trade_pct
        multiplier = global_gate.size_multiplier * sym_gate.size_multiplier
        sizing = self.sizer.size(action=action, price=ctx.snapshot.price, equity=equity, bars=ctx.agg_bars,
                                 risk_pct=risk_pct, multiplier=multiplier)
        if sizing is None:
            return skip("ATR indisponível para dimensionar")
        if sizing.qty < 1:
            return skip("quantidade < 1 ação com o risco configurado")
        # Limiar de probabilidade: break-even do bracket COM custos + margem (ou piso enquanto não calibrado).
        cost = round_trip_cost(sizing.qty, ctx.snapshot.price, self.settings)
        rr = abs(sizing.tp_price - ctx.snapshot.price) / max(sizing.stop_distance, 1e-9)
        threshold = execution_threshold(self.settings, reward_risk_ratio=rr, cost=cost, risk_amount=sizing.risk_amount,
                                        calibrated=self.calibrator.is_fitted)
        self.db.update_decision(decision_id, threshold_used=threshold)
        prob = calibrated if calibrated is not None else 0.0
        if prob < threshold:
            return skip(f"p={prob:.2f} < limiar {threshold:.2f} (break-even R:R {rr:.1f} com custo {cost:.2f} USD)")
        cost_gate = self.gate.check_costs(qty=sizing.qty, price=ctx.snapshot.price,
                                          tp_distance=abs(sizing.tp_price - ctx.snapshot.price),
                                          stop_distance=sizing.stop_distance, probability=prob)
        if not cost_gate.allowed:
            return skip(cost_gate.reason)

        result = await self.ibkr.place_bracket(symbol, action, sizing.qty, ctx.snapshot.price,
                                               stop_price=sizing.stop_price, tp_price=sizing.tp_price,
                                               trailing=self.settings.use_trailing_stop)
        if not result:
            return skip("falha ao colocar Bracket")
        direction = 1 if action == "BUY" else -1
        group_id = self.db.insert_order_group(
            symbol=symbol, decision_id=decision_id, role="ENTRY", direction=direction, qty=sizing.qty,
            parent_order_id=result["parent_order_id"], tp_order_id=result["tp_order_id"],
            sl_order_id=result["sl_order_id"], ref_price=ctx.snapshot.price,
            tp_price=result["tp_price"], sl_price=result["sl_price"])
        self.db.open_trade(symbol=symbol, decision_id=decision_id, group_id=group_id, direction=direction,
                           qty=sizing.qty, stop_price=sizing.stop_price, tp_price=sizing.tp_price,
                           risk_amount=sizing.risk_amount)
        self.db.mark_decision(decision_id, executed=True)
        self.bus.emit("decision_result", decision_id=decision_id, executed=True,
                      reason=f"{action} x{sizing.qty} SL {sizing.stop_price:.2f} TP {sizing.tp_price:.2f} · "
                             f"custo {cost:.2f} USD · ganho líquido no TP {sizing.qty * abs(sizing.tp_price - ctx.snapshot.price) - cost:.2f} USD")
        self._persistence[symbol] = []
        log.warning("[%s] EXECUTADO %s x%d @~%.2f | SL %.2f | TP %.2f | risco %.0f USD (%.2f%% ×%.2f, ATR %.2f) | "
                    "p=%.2f ≥ %.2f | %s",
                    symbol, action, sizing.qty, ctx.snapshot.price, sizing.stop_price, sizing.tp_price,
                    sizing.risk_amount, risk_pct * 100, multiplier, sizing.atr_used, prob, threshold,
                    "; ".join(sizing.notes + cost_gate.notes + global_gate.notes), extra={"category": "ordem"})

    def _estimated_cost_pct(self, ctx: DecisionContext, state: dict[str, Any]) -> Optional[float]:
        """Custo ida+volta em % do notional para a quantidade que o sizer daria agora."""
        equity = state.get("net_liq")
        if not equity or ctx.snapshot.price <= 0:
            return None
        sizing = self.sizer.size(action="BUY", price=ctx.snapshot.price, equity=equity, bars=ctx.agg_bars)
        qty = sizing.qty if sizing and sizing.qty >= 1 else 1
        return round(round_trip_cost(qty, ctx.snapshot.price, self.settings) / (qty * ctx.snapshot.price) * 100.0, 4)

    def _last_sentiment(self, decision_id: int) -> tuple[Optional[float], int]:
        row = self.db._query("SELECT sentiment FROM decisions WHERE id=?", (decision_id,))
        value = row[0]["sentiment"] if row else None
        return (value, self.settings.sentiment_veto_min_news if value is not None else 0)

    def _last_vol(self, decision_id: int) -> Optional[float]:
        row = self.db._query("SELECT vol_forecast FROM decisions WHERE id=?", (decision_id,))
        return row[0]["vol_forecast"] if row else None

    # --------------------------------------------------------------- gates
    def _apply_gates(self, gates: Optional[dict[str, Any]]) -> None:
        if not gates:
            return
        self.gates_passed = bool(gates.get("all_passed"))
        self.risk_multiplier = float(gates.get("risk_multiplier", 0.5))
        self.db.set_kv("gates_passed", "1" if self.gates_passed else "0")
        if not self.gates_passed:
            failed = [k for k, v in gates.get("checks", {}).items() if not v]
            log.warning("Gates estatísticos NÃO passam (%s). Risco por trade mantém-se em %.2f%%.",
                        ", ".join(failed), self.settings.risk_per_trade_pct * 100)
        else:
            log.warning("Gates estatísticos PASSAM. Risco por trade pode subir para %.2f%%.",
                        self.settings.risk_per_trade_pct_validated * 100)

    def _load_gate_state(self) -> None:
        self.gates_passed = self.db.get_kv("gates_passed") == "1"
        self.risk_multiplier = 1.0 if self.gates_passed else 0.5

    # ------------------------------------------------------------ callbacks
    def _price_at(self, symbol: str, when: datetime) -> Optional[float]:
        return self.ibkr.price_at(symbol, when)

    def _on_bar(self, symbol: str, bars: Any, has_new_bar: bool) -> None:
        if has_new_bar and bars:
            last = bars[-1]
            self.bus.emit("bar", symbol=symbol, time=IBKRClient.bar_time_utc(last).isoformat(),
                          close=float(last.close), volume=float(last.volume))

    def _on_disconnect(self) -> None:
        self._emit_status()

    def _on_order_status(self, trade: Any) -> None:
        status = trade.orderStatus.status
        if status in ("Cancelled", "Inactive", "ApiCancelled"):
            log.info("Ordem %d (%s %s) -> %s", trade.order.orderId, trade.order.action,
                     trade.contract.symbol, status, extra={"category": "ordem"})

    def _on_fill(self, trade: Any, fill: Any) -> None:
        execution = fill.execution
        symbol = fill.contract.symbol
        ts = execution.time if isinstance(execution.time, datetime) else datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        report = getattr(fill, "commissionReport", None)
        real = getattr(report, "commission", None) if report is not None else None
        estimated = real is None or real == 0.0
        commission_value = estimate_commission(int(execution.shares), float(execution.price), self.settings) \
            if estimated else float(real)
        new = self.db.insert_fill(exec_id=execution.execId, order_id=execution.orderId, symbol=symbol,
                                  side=execution.side, shares=float(execution.shares), price=float(execution.price), ts=ts,
                                  commission=commission_value, commission_estimated=estimated)
        if not new:
            return
        log.info("Execução %s %s x%g @ %.2f (ordem %d) · comissão %.2f USD%s", symbol, execution.side, execution.shares,
                 execution.price, execution.orderId, commission_value, " (estimada)" if estimated else "",
                 extra={"category": "ordem"})
        group = self.db.group_for_order(execution.orderId)
        if not group:
            return
        if group["role"] == "ENTRY":
            trade_row = self.db.trade_for_group(group["id"])
            if trade_row is None:
                trade_id = self.db.open_trade(symbol=symbol, decision_id=group["decision_id"], group_id=group["id"],
                                              direction=group["direction"], qty=group["qty"])
            else:
                trade_id = trade_row["id"]
            if execution.orderId == group["parent_order_id"]:
                self.db.record_entry_fill(trade_id, float(execution.shares), float(execution.price), ts, commission_value)
            else:
                reason = "TP" if execution.orderId == group["tp_order_id"] else "SL"
                updated = self.db.record_exit_fill(trade_id, float(execution.shares), float(execution.price), ts, reason,
                                                   commission_value)
                self._announce_close(updated, reason)
        elif group["role"] == "CLOSE":
            remaining = float(execution.shares)
            for row in self.db.open_trades(symbol):
                if remaining <= 0:
                    break
                portion = min(remaining, float(row["filled_qty"] or row["qty"]) - float(row["exit_qty"] or 0))
                if portion <= 0:
                    continue
                share = commission_value * portion / max(float(execution.shares), 1e-9)
                updated = self.db.record_exit_fill(row["id"], portion, float(execution.price), ts, "SIGNAL", share)
                remaining -= portion
                self._announce_close(updated, "SIGNAL")
            self._pending_close.pop(symbol, None)

    def _on_commission(self, trade: Any, fill: Any, report: Any) -> None:
        """CommissionReport real da IBKR: substitui a estimativa e corrige o P&L líquido do trade."""
        commission_value = getattr(report, "commission", None)
        exec_id = getattr(getattr(fill, "execution", None), "execId", None)
        if commission_value is None or not exec_id:
            return
        delta = self.db.set_fill_commission(exec_id, float(commission_value))
        if delta is None or abs(delta) < 1e-9:
            return
        group = self.db.group_for_order(fill.execution.orderId)
        if not group:
            return
        if group["role"] == "ENTRY":
            trade_row = self.db.trade_for_group(group["id"])
            if trade_row:
                self.db.apply_commission_delta(trade_row["id"], delta)
        else:
            rows = self.db._query("SELECT id FROM trades WHERE symbol=? ORDER BY id DESC LIMIT 1", (fill.contract.symbol,))
            if rows:
                self.db.apply_commission_delta(rows[0]["id"], delta)
        log.info("Comissão real %s: %.2f USD (ajuste %+.2f)", fill.contract.symbol, float(commission_value), delta,
                 extra={"category": "ordem"})

    def _announce_close(self, trade_row: dict[str, Any], reason: str) -> None:
        if trade_row["status"] == "CLOSED":
            pnl = float(trade_row["pnl"])
            gross = float(trade_row.get("gross_pnl") or 0.0)
            comm = float(trade_row.get("commission") or 0.0)
            log.log(logging.INFO if pnl >= 0 else logging.WARNING,
                    "Trade #%d %s fechado por %s: P&L líquido %+.2f USD (bruto %+.2f, comissões %.2f)",
                    trade_row["id"], trade_row["symbol"], reason, pnl, gross, comm, extra={"category": "ordem"})
            if gross > 0 >= pnl:
                log.warning("Trade #%d: lucro bruto comido pelas comissões (%.2f de %.2f). O gate de custos deve "
                            "ter recusado isto; verifica min_net_gain_multiple.", trade_row["id"], comm, gross)
            self.bus.emit("trade_closed", trade=trade_row)

    # ------------------------------------------------------------- helpers
    def _emit_status(self) -> None:
        now = datetime.now(timezone.utc)
        pauses = {k: v.astimezone().strftime("%H:%M") for k, v in self.gate.active_pauses(now).items()}
        self.bus.emit(
            "status",
            ibkr_connected=self.ibkr.connected,
            ollama_ok=self.ollama_ok,
            trading_enabled=self.trading_enabled,
            model=self.brain.model,
            halted=self.gate.halted,
            pauses=pauses,
            data_delayed=self.ibkr.data_delayed,
            prompt_version=self.brain.prompt_version,
            min_confidence=self.settings.min_confidence,
            lessons=len(self.db.active_lessons()),
            symbols=list(self.settings.symbols),
            calibrated=self.calibrator.is_fitted,
            calibration_n=self.calibrator.model.n_samples if self.calibrator.model else self.db.count_settled(),
            gates_passed=self.gates_passed,
            risk_pct=(self.settings.risk_per_trade_pct_validated if self.gates_passed else self.settings.risk_per_trade_pct),
            llm_interval=self.settings.llm_interval_minutes,
            llm_samples=self.settings.llm_samples,
            n_trials=self.db.experiment_count(),
            mode=self.settings.trading_mode,
            port=self.settings.ib_port,
            accounts=list(getattr(self.ibkr, "accounts", [])),
            lesson_texts=[l["text"] for l in self.db.active_lessons()[:5]],
            gates_detail=(self.db.latest_report("weekly") or {}).get("gates"),
            risk_summary={"stop_mode": self.settings.stop_mode, "atr_mult": self.settings.atr_stop_multiple,
                          "rr": self.settings.reward_risk_ratio, "daily_loss": self.settings.daily_loss_limit_pct,
                          "max_positions": self.settings.max_open_positions,
                          "cooldown": self.settings.cooldown_minutes,
                          "stoploss_guard": self.settings.stoploss_guard_count},
        )
