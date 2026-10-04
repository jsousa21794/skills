# Ollama × IBKR Trader

Sistema de trading autónomo em Python: velas de 1 minuto da **Interactive
Brokers** (`ib_insync`, Paper Trading na porta 7497), decisões tomadas por um
LLM local via **Ollama**, ordens **Bracket** automáticas (Stop Loss 2 % /
Take Profit 5 %), registo em **SQLite** e **retrospetiva diária** que reescreve o
prompt com base no desempenho real. Interface em **CustomTkinter** (modo escuro).

> ⚠️ Destina-se a Paper Trading. Um LLM a decidir sobre velas de 1 minuto com
> RSI e médias móveis não tem vantagem estatística comprovada; o kill-switch
> diário, o limiar de confiança e os Brackets são as verdadeiras proteções.
> Nunca apontes isto para uma conta real sem semanas de resultados em papel.

## Arquitetura

```
main.py                      ponto de entrada (também usado pelo PyInstaller)
trader/
  app.py                     composição: config -> DB -> cérebro -> motor -> GUI
  config.py                  Settings (config.json em ~/.ollamaibkrtrader/)
  gui.py                     CustomTkinter: controlo, portefólio, consola
  ui_bus.py                  fila thread-safe motor -> GUI + handler de logging
  trading_engine.py          loop asyncio numa thread dedicada (ciclo de decisão)
  ibkr_client.py             ib_insync: ligação, velas 1 min, carteira, Brackets
  ollama_brain.py            system prompt "lucrar ou morrer", /api/chat, parser JSON
  indicators.py              RSI, SMA, EMA, variação % (Python puro)
  database.py                SQLite: decisões, ordens, trades, fills, P&L, prompts
  retrospective.py           análise diária -> lições -> novo prompt + recalibração
tests/                       47 testes (parser, indicadores, DB, motor, retrospetiva)
trader.spec, build.sh/.bat   empacotamento PyInstaller num único executável
```

### Threads e loops

- **Thread principal**: Tkinter (obrigatório em macOS/Windows). A GUI nunca
  chama `ib_insync`; envia comandos com `engine.call(coroutine)`
  (`asyncio.run_coroutine_threadsafe`) e consome o `UIBus` com `after()`.
- **Thread `trading-engine`**: cria o seu próprio loop `asyncio` e nele vivem o
  `IB()`, as subscrições de velas, o ciclo de decisão, o loop de portefólio, o
  watchdog de ligação e o agendador da retrospetiva.
- As chamadas `requests` ao Ollama correm em `run_in_executor`, por isso nunca
  bloqueiam o loop nem a interface.

### Ciclo de decisão (a cada `cycle_seconds`, só em horário regular NY)

1. Lê as velas de 1 min (streaming `reqHistoricalData(keepUpToDate=True)`).
2. Calcula RSI(14), SMA20, SMA50, EMA9, variações 5/30 min sobre a última vela **fechada**.
3. Monta o prompt com estado da carteira, posição no ativo e últimas 5 decisões.
4. Ollama responde `{"acao","confianca","razao"}`; o parser tolera fences,
   aspas simples, chaves sem aspas, percentagens, sinónimos PT/EN e texto à
   volta. Qualquer falha => `HOLD` com `parse_ok=false` (nunca executa).
5. Gestão de risco: limiar de confiança, sem pirâmide, sinal contrário fecha a
   posição (não inverte no mesmo ciclo), máximo de posições, short opcional,
   tamanho = `risk_fraction_per_trade × NetLiq / preço`, kill-switch diário.
6. Entrada a mercado + TP limit + SL stop ligados por `parentId` (OCA), GTC.
7. Tudo fica em SQLite; as execuções (`execDetailsEvent`) fecham os trades com P&L.

### Retrospetiva (ciclo fechado)

Corre à hora `retro_time_local` (por defeito 21:30) ou pelo botão
**Retrospetiva agora**. Para cada decisão das últimas 24 h vai buscar o preço
30 minutos depois (histórico IBKR; se offline usa as próprias decisões
seguintes), classifica como certa/errada/neutra/oportunidade perdida e extrai
padrões: compras em sobrecompra, vendas em sobrevenda, entradas contra a
tendência, sobreconfiança, mais stops do que take-profits, pior ativo,
respostas inválidas. Gera lições agressivas (opcionalmente mais 2 pedidas ao
próprio modelo), funde-as com as anteriores (máx. 8), grava uma nova versão do
prompt e **sobe o limiar de confiança** quando as decisões erradas tinham mais
confiança do que as certas.

## Instalação

```bash
cd ollama-ibkr-trader
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Requisitos:
- Python 3.10+ com Tk (no Linux: `sudo apt install python3-tk`).
- **TWS ou IB Gateway** em Paper Trading com API ativa:
  *Configure → API → Settings*: ✔ Enable ActiveX and Socket Clients,
  ✘ Read-Only API, porta **7497**, 127.0.0.1 nos Trusted IPs.
  Sem subscrição de dados o sistema usa dados atrasados (`market_data_type = 3`).
- **Ollama** a correr (`ollama serve`) com pelo menos um modelo
  (`ollama pull llama3`, `ollama pull qwen2.5-coder`). O dropdown lista o que
  `/api/tags` devolver.

Configuração em `~/.ollamaibkrtrader/config.json` (criado no primeiro arranque);
a base de dados e o log ficam na mesma pasta. A variável `OLLAMA_TRADER_HOME`
muda essa pasta.

## Executável único (PyInstaller)

```bash
./build.sh          # Linux/macOS
build.bat           # Windows
```

O `trader.spec` recolhe os assets do CustomTkinter e os submódulos do
`ib_insync`/`eventkit`, exclui bibliotecas pesadas não usadas e gera
`dist/OllamaIBKRTrader(.exe)` sem consola. Para depurar, muda `console=True`.
Os dados continuam a ser gravados na pasta do utilizador, nunca dentro do
bundle.

## Testes

```bash
pip install pytest
python -m pytest -q
```

Os testes não precisam de TWS, Ollama nem ecrã: o motor é exercitado com um
cliente IBKR falso e a retrospetiva com séries de preços sintéticas.

## Notas sobre o prompt

O system prompt segue a especificação pedida (função vital = lucrar; perda
continuada = terminação) e inclui regras de sobrevivência que tornam `HOLD`
uma escolha legítima. Na prática, ameaças no prompt não melhoram a capacidade
preditiva de um modelo e tendem a aumentar respostas sobreconfiantes; é por
isso que a retrospetiva penaliza explicitamente a sobreconfiança e recalibra o
limiar de execução. O que protege o capital são as regras determinísticas de
risco, não o tom do prompt.

`ib_insync` foi arquivado em 2024; o código importa `ib_async` (fork mantido,
mesma API) automaticamente se `ib_insync` não estiver instalado.
