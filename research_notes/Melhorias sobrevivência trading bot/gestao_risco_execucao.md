# Gestão de risco, dimensionamento de posições e qualidade de execução para um bot de trading autónomo (IBKR / ib_async, Python, 2024-2026)

Nota metodológica: os domínios `freqtrade.io`, `nautilustrader.io`, `ib-api-reloaded.github.io`, `interactivebrokers.com`, `ibkb.interactivebrokers.com` e `interactivebrokers.github.io` estavam bloqueados pelo proxy de saída desta sessão. Sempre que possível, o conteúdo foi lido directamente a partir dos ficheiros-fonte nos repositórios GitHub (`raw.githubusercontent.com`), das páginas PyPI e de issues públicas. Quando só havia excertos de motores de pesquisa (não a página original), isso está assinalado.

## Pergunta 1 — Que bibliotecas (URL, actividade, licença) servem para gestão de risco, dimensionamento e análise de execução?

### Takeaway
Não existe uma única biblioteca Python "de risco para bots pequenos"; o caminho pragmático é combinar: (a) `pandas-ta` ou `ta` para ATR; (b) uma camada de "protections" copiada do desenho do freqtrade (StoplossGuard, MaxDrawdown, CooldownPeriod); (c) `quantstats`/`pyfolio-reloaded` para medir drawdown, Kelly e risco de ruína a partir do SQLite; (d) `riskfolio-lib` apenas se se quiser optimizar exposição/correlação entre as 4 posições; (e) `hmmlearn` para regime (com cautela: está em "limited-maintenance"). O `nautilus_trader` e o `pysystemtrade` são referências de arquitectura (RiskEngine, volatility targeting) mais do que dependências a importar.

### Cited Findings

