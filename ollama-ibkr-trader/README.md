# Ollama × IBKR Trader

Sistema de trading autónomo em Python: velas de 1 minuto da **Interactive
Brokers** (`ib_async`, Paper Trading na porta 7497), um LLM local via
**Ollama** que *propõe* BUY/SELL/HOLD, uma **camada de risco determinística**
que decide tamanho, stop, take-profit e vetos, registo em **SQLite**,
**settlement** de cada decisão contra o mercado real, **calibração** da
confiança, **lições calculadas em código** e um **protocolo estatístico** que
só liberta mais risco quando há evidência. Interface **CustomTkinter** (modo
escuro).

> ⚠️ Destina-se a Paper Trading. A pesquisa que orientou este desenho
> (`../reports/Melhorias sobrevivência trading bot.md`) conclui que um LLM de
> 7B a ler indicadores ao minuto não tem vantagem demonstrada: o que protege o
> capital são as regras de risco, não o modelo. Nunca apontes isto para uma
> conta real sem passar os gates estatísticos descritos abaixo.

## Princípio: o LLM propõe, o código decide

```
velas 1 min ──► agregação 5 min ──► RSI/SMA/EMA + dinâmica (cruzamentos, slopes) + ATR
                                              │
                      a cada 15 min ──► LLM: N amostras × (raciocínio livre → JSON schema)
                                              │   fração de acordo, margem, logprob, confiança verbal
                                              ▼
                       calibração (Platt) ──► probabilidade ──► limiar = break-even do bracket + margem
                                              │
   kill-switch · StoplossGuard · cooldown · drawdown · VIX · resultados · horário · custos · sentimento · vol
                                              │  (qualquer um veta)
                                              ▼
            tamanho = risco% × equity / (k × ATR) ──► bracket: entrada MKT + TP limit + SL stop (OCA, GTC)
                                              │
        settlement a 30 min ──► lições em código ──► Platt ──► relatório/gates ──► risco 0,5% → 1%
```

## Arquitetura

```
main.py                      ponto de entrada (também usado pelo PyInstaller)
trader/
  app.py                     composição: config → DB → cérebro → motor → GUI
  config.py                  Settings (config.json em ~/.ollamaibkrtrader/), ~100 parâmetros
  gui.py                     CustomTkinter: controlo, portefólio, consola, estados de risco
  ui_bus.py                  fila thread-safe motor → GUI + handler de logging
  trading_engine.py          loop asyncio numa thread dedicada; ciclo de decisão e execução
  ibkr_client.py             ib_async: ligação, velas, carteira, bracket GTC/OCA, pacing,
                             verificação de dados atrasados, reconciliação, proteção de posições
  ollama_brain.py            prompt neutro, 2 etapas (raciocínio → JSON schema), N amostras,
                             logprobs, REVIEW, anonimização, cache, ensemble/A-B
  indicators.py              RSI, SMA, EMA, ATR, agregação, vol EWMA, dinâmica (Python puro)
  risk.py                    sizing por ATR, protections freqtrade-style, gates de custo/horário/
                             eventos, vetos de sentimento e volatilidade
  market_data.py             resultados, VIX e notícias via yfinance/finnhub (cache, fail-soft)
  calibration.py             Platt scaling em Python puro; limiar de break-even
  settlement.py              retorno a horizonte fixo, alpha vs SPY, rótulo correto/errado
  lessons.py                 lições programáticas (RSI, hora, ativo, confiança) com decaimento
  analytics.py               Brier/ECE, permutação, expectancy, Monte Carlo, PSR/DSR, MinBTL,
                             WFE, gates → multiplicador de risco; relatório markdown
  retrospective.py           orquestra settlement → lições → calibração → relatório
  backtest.py                replay offline (CSV/Alpaca) com cache de decisões e custos
  sentiment.py               FinBERT opcional (feature + veto)
  volmodel.py                Chronos-Bolt opcional com fallback EWMA (largura p90−p10)
  database.py                SQLite: decisões (c/ settlement), ordens, trades, fills, P&L,
                             lições, cache, experiências, protections, relatórios
tests/                       69 testes offline (IBKR e Ollama simulados)
trader.spec, build.sh/.bat   PyInstaller
```

### Threads e loops

- **Thread principal**: Tkinter. A GUI envia comandos com `engine.call(coroutine)`
  e consome o `UIBus` com `after()`; nunca toca no `ib_async`.
- **Thread `trading-engine`**: loop `asyncio` com o `IB()`, as subscrições, o
  ciclo de decisão, o portefólio, o watchdog de ligação e a retrospetiva.
- Chamadas ao Ollama, yfinance, FinBERT e Chronos correm em `run_in_executor`.

## O que cada fase do roteiro implementa

**Fase 1 — risco em código (zero inferência)**
- Tamanho por volatilidade: `qty = equity × risco% / (k × ATR)` com piso de ATR
  no 5.º percentil (pysystemtrade) e teto de 30% do equity por ativo; risco
  0,5% em aprendizagem, 1% quando os gates passam. Stop = 2×ATR, TP = 2× o stop.
