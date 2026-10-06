"""Cliente assíncrono para a Interactive Brokers via ``ib_async``.

Responsabilidades:
- Ligação/reconexão à TWS/Gateway em Paper Trading (porta 7497).
- Subscrição de velas de 1 minuto com ``keepUpToDate=True`` (streaming).
- Leitura do estado da conta e posições.
- Colocação de ordens *Bracket* (entrada a mercado + Take Profit + Stop Loss).
- Encaminhamento de execuções/estados de ordens para o motor.

``ib_async`` (github.com/ib-api-reloaded/ib_async) é o sucessor mantido do
``ib_insync``, que não recebe versões desde 2023; a API é idêntica, por isso
o ``ib_insync`` continua a funcionar como fallback.

Toda a API é pensada para ser chamada a partir da thread/loop do motor.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

try:  # ib_async é o fork mantido do ib_insync (arquivado em 2024); mesma API.
    from ib_async import IB, BarDataList, Contract, Fill, LimitOrder, MarketOrder, Order, Stock, StopOrder, Trade
except ImportError:  # pragma: no cover - instalação antiga com ib_insync
    from ib_insync import IB, BarDataList, Contract, Fill, LimitOrder, MarketOrder, Order, Stock, StopOrder, Trade  # type: ignore

from .config import Settings
from .indicators import Bar

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
        on_commission: Optional[Callable[[Trade, Fill, Any], None]] = None,
    ) -> None:
        self.settings = settings
        self.ib: Optional[IB] = None
        self._contracts: dict[str, Contract] = {}
        self._bars: dict[str, BarDataList] = {}
        self._on_bar = on_bar
        self._on_fill = on_fill
        self._on_order_status = on_order_status
        self._on_disconnect = on_disconnect
        self._on_commission = on_commission
        self._connecting = False
        self._disconnect_handled = False
        self.data_delayed: Optional[bool] = None  # True quando o feed é atrasado (paper sem subscrição)
        self.accounts: list[str] = []
        self.account: str = ""  # conta efetivamente usada pelo bot
        self.base_currency: str = ""
        self._hist_times: list[float] = []  # pacing de pedidos históricos
        self._hist_lock: Optional[asyncio.Lock] = None
        self._cancelling: set[int] = set()  # ordens canceladas pelo bot (distinguir de rejeições)
        self._fraction_warned: set[str] = set()

    def _reset_session_state(self) -> None:
        """Metadados que pertencem à sessão/conta anterior nunca sobrevivem a uma desligação (N09)."""
        self._bars.clear()
        self._contracts.clear()
        self.accounts = []
        self.account = ""
        self.base_currency = ""
        self.data_delayed = None
        self._cancelling.clear()
        self._fraction_warned.clear()

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
                self.ib.commissionReportEvent += self._handle_commission
            await self.ib.connectAsync(
                self.settings.ib_host, self.settings.ib_port,
                clientId=self.settings.ib_client_id, timeout=15,
            )
            self.ib.reqMarketDataType(self.settings.market_data_type)
            self._disconnect_handled = False
            self.base_currency = ""  # detetada de novo para ESTA conta
            self.data_delayed = None
            accounts = self.ib.managedAccounts()
            self.accounts = list(accounts)
            wanted = self.settings.ib_account.strip()
            if wanted and wanted not in accounts:
                log.critical("Conta configurada %s não está entre as contas geridas (%s). A desligar.", wanted, ", ".join(accounts))
                self.ib.disconnect()
                return False
            self.account = wanted or (accounts[0] if accounts else "")
            is_paper_account = all(a.startswith("DU") for a in accounts) if accounts else None
            log.info("IBKR ligado (%s:%s) conta(s): %s [%s]",
                     self.settings.ib_host, self.settings.ib_port, ", ".join(accounts) or "?",
                     "PAPER" if is_paper_account else "REAL" if is_paper_account is False else "?")
            if self.settings.is_live and is_paper_account:
                log.warning("Modo REAL selecionado mas a conta ligada (%s) é de Paper Trading.", ", ".join(accounts))
            if not self.settings.is_live and is_paper_account is False:
                log.critical("Modo PAPER selecionado mas a conta ligada (%s) é REAL. A desligar por segurança.",
                             ", ".join(accounts))
                self.ib.disconnect()
                return False
            if self.settings.is_live and is_paper_account is False:
                log.critical("CONTA REAL LIGADA (%s): todas as ordens usam dinheiro real.", ", ".join(accounts))
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
        self._reset_session_state()

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
        # ib_async 2.1 dispara disconnectedEvent duas vezes (issue #207): handler idempotente.
        if self._disconnect_handled:
            return
        self._disconnect_handled = True
        log.error("Ligação à IBKR perdida.")
        self._reset_session_state()
        if self._on_disconnect:
            self._on_disconnect()

    def _handle_exec(self, trade: Trade, fill: Fill) -> None:
        if self._on_fill:
            try:
                self._on_fill(trade, fill)
            except Exception as exc:  # noqa: BLE001
                log.exception("Erro no callback de execução: %s", exc)

    def _handle_commission(self, trade: Trade, fill: Fill, report: Any) -> None:
        if self._on_commission:
            try:
                self._on_commission(trade, fill, report)
            except Exception as exc:  # noqa: BLE001
                log.exception("Erro no callback de comissão: %s", exc)

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
        await self._pace_historical()
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
        if self.data_delayed is None:
            await self._check_market_data_type(contract)
        return True

    async def _check_market_data_type(self, contract: Contract) -> None:
        """Verifica se o feed é atrasado (paper sem subscrição: 15 min para ações US)."""
        assert self.ib is not None
        try:
            ticker = self.ib.reqMktData(contract, "", False, False)
            await asyncio.sleep(2.0)
            mdt = getattr(ticker, "marketDataType", None)
            self.ib.cancelMktData(contract)
        except Exception as exc:  # noqa: BLE001
            log.debug("Não foi possível verificar marketDataType: %s", exc)
            return
        if mdt in (3, 4):
            self.data_delayed = True
            log.warning("ATENÇÃO: dados de mercado ATRASADOS (marketDataType=%s, ~15 min). Métricas de qualidade "
                        "do LLM medidas com este feed não são válidas; subscreva dados em tempo real para avaliar.", mdt)
        elif mdt in (1, 2):
            self.data_delayed = False
            log.info("Dados de mercado em tempo real (marketDataType=%s).", mdt)

    async def _pace_historical(self) -> None:
        """Pacing IBKR: <= 60 pedidos históricos por 10 min e >= 2 s entre pedidos."""
        import time as _time

        if self._hist_lock is None:
            self._hist_lock = asyncio.Lock()
        async with self._hist_lock:
            now = _time.monotonic()
            self._hist_times = [t for t in self._hist_times if now - t < 600]
            if len(self._hist_times) >= 58:
                wait = 600 - (now - self._hist_times[0]) + 1
                log.warning("Pacing IBKR: a aguardar %.0fs antes de novo pedido histórico.", wait)
                await asyncio.sleep(wait)
            elif self._hist_times and now - self._hist_times[-1] < 2.0:
                await asyncio.sleep(2.0 - (now - self._hist_times[-1]))
            self._hist_times.append(_time.monotonic())

    def bars_as_list(self, symbol: str) -> list[Bar]:
        raw = self._bars.get(symbol)
        if not raw:
            return []
        return [Bar(self.bar_time_utc(b), float(b.open), float(b.high), float(b.low), float(b.close), float(b.volume))
                for b in raw]

    def price_at(self, symbol: str, when: datetime, max_gap_minutes: Optional[int] = None) -> Optional[float]:
        """Fecho da primeira vela com tempo >= ``when``; None se estiver a mais de ``max_gap_minutes``."""
        raw = self._bars.get(symbol)
        if not raw:
            return None
        gap = max_gap_minutes if max_gap_minutes is not None else self.settings.settlement_max_gap_minutes
        for b in raw:
            t = self.bar_time_utc(b)
            if t >= when:
                if (t - when).total_seconds() > gap * 60:
                    return None
                return float(b.close)
        return None

    def bars_between(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        return [b for b in self.bars_as_list(symbol) if start <= b.time <= end]

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
        await self._pace_historical()
        bars = await self.ib.reqHistoricalDataAsync(
            contract, endDateTime="", durationStr=duration, barSizeSetting=bar_size,
            whatToShow="TRADES", useRTH=False, formatDate=2, keepUpToDate=False,
        )
        return list(bars or [])

    # ---------------------------------------------------------- carteira
    def _account_values(self, tag: str) -> dict[str, float]:
        """{moeda: valor} para uma tag, só da conta do bot."""
        assert self.ib is not None
        out: dict[str, float] = {}
        for av in self.ib.accountValues():
            if av.tag != tag or (self.account and av.account and av.account != self.account):
                continue
            try:
                out[av.currency or ""] = float(av.value)
            except ValueError:
                continue
        return out

    def _detect_base_currency(self) -> str:
        if self.base_currency:
            return self.base_currency
        values = self._account_values("NetLiquidation")
        base = values.get("BASE")
        for cur, val in values.items():
            if cur not in ("BASE", "") and (base is None or abs(val - base) < 1e-6):
                self.base_currency = cur
                break
        if not self.base_currency and values:
            self.base_currency = next((c for c in values if c not in ("BASE", "")), "USD")
        return self.base_currency or "USD"

    def exchange_rate(self, currency: str) -> Optional[float]:
        """Quanto vale 1 unidade de ``currency`` na moeda base (tag ExchangeRate)."""
        base = self._detect_base_currency()
        if currency == base:
            return 1.0
        rates = self._account_values("ExchangeRate")
        return rates.get(currency)

    def display_rate(self, currency: str) -> tuple[str, float, bool]:
        """(moeda, fator, exata) para converter montantes da moeda base na moeda de apresentação da GUI.

        O fator multiplica um montante na moeda base; vem da tag ``ExchangeRate`` da conta. Sem taxa
        conhecida (desligado ou moeda não cotada na conta) devolve a moeda base com fator 1 e ``exata=False``.
        """
        base = self._detect_base_currency() if self.connected else (self.base_currency or "USD")
        wanted = (currency or "auto").strip().upper()
        if wanted in ("", "AUTO", "CONTA") or wanted == base:
            return base, 1.0, True
        if not self.connected:
            return base, 1.0, False
        rate = self.exchange_rate(wanted)  # valor de 1 unidade de ``wanted`` em moeda base
        if rate is None or rate <= 0:
            return base, 1.0, False
        return wanted, 1.0 / rate, True

    def _account_value(self, tag: str) -> Optional[float]:
        """Valor na moeda base (prefere a linha BASE; senão a da moeda base; senão qualquer)."""
        values = self._account_values(tag)
        if not values:
            return None
        if "BASE" in values:
            return values["BASE"]
        base = self._detect_base_currency()
        if base in values:
            return values[base]
        return next(iter(values.values()))

    def _to_usd(self, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        rate = self.exchange_rate("USD")
        if rate is None or rate <= 0:
            return None if self._detect_base_currency() != "USD" else value
        return value / rate

    def portfolio_state(self) -> dict[str, Any]:
        if not self.connected:
            return {"connected": False, "net_liq": None, "cash": None, "available_funds": None, "buying_power": None,
                    "unrealized": None, "realized": None, "positions": [], "currency": self.base_currency or "USD",
                    "net_liq_usd": None, "available_funds_usd": None, "account": self.account}
        assert self.ib is not None
        positions = []
        for item in self.ib.portfolio():
            if not item.position:
                continue
            if self.account and item.account and item.account != self.account:
                continue
            positions.append({
                "symbol": item.contract.symbol,
                "con_id": item.contract.conId,
                "sec_type": item.contract.secType,
                "qty": float(item.position),
                "avg_cost": float(item.averageCost or 0.0),
                "market_price": float(item.marketPrice or 0.0),
                "market_value": float(item.marketValue or 0.0),
                "unrealized_pnl": float(item.unrealizedPNL or 0.0),
                "realized_pnl": float(item.realizedPNL or 0.0),
            })
        net_liq = self._account_value("NetLiquidation")
        available = self._account_value("AvailableFunds")
        return {
            "connected": True,
            "account": self.account,
            "currency": self._detect_base_currency(),
            "net_liq": net_liq,
            "net_liq_usd": self._to_usd(net_liq),
            "cash": self._account_value("TotalCashValue"),
            "available_funds": available,
            "available_funds_usd": self._to_usd(available),
            "buying_power": self._account_value("BuyingPower"),
            "unrealized": self._account_value("UnrealizedPnL"),
            "realized": self._account_value("RealizedPnL"),
            "positions": positions,
        }

    def position_qty(self, symbol: str) -> float:
        if not self.connected:
            return 0.0
        assert self.ib is not None
        con_id = self.con_id(symbol)
        for pos in self.ib.positions():
            if self.account and pos.account and pos.account != self.account:
                continue
            if pos.contract.secType != "STK":
                continue
            if (con_id and pos.contract.conId == con_id) or (not con_id and pos.contract.symbol == symbol):
                return float(pos.position)
        return 0.0

    def con_id(self, symbol: str) -> Optional[int]:
        contract = self._contracts.get(symbol)
        return int(contract.conId) if contract is not None and contract.conId else None

    def _same_account(self, order: Order) -> bool:
        """Filtro de conta obrigatório: uma ordem de outra conta nunca conta para nada (N04)."""
        acct = getattr(order, "account", "") or ""
        return not self.account or not acct or acct == self.account

    def _is_ours(self, trade: Trade) -> bool:
        order = trade.order
        if not self._same_account(order):
            return False
        if self.settings.order_ref and getattr(order, "orderRef", "") != self.settings.order_ref:
            return False
        return True

    def open_trades_for(self, symbol: str, ours_only: bool = True) -> list[Trade]:
        """Ordens abertas do símbolo na conta do bot; ``ours_only`` restringe ainda ao ``orderRef`` do bot.

        O filtro de conta é independente do filtro de propriedade: ``ours_only=False`` continua a
        excluir ordens de outras contas visíveis na mesma sessão da TWS.
        """
        if not self.connected:
            return []
        assert self.ib is not None
        con_id = self.con_id(symbol)
        out = []
        for t in self.ib.openTrades():
            if t.contract.secType != "STK":
                continue
            same = (t.contract.conId == con_id) if con_id else (t.contract.symbol == symbol)
            if not same or not self._same_account(t.order):
                continue
            if not ours_only or self._is_ours(t):
                out.append(t)
        return out

    def order_is_ours(self, order: Any, contract: Any = None) -> bool:
        """Identidade completa de uma ordem reportada num callback: conta, clientId e orderRef (W03)."""
        if not self._same_account(order):
            return False
        client_id = getattr(order, "clientId", None)
        if client_id is not None and str(client_id) != "" and int(client_id) != int(self.settings.ib_client_id):
            return False
        ref = getattr(order, "orderRef", None)
        if self.settings.order_ref and ref is not None and ref != self.settings.order_ref:
            return False
        if contract is not None and getattr(contract, "secType", "STK") != "STK":
            return False
        return True

    def our_open_orders(self) -> list[Trade]:
        """Todas as ordens abertas do bot (orderRef + conta), para restaurar estado após reinício (N05)."""
        if not self.connected:
            return []
        assert self.ib is not None
        return [t for t in self.ib.openTrades() if t.contract.secType == "STK" and self._is_ours(t)]

    def last_price(self, symbol: str) -> Optional[tuple[float, datetime]]:
        """Último preço conhecido (fecho da vela em formação) e o seu instante (N15)."""
        raw = self._bars.get(symbol)
        if not raw:
            return None
        last = raw[-1]
        return float(last.close), self.bar_time_utc(last)

    # ----------------------------------------------------- cancelamentos
    def cancel_trade(self, trade: Trade) -> None:
        """Cancelamento intencional: fica registado para o motor o distinguir de uma rejeição (N02)."""
        assert self.ib is not None
        self._cancelling.add(int(trade.order.orderId))
        self.ib.cancelOrder(trade.order)

    def is_intentional_cancel(self, order_id: int) -> bool:
        return int(order_id) in self._cancelling

    def forget_cancel(self, order_id: int) -> None:
        self._cancelling.discard(int(order_id))

    def recent_fills(self) -> list[Fill]:
        """Execuções já conhecidas pela sessão (ib_async carrega as do dia ao ligar)."""
        if not self.connected:
            return []
        assert self.ib is not None
        fills = []
        for f in self.ib.fills():
            if f.contract.secType != "STK":
                continue
            if self.account and f.execution.acctNumber and f.execution.acctNumber != self.account:
                continue
            fills.append(f)
        return fills

    @staticmethod
    def _order_qty(qty: float) -> Optional[int]:
        """Quantidade inteira para a ordem; None se a parte inteira for zero (fração não suportada)."""
        whole = int(abs(qty))
        if abs(abs(qty) - whole) > 1e-9:
            log.warning("Posição fracionária %.4f: só a parte inteira (%d) é gerida; o resto fica por cobrir.", qty, whole)
        return whole if whole > 0 else None

    def _tag(self, order: Order) -> Order:
        order.orderRef = self.settings.order_ref
        if self.account:
            order.account = self.account
        return order

    # ------------------------------------------------------------ ordens
    async def place_bracket(
        self, symbol: str, action: str, quantity: int, ref_price: float, *,
        stop_price: float, tp_price: float, trailing: bool = False, limit_price: Optional[float] = None,
        authorize: Optional[Callable[[], bool]] = None,
    ) -> Optional[dict[str, Any]]:
        """Entrada (limit marketable ou mercado) com filhos TP (limit) e SL (stop ou trailing) por ``parentId``.

        Os filhos partilham um ``ocaGroup`` (um cancela o outro) e são GTC explícitos:
        o default de ``tif`` cai em DAY e deixaria a posição sem proteção overnight.

        ``authorize`` é o token de autorização do motor (geração + ciclo ativo); é reavaliado
        depois de cada ``await`` e imediatamente antes de ``placeOrder`` (N01).
        """
        if not self.connected or quantity <= 0:
            return None
        assert self.ib is not None
        contract = await self.qualify(symbol)
        if contract is None:
            return None
        if authorize is not None and not authorize():
            log.warning("Bracket %s %s x%d NÃO enviado: autorização revogada durante a qualificação.", action, symbol, quantity)
            return None
        if not self.connected:
            return None
        action = action.upper()
        reverse = "SELL" if action == "BUY" else "BUY"
        tp_price = round_tick(tp_price)
        stop_price = round_tick(stop_price)

        if limit_price is not None:
            parent = LimitOrder(action, quantity, round_tick(limit_price))  # marketable: limita o slippage
        else:
            parent = MarketOrder(action, quantity)
        parent.orderId = self.ib.client.getReqId()
        parent.transmit = False
        parent.tif = "DAY"  # a entrada é só para hoje
        oca = f"OCA-{symbol}-{parent.orderId}"
        take_profit = LimitOrder(reverse, quantity, tp_price)
        take_profit.orderId = self.ib.client.getReqId()
        take_profit.parentId = parent.orderId
        take_profit.transmit = False
        take_profit.tif = "GTC"
        take_profit.ocaGroup = oca
        take_profit.ocaType = 1
        if trailing:
            trail_amount = abs(ref_price - stop_price)
            stop_loss = Order(action=reverse, totalQuantity=quantity, orderType="TRAIL", auxPrice=round_tick(trail_amount),
                              trailStopPrice=stop_price)
        else:
            stop_loss = StopOrder(reverse, quantity, stop_price)
        stop_loss.orderId = self.ib.client.getReqId()
        stop_loss.parentId = parent.orderId
        stop_loss.transmit = True  # transmite o grupo inteiro
        stop_loss.tif = "GTC"
        stop_loss.ocaGroup = oca
        stop_loss.ocaType = 1

        for o in (parent, take_profit, stop_loss):
            self._tag(o)
        if authorize is not None and not authorize():
            log.warning("Bracket %s %s x%d NÃO enviado: autorização revogada antes de placeOrder.", action, symbol, quantity)
            return None
        trades = [self.ib.placeOrder(contract, o) for o in (parent, take_profit, stop_loss)]
        log.info(
            "Bracket %s %s x%d @~%.2f | TP %.2f | SL %.2f%s (ids %d/%d/%d)",
            action, symbol, quantity, ref_price, tp_price, stop_price, " TRAIL" if trailing else "",
            parent.orderId, take_profit.orderId, stop_loss.orderId,
        )
        return {
            "parent_order_id": parent.orderId,
            "tp_order_id": take_profit.orderId,
            "sl_order_id": stop_loss.orderId,
            "tp_price": tp_price,
            "sl_price": stop_price,
            "trades": trades,
            "con_id": self.con_id(symbol),
            "account": self.account,
        }

    def cancel_order_id(self, order_id: int) -> bool:
        if not self.connected:
            return False
        assert self.ib is not None
        for t in self.ib.openTrades():
            if t.order.orderId == order_id and self._same_account(t.order):
                self.cancel_trade(t)
                return True
        return False

    async def wait_done(self, trades: list[Trade], timeout: float = 5.0) -> bool:
        """Espera que todas as ordens atinjam estado terminal (cancelada/executada)."""
        import time as _time

        def done(t: Trade) -> bool:
            fn = getattr(t, "isDone", None)
            return bool(fn()) if callable(fn) else t.orderStatus.status in self.TERMINAL_STATUSES

        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            if all(done(t) for t in trades):
                return True
            await asyncio.sleep(0.25)
        return all(done(t) for t in trades)

    ACTIVE_STATUSES = ("PreSubmitted", "Submitted", "PendingSubmit", "ApiPending")
    TERMINAL_STATUSES = ("Filled", "Cancelled", "ApiCancelled", "Inactive")
    STOP_TYPES = ("STP", "TRAIL", "STP LMT")

    @classmethod
    def is_live_order(cls, trade: Trade) -> bool:
        """Toda a ordem NÃO terminal pode ainda executar: ``PendingCancel`` incluído (W01/W02). Um pedido de
        cancelamento não é um cancelamento confirmado. Usa ``Trade.isDone()`` do ib_async quando existe."""
        fn = getattr(trade, "isDone", None)
        if callable(fn):
            return not bool(fn())
        return trade.orderStatus.status not in cls.TERMINAL_STATUSES

    @classmethod
    def in_transition(cls, trade: Trade) -> bool:
        return cls.is_live_order(trade) and trade.orderStatus.status not in cls.ACTIVE_STATUSES

    @staticmethod
    def _remaining(trade: Trade) -> float:
        o, st = trade.order, trade.orderStatus
        remaining = float(st.remaining) if st.remaining else float(o.totalQuantity) - float(st.filled or 0)
        return max(0.0, remaining)

    def _protective_children(self, symbol: str) -> list[Trade]:
        """Ordens de saída ATIVAS do bot (TP limit e SL stop) do lado oposto à posição."""
        qty = self.position_qty(symbol)
        if qty == 0:
            return []
        needed_action = "SELL" if qty > 0 else "BUY"
        out = []
        for t in self.open_trades_for(symbol, ours_only=True):
            o = t.order
            if o.action != needed_action or not self.is_live_order(t):
                continue
            if o.orderType in self.STOP_TYPES or o.orderType == "LMT":
                out.append(t)
        return out

    def transitional_children(self, symbol: str) -> list[Trade]:
        """Filhos do bot num estado de transição (ex.: PendingCancel): nem confirmados nem terminados (W02)."""
        return [t for t in self._protective_children(symbol) if self.in_transition(t)]

    def external_exit_orders(self, symbol: str) -> list[Trade]:
        """Ordens de saída NÃO TERMINAIS na conta que não são do bot (stop/limit manual do lado oposto à
        posição), incluindo as que aguardam cancelamento: enquanto não terminarem podem executar (V01/W01)."""
        qty = self.position_qty(symbol)
        if qty == 0:
            return []
        needed_action = "SELL" if qty > 0 else "BUY"
        out = []
        for t in self.open_trades_for(symbol, ours_only=False):
            if self._is_ours(t) or t.order.action != needed_action:
                continue
            if self.is_live_order(t):
                out.append(t)
        return out

    def has_orphan_children(self, symbol: str) -> bool:
        """Há um TP sem stop ou um stop sem TP entre os filhos do bot (par incompleto) (V02)."""
        return bool(self._orphan_children(symbol))

    def _orphan_children(self, symbol: str) -> list[Trade]:
        """Filhos cujo grupo OCA perdeu a perna irmã (TP sem stop ou stop sem TP): candidatos a substituição (N07)."""
        children = self._protective_children(symbol)
        by_group: dict[str, list[Trade]] = {}
        for t in children:
            by_group.setdefault(getattr(t.order, "ocaGroup", "") or f"solo-{t.order.orderId}", []).append(t)
        orphans: list[Trade] = []
        for legs in by_group.values():
            has_stop = any(t.order.orderType in self.STOP_TYPES for t in legs)
            has_tp = any(t.order.orderType == "LMT" for t in legs)
            if not (has_stop and has_tp):
                orphans.extend(legs)
        return orphans

    async def ensure_protection(self, symbol: str, *, stop_price: float, tp_price: float,
                                max_qty: Optional[float] = None) -> Optional[dict[str, Any]]:
        """Repõe cobertura TP+SL (OCA, GTC) só na quantidade que FALTA cobrir (N03).

        1. Filhos órfãos (TP sem stop ou stop sem TP, do bot) são cancelados e esperados: nunca
           coexistem duas saídas para as mesmas ações.
        2. A cobertura é recalculada e só o residual (posição − stops ativos) recebe um novo par.
        3. Frações abaixo de uma ação não são negociáveis por API: ficam registadas e assinaladas
           uma vez, sem gerar ordens repetidas.
        A reposição de cobertura é uma ação de redução de risco: continua permitida depois de Parar.
        """
        if not self.connected:
            return None
        assert self.ib is not None
        qty = self.position_qty(symbol)
        if qty == 0:
            return None
        ours_only = max_qty is not None  # posição mista: só a cobertura do PRÓPRIO bot conta para a parte própria (W07)
        # Ordens em transição (PendingCancel) ainda podem executar: nunca se cria cobertura nova por cima
        # delas; espera-se o estado terminal e só depois se recalcula (W02).
        transitional = self.transitional_children(symbol)
        if transitional:
            if not await self.wait_done(transitional, timeout=5.0):
                log.error("Reparação de %s adiada: %d ordem(ns) do bot ainda em transição (%s).", symbol, len(transitional),
                          ", ".join(f"{t.order.orderId}:{t.orderStatus.status}" for t in transitional))
                return None
        orphans = [t for t in self._orphan_children(symbol) if not self.in_transition(t)]
        if self.has_protective_orders(symbol, needed_qty=max_qty, ours_only=ours_only) and not orphans:
            return None
        contract = await self.qualify(symbol)
        if contract is None:
            return None
        replaced: list[int] = []
        if orphans:
            for t in orphans:
                self.cancel_trade(t)
                replaced.append(int(t.order.orderId))
            if not await self.wait_done(orphans, timeout=5.0):
                log.error("Reparação de %s: cancelamento de filhos órfãos (%s) não confirmado; sem nova ordem neste ciclo.",
                          symbol, ", ".join(map(str, replaced)))
                return None
        qty = self.position_qty(symbol)
        if qty == 0 or not self.connected:
            return None
        if self.transitional_children(symbol):
            log.error("Reparação de %s adiada: nova ordem em transição depois dos cancelamentos.", symbol)
            return None
        manageable = abs(qty) if max_qty is None else min(abs(qty), float(max_qty))  # posição mista: só a parte própria
        residual = manageable - self.protective_coverage(symbol, ours_only=ours_only)
        n = int(residual + 1e-9)
        if n <= 0:
            if residual > 1e-9 and symbol not in self._fraction_warned:
                self._fraction_warned.add(symbol)
                log.error("Posição %s: %.4f ações sem cobertura possível (fração < 1 ação não é negociável por API).",
                          symbol, residual)
            return None
        reverse = "SELL" if qty > 0 else "BUY"
        oca = f"OCA-{symbol}-FIX-{self.ib.client.getReqId()}"
        take_profit = LimitOrder(reverse, n, round_tick(tp_price))
        stop_loss = StopOrder(reverse, n, round_tick(stop_price))
        for o in (take_profit, stop_loss):
            o.orderId = self.ib.client.getReqId()
            o.tif = "GTC"
            o.ocaGroup = oca
            o.ocaType = 1
            o.transmit = True
            self._tag(o)
        trades = [self.ib.placeOrder(contract, o) for o in (take_profit, stop_loss)]
        log.warning("Posição %s x%g com cobertura %g: colocados TP %.2f / SL %.2f para %d ações (ids %d/%d)%s.",
                    symbol, qty, abs(qty) - residual, tp_price, stop_price, n, take_profit.orderId, stop_loss.orderId,
                    f"; substituídos {replaced}" if replaced else "")
        return {"tp_order_id": take_profit.orderId, "sl_order_id": stop_loss.orderId, "qty": n,
                "direction": 1 if qty > 0 else -1, "trades": trades, "tp_price": round_tick(tp_price),
                "sl_price": round_tick(stop_price), "replaced_order_ids": replaced}

    def protective_coverage(self, symbol: str, ours_only: bool = False) -> float:
        """Quantidade coberta por stops CONFIRMADOS (estados ativos) do lado oposto à posição, na conta do
        bot (N04). Ordens em transição (PendingCancel) não são cobertura confirmada (W02). Com ``ours_only``
        só contam os stops do próprio bot (parcela própria de uma posição mista, W07)."""
        qty = self.position_qty(symbol)
        if qty == 0:
            return 0.0
        needed_action = "SELL" if qty > 0 else "BUY"
        covered = 0.0
        for t in self.open_trades_for(symbol, ours_only=ours_only):  # conta filtrada sempre
            o, st = t.order, t.orderStatus
            if o.orderType not in self.STOP_TYPES or o.action != needed_action:
                continue
            if st.status not in self.ACTIVE_STATUSES:
                continue
            covered += self._remaining(t)
        return covered

    def has_protective_orders(self, symbol: str, needed_qty: Optional[float] = None, ours_only: bool = False) -> bool:
        """True quando os stops confirmados cobrem toda a parte negociável da posição (ou ``needed_qty``, a
        quantidade própria numa posição mista, caso em que só os stops do bot contam: um stop manual
        pertence à parcela manual, W07).

        A parte inteira é o máximo que a API permite cobrir; a fração restante é assinalada
        uma vez em ``ensure_protection`` em vez de disparar reparações a cada ciclo (N03/F10).
        """
        qty = abs(self.position_qty(symbol))
        if qty <= 0:
            return False
        if needed_qty is not None:
            qty = min(qty, abs(float(needed_qty)))
            ours_only = True
        coverable = float(int(qty + 1e-9))
        if coverable <= 0:
            return False
        return self.protective_coverage(symbol, ours_only=ours_only) + 1e-9 >= coverable

    def has_pending_entry(self, symbol: str) -> bool:
        """Entrada do bot ainda viva na corretora: pai (sem parentId) a mercado OU limit (N05)."""
        return any(t.order.parentId == 0 and t.order.orderType in ("MKT", "LMT")
                   and t.orderStatus.status in self.ACTIVE_STATUSES
                   for t in self.open_trades_for(symbol))

    async def close_position(self, symbol: str, authorize: Optional[Callable[[], bool]] = None,
                             max_qty: Optional[float] = None) -> Optional[dict[str, Any]]:
        """Fecha a posição a mercado com segurança contra corridas com o stop.

        1. Reconcilia TODAS as saídas do contrato/conta: uma saída MANUAL ativa (stop ou limit que não
           é do bot) bloqueia o fecho, salvo ``cancel_external_exits_on_close`` (V01).
        2. Cancela os filhos (TP/SL) do bot (cancelamento intencional) e espera o estado terminal.
        3. Volta a ler a posição: se o stop entretanto a fechou, não envia nada.
        4. Reavalia ``authorize`` (geração/ciclo) e só então envia a quantidade remanescente (N01).
        Devolve ``needs_protection`` verdadeiro sempre que a cobertura possa ter ficado
        incompleta (timeout parcial incluído), para o motor a repor (N02).
        """
        if not self.connected:
            return None
        assert self.ib is not None
        qty = self.position_qty(symbol)
        if qty == 0:
            return None
        contract = await self.qualify(symbol)
        if contract is None:
            return None
        if authorize is not None and not authorize():
            log.warning("Fecho de %s não iniciado: autorização revogada.", symbol)
            return {"order_id": None, "qty": 0, "direction": 1 if qty > 0 else -1, "trade": None,
                    "aborted": True, "needs_protection": not self.has_protective_orders(symbol)}
        def conflict(external: list[Trade]) -> dict[str, Any]:
            ids = ", ".join(f"{t.order.orderId}:{t.order.orderType} {t.order.action} x{t.order.totalQuantity:g} [{t.orderStatus.status}]"
                            for t in external)
            log.critical("Fecho de %s BLOQUEADO: há ordens de saída MANUAIS não terminadas na conta (%s). Cancela-as na TWS "
                         "ou ativa cancel_external_exits_on_close; sem isso o bot nunca envia uma saída concorrente.", symbol, ids)
            return {"order_id": None, "qty": 0, "direction": 1 if qty > 0 else -1, "trade": None, "aborted": True,
                    "needs_protection": False, "conflict": [int(t.order.orderId) for t in external]}

        external = self.external_exit_orders(symbol)
        if external and not self.settings.cancel_external_exits_on_close:
            return conflict(external)
        # Cancelar e ESPERAR o estado terminal; depois reconciliar de novo todas as saídas do contrato, porque
        # uma ordem manual pode ter surgido durante a espera e um PendingCancel ainda pode executar (W01).
        for attempt in range(3):
            children = [t for t in self.open_trades_for(symbol) if self.is_live_order(t)] + external
            for trade in children:
                if trade.orderStatus.status in self.ACTIVE_STATUSES:
                    self.cancel_trade(trade)
            done = await self.wait_done(children, timeout=5.0)
            if not done or not self.connected:
                still_covered = self.connected and self.has_protective_orders(symbol)
                log.error("Fecho de %s: cancelamentos não confirmados em 5 s; a abortar o fecho (cobertura %s).",
                          symbol, "mantida" if still_covered else "INCOMPLETA: será reposta")
                return {"order_id": None, "qty": 0, "direction": 1 if qty > 0 else -1, "trade": None,
                        "aborted": True, "needs_protection": not still_covered}
            external = self.external_exit_orders(symbol)
            own_live = [t for t in self.open_trades_for(symbol) if self.is_live_order(t)]
            if not external and not own_live:
                break
            if external and not self.settings.cancel_external_exits_on_close:
                return conflict(external)
        else:
            log.error("Fecho de %s: saídas continuam não terminadas após 3 tentativas; a abortar.", symbol)
            return {"order_id": None, "qty": 0, "direction": 1 if qty > 0 else -1, "trade": None,
                    "aborted": True, "needs_protection": not self.has_protective_orders(symbol)}
        qty_now = self.position_qty(symbol)
        if qty_now == 0 or (qty_now > 0) != (qty > 0):
            log.warning("Fecho de %s: a posição já foi fechada pelo stop/TP durante os cancelamentos.", symbol)
            return {"order_id": None, "qty": 0, "direction": 1 if qty > 0 else -1, "trade": None,
                    "closed_by_children": True, "needs_protection": qty_now != 0}
        if max_qty is not None:
            qty_now = (1 if qty_now > 0 else -1) * min(abs(qty_now), float(max_qty))  # só a parte própria
        n = self._order_qty(qty_now)
        if n is None:
            log.error("Fecho de %s: quantidade inteira zero (posição fracionária %.4f).", symbol, qty_now)
            return {"order_id": None, "qty": 0, "direction": 1 if qty_now > 0 else -1, "trade": None,
                    "aborted": True, "needs_protection": True}
        action = "SELL" if qty_now > 0 else "BUY"
        order = self._tag(MarketOrder(action, n))
        order.orderId = self.ib.client.getReqId()
        if authorize is not None and not authorize():
            log.warning("Fecho de %s NÃO enviado: autorização revogada depois dos cancelamentos; a repor cobertura.", symbol)
            return {"order_id": None, "qty": 0, "direction": 1 if qty_now > 0 else -1, "trade": None,
                    "aborted": True, "needs_protection": True}
        concurrent = self.external_exit_orders(symbol) + [t for t in self.open_trades_for(symbol) if self.is_live_order(t)]
        if concurrent:  # última reconciliação imediatamente antes do envio: nenhuma saída concorrente viva (W01)
            log.critical("Fecho de %s NÃO enviado: saída(s) %s ainda não terminada(s) no instante do envio.", symbol,
                         [int(t.order.orderId) for t in concurrent])
            return {"order_id": None, "qty": 0, "direction": 1 if qty_now > 0 else -1, "trade": None, "aborted": True,
                    "needs_protection": not self.has_protective_orders(symbol), "conflict": [int(t.order.orderId) for t in concurrent]}
        try:
            trade = self.ib.placeOrder(contract, order)
        except Exception as exc:  # noqa: BLE001
            log.error("Fecho de %s falhou ao colocar a ordem: %s", symbol, exc)
            return {"order_id": None, "qty": 0, "direction": 1 if qty_now > 0 else -1, "trade": None,
                    "aborted": True, "needs_protection": True}
        log.info("Fecho de posição %s: %s x%d (id %d)", symbol, action, n, order.orderId)
        return {"order_id": order.orderId, "qty": float(n), "direction": 1 if qty_now > 0 else -1, "trade": trade,
                "needs_protection": False}

    @staticmethod
    def bar_time_utc(bar: Any) -> datetime:
        dt = bar.date
        if isinstance(dt, datetime):
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        # formatDate=2 devolve datetime; velas diárias devolvem date.
        return datetime.combine(dt, datetime.min.time(), tzinfo=timezone.utc)