**Protecções/circuit-breakers (referência de desenho)**
- freqtrade: licença GPLv3, versão 2026.9 lançada em 29-09-2026, Python 3.11-3.14; é um bot cripto via CCXT, logo as *protections* servem de modelo de desenho, não como dependência para acções — [PyPI freqtrade](https://pypi.org/project/freqtrade/)
- As *protections* do freqtrade são 4: StoplossGuard ("Stop trading if a certain amount of stoploss occurred within a certain time window"), MaxDrawdown ("Stop trading if max-drawdown is reached"), LowProfitPairs ("Lock pairs with low profits"), CooldownPeriod ("Don't enter a trade right after selling a trade"). Todos os fins de bloqueio são arredondados para a vela seguinte — [freqtrade docs/includes/protections.md](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/docs/includes/protections.md)
- Parâmetros comuns: `stop_duration_candles` / `stop_duration` (minutos, mutuamente exclusivos), `lookback_period_candles` / `lookback_period`, `trade_limit` (nº mínimo de trades para avaliar), `unlock_at` ("HH:MM"). StoplossGuard tem `only_per_pair`, `only_per_side`, `required_profit` (default 0.0). MaxDrawdown tem `calculation_mode` "ratios" (legado) ou "equity" (pico-vale do equity) e `max_allowed_drawdown` — [freqtrade protections.md](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/docs/includes/protections.md)
- Exemplo completo oficial (timeframe 1h): CooldownPeriod 5 velas; MaxDrawdown equity, lookback 48 velas, trade_limit 20, stop 4 velas, max_allowed_drawdown 0.2; StoplossGuard lookback 24 velas, trade_limit 4, stop 2 velas; LowProfitPairs lookback 6 velas, trade_limit 2, required_profit 0.02 — [freqtrade protections.md](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/docs/includes/protections.md)
- Aviso oficial: "Protections are supported by backtesting and hyperopt, but must be explicitly enabled by using the `--enable-protections` flag" e "Not all Protections will work for all strategies, and parameters will need to be tuned" — [freqtrade protections.md](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/docs/includes/protections.md)

**Motor de risco de referência**
- nautilus_trader: licença LGPL v3.0; Python 3.12-3.14; releases bissemanais; adaptador Interactive Brokers listado como integração "stable"; o README avisa que "live execution still introduces venue, transport, timing, persistence, external-activity, and reconciliation behavior that a simulation may not reproduce" — [nautilus_trader README](https://raw.githubusercontent.com/nautechsystems/nautilus_trader/master/README.md)
- `RiskEngineConfig` (crate Rust `risk`): `bypass` ("Whether to bypass risk checks and order rate limits", default false); `max_order_submit` ("Rate limit for order submission commands", default 100 por 1 s); `max_order_modify` ("Rate limit for order modifications, counting each batch child separately", default 100/1 s); `max_notional_per_order` ("Maximum notional per order by instrument, in each instrument's quote currency", default vazio); `full_position_exit_venues`; `debug` — [nautilus_trader crates/risk/src/engine/config.rs](https://raw.githubusercontent.com/nautechsystems/nautilus_trader/master/crates/risk/src/engine/config.rs)
- A API Python expõe `LiveRiskEngineConfig.max_notional_per_order` como `dict[str, str]`; em V2 as configs são imutáveis e devolvem strings validadas — [nautilus_trader MIGRATION_V2.md e python/nautilus_trader/risk/__init__.pyi](https://github.com/nautechsystems/nautilus_trader/blob/master/MIGRATION_V2.md)

**Analítica de desempenho/risco**
- quantstats: licença Apache; Python 3.10+; módulos `quantstats.stats`, `.plots`, `.reports`; métricas incluem max_drawdown, sharpe, sortino, kelly_criterion, risk_of_ruin, var, cvar, calmar, consecutive_wins/losses, profit_factor, drawdown_details, montecarlo; `qs.reports.html(returns, "SPY")` gera tear sheet HTML; dependências pandas≥1.5, numpy≥1.24, scipy≥1.11, matplotlib≥3.7, seaborn≥0.13, yfinance≥0.2.40 — [quantstats README](https://raw.githubusercontent.com/ranaroussi/quantstats/main/README.md)
- pyfolio-reloaded: v0.9.9 (02-06-2025), Apache, Python 3.9-3.13, mantido por "ml4t" (Stefan Jansen) — [PyPI pyfolio-reloaded](https://pypi.org/project/pyfolio-reloaded/)
- vectorbt (edição comunitária): v1.1.1 (26-09-2026), licença "Apache 2.0 with Commons Clause" (fair-code: não se pode vender produto que seja essencialmente o software), Python 3.11-3.14; a edição PRO comercial acrescenta alavancagem, ordens limite e multiplicadores — [PyPI vectorbt](https://pypi.org/project/vectorbt/)

**Optimização de carteira / exposição**
- Riskfolio-Lib: v7.4.0 (04-10-2026), BSD 3-clause, Python 3.10-3.14, dependências CVXPY≥1.7.2, Clarabel, SCS, scikit-learn≥1.7; 26+ medidas de risco convexas (CVaR, EVaR, RLVaR, CDaR, EDaR, MDD, Tail Gini…), modelos Mean-Risk, Kelly, HRP/HERC, Risk Parity (22 medidas), NCO, Black-Litterman — [Riskfolio-Lib README](https://raw.githubusercontent.com/dcajasn/Riskfolio-Lib/master/README.md); [PyPI Riskfolio-Lib](https://pypi.org/project/Riskfolio-Lib/)

**Indicadores (ATR)**
- pandas-ta: v0.4.71b0 (14-09-2025, pré-release), exige Python ≥3.12, "more than 150 indicators and utilities as well as 60 Candlestick Patterns when TA Lib is installed", usa numba — [PyPI pandas-ta](https://pypi.org/project/pandas-ta/)
- ta (bukosabino): v0.11.0 (02-11-2023), MIT, mantido por Dario Lopez Padial; sem release desde 2023 — [PyPI ta](https://pypi.org/project/ta/)

**Regime (HMM)**
- hmmlearn: README declara "This package is under limited-maintenance mode"; requer numpy, scikit-learn; `pip install --upgrade --user hmmlearn`; docs em hmmlearn.readthedocs.org — [hmmlearn README](https://raw.githubusercontent.com/hmmlearn/hmmlearn/main/README.rst)
- Repositórios activos (2025-2026) que usam HMM para regime: `kratu/wess_hmm` ("Hybrid Wasserstein + HMM Market Regime Detection", 15 estrelas, tópicos hmmlearn, actualizado 09-2026); `zzwjlwwdtg/quant-trading-framework` (acções JP+US, "HMM regime detection, Claude LLM decision gate… paper trading", 7 estrelas, 10-2026); `GifariKemal/xaubot-ai` (XGBoost+HMM para MT5, 87 estrelas) — [GitHub search](https://github.com/search?q=hmm+regime+detection+trading+language%3Apython)
- Tutorial clássico de referência: QuantStart, "Market Regime Detection using Hidden Markov Models in QSTrader" — [QuantStart](https://www.quantstart.com/articles/market-regime-detection-using-hidden-markov-models-in-qstrader/)

**Volatility targeting (referência)**
- pysystemtrade (Rob Carver): GNU v3; mantido por Andy Geach desde 2024; movido para a organização `pst-group` em Janeiro de 2026; usa "the ib_async library to connect to interactive brokers" — [pysystemtrade README](https://raw.githubusercontent.com/robcarver17/pysystemtrade/master/README.md)
- Defaults documentados: EWMA de volatilidade com "35 day span", "Needs 10 periods to generate a value", floor 1e-10, piso de 5º percentil de vol com média móvel de 500 dias; forecast cap ±20, average absolute forecast 10; escalador de volatilidade = "daily cash vol target / instrument value vol"; buffering de posição de 10% — [pysystemtrade docs/backtesting.md](https://raw.githubusercontent.com/robcarver17/pysystemtrade/master/docs/backtesting.md)

**Jesse (helpers de risco)**
- `jesse/utils.py` tem `risk_to_qty(capital, risk_per_capital, entry_price, stop_loss_price, precision=8, fee_rate=0)`, `risk_to_size` (fórmula `((risk_percentage/100 * capital_size) / risk_per_qty) * entry_price`), `size_to_qty`, `qty_to_size`, `kelly_criterion(win_rate, ratio_avg_win_loss) = win_rate - ((1 - win_rate) / ratio_avg_win_loss)`, `estimate_risk = abs(entry_price - stop_price)`, `limit_stop_loss` — [jesse utils.py](https://raw.githubusercontent.com/jesse-ai/jesse/master/jesse/utils.py)

**Monitorização**
- prometheus-client: v0.26.0 (24-07-2026), "Apache-2.0 AND BSD-2-Clause", Python 3.9-3.14; `start_http_server(8000)` + `Gauge`/`Counter` expõe `/metrics` — [PyPI prometheus-client](https://pypi.org/project/prometheus-client/)
- python-telegram-bot: v22.8 (12-06-2026), LGPL-3.0-only, Python 3.10-3.15, "fully asynchronous" sobre asyncio desde a v20; `Bot.send_message()` para alertas — [PyPI python-telegram-bot](https://pypi.org/project/python-telegram-bot/)
- Exemplo de stack: `jordantete/grid_trading_bot` usa Grafana+Loki via Docker Compose e alertas via Apprise (Telegram, Discord, Slack, email); AutoTrader fornece dashboard Grafana sobre o cliente Python do Prometheus — [grid_trading_bot](https://github.com/jordantete/grid_trading_bot); [AutoTrader dashboarding](https://autotrader.readthedocs.io/en/latest/tutorials/misc/dashboarding.html)

### Inferences
- Para um bot de 4 posições em acções US, o `riskfolio-lib` (CVXPY) é sobredimensionado; uma matriz de correlação `pandas` sobre retornos de 1-min agregados a 5/15-min, mais um mapa sector→ticker estático, cobre o controlo de exposição com uma fracção da complexidade.
- `ta` (sem release desde 2023) é adequado se só se precisar de ATR estável; `pandas-ta` é mais completo (Supertrend, Chandelier) mas está em pré-release e exige Python ≥3.12.
- O desenho de *protections* do freqtrade (lookback + trade_limit + stop_duration, avaliado por vela) mapeia directamente para o SQLite do bot: basta uma query sobre trades fechados nas últimas N velas.

### Gaps
- Não foi possível ler as páginas oficiais de docs do nautilus_trader sobre o RiskEngine (domínio bloqueado; o ficheiro `docs/concepts/execution.md` devolveu 404 em `develop` e `master`). A lista completa de verificações pré-trade (ex.: validação de preço/quantidade vs. instrumento, estado de trading ACTIVE/REDUCING/HALTED) não foi confirmada nesta sessão.
- A licença do `hmmlearn` não foi confirmada numa fonte lida (a página PyPI não carregou; o README não a indica no excerto).
- Não encontrei um "risk utils" documentado em Jesse além das funções de `utils.py` citadas; a documentação em `docs.jesse.trade` não foi consultada.

## Pergunta 2 — Que números/regras concretas recomendam praticantes e repositórios (risco por trade, múltiplos de ATR, perda diária, horários)?

### Takeaway
As regras recorrentes: risco fixo de 0,5-2% do capital por trade com tamanho derivado da distância ao stop (não percentagem fixa da conta), stop a 1,5-3×ATR, Kelly fraccionário (0,25-0,5×Kelly), travões de drawdown a 15-25% ou por contagem de stops numa janela, e evitar os primeiros 15-30 minutos (volatilidade em "U" documentada academicamente). A maior parte destes números são convenções de praticantes, não resultados académicos — isso está assinalado.

### Cited Findings

**Risco por trade e dimensionamento por volatilidade**
- Fórmula padrão de dimensionamento por ATR: "Position Size = (Account Equity × Risk%) / (ATR × ATR Multiple × Point Value)"; "Most traders use a basic 1 or 2% Risk Rule"; exemplo com conta de 10 000, risco 1% = 100, stop a 1,5×ATR — [MQL5 blog, ATR-Based Position Sizing](https://www.mql5.com/en/blogs/post/774444); [NexusFi, Volatility-Based Position Sizing](https://nexusfi.com/a/risk-management/volatility-based-position-sizing)
- Argumento-chave contra tamanho fixo: "A fixed 0.10 lots is not one risk — it is a different risk on every symbol and in every regime" — [MQL5 blog](https://www.mql5.com/en/blogs/post/774444)
- Fixed fractional (percent-risk): "risks the same fraction of current equity on every trade, with size back-solved from the stop: equity times the risk fraction, divided by the per-unit distance from entry to stop"; "a bot configured to risk 3% per trade will risk 3% on every trade regardless of streak" — [PineForge, Position Sizing for Trading Bots](https://getpineforge.com/blog/position-sizing-for-trading-bots)
- Implementação de referência da mesma fórmula: `jesse.utils.risk_to_qty` (capital, % de risco, preço de entrada, preço de stop, taxa) — [jesse utils.py](https://raw.githubusercontent.com/jesse-ai/jesse/master/jesse/utils.py)

**Volatility targeting**
- "Target Exposure = Target Volatility / Current Volatility"; exemplo: alvo 10%, vol actual 20% → exposição 0,5×; vol 5% → 2× (deve ser limitado com um cap) — [QuestDB glossary, Volatility Targeting Strategies](https://questdb.com/glossary/volatility-targeting-strategies)
- pysystemtrade: vol estimada por EWMA de 35 dias com mínimo de 10 períodos e piso no 5º percentil (500 dias) para evitar posições gigantes em períodos anormalmente calmos; posição = forecast × (cash vol target diário / vol em valor do instrumento); buffer de posição de 10% para reduzir rotação — [pysystemtrade docs/backtesting.md](https://raw.githubusercontent.com/robcarver17/pysystemtrade/master/docs/backtesting.md)

**Kelly fraccionário**
- "Full Kelly assumes perfect knowledge of your edge, but in practice edge estimates are noisy, so fractional Kelly should always be used—typically 0.25x Kelly for conservative default or 0.50x Kelly for well-measured edges" — [NexusFi, Kelly Criterion](https://nexusfi.com/a/risk-management/kelly-criterion); [PineForge](https://getpineforge.com/blog/position-sizing-for-trading-bots)
- Fórmula (Jesse): `kelly = win_rate - (1 - win_rate)/ratio_avg_win_loss` — [jesse utils.py](https://raw.githubusercontent.com/jesse-ai/jesse/master/jesse/utils.py); quantstats expõe `kelly_criterion` e `risk_of_ruin` sobre a série de retornos — [quantstats README](https://raw.githubusercontent.com/ranaroussi/quantstats/main/README.md)
- Escalonamento por drawdown em zonas: "green zone uses no adjustment, yellow zone halves sizing, and red/critical zone suspends trading"; "drawdown triggers at 15-25% halt thresholds" — [Frontier Wisdom, trading bot bankroll management 2026](https://frontierwisdom.com/trading-bot-bankroll-management-2026/) (fonte de praticante, não académica)

**Circuit breakers por contagem (modelo freqtrade)**
- StoplossGuard: parar se ≥`trade_limit` stops na janela `lookback_period`; exemplo oficial 4 stops em 24 velas → pausa 2-4 velas. MaxDrawdown: 20% em modo "equity" com ≥20 trades em 48 velas → pausa. CooldownPeriod 2-5 velas após cada saída, só por par (nunca global) — [freqtrade protections.md](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/docs/includes/protections.md)

**Horário intradiário**
- Evidência académica: "The average volatility reveals a very clean U-shaped pattern… high after the market opens, then decreases as to reach a minimum around lunch time and increases again steadily until market close" — [Allez & Bouchaud, arXiv:1009.4785](https://arxiv.org/pdf/1009.4785)
- Para o S&P 500, a volatilidade nos primeiros 30 minutos (9:30-10:00) foi "substantially higher than the last 30 minutes of trading"; "a particularly sharp spike for the opening half hour" — [ICI, Volatility in US and European Equity Markets](https://ici.org/print/pdf/node/19741); [Montclair State, Day Of The Week Effects In Intra-Day Volatility](https://clutejournals.com/index.php/IBER/article/view/3485)
- Guias de praticantes: "avoid trading in the first 15 minutes after market open"; volume cai perto das 12:00 ET até ~14:00 — [Bajaj Finserv, Timing of Intraday Trading](https://www.bajajfinserv.in/intraday-trading-time); [Kotak Securities](https://www.kotaksecurities.com/intraday-trading/which-time-frame-is-best-for-intraday-trading/) (contexto indiano, mas o padrão em "U" é o mesmo documentado para os EUA)

**Comissões (para modelar slippage/custos)**
- IBKR Pro Fixed, acções/ETFs US: USD 0,005 por acção, mínimo USD 1,00 por ordem, máximo 1% do valor da transacção — [IBKR Commissions & Fees (CA)](https://www.interactivebrokers.ca/en/pricing/commissions-home.php); [IBKR Fact Sheet](https://www.interactivebrokers.com/en/index.php?f=55459) (excertos de pesquisa; página oficial bloqueada nesta sessão)

### Inferences
- Para o bot em causa (5% de equity por posição, 4 posições, stop fixo 2%): o risco real por trade é 0,1% do equity quando o stop é respeitado, mas com gaps/slippage o stop de 2% é arbitrário face à volatilidade de cada acção. Trocar para "risco = 0,5-1% do equity; quantidade = risco / (k×ATR)", com k≈2 em barras de 1-min agregadas (ou ATR de 14 períodos em 5-min), alinha com as fontes acima.
- Um kill-switch diário de -3% é consistente com as recomendações (2-3%); falta um travão de drawdown multi-dia (ex.: pausa de 1-2 dias após -6% em 5 sessões) e um StoplossGuard (ex.: 3 stops em 2 h → pausa 1 h).
- Com comissão mínima de 1 USD, posições abaixo de ~200 USD pagam >0,5% só de comissão por lado; o bot deve rejeitar ordens cujo custo estimado (comissão ida+volta + 1 tick de slippage) exceda uma fracção do alvo de lucro.

### Gaps
- Não encontrei estudo revisto por pares que quantifique a melhoria de desempenho de "não negociar nos primeiros 15 minutos" para estratégias RSI/SMA em 1-min; a evidência é sobre o padrão de volatilidade, não sobre rentabilidade.
- Os limiares de drawdown (15-25%) e de Kelly fraccionário (0,25-0,5) vêm de praticantes/blogs, não de literatura primária.

## Pergunta 3 — Que gotchas específicos do IBKR documentam ib_async/ib_insync e as docs IBKR?

### Takeaway
Os riscos documentados mais relevantes: dados atrasados 15 min em paper sem partilha de subscrição; fills de paper "limpos" sem slippage nem parciais; limites de pacing em dados históricos (60 pedidos/10 min, 6 pedidos/2 s por contrato, repetição em 15 s); bracket orders com `transmit=False` nos filhos até ao último; erro 135 ao modificar filhos de bracket; `clientId` único por ligação; `disconnectedEvent` dispara duas vezes na v2.1.0; nunca usar `time.sleep()` dentro do loop do ib_async.

### Cited Findings

**Estado e API do ib_async**
- ib_async: licença Simplified BSD; Python ≥3.10; "currently maintained by Matt Stancliff" após a morte do autor original (início de 2024); renomeado de `ib_insync` para `ib_async` sob nova organização GitHub; baseado em asyncio+eventkit; recomenda 4096 MB mínimo de memória Java no Gateway/TWS para evitar crashes em operações de dados em massa — [ib_async README](https://raw.githubusercontent.com/ib-api-reloaded/ib_async/main/README.md)
- `IB.connect(host="127.0.0.1", port=7497, clientId=1, timeout=4, readonly=False, account="", raiseSyncErrors=False, fetchFields=StartupFetchALL)`; `clientId` "must be unique per connection. Setting clientId=0 will automatically merge manual TWS trading with this client"; `timeout` lança `asyncio.TimeoutError` — [ib_async ib.py](https://raw.githubusercontent.com/ib-api-reloaded/ib_async/main/ib_async/ib.py)
- `IB.RequestTimeout` (default 0 = espera indefinida) e `MaxSyncedSubAccounts` (50); regra: "It is important to not block the framework from doing its work" e "For introducing a delay, never use time.sleep() but use :meth:.sleep instead" — [ib_async ib.py](https://raw.githubusercontent.com/ib-api-reloaded/ib_async/main/ib_async/ib.py)
- `placeOrder` "Returns a Trade that is kept live updated with status changes, fills, etc." e serve para novas ordens e modificações — [ib_async ib.py](https://raw.githubusercontent.com/ib-api-reloaded/ib_async/main/ib_async/ib.py)
- `bracketOrder` cria uma ordem limite com take-profit e stop-loss: pai com `transmit=False`; take-profit filho `transmit=False` e `parentId=parent.orderId`; stop-loss filho `transmit=True`; submeter com `for o in bracket: ib.placeOrder(contract, o)` — [ib_async ib.py](https://raw.githubusercontent.com/ib-api-reloaded/ib_async/main/ib_async/ib.py)
- Campos de `Order`: `ocaGroup: str`, `ocaType: int = 0`, `tif: str`, `goodTillDate`, `activeStartTime`/`activeStopTime`, `outsideRth: bool = False`, `auxPrice`, `trailStopPrice`, `trailingPercent`, `algoStrategy`, `algoParams: list[TagValue]`, `parentId`, `orderRef`, `account`, `transmit: bool = True`, `whatIf: bool = False`. Classes: `MarketOrder(action, totalQuantity)`, `LimitOrder(action, totalQuantity, lmtPrice)`, `StopOrder(action, totalQuantity, stopPrice)` (orderType "STP", `auxPrice=stopPrice`), `StopLimitOrder(action, totalQuantity, lmtPrice, stopPrice)` — [ib_async order.py](https://raw.githubusercontent.com/ib-api-reloaded/ib_async/main/ib_async/order.py)
- Estados: `OrderStatus.DoneStates = {"Filled","Cancelled","ApiCancelled","Inactive"}`; `ActiveStates` inclui PendingSubmit, ApiPending, PreSubmitted, Submitted, ValidationError, ApiUpdate; `Trade` tem `fills`, `filled()`, `remaining()`, eventos `statusEvent`, `fillEvent`, `commissionReportEvent`, `filledEvent`, `cancelledEvent` — [ib_async order.py](https://raw.githubusercontent.com/ib-api-reloaded/ib_async/main/ib_async/order.py)

**Trailing stop e Adaptive**
- Trailing stop via API: `orderType="TRAIL"`, `trailStopPrice`, `trailingPercent`; `trailingPercent` é mutuamente exclusivo com o montante de trail (`auxPrice`) — "the API client can send one or the other but not both"; `outsideRth=True` "allows orders to also trigger or fill outside of regular trading hours" — [IBKR TWS API Order reference](https://www.interactivebrokers.com/docs/tws-api/ref/order); [IBKR TWS API Trailing Stop](https://www.interactivebrokers.com/docs/general/order-types/trailing-stop/tws-api-trailing-stop) (excertos de pesquisa; páginas bloqueadas nesta sessão)
- Uso real do Adaptive em código ib_async: `order.algoStrategy = 'Adaptive'` com `algoParams=[TagValue("adaptivePriority","Normal")]` (valores Urgent/Normal/Patient em repositórios) — [lowQuant/IB-Multi-Strategy-ATS broker/trademanager.py](https://github.com/lowQuant/IB-Multi-Strategy-ATS/blob/main/broker/trademanager.py); [olegroshka/blive types.py](https://github.com/olegroshka/blive/blob/main/src/blive/domain/types.py); [pysystemtrade sysbrokers/IB/client/ib_orders_client.py](https://github.com/pst-group/pysystemtrade/blob/master/sysbrokers/IB/client/ib_orders_client.py)
- Nota num repositório de 2026: o Adaptive é "Adapter-side (IBBroker)… Other adapters that do not support algorithmic order routing (paper, mock…) raise on submit" — [olegroshka/blive](https://github.com/olegroshka/blive/blob/main/src/blive/domain/types.py)

**Paper trading vs live**
- Dados não subscritos em paper são atrasados: acções US 15 min, opções US 15 min, futuros US 10 min; metais/forex/obrigações sem atraso — [IBKR Knowledge Base art. 1719](https://ibkb.interactivebrokers.com/article/1719) (excerto de pesquisa)
- Em paper o gestor de subscrições está desactivado; pode-se partilhar subscrições da conta real, mas "if the paper user is set to share live market data subscriptions and real and paper trading sessions are running simultaneously, they must reside on the same device" — [IBKR papertrader delayed data](https://www.interactivebrokers.com/en/trading/papertrader-delayed-data.php) (excerto)
- Outras diferenças: sem suporte a alguns tipos de ordem (VWAP, Auction, RFQ, Pegged to Market); "fills simulated from the top of the book with no deep book access"; "stops and complex order types that are always simulated"; "Simulated fills are clean and near-instant, so they don't always capture slippage or partial fills" — [supa.is, IBKR paper vs live 2026](https://supa.is/article/interactive-brokers-paper-trading-delayed-data-vs-live-account-why-prices-differ-how-to-fix-2026); [IBKR Campus paper trading](https://www.interactivebrokers.com/campus/trading-lessons/request-paper-trading-account/)

**Pacing de dados históricos**
- Regras: pedidos idênticos em <15 s proibidos; ≥6 pedidos para o mesmo Contract/Exchange/Tick Type em 2 s proibidos; >60 pedidos em qualquer janela de 10 min proibidos; BID_ASK conta a dobrar; um pedido não devolve mais de 2000 segundos de barras pequenas — [IBKR TWS API, Pacing Violations for Small Bars](https://www.interactivebrokers.com/docs/tws-api/doc/market-data-historical/historical-data-limitations/pacing-violations-for-small-bars-30-secs-or-less); [MultiCharts, Interactive Brokers Pacing Violation](https://www.multicharts.com/trading-software/index.php/Interactive_Brokers_Pacing_Violation)

**Issues relevantes**
- ib_async #207 (05-04-2026, aberta): "disconnectedEvent fires twice per disconnect" na v2.1.0, porque o `apiEnd` do cliente e o `emit()` explícito em `IB.disconnect()` disparam ambos; impacto em máquinas de estado de reconexão — [ib_async issue #207](https://github.com/ib-api-reloaded/ib_async/issues/207)
- ib_async #159 (07-2025, fechada): "cannot connect to IB using 2 threads with 2 different clientId"; #152 (06-2025, aberta): "Force close a connection for a specific client id?" — [ib_async issue #159](https://github.com/ib-api-reloaded/ib_async/issues/159); [ib_async issue #152](https://github.com/ib-api-reloaded/ib_async/issues/152)
- ib_insync #647 (10-2023): ao modificar o stop de um bracket com `ib.placeOrder(contract, sl_trd.order)` após alterar `auxPrice`, "Error 135, reqId 22: Can't find order with id = 20" e a ordem passa de PreSubmitted a Cancelled; o repositório ib_insync foi arquivado a 14-03-2024 — [ib_insync issue #647](https://github.com/erdewit/ib_insync/issues/647)
- ib_insync #700 (02-2024, aberta): "reqMktData sends an empty ticker after a while"; #687: "Timeouts on historical contract data" — [ib_insync issue #700](https://github.com/erdewit/ib_insync/issues/700); [ib_insync issue #687](https://github.com/erdewit/ib_insync/issues/687)

### Inferences
- Em paper com dados atrasados 15 min, uma estratégia de 1-min "vê" preços que já não existem; qualquer medição de slippage ou de qualidade do LLM é inválida sem partilha de subscrição real (mesma máquina) — isto deve ser verificado no arranque (`reqMarketDataType` e inspecção do `Ticker.marketDataType`).
- Para OCA/trailing: o bracket do ib_async usa `parentId`; para trailing stop como filho, substituir o `StopOrder` por `Order(orderType="TRAIL", trailingPercent=…, parentId=…, tif="GTC")` mantém a lógica de cancelamento automático. Para GTC nos filhos é preciso definir `tif="GTC"` explicitamente (o default `tif=""` cai em DAY); sem isso, filhos de bracket expiram no fecho e a posição fica sem protecção overnight. (Inferência a partir dos defaults do `order.py`; comportamento DAY-default não confirmado em doc IBKR nesta sessão.)
- Reconexão: idempotência obrigatória no handler de `disconnectedEvent` (issue #207), `clientId` fixo por processo, e após reconectar reconciliar `ib.openTrades()`/`ib.positions()` com o SQLite em vez de confiar na memória do processo.
- Modificar filhos de bracket é frágil (erro 135); mais robusto é cancelar o filho e colocar novo stop com o mesmo `ocaGroup`, ou gerir o trailing do lado do bot só quando a ligação está estável.

### Gaps
- O significado dos valores `ocaType` 1/2/3 não está documentado no `order.py` do ib_async e as páginas IBKR estavam bloqueadas; não foi confirmado nesta sessão.
- Comportamento exacto de `tif` default (DAY) e de stops "GTC" em paper vs live não foi lido numa fonte primária.
- Não foi possível ler o notebook oficial `ordering.html` do ib_async (domínio bloqueado).

## Pergunta 4 — Que fontes de dados gratuitas (GitHub) dão calendários de resultados, calendário económico, VIX e notícias para um filtro de blackout?

### Takeaway
Combinação viável e local: `yfinance` (próxima data de resultados via `Ticker.calendar`/`earnings_dates`, e VIX via `^VIX`), `finnhub-python` (earnings_calendar, calendar_economic, company_news; chave gratuita), `stocks-earnings-dates` (base SQLite offline derivada de 8-K da SEC) e `edgartools` (8-K Item 2.02 directamente da EDGAR, sem chave). Os limites do free tier do Finnhub não foram confirmados em fonte primária.

### Cited Findings
- yfinance: `Ticker.calendar` dá a próxima data de resultados com estimativas de receitas/EPS; `Ticker.earnings_dates` devolve DataFrame de datas futuras e históricas; `get_earnings_dates(limit=XX)` para mais; VIX via `yf.Ticker("^VIX").history(period="1mo")` — [IBKR Campus, How to get stock earnings data with Python](https://www.interactivebrokers.com/campus/ibkr-quant-news/how-to-get-stock-earnings-data-with-python/); [PyPI yfinance](https://pypi.org/project/yfinance/)
- finnhub-python: licença Apache; `pip install finnhub-python`; `earnings_calendar(_from=, to=, symbol="", international=False)`, `calendar_economic('2021-01-01','2021-01-07')`, `company_news('AAPL', _from=, to=)`, `general_news('forex', min_id=0)`; usa `_from` por causa da palavra reservada — [finnhub-python README](https://raw.githubusercontent.com/Finnhub-Stock-API/finnhub-python/master/README.md)
- stocks-earnings-dates: v0.1.9 (24-11-2025), Python 3.9-3.13; "uses a built-in SQLite database with over 37,000+ earnings dates collected from the public sources of SEC EDGAR" (8-K Item 2.02), 22 anos, S&P 500 + top 100 Nasdaq; `get_earnings("AAPL")` devolve lista de datas — [PyPI stocks-earnings-dates](https://pypi.org/project/stocks-earnings-dates/)
- edgartools: MIT; `pip install edgartools`; exige `set_identity("your.name@example.com")` (requisito da SEC), "No API key, signup, or rate-limit tiers are needed"; `get_filings(form="8-K").latest().obj().items`; cobre 10-K/10-Q, 8-K, 13F, Form 4; mantido por um único programador — [edgartools README](https://raw.githubusercontent.com/dgunning/edgartools/main/README.md)
- Outras opções mencionadas: wrapper da API pública do NASDAQ (earnings/IPO/dividend calendar) e `yahoo_fin.get_earnings_history` — [Towards Data Science, earnings calendar with Python](https://towardsdatascience.com/how-to-download-the-public-companies-earnings-calendar-with-python-9241dd3d15d/)

### Inferences
- Para um blackout robusto e offline: cache diário em SQLite das próximas datas de resultados de todos os tickers do universo (yfinance + fallback finnhub), regra "não abrir posições em T-1 a T+1 de resultados", e um gate de regime "VIX > limiar (ex.: 25-30) → reduzir tamanho/parar" — os limiares concretos de VIX não foram encontrados em fonte citável e devem ser calibrados.
- `stocks-earnings-dates` é histórico (até 2024/2025 nos exemplos); serve para backtests de blackout, não para datas futuras — para o futuro usar yfinance/finnhub.

### Gaps
- Limites do free tier do Finnhub (pedidos/min) não estão no README; não confirmados.
- Não encontrei biblioteca GitHub gratuita e mantida para "calendário económico" (FOMC/CPI/NFP) além do endpoint do Finnhub; `alpha_vantage` e `polygon` não foram verificados nesta sessão.
- Não foi encontrada fonte primária para um limiar concreto de VIX usado como filtro de regime em bots de retalho.
