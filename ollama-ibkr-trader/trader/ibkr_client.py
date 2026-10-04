"""Cliente assíncrono para a Interactive Brokers via ``ib_insync``.

Responsabilidades:
- Ligação/reconexão à TWS/Gateway em Paper Trading (porta 7497).
- Subscrição de velas de 1 minuto com ``keepUpToDate=True`` (streaming).
- Leitura do estado da conta e posições.
- Colocação de ordens *Bracket* (entrada a mercado + Take Profit + Stop Loss).
- Encaminhamento de execuções/estados de ordens para o motor.

Toda a API é pensada para ser chamada a partir da thread/loop do motor.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

try:  # ib_insync foi arquivado em 2024; ib_async é o fork mantido com a mesma API.
    from ib_insync import IB, BarDataList, Contract, Fill, LimitOrder, MarketOrder, Stock, StopOrder, Trade
except ImportError:  # pragma: no cover
    from ib_async import IB, BarDataList, Contract, Fill, LimitOrder, MarketOrder, Stock, StopOrder, Trade  # type: ignore

from .config import Settings

log = logging.getLogger("trader.ibkr")

BarCallback = Callable[[str, BarDataList, bool], None]
FillCallback = Callable[[Trade, Fill], None]
StatusCallback = Callable[[Trade], None]


def round_tick(price: float, tick: float = 0.01) -> float:
    return round(math.floor(price / tick + 0.5) * tick, 2)


class IBKRClient:
    def __init__(
        self,
        settings: Settings,
        *,
        on_bar: Optional[BarCallback] = None,
        on_fill: Optional[FillCallback] = None,
        on_order_status: Optional[StatusCallback] = None,
        on_disconnect: Optional[Callable[[], None]] = None,
    ) -> None:
        self.settings = settings
        self.ib: Optional[IB] = None
        self._contracts: dict[str, Contract] = {}
        self._bars: dict[str, BarDataList] = {}
        self._on_bar = on_bar
        self._on_fill = on_fill
        self._on_order_status = on_order_status
        self._on_disconnect = on_disconnect
        self._connecting = False

    # ----------------------------------------------------------- ligação
    @property
    def connected(self) -> bool:
        return self.ib is not None and self.ib.isConnected()

    async def connect(self) -> bool:
        if self.connected or self._connecting:
            return self.connected
        self._connecting = True
        try:
            # O IB() é criado aqui para ficar associado ao loop desta thread.
            if self.ib is None:
                self.ib = IB()
                self.ib.errorEvent += self._handle_error
                self.ib.disconnectedEvent += self._handle_disconnected
                self.ib.execDetailsEvent += self._handle_exec
                self.ib.orderStatusEvent += self._handle_order_status
            await self.ib.connectAsync(
                self.settings.ib_host, self.settings.ib_port,
                clientId=self.settings.ib_client_id, timeout=15,
            )
            self.ib.reqMarketDataType(self.settings.market_data_type)
            accounts = self.ib.managedAccounts()
            log.info("IBKR ligado (%s:%s) conta(s): %s",
                     self.settings.ib_host, self.settings.ib_port, ", ".join(accounts) or "?")
            return True
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as exc:
            log.error("Falha ao ligar à IBKR em %s:%s — %s. A TWS está aberta com a API ativa?",
                      self.settings.ib_host, self.settings.ib_port, exc)
            return False
        except Exception as exc:  # noqa: BLE001
            log.error("Erro inesperado ao ligar à IBKR: %s", exc)
            return False
        finally:
            self._connecting = False

    async def disconnect(self) -> None:
        if self.ib is not None and self.ib.isConnected():
            self.ib.disconnect()
        self._bars.clear()
        self._contracts.clear()

    def _handle_error(self, reqId: int, errorCode: int, errorString: str, contract: Any = None) -> None:
        # Códigos 2104/2106/2158 são mensagens informativas de "market data farm OK".
        if errorCode in (2104, 2106, 2158, 2107, 2108, 2119):
            log.debug("IBKR info %s: %s", errorCode, errorString)
            return
        if errorCode in (10167, 10168, 354):  # sem subscrição de dados em tempo real
            log.warning("IBKR aviso %s: %s (a usar dados atrasados)", errorCode, errorString)
            return
        log.warning("IBKR erro %s (req %s): %s", errorCode, reqId, errorString)

    def _handle_disconnected(self) -> None:
        log.error("Ligação à IBKR perdida.")
        self._bars.clear()
        if self._on_disconnect:
            self._on_disconnect()

    def _handle_exec(self, trade: Trade, fill: Fill) -> None:
        if self._on_fill:
            try:
                self._on_fill(trade, fill)
            except Exception as exc:  # noqa: BLE001
                log.exception("Erro no callback de execução: %s", exc)

    def _handle_order_status(self, trade: Trade) -> None:
        if self._on_order_status:
            try:
                self._on_order_status(trade)
            except Exception as exc:  # noqa: BLE001
                log.exception("Erro no callback de estado de ordem: %s", exc)

    # ------------------------------------------------------------ dados
    async def qualify(self, symbol: str) -> Optional[Contract]:
        if symbol in self._contracts:
            return self._contracts[symbol]
        assert self.ib is not None
        contract = Stock(symbol, self.settings.exchange, self.settings.currency)
        details = await self.ib.qualifyContractsAsync(contract)
        if not details:
            log.error("Contrato não encontrado: %s", symbol)
            return None
        self._contracts[symbol] = contract
        return contract

    async def subscribe_bars(self, symbol: str) -> bool:
        """Subscreve velas de 1 minuto em streaming (histórico + atualizações)."""
        if symbol in self._bars:
            return True
        assert self.ib is not None
        contract = await self.qualify(symbol)
        if contract is None:
            return False
        bars = await self.ib.reqHistoricalDataAsync(
            contract,
            endDateTime="",
            durationStr=self.settings.history_duration,
            barSizeSetting=self.settings.bar_size,
            whatToShow="TRADES",
            useRTH=self.settings.use_rth_for_bars,
            formatDate=2,  # UTC
            keepUpToDate=True,
        )
        if bars is None:
            log.error("Sem dados históricos para %s", symbol)
            return False
        self._bars[symbol] = bars

        def _relay(bar_list: BarDataList, has_new_bar: bool, _symbol: str = symbol) -> None:
            if self._on_bar:
                try:
                    self._on_bar(_symbol, bar_list, has_new_bar)
                except Exception as exc:  # noqa: BLE001
                    log.exception("Erro no callback de velas: %s", exc)

        bars.updateEvent += _relay
        log.info("Subscrito %s: %d velas de %s carregadas", symbol, len(bars), self.settings.bar_size)
        return True

    def bars(self, symbol: str) -> Optional[BarDataList]:
        return self._bars.get(symbol)

    async def fetch_history(self, symbol: str, duration: str = "2 D", bar_size: str = "1 min") -> list[Any]:
        """Histórico pontual (usado pela retrospetiva)."""
        if not self.connected:
            return []
        assert self.ib is not None
        contract = await self.qualify(symbol)
        if contract is None:
            return []
        bars = await self.ib.reqHistoricalDataAsync(
            contract, endDateTime="", durationStr=duration, barSizeSetting=bar_size,
            whatToShow="TRADES", useRTH=False, formatDate=2, keepUpToDate=False,
        )
        return list(bars or [])

    # ---------------------------------------------------------- carteira
    def _account_value(self, tag: str) -> Optional[float]:
        assert self.ib is not None
        best: Optional[float] = None
        for av in self.ib.accountValues():
            if av.tag != tag:
                continue
            if av.currency in ("BASE", self.settings.currency, ""):
                try:
                    best = float(av.value)
                except ValueError:
                    continue
                if av.currency == "BASE":
                    break
        return best

    def portfolio_state(self) -> dict[str, Any]:
        if not self.connected:
            return {"connected": False, "net_liq": None, "cash": None,
                    "unrealized": None, "realized": None, "positions": []}
        assert self.ib is not None
        positions = []
        for item in self.ib.portfolio():
            if not item.position:
                continue
            positions.append({
                "symbol": item.contract.symbol,
                "qty": float(item.position),
                "avg_cost": float(item.averageCost or 0.0),
                "market_price": float(item.marketPrice or 0.0),
                "market_value": float(item.marketValue or 0.0),
                "unrealized_pnl": float(item.unrealizedPNL or 0.0),
                "realized_pnl": float(item.realizedPNL or 0.0),
            })
        return {
            "connected": True,
            "net_liq": self._account_value("NetLiquidation"),
            "cash": self._account_value("TotalCashValue"),
            "unrealized": self._account_value("UnrealizedPnL"),
            "realized": self._account_value("RealizedPnL"),
            "positions": positions,
        }

    def position_qty(self, symbol: str) -> float:
        if not self.connected:
            return 0.0
        assert self.ib is not None
        for pos in self.ib.positions():
            if pos.contract.symbol == symbol:
                return float(pos.position)
        return 0.0

    def open_trades_for(self, symbol: str) -> list[Trade]:
        if not self.connected:
            return []
        assert self.ib is not None
        return [t for t in self.ib.openTrades() if t.contract.symbol == symbol]

    # ------------------------------------------------------------ ordens
    async def place_bracket(
        self, symbol: str, action: str, quantity: int, ref_price: float,
        stop_loss_pct: float, take_profit_pct: float,
    ) -> Optional[dict[str, Any]]:
        """Entrada a MERCADO com filhos TP (limit) e SL (stop) ligados por ``parentId``.

        Os filhos são OCA (um cancela o outro) por partilharem o mesmo parent.
        """
        if not self.connected or quantity <= 0:
            return None
        assert self.ib is not None
        contract = await self.qualify(symbol)
        if contract is None:
            return None
        action = action.upper()
        reverse = "SELL" if action == "BUY" else "BUY"
        if action == "BUY":
            tp_price = round_tick(ref_price * (1 + take_profit_pct))
            sl_price = round_tick(ref_price * (1 - stop_loss_pct))
        else:
            tp_price = round_tick(ref_price * (1 - take_profit_pct))
            sl_price = round_tick(ref_price * (1 + stop_loss_pct))

        parent = MarketOrder(action, quantity)
        parent.orderId = self.ib.client.getReqId()
        parent.transmit = False
        parent.tif = "GTC"
        take_profit = LimitOrder(reverse, quantity, tp_price)
        take_profit.orderId = self.ib.client.getReqId()
        take_profit.parentId = parent.orderId
        take_profit.transmit = False
        take_profit.tif = "GTC"
        stop_loss = StopOrder(reverse, quantity, sl_price)
        stop_loss.orderId = self.ib.client.getReqId()
        stop_loss.parentId = parent.orderId
        stop_loss.transmit = True  # transmite o grupo inteiro
        stop_loss.tif = "GTC"

        trades = [self.ib.placeOrder(contract, o) for o in (parent, take_profit, stop_loss)]
        log.info(
            "Bracket %s %s x%d @~%.2f | TP %.2f | SL %.2f (ids %d/%d/%d)",
            action, symbol, quantity, ref_price, tp_price, sl_price,
            parent.orderId, take_profit.orderId, stop_loss.orderId,
        )
        return {
            "parent_order_id": parent.orderId,
            "tp_order_id": take_profit.orderId,
            "sl_order_id": stop_loss.orderId,
            "tp_price": tp_price,
            "sl_price": sl_price,
            "trades": trades,
        }

    async def close_position(self, symbol: str) -> Optional[dict[str, Any]]:
        """Cancela ordens pendentes do ativo e fecha a posição a mercado."""
        if not self.connected:
            return None
        assert self.ib is not None
        qty = self.position_qty(symbol)
        if qty == 0:
            return None
        contract = await self.qualify(symbol)
        if contract is None:
            return None
        for trade in self.open_trades_for(symbol):
            self.ib.cancelOrder(trade.order)
        await asyncio.sleep(0.5)  # dá tempo aos cancelamentos antes da ordem de fecho
        action = "SELL" if qty > 0 else "BUY"
        order = MarketOrder(action, abs(int(qty)))
        order.orderId = self.ib.client.getReqId()
        trade = self.ib.placeOrder(contract, order)
        log.info("Fecho de posição %s: %s x%d (id %d)", symbol, action, abs(int(qty)), order.orderId)
        return {"order_id": order.orderId, "qty": abs(qty), "direction": 1 if qty > 0 else -1, "trade": trade}

    @staticmethod
    def bar_time_utc(bar: Any) -> datetime:
        dt = bar.date
        if isinstance(dt, datetime):
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        # formatDate=2 devolve datetime; velas diárias devolvem date.
        return datetime.combine(dt, datetime.min.time(), tzinfo=timezone.utc)
