"""Motor de trading: loop ``asyncio`` numa thread dedicada.

Separação de responsabilidades:
- A GUI (thread principal, Tkinter) nunca chama ``ib_insync`` diretamente;
  envia pedidos através de ``TradingEngine.call()`` (``run_coroutine_threadsafe``).
- O motor publica estado/logs no ``UIBus`` e nunca toca em widgets.

Ciclo por ativo (a cada ``cycle_seconds``):
  velas 1 min -> indicadores -> snapshot -> Ollama -> parser -> gestão de
  risco -> ordem Bracket -> registo em SQLite.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Awaitable, Callable, Coroutine, Optional
from zoneinfo import ZoneInfo

from .config import Settings
from .database import Database
from .ibkr_client import IBKRClient
from .indicators import MarketSnapshot, build_snapshot
from .ollama_brain import Decision, OllamaBrain
from .retrospective import Retrospective
from .ui_bus import UIBus

log = logging.getLogger("trader.engine")
NY = ZoneInfo("America/New_York")


def in_regular_hours(now_utc: datetime) -> bool:
    local = now_utc.astimezone(NY)
    if local.weekday() >= 5:
        return False
    return dtime(9, 30) <= local.time() < dtime(16, 0)


class TradingEngine:
    def __init__(self, settings: Settings, db: Database, bus: UIBus, brain: OllamaBrain) -> None:
        self.settings = settings
        self.db = db
        self.bus = bus
        self.brain = brain
        self.ibkr = IBKRClient(
            settings,
            on_bar=self._on_bar,
            on_fill=self._on_fill,
            on_order_status=self._on_order_status,
            on_disconnect=self._on_disconnect,
        )
        self.retro = Retrospective(settings, db, brain, price_fetcher=self._history_prices)

        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._stop_event: Optional[asyncio.Event] = None
        self._decision_lock: Optional[asyncio.Lock] = None

        self.trading_enabled = False
        self.ollama_ok = False
        self.halted_day: Optional[date] = None
        self._day_start_net_liq: Optional[float] = None
        self._day_start_date: Optional[date] = None
        self._last_decided_bar: dict[str, datetime] = {}
        self._last_bar_update: dict[str, datetime] = {}
        self._pending_close: dict[str, int] = {}

    # ------------------------------------------------------------ threading
    def start(self) -> None:
        """Arranca a thread do motor (chamado uma vez pela GUI/main)."""
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
        """Agenda uma corrotina no loop do motor a partir de outra thread."""
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
        log.info("Motor de trading iniciado (modelo %s, limiar %.2f, prompt v%d)",
                 self.brain.model, self.settings.min_confidence, self.brain.prompt_version)
        await self._refresh_models()
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
            log.info("Modelo Ollama alterado para %s", model)
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

    async def run_retrospective(self) -> dict[str, Any]:
        log.info("Retrospetiva manual iniciada…")
        report = await self.retro.run()
        self.bus.emit("retrospective", report=report)
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
        if not self.ibkr.connected:
            ok = await self.ibkr.connect()
            if not ok:
                return False
        for symbol in self.settings.symbols:
            await self.ibkr.subscribe_bars(symbol)
        self._emit_status()
        return True

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
                log.log(logging.INFO if ok else logging.ERROR,
                        "Ollama %s", "disponível" if ok else "INDISPONÍVEL em " + self.settings.ollama_url)
                if ok:
                    await self._refresh_models()
            self._emit_status()

    async def _portfolio_loop(self) -> None:
        tick = 0
        while True:
            try:
                state = self.ibkr.portfolio_state()
                self._update_day_baseline(state)
                state["day_pnl_pct"] = self._day_pnl_pct(state)
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
                report = await self.retro.run()
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
        self._update_day_baseline(state)
        if self._check_kill_switch(state):
            return
        state["day_pnl_pct"] = self._day_pnl_pct(state)
        for symbol in list(self.settings.symbols):
            if not self.trading_enabled or not self.ibkr.connected:
                return
            async with self._decision_lock:
                await self._process_symbol(symbol, state, now)

    async def _process_symbol(self, symbol: str, state: dict[str, Any], now: datetime) -> None:
        bars = self.ibkr.bars(symbol)
        if not bars:
            await self.ibkr.subscribe_bars(symbol)
            return
        # A última vela ainda está a formar-se; decidimos sobre a última fechada.
        closed = list(bars)[:-1] if len(bars) > 1 else list(bars)
        if len(closed) < self.settings.sma_slow + 1:
            log.debug("%s: apenas %d velas; a aguardar histórico suficiente.", symbol, len(closed))
            return
        last = closed[-1]
        bar_time = IBKRClient.bar_time_utc(last)
        if (now - bar_time).total_seconds() > self.settings.max_bar_age_seconds + 60:
            log.debug("%s: dados desatualizados (%s).", symbol, bar_time.isoformat())
            return
        if self._last_decided_bar.get(symbol) == bar_time:
            return  # já decidimos sobre esta vela
        snapshot = build_snapshot(
            symbol,
            [float(b.close) for b in closed],
            [float(b.volume) for b in closed],
            bar_time,
            rsi_period=self.settings.rsi_period,
            sma_fast_period=self.settings.sma_fast,
            sma_slow_period=self.settings.sma_slow,
            ema_period=self.settings.ema_period,
        )
        if snapshot is None:
            return
        self._last_decided_bar[symbol] = bar_time

        recent = self.db.recent_decisions(5, symbol)
        loop = asyncio.get_running_loop()
        started = loop.time()
        decision: Decision = await loop.run_in_executor(None, self.brain.decide, snapshot, state, recent)
        elapsed = loop.time() - started
        self.ollama_ok = decision.error not in ("timeout", "connection")

        position_qty = self.ibkr.position_qty(symbol)
        decision_id = self.db.insert_decision(
            symbol=symbol, model=self.brain.model, action=decision.acao, confidence=decision.confianca,
            reason=decision.razao, snapshot=snapshot.to_dict(), position_qty=position_qty,
            net_liq=state.get("net_liq"), prompt_version=self.brain.prompt_version,
            parse_ok=decision.parse_ok, raw_response=decision.raw,
        )
        level = logging.INFO if decision.parse_ok else logging.WARNING
        log.log(
            level,
            "[%s] %s conf=%.2f (%.1fs) | %.2f RSI=%s | %s",
            symbol, decision.acao, decision.confianca, elapsed, snapshot.price,
            f"{snapshot.rsi:.0f}" if snapshot.rsi is not None else "n/d", decision.razao,
            extra={"category": "ollama"},
        )
        self.bus.emit("decision", symbol=symbol, decision=decision.to_dict(), snapshot=snapshot.to_dict())
        await self._execute(decision, snapshot, state, decision_id, position_qty)

    # ------------------------------------------------------------ execução
    async def _execute(self, decision: Decision, snapshot: MarketSnapshot, state: dict[str, Any],
                       decision_id: int, position_qty: float) -> None:
        symbol = snapshot.symbol
        action = decision.acao

        def skip(reason: str) -> None:
            self.db.mark_decision(decision_id, executed=False, skip_reason=reason)
            if action != "HOLD":
                log.info("[%s] %s não executado: %s", symbol, action, reason)

        if action == "HOLD":
            return skip("HOLD")
        if not decision.parse_ok:
            return skip("resposta inválida")
        if decision.confianca < self.settings.min_confidence:
            return skip(f"confiança {decision.confianca:.2f} < limiar {self.settings.min_confidence:.2f}")
        if symbol in self._pending_close:
            return skip("fecho de posição ainda pendente")

        # Ação alinhada com posição existente -> nada a fazer (sem pirâmide).
        if (action == "BUY" and position_qty > 0) or (action == "SELL" and position_qty < 0):
            return skip("já posicionado nessa direção")

        # Ação oposta à posição -> fecha (cancela Bracket) e não inverte no mesmo ciclo.
        if (action == "BUY" and position_qty < 0) or (action == "SELL" and position_qty > 0):
            result = await self.ibkr.close_position(symbol)
            if result:
                group_id = self.db.insert_order_group(
                    symbol=symbol, decision_id=decision_id, role="CLOSE", direction=result["direction"],
                    qty=result["qty"], parent_order_id=result["order_id"], tp_order_id=None,
                    sl_order_id=None, ref_price=snapshot.price, tp_price=None, sl_price=None,
                )
                self._pending_close[symbol] = group_id
                self.db.mark_decision(decision_id, executed=True)
                log.warning("[%s] Sinal %s contra posição existente: posição FECHADA (ordem %d).",
                            symbol, action, result["order_id"])
            else:
                skip("falha ao fechar posição")
            return

        if action == "SELL" and not self.settings.allow_short:
            return skip("short desativado na configuração")
        if any(t.order.parentId == 0 and t.orderStatus.status in ("PreSubmitted", "Submitted", "PendingSubmit")
               for t in self.ibkr.open_trades_for(symbol)):
            return skip("ordem de entrada ainda pendente")
        open_positions = len(state.get("positions", []))
        if open_positions >= self.settings.max_open_positions:
            return skip(f"máximo de posições ({self.settings.max_open_positions}) atingido")

        net_liq = state.get("net_liq")
        if not net_liq or snapshot.price <= 0:
            return skip("NetLiq desconhecido")
        qty = math.floor(net_liq * self.settings.risk_fraction_per_trade / snapshot.price)
        if qty < 1:
            return skip("capital insuficiente para 1 ação")

        result = await self.ibkr.place_bracket(
            symbol, action, qty, snapshot.price,
            stop_loss_pct=self.settings.stop_loss_pct, take_profit_pct=self.settings.take_profit_pct,
        )
        if not result:
            return skip("falha ao colocar Bracket")
        direction = 1 if action == "BUY" else -1
        group_id = self.db.insert_order_group(
            symbol=symbol, decision_id=decision_id, role="ENTRY", direction=direction, qty=qty,
            parent_order_id=result["parent_order_id"], tp_order_id=result["tp_order_id"],
            sl_order_id=result["sl_order_id"], ref_price=snapshot.price,
            tp_price=result["tp_price"], sl_price=result["sl_price"],
        )
        self.db.open_trade(symbol=symbol, decision_id=decision_id, group_id=group_id, direction=direction, qty=qty)
        self.db.mark_decision(decision_id, executed=True)
        log.warning("[%s] EXECUTADO %s x%d @~%.2f | TP %.2f | SL %.2f | conf %.2f",
                    symbol, action, qty, snapshot.price, result["tp_price"], result["sl_price"], decision.confianca,
                    extra={"category": "ordem"})

    # ------------------------------------------------------- kill-switch
    def _update_day_baseline(self, state: dict[str, Any]) -> None:
        net_liq = state.get("net_liq")
        if net_liq is None:
            return
        today = datetime.now(timezone.utc).date()
        if self._day_start_date != today:
            persisted = self.db.first_net_liq_on(datetime.now(timezone.utc))
            self._day_start_net_liq = persisted if persisted is not None else net_liq
            self._day_start_date = today
            if self.halted_day and self.halted_day != today:
                self.halted_day = None
                log.info("Novo dia: kill-switch diário reposto.")

    def _day_pnl_pct(self, state: dict[str, Any]) -> Optional[float]:
        net_liq = state.get("net_liq")
        if net_liq is None or not self._day_start_net_liq:
            return None
        return round((net_liq - self._day_start_net_liq) / self._day_start_net_liq * 100.0, 3)

    def _check_kill_switch(self, state: dict[str, Any]) -> bool:
        today = datetime.now(timezone.utc).date()
        if self.halted_day == today:
            return True
        pct = self._day_pnl_pct(state)
        if pct is not None and pct <= -self.settings.daily_loss_limit_pct * 100.0:
            self.halted_day = today
            log.critical(
                "KILL-SWITCH: perda diária %.2f%% ultrapassa o limite de %.1f%%. "
                "Novas entradas suspensas até amanhã; posições abertas mantêm TP/SL.",
                pct, self.settings.daily_loss_limit_pct * 100.0,
            )
            self._emit_status()
            return True
        return False

    # ------------------------------------------------------------ callbacks
    def _on_bar(self, symbol: str, bars: Any, has_new_bar: bool) -> None:
        self._last_bar_update[symbol] = datetime.now(timezone.utc)
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
        new = self.db.insert_fill(
            exec_id=execution.execId, order_id=execution.orderId, symbol=symbol, side=execution.side,
            shares=float(execution.shares), price=float(execution.price), ts=ts,
        )
        if not new:
            return
        log.info("Execução %s %s x%g @ %.2f (ordem %d)", symbol, execution.side, execution.shares,
                 execution.price, execution.orderId, extra={"category": "ordem"})
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
                self.db.record_entry_fill(trade_id, float(execution.shares), float(execution.price), ts)
            else:
                reason = "TP" if execution.orderId == group["tp_order_id"] else "SL"
                updated = self.db.record_exit_fill(trade_id, float(execution.shares), float(execution.price), ts, reason)
                self._announce_close(updated, reason)
        elif group["role"] == "CLOSE":
            open_trades = self.db.open_trades(symbol)
            remaining = float(execution.shares)
            for row in open_trades:
                if remaining <= 0:
                    break
                portion = min(remaining, float(row["filled_qty"] or row["qty"]) - float(row["exit_qty"] or 0))
                if portion <= 0:
                    continue
                updated = self.db.record_exit_fill(row["id"], portion, float(execution.price), ts, "SIGNAL")
                remaining -= portion
                self._announce_close(updated, "SIGNAL")
            self._pending_close.pop(symbol, None)

    def _announce_close(self, trade_row: dict[str, Any], reason: str) -> None:
        if trade_row["status"] == "CLOSED":
            pnl = float(trade_row["pnl"])
            log.log(logging.INFO if pnl >= 0 else logging.WARNING,
                    "Trade #%d %s fechado por %s: P&L %+.2f USD", trade_row["id"], trade_row["symbol"], reason, pnl,
                    extra={"category": "ordem"})
            self.bus.emit("trade_closed", trade=trade_row)

    # ------------------------------------------------------------- helpers
    async def _history_prices(self, symbol: str) -> list[tuple[datetime, float]]:
        bars = await self.ibkr.fetch_history(symbol, duration="2 D", bar_size="1 min")
        return [(IBKRClient.bar_time_utc(b), float(b.close)) for b in bars]

    def _emit_status(self) -> None:
        self.bus.emit(
            "status",
            ibkr_connected=self.ibkr.connected,
            ollama_ok=self.ollama_ok,
            trading_enabled=self.trading_enabled,
            model=self.brain.model,
            halted=self.halted_day is not None,
            prompt_version=self.brain.prompt_version,
            min_confidence=self.settings.min_confidence,
            lessons=len(self.brain.lessons),
            symbols=list(self.settings.symbols),
        )
