"""Motor de trading: loop ``asyncio`` numa thread dedicada.

Separação de responsabilidades:
- A GUI (thread principal, Tkinter) nunca chama ``ib_async`` diretamente;
  envia pedidos através de ``TradingEngine.call()`` (``run_coroutine_threadsafe``).
- O motor publica estado/logs no ``UIBus`` e nunca toca em widgets.
- A política de execução (``policy.decide_execution``) é partilhada com o replay.

Invariantes de segurança (resposta à auditoria 1.0.2):
- Toda a decisão pertence a uma *geração*; parar, mudar de conta ou de ativos
  incrementa a geração e nenhuma ordem é enviada por uma decisão antiga.
- Antes de cada ordem o estado é relido (conta, fundos, gates, idade da decisão).
- Fechos e entradas pendentes têm máquina de estados; entradas não executadas
  são canceladas após ``entry_timeout_seconds``.
- A cobertura das posições é verificada a cada ciclo e reposta se faltar.
- Só se tocam ordens com o ``orderRef`` do bot, na conta configurada.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Coroutine, Optional
from zoneinfo import ZoneInfo

from . import __version__
from .analytics import Analytics
from .calibration import Calibrator, ConfidenceSignals
from .config import Settings
from .database import Database
from .ibkr_client import IBKRClient
from .indicators import Bar, MarketSnapshot, aggregate_bars, atr as atr_fn, build_snapshot, compute_dynamics, Dynamics
from .lessons import LessonEngine
from .market_data import EventData
from .ollama_brain import DecisionOutcome, OllamaBrain
from .policy import ExecutionPlan, PersistenceTracker, Skip, decide_execution
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
    """Agrega velas (só buckets completos), calcula indicadores e dinâmica sobre a última vela fechada."""
    if not bars_1m:
        return None
    closed = bars_1m[:-1] if (drop_forming and len(bars_1m) > 1) else bars_1m
    agg = aggregate_bars(closed, settings.decision_bar_minutes, drop_incomplete=True)
    if len(agg) < settings.sma_slow + 5:
        return None
    closes = [b.close for b in agg]
    volumes = [b.volume for b in agg]
    snapshot = build_snapshot(symbol, closes, volumes, agg[-1].time, rsi_period=settings.rsi_period,
                              sma_fast_period=settings.sma_fast, sma_slow_period=settings.sma_slow,
                              ema_period=settings.ema_period, bar_minutes=settings.decision_bar_minutes)
    if snapshot is None:
        return None
    dynamics = compute_dynamics(closes, agg, rsi_period=settings.rsi_period, sma_fast_period=settings.sma_fast,
                                sma_slow_period=settings.sma_slow, ema_period=settings.ema_period,
                                atr_period=settings.atr_period)
    # O instante de mercado da decisão é o FECHO da última vela agregada (o preço usado é o seu fecho),
    # não o início do bucket: idade da decisão e percurso do settlement partem daqui (N10).
    bar_end = agg[-1].time + timedelta(minutes=settings.decision_bar_minutes)
    snapshot.bar_time = bar_end
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
        # Um calibrador por experiência (modelo + versão do prompt); o A/B usa o do modelo da amostra (N11).
        self.calibrator = Calibrator(settings, db, brain.model, prompt_version=brain.prompt_version)
        self._calibrators: dict[str, Calibrator] = {brain.model: self.calibrator}
        self.settler = Settler(settings, db, self._price_at, bars_between=self._bars_between)
        self.lessons = LessonEngine(settings, db)
        self.analytics = Analytics(settings, db)
        self.retro = Retrospective(settings, db, brain, self.settler, self.lessons, self.calibrator, self.analytics)
        self.sentiment = SentimentScorer(settings.sentiment_model) if settings.sentiment_enabled else None
        self.volmodel = VolatilityForecaster(settings.volmodel_name, settings.volmodel_enabled)
        self.persistence = PersistenceTracker(settings.signal_persistence_cycles)

        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._stop_event: Optional[asyncio.Event] = None
        self._decision_lock: Optional[asyncio.Lock] = None

        self.trading_enabled = False
        self.ollama_ok = False
        self.generation = 0  # invalida decisões em curso ao parar/mudar de conta/ativos
        self.risk_multiplier = settings.learning_risk_multiplier
        self.gates_passed = False
        self._last_llm: dict[str, datetime] = {}
        self._last_decided_bar: dict[str, datetime] = {}
        self._pending_close: dict[str, dict[str, Any]] = {}   # symbol -> {state, order_id, group_id, qty, filled, ts}
        self._pending_entries: dict[int, dict[str, Any]] = {}  # parent_order_id -> {symbol, qty, notional, ts, deadline, trade_id}
        self._symbol_locks: dict[str, asyncio.Lock] = {}      # serializa fecho/reproteção por ativo (N02)
        self._reconciled = False                              # nenhuma decisão até reconciliar o estado da corretora (N05)
        self._external_positions: set[str] = set()            # posições não abertas pelo bot (N09)
        self._mixed_warned: set[str] = set()                  # posições mistas já assinaladas (V07)
        self._discrepancies: set[str] = set()                 # ativos com discrepância posição/saídas por resolver (X01)
        self.mcp_thread: Any = None                           # servidor MCP (ChatGPT), quando ativo
        self._reduction_warned: set[str] = set()
        self._last_weekly_report: Optional[date] = None
        self._load_gate_state()
        self.db.record_experiment("config", json.dumps({
            "model": brain.model, "threshold": settings.min_confidence, "edge_margin": settings.edge_margin,
            "samples": settings.llm_samples, "two_stage": settings.llm_two_stage, "interval": settings.llm_interval_minutes,
            "mode": settings.trading_mode}))
        for warning in Settings.load_warnings:
            log.warning("config.json: %s", warning)

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
        log.info("Motor iniciado: modelo %s | LLM a cada %d min, %d amostras, %s | risco/trade %.2f%% ×%.2f | "
                 "stop %s | calibração %s | prompt v%d | modo %s (porta %d) | DB %s",
                 self.brain.model, self.settings.llm_interval_minutes, self.settings.llm_samples,
                 "2 etapas" if self.settings.llm_two_stage else "1 etapa", self.settings.risk_per_trade_pct * 100,
                 self.risk_multiplier, self.settings.stop_mode, "Platt" if self.calibrator.is_fitted else "heurística",
                 self.brain.prompt_version, self.settings.trading_mode.upper(), self.settings.ib_port, self.db.path)
        await self._refresh_models()
        self.bus.emit("equity_history", points=self.equity_history())
        tasks = [
            asyncio.create_task(self._cycle_loop(), name="cycle"),
            asyncio.create_task(self._supervisor_loop(), name="supervisor"),
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
        self.generation += 1
        if self._stop_event:
            self._stop_event.set()

    # ------------------------------------------------------------ comandos
    async def start_trading(self) -> None:
        if self.trading_enabled:
            return
        if self.settings.is_live and not self.settings.live_confirmed:
            log.error("Modo REAL sem confirmação: confirma na GUI (escrever REAL) antes de iniciar.")
            self._emit_status()
            return
        self.trading_enabled = True
        self.generation += 1
        log.info("Ciclo de trading INICIADO (geração %d). Ativos: %s", self.generation, ", ".join(self.settings.symbols))
        await self._ensure_connected()
        self._emit_status()

    async def stop_trading(self) -> None:
        self.trading_enabled = False
        self.generation += 1  # decisões em curso ficam inválidas
        self.persistence.clear()
        log.info("Ciclo de trading PARADO (geração %d). Posições abertas mantêm os seus Brackets (GTC) na IBKR.",
                 self.generation)
        self._emit_status()

    async def set_mode(self, mode: str, confirmed: bool = False) -> bool:
        """Alterna entre real (7496, predefinido) e paper (7497): conta, base de dados e estado separados."""
        mode = "live" if mode == "live" else "paper"
        if mode == self.settings.trading_mode and self.ibkr.connected:
            return True
        if mode == self.settings.trading_mode and mode == "live" and confirmed and not self.ibkr.connected:
            self.settings.live_confirmed = True
            self.settings.save()
            return True
        if mode == "live" and not confirmed and not self.settings.live_confirmed:
            log.error("Modo REAL recusado: falta confirmação explícita.")
            return False
        was_trading = self.trading_enabled
        self.trading_enabled = False
        self.generation += 1
        assert self._decision_lock is not None
        async with self._decision_lock:  # espera por qualquer decisão em curso
            await self.ibkr.disconnect()
            self._last_llm.clear()
            self._last_decided_bar.clear()
            self._pending_close.clear()
            self._pending_entries.clear()
            self._external_positions.clear()
            self._reconciled = False
            self.persistence.clear()
            self.settings.trading_mode = mode
            self.settings.live_confirmed = bool(mode == "live" and (confirmed or self.settings.live_confirmed))
            self.settings.save()
            # Base de dados, pausas, calibração e lições pertencem à conta: recarregar tudo.
            self._switch_db(self.settings.db_path())
            self.db.record_experiment("config", f"mode={mode}")
        if mode == "live":
            log.critical("MODO REAL ATIVADO (porta %d, DB %s). Ordens com dinheiro real. Camada de risco: %.2f%%/trade, "
                         "kill-switch %.0f%%/dia.", self.settings.ib_port, self.db.path,
                         self.settings.risk_per_trade_pct * 100, self.settings.daily_loss_limit_pct * 100)
        else:
            log.warning("Modo PAPER ativado (porta %d, DB %s).", self.settings.ib_port, self.db.path)
        ok = await self._ensure_connected()
        self.trading_enabled = was_trading and ok
        self.bus.emit("equity_history", points=self.equity_history())
        self._emit_status()
        return ok

    def _switch_db(self, path: Any) -> None:
        """Muda de ficheiro SQLite e recarrega todo o estado que pertence a essa base."""
        self.db.switch_path(path)
        self.gate.reset()
        self._calibrators.clear()
        self.brain._load_addendum()
        self._set_calibrator(self._calibrator_for(self.brain.model))
        self.lessons.rebuild()
        self._load_gate_state()

    def _set_calibrator(self, cal: Calibrator) -> None:
        """Calibrador atual: TODAS as dependências (retrospetiva incluída) passam a usá-lo (V08)."""
        self.calibrator = cal
        self.retro.calibrator = cal

    def _gates_key(self, model: str) -> str:
        """Chave de persistência dos gates = identificador da experiência (modelo + versão do prompt). Chaves
        antigas só por modelo não são lidas: um prompt novo nunca herda validação (W05)."""
        return f"gates_passed:{Analytics.experiment_id(model, self.brain.prompt_version)}"

    def _gates_for(self, model: str) -> tuple[bool, float]:
        """Estado de validação (gates) e multiplicador de risco DA experiência (modelo + prompt atual) (V08/W05)."""
        passed = self.db.get_kv(self._gates_key(model)) == "1"
        return passed, (1.0 if passed else self.settings.learning_risk_multiplier)

    def _calibrator_for(self, model: str) -> Calibrator:
        cal = self._calibrators.get(model)
        if cal is None:
            cal = Calibrator(self.settings, self.db, model, prompt_version=self.brain.prompt_version)
            self._calibrators[model] = cal
        elif cal.prompt_version != self.brain.prompt_version:
            cal.set_prompt_version(self.brain.prompt_version)
            self._load_gate_state()  # a experiência mudou: o estado de validação é o do novo prompt (W05)
        return cal

    async def set_model(self, model: str) -> None:
        if model and model != self.brain.model:
            self.brain.set_model(model)
            self._set_calibrator(self._calibrator_for(model))
            self._load_gate_state()
            self.generation += 1
            self.persistence.clear()
            log.info("Modelo Ollama alterado para %s (calibração própria: %s; N experiências %d)", model,
                     "ajustada" if self.calibrator.is_fitted else "heurística", self.db.experiment_count())
            self._emit_status()

    async def set_symbols(self, symbols: list[str]) -> None:
        cleaned = sorted({s.strip().upper() for s in symbols if s.strip()})
        if not cleaned:
            return
        self.settings.symbols = cleaned
        self.settings.save()
        self.generation += 1
        log.info("Ativos atualizados: %s", ", ".join(cleaned))
        if self.ibkr.connected:
            for symbol in cleaned:
                await self.ibkr.subscribe_bars(symbol)

    async def reconnect(self) -> bool:
        """Aplica novas definições de ligação (host, porta, clientId, conta): desliga e volta a ligar."""
        was_connected = self.ibkr.connected
        await self.ibkr.disconnect()
        self.generation += 1
        self._reconciled = False
        self._emit_status()
        if was_connected or self.trading_enabled:
            return await self._ensure_connected()
        return True

    async def settings_changed(self) -> None:
        """Definições alteradas na GUI: reconstrói o que foi dimensionado no arranque (tracker de persistência,
        fonte de eventos), regista a experiência e atualiza o estado publicado (V11)."""
        self.persistence = PersistenceTracker(self.settings.signal_persistence_cycles)
        self.events = EventData(self.db, self.settings.finnhub_api_key, self.settings.news_source)
        self.gate.events = self.events
        self._mixed_warned.clear()
        self.db.record_experiment("config", json.dumps({
            "model": self.brain.model, "threshold": self.settings.min_confidence, "edge_margin": self.settings.edge_margin,
            "samples": self.settings.llm_samples, "two_stage": self.settings.llm_two_stage,
            "interval": self.settings.llm_interval_minutes, "mode": self.settings.trading_mode,
            "risk": self.settings.risk_per_trade_pct, "daily_loss": self.settings.daily_loss_limit_pct}))
        self._emit_status()

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
        report = self.analytics.build_report(model=self.brain.model, prompt_version=self.brain.prompt_version)  # só a experiência atual
        self._apply_gates(report["gates"])
        md = Analytics.render_markdown(report)
        path = self.settings.log_path().with_name(f"relatorio_estatistico_{self.settings.trading_mode}.md")
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
            self._set_calibrator(self._calibrator_for(models[0]))
            self._load_gate_state()
        self.bus.emit("models", models=models, current=self.brain.model)
        self._emit_status()

    # -------------------------------------------------------------- loops
    async def _ensure_connected(self) -> bool:
        was_connected = self.ibkr.connected
        if not was_connected:
            if not await self.ibkr.connect():
                return False
            if not self._bind_account_db():
                await self.ibkr.disconnect()
                return False
        for symbol in list(self.settings.symbols) + ([self.settings.benchmark_symbol] if self.settings.benchmark_symbol else []):
            await self.ibkr.subscribe_bars(symbol)
        if not was_connected or not self._reconciled:
            await self._reconcile()
        self._emit_status()
        return True

    def _bind_account_db(self) -> bool:
        """Base de dados POR CONTA (N09): ao conhecer a conta, passa para o ficheiro dessa conta.

        A primeira vez copia o ficheiro por modo (estado pré-ligação) para o da conta. Uma base já
        associada a outra conta nunca é reutilizada.
        """
        account = self.ibkr.account
        if not account:
            return True
        target = self.settings.db_path(account)
        current = self.db.path
        if str(target) != str(current):
            unbound = self.db.get_kv("bound_account") is None  # só o estado pré-ligação (por modo) é copiado
            foreign = self.db.accounts_in_records() - {account}
            if unbound and not target.exists() and current not in (":memory:", "") and Path(current).exists():
                if foreign:
                    log.critical("Base %s tem registos das contas %s: NÃO é copiada para %s; a conta começa com base vazia.",
                                 current, ", ".join(sorted(foreign)), account)
                else:
                    try:
                        self.db.copy_to(target)
                        # A origem fica atribuída de forma durável: nunca volta a ser copiada para outra conta (V06).
                        self.db.set_kv("bound_account", account)
                        log.warning("Base de dados da conta %s criada a partir de %s.", account, current)
                    except Exception as exc:  # noqa: BLE001
                        log.error("Não foi possível criar a base de dados da conta %s: %s", account, exc)
                        return False
            self._switch_db(target)
        bound = self.db.get_kv("bound_account")
        if bound and bound != account:
            log.critical("Base de dados %s pertence à conta %s mas a sessão ligou-se a %s. A desligar por segurança.",
                         self.db.path, bound, account)
            return False
        if not bound:
            self.db.set_kv("bound_account", account)
        return True

    # ------------------------------------------------- pausa de novas entradas (local ou remota via MCP)
    @property
    def entries_paused(self) -> bool:
        """Pausa PERSISTENTE de novas entradas: bloqueia decisões de entrada sem desligar a supervisão das
        posições nem remover proteções; não cancela entradas já enviadas (continuam pendentes e visíveis)."""
        return self.db.get_kv("entries_paused") == "1"

    async def pause_entries(self, reason: str, actor: str = "gui", expected: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Idempotente: só é confirmada depois de aplicada e persistida. ``expected`` transporta a identidade EXATA
        (conta, modo, geração) para que o pedido foi validado; é reavaliada aqui, no loop do motor, imediatamente
        antes da escrita — uma troca de conta/modo ou um reinício entretanto recusa o pedido (Z08)."""
        if expected:
            current = {"account": self.ibkr.account, "mode": self.settings.trading_mode, "generation": self.generation,
                       "db": self.db.path}
            for key, value in expected.items():
                if key in current and value is not None and current[key] != value:
                    log.error("Pausa (%s) recusada: %s mudou entre a validação e a aplicação (%r -> %r).", actor, key, value, current[key])
                    return {"paused": self.entries_paused, "applied": False,
                            "error": f"contexto mudou antes de aplicar: {key} ({'conta' if key == 'account' else key}) já não coincide"}
        already = self.entries_paused
        self.db.set_kv("entries_paused", "1")
        self.db.set_kv("entries_paused_reason", f"{reason} [{actor}]")
        self.db.set_kv("entries_paused_at", datetime.now(timezone.utc).isoformat())
        if not already:
            log.critical("NOVAS ENTRADAS EM PAUSA (%s): %s. Posições, proteções e entradas já enviadas mantêm-se.", actor, reason)
        self._emit_status()
        return {"paused": True, "applied": True, "already_paused": already, "reason": reason, "actor": actor,
                "pending_entries": len(self._pending_entries)}

    async def resume_entries(self, actor: str = "gui") -> dict[str, Any]:
        was = self.entries_paused
        self.db.set_kv("entries_paused", "0")
        if was:
            log.warning("Novas entradas RETOMADAS (%s).", actor)
        self._emit_status()
        return {"paused": False, "was_paused": was}

    def remote_snapshot(self) -> dict[str, Any]:
        """Estado para supervisão técnica (MCP): sem credenciais, conta identificada de forma mínima."""
        now = datetime.now(timezone.utc)
        acct = self.ibkr.account or ""
        masked = (acct[:2] + "***" + acct[-2:]) if len(acct) > 4 else ("***" if acct else "")
        last_bars = {}
        for symbol in self.settings.symbols:
            q = self.ibkr.last_price(symbol)
            last_bars[symbol] = {"price": q[0], "age_seconds": round((now - q[1]).total_seconds(), 1)} if q else None
        return {
            "version": __version__, "mode": self.settings.trading_mode, "port": self.settings.ib_port, "account": masked,
            "ibkr_connected": self.ibkr.connected, "ollama_ok": self.ollama_ok, "model": self.brain.model,
            "trading_enabled": self.trading_enabled, "entries_paused": self.entries_paused,
            "entries_paused_reason": self.db.get_kv("entries_paused_reason"), "kill_switch": self.gate.halted,
            "pauses": {k: v.isoformat() for k, v in self.gate.active_pauses(now).items()},
            "reconciled": self._reconciled, "discrepancies": sorted(self._discrepancies),
            "conflicts": self.db.kv_with_prefix("conflict:"),  # redução/inversão/execuções por receber, persistido (Z01/Z05)
            "external_positions": sorted(self._external_positions),
            "pending_entries": [{"order_id": oid, **{k: v for k, v in e.items() if k in ("symbol", "qty", "notional", "filled", "cancel_sent")}}
                                for oid, e in self._pending_entries.items()],
            "pending_closes": {s: {"state": pc.get("state"), "order_id": pc.get("order_id"), "qty": pc.get("qty")}
                               for s, pc in self._pending_close.items()},
            "data_delayed": self.ibkr.data_delayed, "last_bars": last_bars, "symbols": list(self.settings.symbols),
            "migration_blocked": self._migration_blocked(), "experiment": self.calibrator.experiment,
            "gates_passed": self.gates_passed, "generation": self.generation,
        }

    def _migration_blocked(self) -> bool:
        """Falha de migração registada no ficheiro-marcador (independente da base aberta) ou na base atual (X03)."""
        try:
            if self.settings.migration_flag_path().exists():
                return True
        except OSError:
            return True
        return bool(self.db.get_kv("migration_failed"))

    def _lock_for(self, symbol: str) -> asyncio.Lock:
        lock = self._symbol_locks.get(symbol)
        if lock is None:
            lock = self._symbol_locks[symbol] = asyncio.Lock()
        return lock

    def _authorize(self, generation: int, *, bar_time: Optional[datetime] = None, symbol: Optional[str] = None,
                   action: Optional[str] = None, limit_price: Optional[float] = None, entry: bool = False) -> Any:
        """Token de autorização reavaliado pelo cliente IBKR antes de cada ``placeOrder`` (N01/V10).

        Além da geração e do estado do ciclo, revalida a IDADE da decisão (relógio de parede e prazo
        monotónico, contra suspensões durante a qualificação) e a cotação face ao limite da entrada.
        """
        import time as _time

        deadline = None
        if bar_time is not None:
            remaining = self.settings.decision_max_age_seconds - (datetime.now(timezone.utc) - bar_time).total_seconds()
            deadline = _time.monotonic() + remaining

        def ok() -> bool:
            if not (generation == self.generation and self.trading_enabled and self.ibkr.connected and not self.gate.halted):
                return False
            if entry and (self.entries_paused or (symbol is not None and symbol in self._discrepancies)):
                # A pausa (local ou remota) revoga entradas ainda em preparação; fechos e proteções não passam por aqui (Z02).
                log.warning("[%s] Entrada revogada antes do envio: novas entradas em pausa/ativo em discrepância.", symbol)
                return False
            if bar_time is not None:
                age = (datetime.now(timezone.utc) - bar_time).total_seconds()
                if age > self.settings.decision_max_age_seconds or (deadline is not None and _time.monotonic() > deadline):
                    log.warning("[%s] Decisão expirou antes do envio (%.0fs): ordem não enviada.", symbol, age)
                    return False
            if symbol and limit_price is not None and action in ("BUY", "SELL"):
                quote = self.ibkr.last_price(symbol)
                if quote is not None:
                    last_price, last_ts = quote
                    if (datetime.now(timezone.utc) - last_ts).total_seconds() > self.settings.max_bar_age_seconds + 60:
                        log.warning("[%s] Cotação desatualizada (%s) antes do envio: ordem não enviada.", symbol, last_ts.isoformat())
                        return False
                    if (action == "BUY" and last_price > limit_price) or (action == "SELL" and last_price < limit_price):
                        log.warning("[%s] Cotação %.2f já além do limite %.2f antes do envio: ordem não enviada.", symbol, last_price, limit_price)
                        return False
            return True

        return ok

    def _managed_symbols(self) -> set[str]:
        """Ativos que o bot gere: os que têm trades abertos na base de dados (abertos pelo bot ou adotados) E os
        que ainda têm ordens PRÓPRIAS vivas na corretora, mesmo sem trade aberto (Y01)."""
        managed = {t["symbol"] for t in self.db.open_trades()}
        try:
            managed |= {t.contract.symbol for t in self.ibkr.our_open_orders() if IBKRClient.is_live_order(t)}
        except Exception:  # noqa: BLE001
            pass
        if self.settings.manage_external_positions:
            managed |= {p["symbol"] for p in self.ibkr.portfolio_state().get("positions", [])
                        if p.get("sec_type", "STK") == "STK"}
        return managed

    def _pending_entry_ids(self, symbol: str) -> set[int]:
        """IDs das entradas pendentes do ativo (pais) — nunca são tratadas como saídas (Y02)."""
        ids = {int(oid) for oid, e in self._pending_entries.items() if e["symbol"] == symbol}
        try:
            for t in self.ibkr.our_open_orders():
                o = t.order
                if t.contract.symbol == symbol and int(getattr(o, "parentId", 0) or 0) == 0 and IBKRClient.is_live_order(t):
                    known = self.db.group_for_order(int(o.orderId), symbol=symbol)
                    if known is not None and self.db.order_leg(known, int(o.orderId)) == "PARENT":
                        ids.add(int(o.orderId))
        except Exception:  # noqa: BLE001
            pass
        return ids

    def _conflict_pending(self, symbol: str) -> bool:
        """Conflito operacional PERSISTIDO (redução/inversão fora do bot) com trades ABERTOS por explicar (Z01): bloqueia
        decisões e proteção automática. Um trade já reconciliado com execuções por receber fica apenas assinalado
        (P&L indeterminado) e deixa de bloquear o ativo."""
        return bool(self.db.get_kv(f"conflict:{symbol}")) and self.db.open_adjustments_for_symbol(symbol) > 1e-9

    def _mark_conflict(self, symbol: str, kind: str) -> None:
        self._discrepancies.add(symbol)
        self.db.set_kv(f"conflict:{symbol}", f"{kind} @ {datetime.now(timezone.utc).isoformat()}")

    def _resolve_conflict(self, symbol: str) -> None:
        self._discrepancies.discard(symbol)
        self._reduction_warned.discard(symbol)
        if self.db.get_kv(f"conflict:{symbol}"):
            self.db.set_kv(f"conflict:{symbol}", "")
            log.warning("[%s] Conflito posição/ledger resolvido: execuções explicam a diferença.", symbol)

    def _load_conflicts(self) -> None:
        """Os conflitos sobrevivem a um reinício: ficam na base de dados até as execuções os explicarem (Z01)."""
        for symbol in self.db.kv_with_prefix("conflict:"):
            if self._conflict_pending(symbol):
                self._discrepancies.add(symbol)

    def _own_qty(self, symbol: str) -> float:
        """Quantidade (com sinal) que o bot abriu e ainda não fechou, segundo a base de dados."""
        total = 0.0
        for t in self.db.open_trades(symbol):
            total += int(t["direction"]) * max(0.0, float(t["filled_qty"] or 0) - float(t["exit_qty"] or 0)
                                              - self.db.open_adjustment_qty(int(t["id"])))
        return total

    def _is_external(self, symbol: str, position_qty: float) -> bool:
        """Posição total ou parcialmente alheia ao bot (sem adoção explícita).

        A posição da corretora é líquida: com 20 ações do bot e 80 manuais no mesmo contrato, a posição é
        MISTA e não é gerida além das 20 próprias (V07). Sinais nesse ativo ficam bloqueados.
        """
        if position_qty == 0 or self.settings.manage_external_positions:
            return False
        own = self._own_qty(symbol)
        if own == 0:
            return True
        if (own > 0) != (position_qty > 0):
            return True
        return abs(position_qty) > abs(own) + 1e-6

    async def _reconcile(self) -> None:
        """Após (re)ligar: importa execuções offline, restaura ordens vivas, fecha trades órfãos e repõe cobertura.

        Até esta função terminar, ``_reconciled`` é falso e o ciclo de decisão não corre (N05).
        """
        self._reconciled = False
        self._load_conflicts()
        imported = 0
        for fill in self.ibkr.recent_fills():
            before = self.db.fill_by_exec(fill.execution.execId)
            self._on_fill(None, fill)
            if before is None and self.db.fill_by_exec(fill.execution.execId) is not None:
                imported += 1
        if imported:
            log.warning("Reconciliação: %d execuções ocorridas sem o bot ligado foram importadas.", imported)
        self._restore_pending_orders()
        positions = {p["symbol"]: p for p in self.ibkr.portfolio_state().get("positions", [])
                     if p.get("sec_type", "STK") == "STK"}
        # Reconciliação de arranque UNIFICADA com a periódica (Y01): um trade sem posição passa pela mesma rotina
        # que cancela e confirma as saídas ANTES de fechar o vínculo; sem confirmação, o conflito persiste.
        for symbol in {t["symbol"] for t in self.db.open_trades()}:
            if symbol not in positions and symbol not in self._pending_close:
                await self._reconcile_vanished_position(symbol)
        managed = self._managed_symbols()
        self._external_positions = set()
        for symbol, pos in positions.items():
            if symbol not in managed:
                self._external_positions.add(symbol)
                log.critical("Posição %s x%g NÃO foi aberta pelo bot: não é gerida, fechada nem protegida "
                             "(manage_external_positions=False). Sinais para %s ficam bloqueados.", symbol, pos["qty"], symbol)
                continue
            if symbol in self._pending_close:
                continue
            # A MESMA classificação da supervisão (inversão, redução, excesso, lado errado) antes de qualquer proteção (AA01).
            await self._supervise_symbol(symbol, pos)
        self._reconciled = True

    def _restore_pending_orders(self) -> None:
        """Reconstrói entradas e fechos pendentes a partir das ordens do bot ainda vivas na corretora (N05)."""
        import time as _time

        self._pending_entries.clear()
        self._pending_close.clear()
        for t in self.ibkr.our_open_orders():
            o, st = t.order, t.orderStatus
            if o.parentId != 0 or o.orderType not in ("MKT", "LMT") or not IBKRClient.is_live_order(t):
                continue
            in_transition = IBKRClient.in_transition(t)  # PendingCancel: ainda pode executar; reserva mantida (X05)
            symbol = t.contract.symbol
            # Classificação pela identidade PERSISTIDA (order_history): um TP/SL autónomo (parentId=0, criado por
            # ensure_protection) é proteção e fica; só pernas PARENT são entradas/fechos pendentes (V02).
            known = self.db.group_for_order(int(o.orderId), symbol=symbol)
            leg = self.db.order_leg(known, int(o.orderId)) if known else None
            if leg in ("TP", "SL"):
                continue
            group = self.db.group_by_parent(int(o.orderId))
            if group is None:
                log.error("Ordem %d (%s %s x%g) do bot sem grupo na base de dados: a cancelar por segurança.",
                          o.orderId, o.action, symbol, float(o.totalQuantity))
                self.ibkr.cancel_order_id(int(o.orderId))
                continue
            placed = datetime.fromisoformat(group["ts"])
            age = (datetime.now(timezone.utc) - placed).total_seconds()
            if group["role"] == "ENTRY":
                trade = self.db.trade_for_group(int(group["id"]))
                self._pending_entries[int(o.orderId)] = {
                    "symbol": symbol, "qty": float(o.totalQuantity),
                    "notional": float(o.totalQuantity) * float(group.get("ref_price") or 0.0), "ts": placed,
                    "deadline": _time.monotonic() + max(0.0, self.settings.entry_timeout_seconds - age),
                    "trade_id": trade["id"] if trade else None, "filled": float(st.filled or 0.0),
                    "cancel_sent": in_transition}
                log.warning("Entrada pendente restaurada: ordem %d %s x%g (há %.0fs%s).", o.orderId, symbol,
                            float(o.totalQuantity), age, "; cancelamento em curso" if in_transition else "")
            elif group["role"] == "CLOSE":
                self._pending_close[symbol] = {"state": "SENT", "order_id": int(o.orderId), "group_id": int(group["id"]),
                                               "qty": float(o.totalQuantity), "filled": float(st.filled or 0.0), "ts": placed}
                log.warning("Fecho pendente restaurado: ordem %d %s x%g.", o.orderId, symbol, float(o.totalQuantity))

    async def _protect_if_naked(self, symbol: str, pos: dict[str, Any]) -> None:
        async with self._lock_for(symbol):
            await self._protect_if_naked_locked(symbol, pos)

    async def _protect_if_naked_locked(self, symbol: str, pos: dict[str, Any]) -> None:
        if symbol in self._pending_close or symbol in self._external_positions:
            return
        own_signed = self._own_qty(symbol)
        own = abs(own_signed)
        if own > 1e-9 and float(pos["qty"]) != 0 and (own_signed > 0) != (float(pos["qty"]) > 0):
            # A posição na corretora tem o sinal CONTRÁRIO ao do ledger: proteger "pelo valor absoluto" colocaria ordens a
            # favor de uma posição que o bot não abriu (AA01). Conflito explícito; a supervisão trata a inversão.
            self._mark_conflict(symbol, "posição invertida fora do bot")
            log.critical("[%s] Proteção RECUSADA: ledger %+g vs corretora %+g (sinais opostos).", symbol, own_signed, float(pos["qty"]))
            return
        if own <= 1e-9 and not self.settings.manage_external_positions and self.db.open_trades(symbol):
            # Há trades abertos mas nada da posição é do bot (tudo explicado por ajustes provisórios, ou entrada ainda
            # sem execução): nunca se colocam ordens sobre uma posição que o bot não abriu, em nenhum ciclo nem após
            # reinício (Z01). (Sem trade nenhum, só se chega aqui com ordens próprias vivas: adoção do registo perdido.)
            if symbol not in self._mixed_warned:
                self._mixed_warned.add(symbol)
                log.critical("[%s] Posição %+g na corretora não pertence ao bot (quantidade própria 0): sem proteção automática; "
                             "ativo bloqueado até as execuções explicarem a diferença.", symbol, float(pos["qty"]))
            return
        mixed = not self.settings.manage_external_positions and own > 0 and abs(float(pos["qty"])) > own + 1e-6
        max_qty = own if mixed else None
        if mixed and symbol not in self._mixed_warned:
            self._mixed_warned.add(symbol)
            log.critical("[%s] Posição MISTA: %g ações na corretora, %g abertas pelo bot. Só as %g próprias são geridas/protegidas; "
                         "sinais neste ativo ficam bloqueados até adotares o resto (manage_external_positions).",
                         symbol, float(pos["qty"]), own, own)
        if self.ibkr.has_protective_orders(symbol, needed_qty=max_qty, ours_only=mixed) and not self.ibkr.has_orphan_children(symbol):
            return
        trade = next((t for t in self.db.open_trades(symbol)), None)
        price = pos.get("market_price") or pos.get("avg_cost") or 0.0
        sign = 1 if pos["qty"] > 0 else -1
        if trade and trade.get("stop_price") and trade.get("tp_price"):
            stop, tp = float(trade["stop_price"]), float(trade["tp_price"])
        else:
            bars = aggregate_bars(self.ibkr.bars_as_list(symbol), self.settings.decision_bar_minutes)
            sizing = self.sizer.size(action="BUY" if sign > 0 else "SELL", price=price,
                                     equity=max(price * abs(pos["qty"]), 1.0), bars=bars, risk_pct=1.0) if price > 0 else None
            if sizing and sizing.qty >= 1:
                stop, tp = sizing.stop_price, sizing.tp_price
            else:
                stop = price * (1 - sign * self.settings.stop_loss_pct)
                tp = price * (1 + sign * self.settings.take_profit_pct)
        result = await self.ibkr.ensure_protection(symbol, stop_price=stop, tp_price=tp, max_qty=max_qty)
        if not result:
            return
        # Persistir a proteção reconstruída para que os seus fills fechem o trade certo; os filhos
        # substituídos ficam arquivados em order_history (N07).
        if trade is None:
            group_id = self.db.insert_order_group(symbol=symbol, decision_id=None, role="ENTRY", direction=sign,
                                                  qty=abs(pos["qty"]), parent_order_id=None,
                                                  tp_order_id=result["tp_order_id"], sl_order_id=result["sl_order_id"],
                                                  ref_price=price, tp_price=result["tp_price"], sl_price=result["sl_price"],
                                                  account=self.ibkr.account, con_id=self.ibkr.con_id(symbol))
            trade_id = self.db.open_trade(symbol=symbol, decision_id=None, group_id=group_id, direction=sign,
                                          qty=abs(pos["qty"]), stop_price=result["sl_price"], tp_price=result["tp_price"])
            self.db.record_entry_fill(trade_id, abs(pos["qty"]), float(pos.get("avg_cost") or price), datetime.now(timezone.utc))
            log.warning("Posição %s sem registo: trade #%d criado a partir da posição da corretora.", symbol, trade_id)
        else:
            self.db.update_group_orders(int(trade["group_id"]), tp_order_id=result["tp_order_id"], sl_order_id=result["sl_order_id"])
            self.db._execute("UPDATE trades SET stop_price=?, tp_price=? WHERE id=?",
                             (result["sl_price"], result["tp_price"], trade["id"]))

    async def _supervisor_loop(self) -> None:
        """Tarefa CURTA e independente do ciclo de inferência (N14): prazos monotónicos das entradas,
        cobertura das posições e fechos pendentes, a cada poucos segundos."""
        period = max(2.0, min(5.0, float(self.settings.cycle_seconds)))
        while True:
            try:
                if self.ibkr.connected:
                    await self._supervise()
            except Exception as exc:  # noqa: BLE001
                log.exception("Erro no supervisor: %s", exc)
            await asyncio.sleep(period)

    async def _supervise(self) -> None:
        """Cobertura das posições, entradas pendentes expiradas (relógio monotónico) e fechos pendentes."""
        import time as _time

        now = datetime.now(timezone.utc)
        mono = _time.monotonic()
        for oid, entry in list(self._pending_entries.items()):
            deadline = entry.get("deadline")
            expired = (mono > deadline) if deadline is not None else \
                (now - entry["ts"]).total_seconds() > self.settings.entry_timeout_seconds
            if expired and not entry.get("cancel_sent"):
                if self.ibkr.cancel_order_id(oid):
                    entry["cancel_sent"] = True
                    log.warning("[%s] Entrada %d não executada em %ds: cancelada.", entry["symbol"], oid,
                                self.settings.entry_timeout_seconds)
                else:
                    self._pending_entries.pop(oid, None)
        positions = {p["symbol"]: p for p in self.ibkr.portfolio_state().get("positions", [])
                     if p.get("sec_type", "STK") == "STK"}
        for symbol in self._managed_symbols():
            pos = positions.get(symbol)
            if symbol in self._pending_close or symbol in self._external_positions:
                continue
            if not pos:
                await self._reconcile_vanished_position(symbol)
                continue
            await self._supervise_symbol(symbol, pos)
        for symbol, pc in list(self._pending_close.items()):
            if pc.get("state") == "CLOSING":
                continue  # fecho em curso dentro do lock do ativo
            if (now - pc["ts"]).total_seconds() > 600 and self.ibkr.position_qty(symbol) == 0:
                self._pending_close.pop(symbol, None)

    async def _supervise_symbol(self, symbol: str, pos: dict[str, Any]) -> None:
        """Classificação ÚNICA de um ativo gerido com posição na corretora — direção, titularidade, redução, excesso,
        lado errado e só depois cobertura — usada pela supervisão periódica E pelo arranque (AA01): o primeiro
        contacto após um período desligado nunca repõe proteção antes de reconhecer um conflito."""
        own_signed = self._own_qty(symbol)
        broker_signed = float(pos["qty"])
        if own_signed != 0 and broker_signed != 0 and (own_signed > 0) != (broker_signed > 0):
            await self._reconcile_reversed_position(symbol, pos, own_signed, broker_signed)
            return
        if own_signed == 0 and broker_signed != 0 and not self.settings.manage_external_positions \
                and self.db.open_adjustments_for_symbol(symbol) > 1e-9:
            await self._hold_external_conflict(symbol, pos)
            return
        own = abs(own_signed)
        broker = abs(broker_signed)
        if own > broker + 1e-6:
            await self._reconcile_reduced_position(symbol, pos, own, broker)
            return
        mixed = not self.settings.manage_external_positions and own > 0 and broker > own + 1e-6
        needed = own if mixed else None
        if self.ibkr.wrong_side_exit_quantity(symbol) > 1e-9 or self.ibkr.has_excess_exits(symbol, allowed_qty=needed):
            await self._resize_exits(symbol, pos, needed, "saídas do bot maiores do que a posição ou do lado errado")
            return
        if self.db.open_adjustments_for_symbol(symbol) > 1e-9:
            self._discrepancies.add(symbol)  # ajuste provisório por explicar: bloqueio persiste até à execução real (Y03/Y06)
        else:
            self._discrepancies.discard(symbol)
            if self.db.open_adjustments_for_symbol(symbol, include_closed=True) <= 1e-9:
                self._resolve_conflict(symbol)
        if not self.ibkr.has_protective_orders(symbol, needed_qty=needed, ours_only=mixed):
            log.critical("[%s] Posição x%g SEM cobertura completa (stops ativos %.0f): a repor proteção.",
                         symbol, pos["qty"], self.ibkr.protective_coverage(symbol))
            await self._protect_if_naked(symbol, pos)
        elif self.ibkr.has_orphan_children(symbol):
            log.warning("[%s] Par de proteção incompleto (TP sem stop ou stop sem TP): a repor o par.", symbol)
            await self._protect_if_naked(symbol, pos)

    async def _hold_external_conflict(self, symbol: str, pos: dict[str, Any]) -> None:
        """Toda a quantidade própria está explicada por ajustes provisórios e a corretora ainda mostra posição: o que
        resta NÃO é do bot (inversão ou redução externa em ciclos anteriores). Em cada ciclo: titularidade, direção e
        níveis são revalidados; nenhuma ordem é colocada; saídas próprias que sobrem são canceladas (Z01)."""
        async with self._lock_for(symbol):
            self._mark_conflict(symbol, "posição externa sobre ledger ajustado")
            keep = self._pending_entry_ids(symbol)
            if not await self.ibkr.cancel_own_exits(symbol, keep_parent_ids=keep):
                log.error("[%s] Conflito externo: saídas do bot ainda não terminadas; a aguardar confirmação.", symbol)
            if symbol not in self._mixed_warned:
                self._mixed_warned.add(symbol)
                log.critical("[%s] Posição %+g na corretora não é do bot (quantidade própria 0 após ajustes): não é gerida, "
                             "protegida nem fechada; decisões bloqueadas até as execuções explicarem a diferença.",
                             symbol, float(pos["qty"]))

    async def _reconcile_reduced_position(self, symbol: str, pos: dict[str, Any], own: float, broker: float) -> None:
        """A posição líquida DIMINUIU abaixo do que o bot abriu (execução ainda não vista ou operação externa).
        Discrepância operacional ≠ execução contabilística (Y03): regista-se um AJUSTE PROVISÓRIO de quantidade
        (sem preço comprovado, P&L indeterminado) que a execução real, quando chegar, substitui na mesma
        quantidade; as decisões no ativo ficam bloqueadas e as saídas do bot são canceladas (confirmação terminal)
        e repostas com a quantidade existente (X01)."""
        async with self._lock_for(symbol):
            self._mark_conflict(symbol, "posição reduzida fora do bot")
            missing = own - broker
            price = float(pos.get("market_price") or pos.get("avg_cost") or 0.0)
            if symbol not in self._reduction_warned:
                self._reduction_warned.add(symbol)
                log.critical("[%s] Posição na corretora (%g) é MENOR do que a aberta pelo bot (%g): %g ações saíram fora do "
                             "bot. Ajuste provisório registado (P&L indeterminado até chegar a execução), decisões "
                             "bloqueadas e saídas a redimensionar.", symbol, broker, own, missing)
            remaining = missing
            for row in self.db.open_trades(symbol):
                if remaining <= 1e-9:
                    break
                open_qty = float(row["filled_qty"] or row["qty"]) - float(row["exit_qty"] or 0) - self.db.open_adjustment_qty(int(row["id"]))
                portion = min(remaining, open_qty)
                if portion <= 0:
                    continue
                self.db.add_adjustment(int(row["id"]), portion, price, "posição reduzida fora do bot (provisório)")
                remaining -= portion
            await self._resize_exits_locked(symbol, pos, broker if broker > 0 else None, "posição reduzida externamente")

    async def _reconcile_reversed_position(self, symbol: str, pos: dict[str, Any], own_signed: float, broker_signed: float) -> None:
        """A posição INVERTEU de sinal fora do bot (ex.: +100 no ledger, -20 na corretora): conflito explícito e
        persistente. Todas as saídas próprias (de qualquer lado) são canceladas com confirmação terminal; o ledger
        recebe um ajuste provisório pela quantidade própria inteira; nenhuma proteção nova é colocada para uma
        posição que o bot não abriu (Y06). O bloqueio só sai quando as execuções explicarem a diferença."""
        async with self._lock_for(symbol):
            self._mark_conflict(symbol, "posição invertida fora do bot")
            if symbol not in self._reduction_warned:
                self._reduction_warned.add(symbol)
                log.critical("[%s] Posição INVERTIDA fora do bot: ledger %+g, corretora %+g. Saídas próprias canceladas; o ativo "
                             "fica bloqueado até as execuções explicarem a diferença.", symbol, own_signed, broker_signed)
            keep = self._pending_entry_ids(symbol)
            if not await self.ibkr.cancel_own_exits(symbol, keep_parent_ids=keep):
                log.error("[%s] Inversão: saídas do bot ainda não terminadas; a aguardar confirmação.", symbol)
                return
            price = float(pos.get("market_price") or pos.get("avg_cost") or 0.0)
            for row in self.db.open_trades(symbol):
                open_qty = float(row["filled_qty"] or row["qty"]) - float(row["exit_qty"] or 0) - self.db.open_adjustment_qty(int(row["id"]))
                if open_qty > 1e-9:
                    self.db.add_adjustment(int(row["id"]), open_qty, price, "posição invertida fora do bot (provisório)")

    async def _reconcile_vanished_position(self, symbol: str) -> None:
        """Ativo gerido sem posição na corretora. Três casos distintos (Y01/Y02):
        - entrada PENDENTE (trade sem execução, pai vivo): nada a limpar; o prazo da entrada manda;
        - posição que existiu e desapareceu: cancela as saídas vivas do bot (abririam uma posição inversa), espera a
          confirmação terminal e só depois fecha o trade como RECONCILED;
        - saídas próprias vivas sem trade aberto (herdadas/reinício): cancela e confirma.
        Sem confirmação terminal o conflito persiste (discrepância) e o supervisor repete."""
        async with self._lock_for(symbol):
            keep = self._pending_entry_ids(symbol)
            filled_trades = [t for t in self.db.open_trades(symbol) if float(t["filled_qty"] or 0) > 0]
            unfilled = [t for t in self.db.open_trades(symbol) if float(t["filled_qty"] or 0) <= 0]
            if unfilled and not filled_trades and keep:
                return  # entrada ainda por executar: não é uma posição desaparecida
            if not await self.ibkr.cancel_own_exits(symbol, keep_parent_ids=keep):
                self._discrepancies.add(symbol)
                log.error("[%s] Posição desapareceu mas há saídas do bot ainda não terminadas: a aguardar confirmação.", symbol)
                return
            for row in filled_trades:
                self.db.close_trade_reconciled(row["id"])
                log.warning("Trade #%d %s sem posição na IBKR -> fechado como RECONCILED (verifica o extrato).", row["id"], symbol)
            self._discrepancies.discard(symbol)
            self._reduction_warned.discard(symbol)
            if self.db.open_adjustments_for_symbol(symbol, include_closed=True) > 1e-9:
                # execuções ainda por receber: o resultado fica INDETERMINADO (assinalado na base) até chegarem (Z05);
                # sem posição não há risco operacional, por isso o ativo deixa de estar bloqueado.
                self.db.set_kv(f"conflict:{symbol}", f"trade reconciliado com execuções por receber @ {datetime.now(timezone.utc).isoformat()}")
                log.warning("[%s] Trade(s) reconciliado(s) com %g ações por explicar: P&L indeterminado até chegarem as execuções.",
                            symbol, self.db.open_adjustments_for_symbol(symbol, include_closed=True))
            else:
                self._resolve_conflict(symbol)

    async def _resize_exits(self, symbol: str, pos: dict[str, Any], allowed: Optional[float], reason: str) -> None:
        async with self._lock_for(symbol):
            await self._resize_exits_locked(symbol, pos, allowed, reason)

    async def _resize_exits_locked(self, symbol: str, pos: dict[str, Any], allowed: Optional[float], reason: str) -> None:
        self._discrepancies.add(symbol)
        trade = next((t for t in self.db.open_trades(symbol)), None)
        price = float(pos.get("market_price") or pos.get("avg_cost") or 0.0)
        sign = 1 if float(pos["qty"]) > 0 else -1
        if trade and trade.get("stop_price") and trade.get("tp_price"):
            stop, tp = float(trade["stop_price"]), float(trade["tp_price"])
        else:
            stop = price * (1 - sign * self.settings.stop_loss_pct)
            tp = price * (1 + sign * self.settings.take_profit_pct)
        log.critical("[%s] %s: a cancelar as saídas do bot (confirmação terminal) e a repor para %g ações.",
                     symbol, reason, allowed if allowed is not None else abs(float(pos["qty"])))
        result = await self.ibkr.resize_exits(symbol, stop_price=stop, tp_price=tp, max_qty=allowed,
                                              keep_parent_ids=self._pending_entry_ids(symbol))
        if result is None:
            return  # cancelamentos não confirmados: a discrepância mantém o bloqueio e o supervisor repete
        if result.get("tp_order_id"):
            # A proteção agregada fica associada a TODOS os trades abertos do ativo (Y07): o fill é depois
            # distribuído pelos saldos de cada um.
            for row in self.db.open_trades(symbol):
                self.db.update_group_orders(int(row["group_id"]), tp_order_id=result["tp_order_id"], sl_order_id=result["sl_order_id"])
                self.db._execute("UPDATE trades SET stop_price=?, tp_price=? WHERE id=?", (result["sl_price"], result["tp_price"], row["id"]))
        if not self.ibkr.has_excess_exits(symbol, allowed_qty=allowed) and self.db.open_adjustments_for_symbol(symbol, include_closed=True) <= 1e-9:
            self._resolve_conflict(symbol)

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
                    pos["covered"] = self.ibkr.has_protective_orders(pos["symbol"])
                self.gate.update_day_baseline(state.get("net_liq"), now)
                day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                state["commissions_today"] = round(self.db.commissions_since(day_start), 2)
                pct = self.gate.day_pnl_pct(state.get("net_liq"))
                state["day_pnl_pct"] = round(pct, 3) if pct is not None else None
                code, rate, exact = self.ibkr.display_rate(self.settings.display_currency)
                state["display_currency"], state["display_rate"], state["display_exact"] = code, rate, exact
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
                if self.trading_enabled and self.ibkr.connected and self._reconciled:
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
        loop = asyncio.get_running_loop()
        # Rede (resultados, VIX, notícias) fora do loop: os gates leem só a cache.
        await loop.run_in_executor(None, self.events.prefetch, list(self.settings.symbols),
                                   self.settings.sentiment_window_hours if self.sentiment else 0)
        state = self.ibkr.portfolio_state()
        global_gate = self.gate.check_global(equity=state.get("net_liq"), now=now)
        if self.gate.halted and self.trading_enabled:
            self.trading_enabled = False
            self.generation += 1
            log.critical("Ciclo de trading PARADO automaticamente pelo kill-switch diário (%.0f%%). "
                         "Posições abertas mantêm os brackets. Reinicia manualmente amanhã.",
                         self.settings.daily_loss_limit_pct * 100)
            self._emit_status()
            return
        generation = self.generation
        for symbol in list(self.settings.symbols):
            if not self.trading_enabled or not self.ibkr.connected or generation != self.generation:
                return
            async with self._decision_lock:
                await self._process_symbol(symbol, generation)

    async def _process_symbol(self, symbol: str, generation: int) -> None:
        now = datetime.now(timezone.utc)
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
        state = self.ibkr.portfolio_state()
        ctx = build_decision_context(self.settings, symbol, bars, state.get("net_liq_usd") or 0.0)
        if ctx is None:
            log.debug("%s: histórico insuficiente para indicadores.", symbol)
            return
        if self._last_decided_bar.get(symbol) == ctx.snapshot.bar_time:
            return
        self._last_llm[symbol] = now
        self._last_decided_bar[symbol] = ctx.snapshot.bar_time
        state["day_pnl_pct"] = self.gate.day_pnl_pct(state.get("net_liq"))

        loop = asyncio.get_running_loop()
        sentiment: tuple[Optional[float], int] = (None, 0)
        if self.sentiment is not None:
            # Só cache: a rede corre no prefetch (executor), nunca no loop da corretora (N15/F36).
            headlines = [n["title"] for n in self.events.recent_news(symbol, self.settings.sentiment_window_hours,
                                                                    cached_only=True)]
            sentiment = await loop.run_in_executor(None, self.sentiment.score_news, headlines)
        vol_width, _vol_source = await loop.run_in_executor(
            None, self.volmodel.interval_width_pct, ctx.closes, self.settings.volmodel_horizon_bars)
        lessons = self.lessons.for_prompt(symbol, ctx.snapshot.rsi, ctx.hour_ny, ctx.snapshot.trend_label())
        recent = self.db.recent_decisions(5, symbol)
        model = self.brain.next_ab_model() if self.settings.ab_test_models else None

        outcome: DecisionOutcome = await loop.run_in_executor(
            None, lambda: self.brain.decide(ctx.snapshot, state, recent, dynamics=ctx.dynamics, lessons=lessons,
                                            sentiment=sentiment, vol_width=vol_width, model=model))
        if outcome.review and outcome.decision.error == "review" and generation == self.generation:
            log.error("[%s] REVIEW: %s. A repetir uma vez a temperatura %.1f.", symbol, outcome.decision.razao,
                      self.settings.review_retry_temperature)
            outcome = await loop.run_in_executor(
                None, lambda: self.brain.decide(ctx.snapshot, state, recent, dynamics=ctx.dynamics, lessons=lessons,
                                                sentiment=sentiment, vol_width=vol_width, model=model,
                                                temperature_override=self.settings.review_retry_temperature))
        self.ollama_ok = outcome.decision.error not in ("timeout", "connection")

        decision = outcome.decision
        position_qty = self.ibkr.position_qty(symbol)
        cost_pct, stop_pct, tp_pct = self._estimated_levels_pct(ctx, state)
        signals = ConfidenceSignals(decision.confianca, outcome.agree_frac, outcome.margin, outcome.action_logprob)
        used_model = outcome.models[0] if outcome.models else self.brain.model
        calibrator = self._calibrator_for(used_model)  # a probabilidade é do modelo que respondeu (N11)
        calibrated = calibrator.probability(signals) if decision.acao in ("BUY", "SELL") else None
        decision_id = self.db.insert_decision(
            symbol=symbol, model=used_model, action=decision.acao,
            confidence=decision.confianca, reason=decision.razao, snapshot=ctx.snapshot.to_dict(),
            position_qty=position_qty, net_liq=state.get("net_liq"), prompt_version=self.brain.prompt_version,
            parse_ok=decision.parse_ok, raw_response=decision.raw,
            extra={"verbal_conf": decision.confianca, "agree_frac": outcome.agree_frac,
                   "action_logprob": outcome.action_logprob, "calibrated_prob": calibrated,
                   "n_samples": len(outcome.samples), "samples_json": outcome.samples, "prompt_hash": outcome.prompt_hash,
                   "dynamics_json": ctx.dynamics.to_dict() if ctx.dynamics else None, "atr": ctx.atr,
                   "sentiment": sentiment[0], "sentiment_n": sentiment[1], "vol_forecast": vol_width,
                   "review": int(outcome.review), "hour_ny": ctx.hour_ny, "regime": ctx.snapshot.trend_label(),
                   "cost_pct": cost_pct, "stop_pct": stop_pct, "tp_pct": tp_pct,
                   "market_ts": ctx.snapshot.bar_time.isoformat(), "generation": generation,
                   "account": self.ibkr.account},
        )
        level = logging.ERROR if outcome.review else (logging.INFO if decision.parse_ok else logging.WARNING)
        log.log(level, "[%s] %s verbal=%.2f acordo=%.0f%% p=%s (%.0fs, %d/%d amostras válidas%s) | RSI=%s ATR=%s%% | %s",
                symbol, decision.acao, decision.confianca, outcome.agree_frac * 100,
                f"{calibrated:.2f}" if calibrated is not None else "n/a", outcome.elapsed,
                sum(1 for x in outcome.samples if x.get("parse_ok")), max(len(outcome.samples), 1),
                f", logprob {outcome.action_logprob:.2f}" if outcome.action_logprob is not None else "",
                f"{ctx.snapshot.rsi:.0f}" if ctx.snapshot.rsi is not None else "n/d",
                f"{ctx.dynamics.atr_pct:.2f}" if ctx.dynamics and ctx.dynamics.atr_pct is not None else "n/d",
                decision.razao, extra={"category": "ollama"})
        self.bus.emit("decision", decision_id=decision_id, symbol=symbol, decision=decision.to_dict(),
                      snapshot=ctx.snapshot.to_dict(), agree_frac=outcome.agree_frac, calibrated=calibrated, model=used_model)
        if outcome.review:
            self._skip(decision_id, symbol, decision.acao, "REVIEW")
            return
        await self._execute(outcome, calibrated, ctx, decision_id, generation, sentiment, vol_width, calibrator=calibrator)

    def _estimated_levels_pct(self, ctx: DecisionContext, state: dict[str, Any]) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """Custo ida+volta, stop e TP em % do preço, para a quantidade que o sizer daria agora (settlement)."""
        equity = state.get("net_liq_usd") or state.get("net_liq")
        if not equity or ctx.snapshot.price <= 0:
            return None, None, None
        sizing = self.sizer.size(action="BUY", price=ctx.snapshot.price, equity=equity, bars=ctx.agg_bars)
        if sizing is None:
            return None, None, None
        qty = sizing.qty if sizing.qty >= 1 else 1
        cost_pct = round(round_trip_cost(qty, ctx.snapshot.price, self.settings) / (qty * ctx.snapshot.price) * 100.0, 4)
        stop_pct = round(sizing.stop_distance / ctx.snapshot.price * 100.0, 4)
        return cost_pct, stop_pct, round(stop_pct * self.settings.reward_risk_ratio, 4)

    def _skip(self, decision_id: int, symbol: str, action: str, reason: str) -> None:
        self.db.mark_decision(decision_id, executed=False, skip_reason=reason)
        self.bus.emit("decision_result", decision_id=decision_id, executed=False, reason=reason)
        if action != "HOLD":
            log.info("[%s] %s não executado: %s", symbol, action, reason)

    # ------------------------------------------------------------ execução
    async def _execute(self, outcome: DecisionOutcome, calibrated: Optional[float], ctx: DecisionContext,
                       decision_id: int, generation: int, sentiment: tuple[Optional[float], int],
                       vol_width: Optional[float], calibrator: Optional[Calibrator] = None) -> None:
        symbol = ctx.snapshot.symbol
        action = outcome.decision.acao
        calibrator = calibrator or self.calibrator
        # Revalidação imediatamente antes de qualquer ordem: geração, estado, conta, gates e dados frescos.
        if generation != self.generation or not self.trading_enabled:
            return self._skip(decision_id, symbol, action, "decisão invalidada (paragem/mudança de conta)")
        if not self.ibkr.connected or not self._reconciled:
            return self._skip(decision_id, symbol, action, "sem ligação à corretora ou estado por reconciliar")
        if self._migration_blocked():
            return self._skip(decision_id, symbol, action, "migração da base de dados por resolver (ver log)")
        now = datetime.now(timezone.utc)
        state = self.ibkr.portfolio_state()
        global_gate = self.gate.check_global(equity=state.get("net_liq"), now=now)
        if self.gate.halted:
            return self._skip(decision_id, symbol, action, "kill-switch diário")
        position_qty = self.ibkr.position_qty(symbol)
        persistence = self.persistence.push(symbol, action)
        reserved = sum(e["notional"] for e in self._pending_entries.values())
        # No A/B, o estado de validação aplicado é o do modelo que respondeu, não o global (V08).
        model_gates, model_multiplier = self._gates_for(calibrator.model_name)
        plan = decide_execution(
            settings=self.settings, gate=self.gate, sizer=self.sizer, calibrator=calibrator, outcome=outcome,
            calibrated=calibrated, snapshot=ctx.snapshot, agg_bars=ctx.agg_bars, now=now, position_qty=position_qty,
            open_positions=len(state.get("positions", [])), pending_entries=len(self._pending_entries),
            equity_usd=state.get("net_liq_usd"), available_funds_usd=state.get("available_funds_usd"),
            reserved_notional=reserved, persistence=persistence, pending_close=symbol in self._pending_close,
            pending_entry=any(e["symbol"] == symbol for e in self._pending_entries.values()) or self.ibkr.has_pending_entry(symbol),
            global_gate=global_gate, sentiment=sentiment, vol_width=vol_width, risk_multiplier=model_multiplier,
            gates_passed=model_gates,
            external_position=symbol in self._external_positions or self._is_external(symbol, position_qty))
        if not isinstance(plan, Skip) and symbol in self._discrepancies:
            plan = Skip("discrepância posição/saídas por resolver (reconciliação em curso)")
        if isinstance(plan, Skip):
            return self._skip(decision_id, symbol, action, plan.reason)
        if plan.kind == "CLOSE":
            # Um fecho REDUZ exposição: a pausa de novas entradas não o trava (Z03); as restantes validações mantêm-se.
            return await self._execute_close(plan, ctx, decision_id, position_qty, generation)
        if self.entries_paused:
            return self._skip(decision_id, symbol, action, f"novas entradas em pausa ({self.db.get_kv('entries_paused_reason') or 'sem razão'})")
        await self._execute_entry(plan, ctx, decision_id, generation)

    async def _execute_close(self, plan: ExecutionPlan, ctx: DecisionContext, decision_id: int, position_qty: float,
                             generation: Optional[int] = None) -> None:
        """Fecho por sinal contrário, serializado por ativo e com estado CLOSING instalado ANTES de cancelar
        os filhos (N02): a reproteção automática vê o fecho em curso e não repõe TP/SL."""
        symbol = plan.symbol
        generation = self.generation if generation is None else generation
        async with self._lock_for(symbol):
            if symbol in self._pending_close:
                return self._skip(decision_id, symbol, plan.action, "fecho de posição ainda pendente")
            self._pending_close[symbol] = {"state": "CLOSING", "order_id": None, "group_id": None, "qty": 0.0,
                                           "filled": 0.0, "ts": datetime.now(timezone.utc)}
            try:
                own = abs(self._own_qty(symbol))
                result = await self.ibkr.close_position(symbol, authorize=self._authorize(generation),
                                                        max_qty=own if (own > 0 and not self.settings.manage_external_positions) else None)
            except Exception:
                self._pending_close.pop(symbol, None)
                raise
            if not result or result.get("closed_by_children") or result.get("aborted"):
                self._pending_close.pop(symbol, None)
                reason = ("falha ao fechar posição" if not result else
                          "posição já fechada pelo stop/TP" if result.get("closed_by_children") else "fecho abortado")
                self._skip(decision_id, symbol, plan.action, reason)
                if result and result.get("conflict"):
                    self.bus.emit("log", level="CRITICAL", message=f"[{symbol}] fecho bloqueado por ordens manuais {result['conflict']}")
                if result and result.get("needs_protection"):
                    positions = {p["symbol"]: p for p in self.ibkr.portfolio_state().get("positions", [])}
                    if symbol in positions:
                        await self._protect_if_naked_locked(symbol, positions[symbol])
                return
            group_id = self.db.insert_order_group(
                symbol=symbol, decision_id=decision_id, role="CLOSE", direction=result["direction"], qty=result["qty"],
                parent_order_id=result["order_id"], tp_order_id=None, sl_order_id=None, ref_price=ctx.snapshot.price,
                tp_price=None, sl_price=None, account=self.ibkr.account, con_id=self.ibkr.con_id(symbol))
            self.db.add_exit_coverage(group_id, [int(t["id"]) for t in self.db.open_trades(symbol)])  # Z04
            self._pending_close[symbol] = {"state": "SENT", "order_id": result["order_id"], "group_id": group_id,
                                           "qty": float(result["qty"]), "filled": 0.0, "ts": datetime.now(timezone.utc)}
        self.persistence.reset(symbol)
        self.db.mark_decision(decision_id, executed=True)
        self.bus.emit("decision_result", decision_id=decision_id, executed=True, reason="fecho de posição")
        log.warning("[%s] Sinal %s contra posição existente: posição a FECHAR (ordem %d, x%g).",
                    symbol, plan.action, result["order_id"], result["qty"])

    async def _execute_entry(self, plan: ExecutionPlan, ctx: DecisionContext, decision_id: int, generation: int) -> None:
        import time as _time

        symbol, sizing = plan.symbol, plan.sizing
        assert sizing is not None
        if generation != self.generation or not self.trading_enabled:
            return self._skip(decision_id, symbol, plan.action, "decisão invalidada antes da ordem")
        # Cotação fresca: a referência da decisão é o fecho da vela anterior à inferência (N15/F12).
        quote = self.ibkr.last_price(symbol)
        if quote is not None:
            last_price, last_ts = quote
            if (datetime.now(timezone.utc) - last_ts).total_seconds() > self.settings.max_bar_age_seconds + 60:
                return self._skip(decision_id, symbol, plan.action, f"cotação desatualizada ({last_ts.isoformat()})")
            drift = abs(last_price - ctx.snapshot.price) / ctx.snapshot.price if ctx.snapshot.price else 0.0
            if plan.limit_price is not None and ((plan.action == "BUY" and last_price > plan.limit_price)
                                                 or (plan.action == "SELL" and last_price < plan.limit_price)):
                return self._skip(decision_id, symbol, plan.action,
                                  f"preço atual {last_price:.2f} já além do limite {plan.limit_price:.2f} "
                                  f"(referência {ctx.snapshot.price:.2f}, desvio {drift:.2%})")
            if drift > max(self.settings.max_entry_slippage_pct, 0.001) * 2:
                return self._skip(decision_id, symbol, plan.action,
                                  f"preço atual {last_price:.2f} desviou {drift:.2%} da referência {ctx.snapshot.price:.2f}")
        self.db.update_decision(decision_id, threshold_used=plan.threshold)
        result = await self.ibkr.place_bracket(symbol, plan.action, sizing.qty, ctx.snapshot.price,
                                               stop_price=sizing.stop_price, tp_price=sizing.tp_price,
                                               trailing=self.settings.use_trailing_stop, limit_price=plan.limit_price,
                                               authorize=self._authorize(generation, bar_time=ctx.snapshot.bar_time, symbol=symbol,
                                                                         action=plan.action, limit_price=plan.limit_price, entry=True))
        if not result:
            return self._skip(decision_id, symbol, plan.action, "Bracket não enviado (falha ou autorização revogada)")
        direction = 1 if plan.action == "BUY" else -1
        group_id = self.db.insert_order_group(
            symbol=symbol, decision_id=decision_id, role="ENTRY", direction=direction, qty=sizing.qty,
            parent_order_id=result["parent_order_id"], tp_order_id=result["tp_order_id"],
            sl_order_id=result["sl_order_id"], ref_price=ctx.snapshot.price,
            tp_price=result["tp_price"], sl_price=result["sl_price"], account=self.ibkr.account,
            con_id=self.ibkr.con_id(symbol))
        trade_id = self.db.open_trade(symbol=symbol, decision_id=decision_id, group_id=group_id, direction=direction,
                                      qty=sizing.qty, stop_price=sizing.stop_price, tp_price=sizing.tp_price,
                                      risk_amount=sizing.risk_amount)
        self._pending_entries[result["parent_order_id"]] = {
            "symbol": symbol, "qty": float(sizing.qty), "notional": sizing.qty * ctx.snapshot.price,
            "ts": datetime.now(timezone.utc), "deadline": _time.monotonic() + self.settings.entry_timeout_seconds,
            "trade_id": trade_id, "filled": 0.0}
        self.db.mark_decision(decision_id, executed=True)
        net_gain = sizing.qty * abs(sizing.tp_price - ctx.snapshot.price) - plan.cost
        self.bus.emit("decision_result", decision_id=decision_id, executed=True,
                      reason=f"{plan.action} x{sizing.qty} SL {sizing.stop_price:.2f} TP {sizing.tp_price:.2f} · "
                             f"custo {plan.cost:.2f} USD · ganho líquido no TP {net_gain:.2f} USD")
        self.persistence.reset(symbol)
        log.warning("[%s] EXECUTADO %s x%d @~%.2f (limit %s) | SL %.2f | TP %.2f | risco %.0f USD (ATR %.2f) | "
                    "p=%.2f ≥ %.2f | %s",
                    symbol, plan.action, sizing.qty, ctx.snapshot.price,
                    f"{plan.limit_price:.2f}" if plan.limit_price else "mercado", sizing.stop_price, sizing.tp_price,
                    sizing.risk_amount, sizing.atr_used, plan.probability, plan.threshold, "; ".join(plan.notes),
                    extra={"category": "ordem"})

    # --------------------------------------------------------------- gates
    def _apply_gates(self, gates: Optional[dict[str, Any]]) -> None:
        if not gates:
            return
        # Só gates da PRÓPRIA experiência (modelo + versão do prompt atuais) validam o modelo atual (V08/A11).
        expected = Analytics.experiment_id(self.brain.model, self.brain.prompt_version)
        if gates.get("experiment") != expected:
            log.warning("Gates da experiência %r ignorados: a experiência atual é %r.", gates.get("experiment"), expected)
            return
        self.gates_passed = bool(gates.get("all_passed"))
        self.risk_multiplier = 1.0 if self.gates_passed else self.settings.learning_risk_multiplier
        self.db.set_kv(self._gates_key(self.brain.model), "1" if self.gates_passed else "0")
        if not self.gates_passed:
            failed = [k for k, v in gates.get("checks", {}).items() if not v]
            log.warning("Gates estatísticos NÃO passam (%s). Risco por trade %.2f%% × %.2f.",
                        ", ".join(failed), self.settings.risk_per_trade_pct * 100, self.risk_multiplier)
        else:
            log.warning("Gates estatísticos PASSAM. Risco por trade %.2f%%.", self.settings.risk_per_trade_pct_validated * 100)

    def _load_gate_state(self) -> None:
        self.gates_passed, self.risk_multiplier = self._gates_for(self.brain.model)

    # ------------------------------------------------------------ callbacks
    def _price_at(self, symbol: str, when: datetime) -> Optional[float]:
        return self.ibkr.price_at(symbol, when)

    def _bars_between(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        return self.ibkr.bars_between(symbol, start, end)

    def _on_bar(self, symbol: str, bars: Any, has_new_bar: bool) -> None:
        if has_new_bar and bars:
            last = bars[-1]
            self.bus.emit("bar", symbol=symbol, time=IBKRClient.bar_time_utc(last).isoformat(),
                          close=float(last.close), volume=float(last.volume))

    def _on_disconnect(self) -> None:
        self.generation += 1
        self._reconciled = False
        self._emit_status()

    def _on_order_status(self, trade: Any) -> None:
        status = trade.orderStatus.status
        order_id = trade.order.orderId
        symbol = trade.contract.symbol
        # Identidade COMPLETA antes de tocar em histórico, reservas ou fechos (W03): conta, clientId, orderRef,
        # contrato (símbolo/conId) e o grupo a que a ordem pertence. Um estado de outro cliente/conta/contrato
        # com o mesmo orderId nunca altera o estado do motor.
        if not self.ibkr.order_is_ours(trade.order, trade.contract):
            log.debug("Estado da ordem %d (%s) de outra conta/cliente: ignorado.", order_id, symbol)
            return
        group = self.db.group_for_order(order_id, symbol=symbol, con_id=getattr(trade.contract, "conId", None) or None,
                                        account=getattr(trade.order, "account", None) or None)
        if group is None and order_id not in self._pending_entries and not any(
                pc.get("order_id") == order_id for pc in self._pending_close.values()):
            return
        perm_id = getattr(trade.order, "permId", None)
        if perm_id and group is not None:
            self.db.set_perm_id(int(order_id), int(perm_id), group_id=int(group["id"]), overwrite=True)  # identidade durável (V03/W03)
            self._reconcile_unallocated_fills(symbol, order_id=int(order_id))
        if status in ("Cancelled", "Inactive", "ApiCancelled"):
            intentional = self.ibkr.is_intentional_cancel(order_id)
            self.ibkr.forget_cancel(order_id)
            log.info("Ordem %d (%s %s) -> %s%s", order_id, trade.order.action, symbol, status,
                     " (cancelada pelo bot)" if intentional else "", extra={"category": "ordem"})
            # Fecho pendente cancelado/rejeitado: libertar o bloqueio e repor cobertura.
            pc = self._pending_close.get(symbol)
            if pc and pc.get("order_id") == order_id:
                self._pending_close.pop(symbol, None)
                log.error("[%s] Ordem de fecho %d terminou em %s sem executar: posição mantém-se; cobertura será reposta.",
                          symbol, order_id, status)
                if self.loop:
                    self.loop.create_task(self._reprotect(symbol))
            # Entrada pendente cancelada: libertar reserva e marcar o trade como cancelado.
            entry = self._pending_entries.pop(order_id, None)
            if entry and entry["filled"] <= 0 and entry.get("trade_id") is not None:
                self.db._execute("UPDATE trades SET status='CANCELLED', exit_reason='CANCELLED' WHERE id=? AND filled_qty=0",
                                 (entry["trade_id"],))
            # Filho de proteção REJEITADO/cancelado externamente com posição aberta: verificar cobertura.
            # Cancelamentos intencionais (fecho em curso, substituição de órfãos) não disparam reproteção (N02).
            if intentional:
                return
            if group and group["role"] == "ENTRY" and self.db.order_leg(group, order_id) in ("TP", "SL") \
                    and symbol not in self._pending_close and self.loop:
                self.loop.create_task(self._reprotect(symbol))

    def _reconcile_unallocated_fills(self, symbol: str, order_id: Optional[int] = None) -> None:
        """Execuções já registadas mas sem alocação são reprocessadas com a sua identidade ORIGINAL persistida
        (conta, clientId, permId, orderRef, conId): identidade incompatível continua incompatível; só a
        identidade antes desconhecida pode passar a corresponder (W03/X02). Limitado à ordem indicada."""
        for row in self.db.unallocated_fills(symbol, order_id=order_id):
            execution = SimpleNamespace(execId=row["exec_id"], orderId=int(row["order_id"] or 0), side=row["side"],
                                        shares=float(row["shares"]), price=float(row["price"]),
                                        time=datetime.fromisoformat(row["ts"]), acctNumber=row.get("account") or "",
                                        clientId=row.get("client_id"), orderRef=row.get("order_ref"), permId=row.get("perm_id"))
            fill = SimpleNamespace(contract=SimpleNamespace(symbol=symbol, secType="STK", conId=row.get("con_id")),
                                   execution=execution, commissionReport=None)
            self._on_fill(None, fill, reprocess=True)

    async def _reprotect(self, symbol: str) -> None:
        await asyncio.sleep(1.0)  # dá tempo ao OCA irmão/fill de chegar
        async with self._lock_for(symbol):
            if symbol in self._pending_close:
                return
            positions = {p["symbol"]: p for p in self.ibkr.portfolio_state().get("positions", [])}
            if symbol in positions and not self.ibkr.has_protective_orders(symbol):
                log.critical("[%s] Proteção perdida (filho cancelado/rejeitado): a repor.", symbol)
                await self._protect_if_naked_locked(symbol, positions[symbol])

    def _on_fill(self, trade: Any, fill: Any, reprocess: bool = False) -> None:
        execution = fill.execution
        symbol = fill.contract.symbol
        if getattr(fill.contract, "secType", "STK") != "STK":
            return
        if self.ibkr.account and getattr(execution, "acctNumber", "") and execution.acctNumber != self.ibkr.account:
            return
        # Identidade da execução (N06): orderIds são por cliente; uma execução de outro clientId (TWS manual,
        # outra sessão) com o mesmo número nunca pode tocar nos trades do bot.
        client_id = getattr(execution, "clientId", None)
        if client_id is not None and str(client_id) != "" and int(client_id) != int(self.settings.ib_client_id):
            # O cliente 0 (TWS manual) é uma identidade como outra qualquer, não ausência de identidade (V03).
            log.info("Execução %s de outro cliente (clientId %s): ignorada.", symbol, client_id)
            return
        exec_ref = getattr(execution, "orderRef", None)
        if self.settings.order_ref and exec_ref is not None and exec_ref != self.settings.order_ref:
            log.info("Execução %s com orderRef %r (não é do bot): ignorada.", symbol, exec_ref)
            return
        perm_id = getattr(execution, "permId", None) or None
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
                                  commission=commission_value, commission_estimated=estimated,
                                  account=getattr(execution, "acctNumber", None) or None,
                                  client_id=int(client_id) if client_id not in (None, "") else None, perm_id=perm_id,
                                  order_ref=exec_ref, con_id=getattr(fill.contract, "conId", None) or None)
        if not new:
            # Já registada: só o SALDO por alocar volta a ser processado (idempotente) — uma execução parcialmente
            # alocada (saída recebida antes do resto da entrada) não se perde da recuperação (W03/AA03).
            allocated = self.db.allocated_shares(execution.execId)
            if allocated >= float(execution.shares) - 1e-9:
                return
            stored = self.db.fill_by_exec(execution.execId)
            if stored is not None:
                commission_value = float(stored.get("commission") or commission_value)
            if allocated > 1e-9:
                total = float(execution.shares)
                remaining_shares = total - allocated
                commission_value = commission_value * remaining_shares / max(total, 1e-9)
                execution = SimpleNamespace(**{k: getattr(execution, k) for k in dir(execution) if not k.startswith("_")})
                execution.shares = remaining_shares
        if new:
            log.info("Execução %s %s x%g @ %.2f (ordem %d) · comissão %.2f USD%s", symbol, execution.side, execution.shares,
                     execution.price, execution.orderId, commission_value, " (estimada)" if estimated else "",
                     extra={"category": "ordem"})
        group = self.db.group_for_order(execution.orderId, symbol=symbol, con_id=getattr(fill.contract, "conId", None),
                                        account=getattr(execution, "acctNumber", None) or None, perm_id=perm_id)
        if not group:
            if perm_id and self.db.trades_for_order(int(execution.orderId), symbol=symbol):
                # Há grupos do bot com este orderId mas nenhum com permId conhecido igual: identidade AMBÍGUA. A execução
                # fica pendente (não consome nenhum trade) e o ativo bloqueado até a corretora revelar o permId (AB01).
                self._discrepancies.add(symbol)
                if not reprocess:
                    log.critical("[%s] Execução %s (ordem %d, permId %s) com identidade ambígua entre grupos do bot: fica por "
                                 "alocar até o permId da ordem ser conhecido; decisões bloqueadas.", symbol, execution.execId,
                                 execution.orderId, perm_id)
            elif not reprocess:
                log.warning("Execução %s (ordem %d) sem grupo conhecido para este contrato/conta: ordem externa ou de "
                            "outra sessão; só registada (reconciliável quando a identidade for conhecida).", symbol, execution.orderId)
            return
        if group["role"] == "ENTRY":
            trade_row = self.db.trade_for_group(group["id"])
            if trade_row is None:
                trade_id = self.db.open_trade(symbol=symbol, decision_id=group["decision_id"], group_id=group["id"],
                                              direction=group["direction"], qty=group["qty"])
            else:
                trade_id = trade_row["id"]
            leg = self.db.order_leg(group, execution.orderId)
            if leg == "PARENT":
                booked = self.db.record_entry_fill(trade_id, float(execution.shares), float(execution.price), ts, commission_value)
                self.db.allocate_fill(execution.execId, trade_id, float(execution.shares), commission_value)
                if booked.get("reopened"):
                    # Entrada tardia excede as saídas de um trade dado como fechado: o saldo volta a ser próprio, o ativo
                    # fica em discrepância até reconciliar com a corretora e o rótulo da decisão deixa de ser final (AB04).
                    self._discrepancies.add(symbol)
                    reopened = booked["trade"]
                    if reopened.get("decision_id"):
                        self.db.unfinalize_label(int(reopened["decision_id"]))
                    log.critical("[%s] Trade #%d REABERTO por entrada tardia: %g executadas vs %g saídas; saldo próprio %g a "
                                 "reconciliar com a corretora.", symbol, trade_id, float(reopened["filled_qty"] or 0),
                                 float(reopened["exit_qty"] or 0), float(reopened["filled_qty"] or 0) - float(reopened["exit_qty"] or 0))
                entry = self._pending_entries.get(execution.orderId)
                if entry:
                    entry["filled"] += float(execution.shares)
                    if entry["filled"] + 1e-9 >= entry["qty"]:
                        self._pending_entries.pop(execution.orderId, None)
                if not reprocess and self.db.unallocated_fills(symbol):
                    # Uma saída recebida ANTES deste resto da entrada ficou com saldo por alocar: recupera-o agora (AA03).
                    self._reconcile_unallocated_fills(symbol)
            else:
                reason = "TP" if leg == "TP" else "SL"  # inclui filhos já substituídos (order_history, N07)
                self._allocate_exit(symbol, trade_id, execution, ts, reason, commission_value, group_id=int(group["id"]))
        elif group["role"] == "CLOSE":
            self._allocate_exit(symbol, None, execution, ts, "SIGNAL", commission_value, group_id=int(group["id"]))
            pc = self._pending_close.get(symbol)
            if pc and pc.get("order_id") == execution.orderId:
                pc["filled"] += float(execution.shares)
                if pc["filled"] + 1e-9 >= pc["qty"]:
                    self._pending_close.pop(symbol, None)

    def _allocate_exit(self, symbol: str, first_trade_id: Optional[int], execution: Any, ts: datetime, reason: str,
                       commission_value: float, group_id: Optional[int] = None) -> None:
        """Distribui uma execução de saída APENAS pelos trades que a ordem cobre (identidade persistida: grupo da
        ordem, pernas arquivadas e ``exit_coverage``), começando pelo trade da ordem, sem deixar ``exit_qty``
        ultrapassar ``filled_qty`` (Y07/Z04). Trades cobertos já fechados por reconciliação recebem a execução
        tardia como correção (sem reabrir); nunca se toca num trade novo do mesmo ativo. Uma execução REAL consome o
        ajuste provisório só quando o explica (Z05). Sem saldo coberto, a execução fica por alocar (discrepância)."""
        shares = float(execution.shares)
        remaining = shares
        order_id = int(execution.orderId or 0)
        covered = self.db.trades_for_order(order_id, symbol=symbol, perm_id=getattr(execution, "permId", None) or None,
                                           group_id=group_id)  # identidade completa até à cobertura (AA02)
        if group_id is not None:
            covered_ids = {int(r["id"]) for r in covered}
            covered += [r for r in self.db._query("SELECT * FROM trades WHERE id IN (SELECT trade_id FROM exit_coverage WHERE group_id=?)",
                                                  (group_id,)) if int(r["id"]) not in covered_ids]
        if first_trade_id is not None and not any(int(r["id"]) == int(first_trade_id) for r in covered):
            covered += self.db._query("SELECT * FROM trades WHERE id=?", (int(first_trade_id),))
        open_rows = [r for r in covered if r["status"] == "OPEN"]
        closed_rows = [r for r in covered if r["status"] != "OPEN"]
        if first_trade_id is not None:
            open_rows.sort(key=lambda r: 0 if int(r["id"]) == int(first_trade_id) else 1)
            closed_rows.sort(key=lambda r: 0 if int(r["id"]) == int(first_trade_id) else 1)
        for row in open_rows + closed_rows:
            if remaining <= 1e-9:
                break
            balance = float(row["filled_qty"] or row["qty"]) - float(row["exit_qty"] or 0)
            portion = min(remaining, balance)
            if portion <= 1e-9:
                continue
            self.db.consume_adjustment(int(row["id"]), portion, order_id=order_id)
            share = commission_value * portion / max(shares, 1e-9)
            self.db.allocate_fill(execution.execId, int(row["id"]), portion, share, reason=reason)
            if row["status"] == "OPEN":
                updated = self.db.record_exit_fill(int(row["id"]), portion, float(execution.price), ts, reason, share)
                updated = self.db.refresh_exit_summary(int(row["id"])) or updated  # cronologia das execuções (AB06)
                self._announce_close(updated, updated.get("exit_reason") or reason)
                if updated["status"] == "CLOSED":
                    self._relabel_decision(updated)
            else:
                updated = self.db.record_late_exit_fill(int(row["id"]), portion, float(execution.price), ts, reason, share)
                updated = self.db.refresh_exit_summary(int(row["id"])) or updated
                log.warning("Trade #%d %s (fechado por %s) corrigido por execução tardia %s: %g ações @ %.2f, P&L bruto %+.2f.",
                            row["id"], symbol, row.get("exit_reason"), execution.execId, portion, float(execution.price),
                            float(updated.get("gross_pnl") or 0.0))
                self._relabel_decision(updated)
            remaining -= portion
        if remaining > 1e-9:
            self._discrepancies.add(symbol)
            log.critical("[%s] Execução %s de %g ações excede os saldos dos trades que a ordem %d cobre em %g: fica por "
                         "alocar (discrepância registada).", symbol, execution.execId, shares, order_id, remaining)
        elif self.db.open_adjustments_for_symbol(symbol, include_closed=True) <= 1e-9 and self.db.get_kv(f"conflict:{symbol}"):
            self._resolve_conflict(symbol)  # as execuções explicaram toda a diferença
        elif not self.db.unallocated_fills(symbol) and self.db.open_adjustments_for_symbol(symbol, include_closed=True) <= 1e-9:
            self._discrepancies.discard(symbol)  # saldo por alocar recuperado na totalidade (AA03); o supervisor revalida o resto

    def _relabel_decision(self, trade_row: dict[str, Any]) -> None:
        """Uma correção tardia que muda o resultado de um trade encerrado atualiza, na mesma operação lógica, o rótulo
        FINAL da decisão associada e marca ``labels_changed_at`` (invalida a calibração dependente) (AA04)."""
        decision_id = trade_row.get("decision_id")
        if not decision_id:
            return
        rows = self.db._query("SELECT settled_ts, correct, label_source FROM decisions WHERE id=?", (int(decision_id),))
        if not rows or rows[0]["settled_ts"] is None:
            return  # ainda não avaliada: o avaliador usará o ledger já corrigido
        label, source = Settler._ledger_label(trade_row, datetime.now(timezone.utc))
        source = source or "ledger:OUTRO"
        if rows[0]["correct"] == label and rows[0]["label_source"] == source:
            return
        self.db.finalize_label(int(decision_id), correct=label, label_source=source)
        log.warning("Decisão #%d: rótulo final atualizado por execução tardia (%s -> %s, acerto %s).",
                    int(decision_id), rows[0]["label_source"], source, label)

    def _on_commission(self, trade: Any, fill: Any, report: Any) -> None:
        """CommissionReport real da IBKR: substitui a estimativa e corrige exatamente os trades que a execução tocou."""
        commission_value = getattr(report, "commission", None)
        exec_id = getattr(getattr(fill, "execution", None), "execId", None)
        if commission_value is None or not exec_id:
            return
        delta = self.db.set_fill_commission(exec_id, float(commission_value))
        if delta is None or abs(delta) < 1e-9:
            return
        allocations = self.db.allocations_for_fill(exec_id)
        if not allocations:
            return
        stored = self.db.fill_by_exec(exec_id)
        total = float(stored["shares"]) if stored and stored.get("shares") else sum(float(a["shares"]) for a in allocations) or 1.0
        # A comissão devida a cada alocação é a quota da quantidade TOTAL da execução; só a diferença face ao já
        # atribuído é aplicada, e a parte ainda não alocada conserva a sua quota para a recuperação (AB02).
        applied = 0.0
        for a in allocations:
            due = float(commission_value) * float(a["shares"]) / total
            diff = due - float(a["commission"] or 0.0)
            if abs(diff) > 1e-9:
                self.db.set_allocation_commission(int(a["id"]), due)
                self.db.apply_commission_delta(int(a["trade_id"]), diff)
                applied += diff
        log.info("Comissão real %s: %.2f USD (ajuste %+.2f em %d alocação(ões))", fill.contract.symbol, float(commission_value),
                 applied, len(allocations), extra={"category": "ordem"})

    def _announce_close(self, trade_row: dict[str, Any], reason: str) -> None:
        if trade_row["status"] == "CLOSED":
            pnl = float(trade_row["pnl"])
            gross = float(trade_row.get("gross_pnl") or 0.0)
            comm = float(trade_row.get("commission") or 0.0)
            log.log(logging.INFO if pnl >= 0 else logging.WARNING,
                    "Trade #%d %s fechado por %s: P&L líquido %+.2f USD (bruto %+.2f, comissões %.2f)",
                    trade_row["id"], trade_row["symbol"], reason, pnl, gross, comm, extra={"category": "ordem"})
            if gross > 0 >= pnl:
                log.warning("Trade #%d: lucro bruto comido pelas comissões (%.2f de %.2f).", trade_row["id"], comm, gross)
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
            risk_pct=(self.settings.risk_per_trade_pct_validated if self.gates_passed
                      else self.settings.risk_per_trade_pct * self.risk_multiplier),
            llm_interval=self.settings.llm_interval_minutes,
            llm_samples=self.settings.llm_samples,
            n_trials=self.db.experiment_count(),
            mode=self.settings.trading_mode,
            port=self.settings.ib_port,
            accounts=[self.ibkr.account] if self.ibkr.account else list(getattr(self.ibkr, "accounts", [])),
            currency=self.ibkr.base_currency or "USD",
            lesson_texts=[l["text"] for l in self.db.active_lessons()[:5]],
            gates_detail=(self.db.latest_report("weekly") or {}).get("gates"),
            pending_entries=len(self._pending_entries),
            pending_closes=list(self._pending_close.keys()),
            reconciled=self._reconciled,
            external_positions=sorted(self._external_positions),
            discrepancies=sorted(self._discrepancies),
            entries_paused=self.entries_paused,
            entries_paused_reason=self.db.get_kv("entries_paused_reason"),
            bound_account=self.db.get_kv("bound_account"),
            experiment=self.calibrator.experiment,
            risk_summary={"stop_mode": self.settings.stop_mode, "atr_mult": self.settings.atr_stop_multiple,
                          "rr": self.settings.reward_risk_ratio, "daily_loss": self.settings.daily_loss_limit_pct,
                          "max_positions": self.settings.max_open_positions,
                          "cooldown": self.settings.cooldown_minutes,
                          "stoploss_guard": self.settings.stoploss_guard_count},
        )
