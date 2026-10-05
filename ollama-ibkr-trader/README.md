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
  lessons_offline.py         lições calculadas a partir de histórico de velas de 1 min
data/seed_lessons.json       lições iniciais com fonte (importadas uma vez)
  sentiment.py               FinBERT opcional (feature + veto)
  volmodel.py                Chronos-Bolt opcional com fallback EWMA (largura p90−p10)
  database.py                SQLite: decisões (c/ settlement), ordens, trades, fills, P&L,
                             lições, cache, experiências, protections, relatórios
tests/                       75 testes offline (IBKR e Ollama simulados)
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

## Custos: a comissão faz parte do P&L

Pagar 1 USD de comissão para ganhar 0,50 USD é prejuízo. Por isso:
- cada execução regista a comissão (o `CommissionReport` real da IBKR quando
  chega; até lá, a estimativa 0,005 USD/ação com mínimo de 1 USD); os trades
  guardam P&L bruto, comissões e P&L líquido, e é o líquido que entra em todas
  as métricas, lições e gates;
- o gate de custos recusa a entrada se a comissão ida+volta exceder 20% do
  ganho bruto no take-profit, se o ganho líquido no TP for inferior a 3× o
  custo, ou se o valor esperado líquido com a probabilidade calibrada for
  negativo; o limiar de execução já incorpora o custo no break-even;
- no settlement, um movimento menor do que o custo ida+volta nunca conta como
  acerto;
- a consola e a tabela de decisões mostram custo e ganho líquido no TP; o
  cartão "P&L realizado" mostra as comissões do dia; o relatório mostra a
  fração do lucro bruto consumida por comissões.

## Lições iniciais e material de treino

A pesquisa no GitHub e no Hugging Face (notas em
`../research_notes/Material de treino para o bot/`) não encontrou nenhum
corpus de "lições" de agentes LLM de trading pronto a importar, nem datasets
de features técnicas intradiárias → BUY/SELL/HOLD, nem adaptadores LoRA para
isso em modelos compatíveis com o bot. O Ollama atual, além disso, deixou de
aceitar adaptadores LoRA (só GGUF já fundidos). O que existe e é usável:

- **`data/seed_lessons.json`**: 10 lições iniciais com fonte, derivadas da
  literatura (volatilidade da abertura, dinâmica vs níveis, custos,
  sobreconfiança, saídas mecânicas, volatilidade e tamanho). São importadas
  uma vez no arranque (`seed_lessons_file`), têm importância baixa e são
  ultrapassadas pelas lições medidas nas tuas decisões assim que houver
  suporte estatístico.
- **`python -m trader.lessons_offline`**: calcula lições reais a partir de
  velas de 1 minuto (CSV, ou Parquet dos datasets Hugging Face
  `Rrishab/OHLCV-1m` / `ggaddam/OHLCV-1m`), com os mesmos indicadores do bot,
  sinais em transição e retorno a 30 minutos líquido de custos. Produz um JSON
  no mesmo formato, pronto a apontar por `seed_lessons_file`.
- Para avaliação (não treino): `TheFinAI/flare-sm-acl` e irmãos (diário,
  tweets, 2014-2018) com o scorer do PIXIU.
- A evitar: LoRAs FinGPT (base Llama-2, rótulos gerados por GPT-4, horizonte
  semanal), InvestLM/FinMA (licença ou base antiga), Fin-R1 como dados de
  trading (é QA financeiro). Trading-R1 e o seu dataset não estão publicados.

## Interface

- **Cabeçalho permanente** com o valor da carteira (Net Liquidation), variação do
  dia, estado IBKR/Ollama/ciclo/dados. Sem ligação, mostra o **último valor
  conhecido** com a hora, lido do histórico em SQLite; o título da janela
  repete o valor para ficar visível na barra de tarefas. Interruptor "Janela
  sempre visível" (always-on-top).
- **Conta**: seletor *Paper* / *Real* na barra lateral. *Real* liga à porta
  7496 (TWS) e exige escrever `REAL` numa caixa de confirmação; o distintivo
  no cabeçalho passa a vermelho. Ao ligar, o bot confirma o tipo de conta pelo
  identificador (`DU…` = paper): em modo paper com conta real desliga-se por
  segurança; em modo real com conta paper avisa. Não é preciso passar por
  paper: a camada de risco, o kill-switch e os brackets funcionam igual em
  ambos os modos, e os gates estatísticos apenas decidem se o risco por trade
  é 0,5% ou 1%.
- **Separadores**: *Visão geral* (cartões, gráfico do valor da carteira nas
  últimas 48 h, posições com stop e take-profit), *Decisões* (cada proposta do
  LLM com confiança verbal, acordo, probabilidade calibrada e o veredicto da
  camada de risco), *Risco* (proteções ativas, gates, calibração, lições) e
  *Consola*.

## Instalação

```bash
cd ollama-ibkr-trader
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Requisitos:
- Python 3.10+ (exigido pelo `ib_async`) com Tk (`sudo apt install python3-tk` no Linux).
- **TWS ou IB Gateway** com API ativa: *Configure → API → Settings*:
  ✔ Enable ActiveX and Socket Clients, ✘ Read-Only API, porta **7497**
  (paper) ou **7496** (real), 127.0.0.1 nos Trusted IPs. Sem subscrição, o paper usa dados com
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