- Protections: StoplossGuard (3 stops/2 h → pausa 1 h), cooldown 30 min por
  ativo, MaxDrawdown multi-dia (6% em 5 dias → pausa 1 sessão; 3% → tamanho a
  metade), 5 perdas seguidas → pausa até ao dia seguinte, 6 entradas/dia,
  kill-switch −3%/dia.
- Gate de custos (comissão IBKR 0,005/ação mín. 1 USD + slippage ≤ 25% do
  ganho alvo; notional ≥ 200 USD) e de horário (sem entradas nos primeiros 15
  e últimos 10 min).
- Blackout de resultados T−1..T+1 e VIX (≥25 metade, ≥35 bloqueia) via yfinance.
- ib_async: `tif="GTC"` + `ocaGroup` nos filhos, verificação de
  `marketDataType` (aviso de dados atrasados 15 min), `disconnectedEvent`
  idempotente, reconciliação SQLite↔IBKR ao religar, proteção automática de
  posições sem stop, pacing de pedidos históricos, TRAIL opcional.
- Cadência do LLM: 15 min por ativo, sinal só executa se persistir 2 ciclos.
- Prompt neutro (sem ameaças), HOLD por omissão, sinais em transição;
  sentinela REVIEW em vez de HOLD silencioso (uma repetição a temperatura 0).

**Fase 2 — o LLM decide menos e é medido**
- Duas chamadas: análise livre → JSON com `format` = JSON Schema (Ollama ≥ 0.5),
  temperatura 0, `seed` fixo; `logprobs` pedidos (Ollama ≥ 0.12.11, fail-soft).
- N = 5 amostras a temperatura 0,7: fração de acordo e margem como sinal.
- Platt scaling (regressão logística em Python puro) sobre [confiança verbal,
  acordo, margem, logprob] vs resultado settled, a partir de 200 decisões,
  reajustada a cada 24 h; antes disso, heurística conservadora.
- Limiar de execução = break-even do bracket `p* = (1 + c/R)/(1 + a)` + margem
  de 0,08 (piso 0,65 enquanto não calibrado).
- Settlement a 30 min com alpha vs SPY; lições calculadas em código com score
  |z| × √suporte × decaimento (meia-vida 10 dias), top-3 relevantes no prompt.
- Relatório estatístico (botão ou sextas-feiras): Brier vs climatologia, ECE,
  diagrama de fiabilidade, hit-rate vs permutação, expectancy, profit factor,
  Kelly, Monte Carlo de ruína, Sharpe/Sortino, PSR, DSR com N honesto de
  experiências (cada mudança de prompt/modelo/config conta), MinBTL, WFE.
- Replay offline (`python -m trader.backtest --csv ...` ou `--alpaca-days N`)
  com cache por hash do prompt, custos e anonimização de tickers.
- Ensemble (`ensemble_models`) com acordo obrigatório e A/B (`ab_test_models`).

**Fase 3 — opcionais, desligados por defeito**
- `sentiment_enabled`: FinBERT (`ProsusAI/finbert`) sobre manchetes das
  últimas 2 h como feature no prompt e veto (BUY com sentimento ≤ −0,4 em ≥ 3
  notícias).
- `volmodel_enabled`: Chronos-Bolt para largura p90−p10; sem a biblioteca usa
  vol EWMA. Bloqueia entradas quando a largura prevista excede 3%.
- Gates estatísticos: só quando todos passam o risco por trade sobe de 0,5%
  para 1%. O bot nunca "decide" sair do paper: isso é teu.

## Instalação

```bash
cd ollama-ibkr-trader
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Requisitos:
- Python 3.10+ (exigido pelo `ib_async`) com Tk (`sudo apt install python3-tk` no Linux).
- **TWS ou IB Gateway** em Paper Trading com API ativa: *Configure → API →
  Settings*: ✔ Enable ActiveX and Socket Clients, ✘ Read-Only API, porta
  **7497**, 127.0.0.1 nos Trusted IPs. Sem subscrição, o paper usa dados com
  15 min de atraso; o bot avisa e as métricas de qualidade do LLM não são válidas.
- **Ollama** (`ollama serve`) com pelo menos um modelo; o dropdown lista `/api/tags`.

Configuração em `~/.ollamaibkrtrader/config.json`; base de dados, log e
`relatorio_estatistico.md` na mesma pasta (`OLLAMA_TRADER_HOME` muda-a).

## Executável único (PyInstaller)

```bash
./build.sh          # Linux/macOS
build.bat           # Windows
```

`trader.spec` recolhe CustomTkinter, ib_async e eventkit e exclui torch,
transformers e chronos (os módulos opcionais são importados tardiamente e
ficam desativados no executável se não estiverem presentes).

## Testes

```bash
pip install pytest
python -m pytest -q
```

Sem TWS, Ollama nem ecrã: o motor é exercitado com um cliente IBKR falso e o
pipeline do LLM com um Ollama simulado.

## Biblioteca IBKR

`ib_insync` não recebe versões desde julho de 2023 (0.9.86) e o repositório
foi arquivado. O projeto usa `ib_async` (github.com/ib-api-reloaded/ib_async),
fork oficial mantido pela comunidade com a mesma API; se só existir
`ib_insync` instalado, o código recorre a ele automaticamente.
