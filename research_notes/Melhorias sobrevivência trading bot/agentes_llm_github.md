# Projetos open-source de agentes de trading com LLM no GitHub (2024-2026): inventário e componentes portáveis

Notas de investigação para o bot Python (ib_async / IBKR paper, barras de 1 min, RSI/SMA/EMA, Ollama Llama3/Qwen2.5, JSON BUY/SELL/HOLD + confiança, bracket 2%/5%, SQLite, retrospectiva diária). Metadados GitHub (stars, licença, último push) recolhidos via API GitHub em 2026-10-05. Prosa em português europeu; nomes, URLs e termos técnicos mantidos no original.

Nota metodológica: arxiv.org, alphaxiv.org, huggingface.co, dev.to, suchow.io, emergentmind.com e chatpaper.com estavam bloqueados pelo proxy de saída desta sessão; os números dos papers abaixo vêm de resumos indexados por pesquisa web, READMEs e código-fonte dos repositórios (raw.githubusercontent.com e páginas github.com), e estão assinalados como tal. Onde não consegui confirmar um valor na fonte primária, está nas Gaps.

---

## Q1. Quais são os repositórios mais ativos/credíveis e o que implementa cada um?

### Takeaway
Em 2026 o ecossistema tem dois "gigantes" ativos (TradingAgents, 109,8k stars; ai-hedge-fund, 63,9k stars), um novato muito ativo (HKUDS/Vibe-Trading, 34,7k stars em 6 meses), e vários projetos académicos mais antigos cujo valor está nas ideias e não no código (FinMem sem commits desde 2024-08; StockAgent é só simulação). Os projetos mais úteis para portar mecanismos de sobrevivência não são os maiores, mas os que separam "LLM propõe / código decide" (ALTA, astra-quant-agent) e os que medem comportamento em produção (DXRG).

### Cited Findings

**Inventário (metadados API GitHub, 2026-10-05)**

| Projeto | URL | Stars / forks | Licença | Linguagem | Último push | Natureza |
|---|---|---|---|---|---|---|
| TauricResearch/TradingAgents | https://github.com/TauricResearch/TradingAgents | 109 778 / 21 102 | Apache-2.0 | Python | 2026-10-03 | Framework multi-agente (análise → debate → trader → risco → PM) |
| virattt/ai-hedge-fund | https://github.com/virattt/ai-hedge-fund | 63 858 / 11 224 | MIT | Python | 2026-10-02 | "Equipa" de agentes-persona + risk/portfolio manager, paper trading com ledger |
| HKUDS/Vibe-Trading | https://github.com/HKUDS/Vibe-Trading | 34 669 / 5 628 | MIT | Python | 2026-10-04 | Agente único + 74 ferramentas MCP, backtest, 18 brokers (incl. IBKR read-only) |
| hsliuping/TradingAgents-CN | https://github.com/hsliuping/TradingAgents-CN | 32 148 / 6 719 | NOASSERTION (não-standard) | Python | 2026-09-22 | Fork chinês do TradingAgents |
| AI4Finance-Foundation/FinGPT | https://github.com/AI4Finance-Foundation/FinGPT | 21 351 / 3 026 | MIT | Jupyter | 2026-09-23 | LLMs financeiros fine-tuned (sentimento, etc.), não é um bot |
| AI4Finance-Foundation/FinRobot | https://github.com/AI4Finance-Foundation/FinRobot | 8 140 / 1 374 | Apache-2.0 | Python | 2026-09-28 | Plataforma de agentes (AutoGen → OpenAI Agents SDK → PydanticAI) |
| jesse-ai/jesse | https://github.com/jesse-ai/jesse | 8 612 / 1 252 | (não recolhido) | Python | ativo (updated 2026-10-04) | Bot cripto clássico, sem LLM |
| LuckyOne7777/LLM-Trading-Lab | https://github.com/LuckyOne7777/LLM-Trading-Lab | 7 501 / 1 555 | (não indicado no README) | Python | ativo (updated 2026-10-04) | Experiência real: ChatGPT a gerir micro-caps com $100 |
| ginlix-ai/LangAlpha | https://github.com/ginlix-ai/LangAlpha | 1 801 | Apache-2.0 | Python | 2026-10-04 | Plataforma de research ("Claude Code for financial markets"); não executa ordens |
| kyky2347/ALTA | https://github.com/kyky2347/ALTA | 1 031 | Apache-2.0 | Python | 2026-10-01 | Multi-agente "evidence-first" com Shadow book e gates de autorização |
| pipiku915/FinMem-LLM-StockTrading | https://github.com/pipiku915/FinMem-LLM-StockTrading | 962 / 195 | MIT | Python | 2024-08-18 (inativo) | Agente com memória em camadas + perfil de risco |
| MingyuJ666/Stockagent | https://github.com/MingyuJ666/Stockagent | 709 / 170 | (não recolhido) | Python | criado 2024-02; updated 2026-10-02 | Simulação de mercado com agentes LLM (não é bot) |
| 51bitquant/ai-hedge-fund-crypto | https://github.com/51bitquant/ai-hedge-fund-crypto | 624 / 155 | MIT | Python | 2025-09-05 | Ensemble de estratégias multi-timeframe + LLM PM, cripto |
| TauricResearch/Trading-R1 | https://github.com/TauricResearch/Trading-R1 | 500 | — | — | criado 2025-09-15 | Só "Terminal coming soon"; sem código |
| jason8745/llm-agent-trader | https://github.com/jason8745/llm-agent-trader | 492 | MIT | Python | 2025-12-04 | Backtesting com decisão LLM, FastAPI + Next.js |
| 0xethanq/astra-quant-agent | https://github.com/0xethanq/astra-quant-agent | 338 | AGPL-3.0 + Commons Clause (README) | Python | 2026-10-03 | Conselho multi-LLM + risco determinístico em Python, OKX |
| huygiatrng/AlpacaTradingAgent | https://github.com/huygiatrng/AlpacaTradingAgent | 274 | Apache-2.0 | Python | 2026-10-03 | TradingAgents + execução real em Alpaca, scheduler, UI Dash |
| ProjectDXAI/continuous-record-llm-trading-agents | https://github.com/ProjectDXAI/continuous-record-llm-trading-agents | 79 | — | — | 2026-10-01 | Artefactos do paper DXRG (231 638 turnos em produção) |
| Y-Research-SBU/QuantAgent (QuantHarness) | https://github.com/Y-Research-SBU/QuantAgent | (não recolhido) | — | — | — | 4 agentes só com OHLC para HFT (paper 2509.09995) |
| freqtrade/freqtrade | https://github.com/freqtrade/freqtrade | 55 030 / 11 381 | (não recolhido) | Python | 2026-10-04 | Bot cripto com módulo FreqAI (ML clássico, não LLM) |
| nautechsystems/nautilus_trader | https://github.com/nautechsystems/nautilus_trader | 29 626 | (não recolhido) | Rust/Python | 2026-10-04 | Motor de execução event-driven; sem LLM |

- TradingAgents: criado 2024-12-28; 77 issues abertos; tópicos agent/finance/llm/multiagent/trading; homepage aponta para o paper arXiv 2412.20138 — [GitHub API](https://github.com/TauricResearch/TradingAgents)
- TradingAgents organiza-se em `tradingagents/agents/{analysts, managers, researchers, risk_mgmt, trader}` mais `context.py, post_screen.py, rating.py, schemas.py, state.py, structured.py, tools.py` — [github.com/TauricResearch/TradingAgents/tree/main/tradingagents/agents](https://github.com/TauricResearch/TradingAgents/tree/main/tradingagents/agents)
- O módulo de memória do TradingAgents tem `log.py`, `reflection.py`, `settlement.py` — [tradingagents/memory](https://github.com/TauricResearch/TradingAgents/tree/main/tradingagents/memory)
- ai-hedge-fund (reestruturado) tem pacote `hedge_fund/` com `backtesting/, brokers/, data/, event_study/, features/ (point-in-time fundamentals), fund/, llm/, paper/, pipeline/, portfolio/ (view blending e target weights), risk/ (position and exposure limits), signals/ (alpha models e agent personas), strategies/, tui/, validation/ (cross-validation)` — [github.com/virattt/ai-hedge-fund/tree/main/hedge_fund](https://github.com/virattt/ai-hedge-fund/tree/main/hedge_fund)
- ai-hedge-fund: "This project is for educational and research purposes only"; paper trading com "hash-chained ledger" em `~/.hedge-fund/paper/`; dados de Financial Datasets API; MIT — [README](https://raw.githubusercontent.com/virattt/ai-hedge-fund/main/README.md)
- FinRobot: arquitetura em 4 camadas (Financial AI Agents; Financial LLM Algorithms; LLMOps/DataOps; Multi-source LLM foundation), "Director Agent" que aloca tarefas, V0 em AutoGen, V1 em OpenAI Agents SDK, V2 em PydanticAI ("production-ready", app desktop), V3 "DeepSeek-Harness" em desenvolvimento; dados Finnhub, yfinance, SEC EDGAR, FMP — [README](https://raw.githubusercontent.com/AI4Finance-Foundation/FinRobot/master/README.md)
- Vibe-Trading: criado 2026-04-01; "74 tools" via MCP; motor de backtest com Sharpe/Sortino/drawdown; "Memory System" com compaction; "Risk Management: position limits, exposure caps, pre-trade gates, compliance audits"; 18 conectores de broker incl. "Interactive Brokers (official MCP read-only)" e Alpaca; Python 3.11+, FastAPI, React 19 — [README](https://raw.githubusercontent.com/HKUDS/Vibe-Trading/main/README.md)
- ALTA: "Research is open-ended; authority is explicit" — LLMs propõem, código impõe frescura dos dados, isolamento de risco e transições duráveis; "Locked, sequential assessments: reviewers commit positions before seeing alternatives"; ranking feito por código, não por mais um voto de agente; "Shadow book" simula fills e custos sem broker; "Wait" é resultado válido; 6 conectores (Tiger, Alpaca, IBKR, Futu condicionais); "Still unproven: sustained out-of-sample Alpha, live-account order acceptance"; 916 testes a passar (set. 2026) mas sem ordens reais submetidas — [README](https://raw.githubusercontent.com/kyky2347/ALTA/main/README.md)
- astra-quant-agent: "seats" especializados (macro, momentum técnico, microestrutura, risco contrarian); modos de consenso "Paranoid Veto" (qualquer anomalia → WAIT), weighted majority voting, alpha momentum weighting; ">90% of context consists of static doctrine and risk rules" com prompt caching; só OKX; licença AGPL-3.0 + Commons Clause — [README](https://raw.githubusercontent.com/0xethanq/astra-quant-agent/main/README.md)
- AlpacaTradingAgent: cinco analistas em paralelo (Market, Social Sentiment, News, Fundamental, Macro via FRED), "Automated Trading Scheduler" em horário de mercado, "Position Sizing & Execution Controls", logging persistente, resume por checkpoint, "structured output fallback mechanisms"; sem métricas de desempenho publicadas — [README](https://raw.githubusercontent.com/huygiatrng/AlpacaTradingAgent/main/README.md)
- StockAgent: simulação em 4 fases (Initial, Trading, Post-Trading, Special Events), GPT-3.5 e Gemini, "avoids the test set leakage issue present in existing trading simulation systems"; é framework de simulação, não bot — [README](https://raw.githubusercontent.com/MingyuJ666/Stockagent/main/README.md); publicado em TIST — [GitHub API](https://github.com/MingyuJ666/Stockagent)
- LLM-Trading-Lab: "6-month live trading experiment" com $100 iniciais, micro-caps, "Automated stop-loss", "LLM-driven trade selection under hard constraints", contabilidade diária em CSV, dados yfinance/Stooq, benchmarks S&P 500 e Russell 2000, avaliação em PDF de 40 páginas — [README](https://raw.githubusercontent.com/LuckyOne7777/LLM-Trading-Lab/main/README.md)
- LangAlpha: LangGraph + ReAct, subagentes paralelos com contexto isolado, execução de código Python em sandboxes Daytona em vez de despejar dados no contexto ("PTC"); "Does NOT execute trades"; sem menção a Ollama — [README](https://raw.githubusercontent.com/ginlix-ai/LangAlpha/main/README.md)
- Trading-R1: a página só diz "Trading-R1 Terminal is Releasing soon" — [README](https://raw.githubusercontent.com/TauricResearch/Trading-R1/main/README.md)
- QuantAgent/QuantHarness (paper 2509.09995, Stony Brook/CMU/UBC/Yale/Fudan): quatro agentes (IndicatorAgent, PatternAgent, TrendAgent, RiskAgent) que operam "solely on price-derived market signals" (OHLC + indicadores), construído em LangGraph, avaliado zero-shot em dez instrumentos incl. Bitcoin e futuros Nasdaq em intervalos de 4 horas — [pesquisa web, arXiv 2509.09995](https://arxiv.org/pdf/2509.09995), [repo](https://github.com/Y-Research-SBU/QuantAgent)
- FreqAI (freqtrade) é descrito como ferramenta para treinar modelos de ML (classificadores, regressores, redes neuronais) com "self-adaptive retraining" em live; a pesquisa não encontrou integração oficial de Ollama/LLM — [GitHub topic freqai](https://www.Github.com/topics/freqai) (fonte fraca; ver Gaps)

### Inferences
- Para o objetivo "sobrevivência", os repositórios com mais a ensinar são ALTA e astra-quant-agent (separação dura entre proposta do LLM e veto/limites em código) e o DXRG (dados empíricos de produção), mais do que o TradingAgents em si, cuja contribuição é o padrão organizacional (debate + risco + PM + reflexão).
- FinMem está arquivado na prática (último push 2024-08-18), mas o desenho da memória em camadas continua a ser a referência citada por todos os outros; vale mais reimplementar a ideia em SQLite do que importar o código.
- Vibe-Trading e LangAlpha são "agentes de research com ferramentas" (tipo Claude Code) e não bots de sinal por barra; a parte útil para o bot é a lista de ferramentas/MCP e os pre-trade gates, não o loop de agente.
- Jesse, freqtrade e nautilus_trader são infraestrutura clássica (sem LLM); relevantes apenas como referência de como estruturar backtest/execução, não como fonte de componentes LLM.

### Gaps
- Licenças de freqtrade, jesse, nautilus_trader e StockAgent não foram recolhidas nesta pesquisa (as chamadas em modo "minimal" omitem o campo); o campo de licença do astra-quant-agent na API aparece como NOASSERTION mas o README declara AGPL-3.0 + Commons Clause.
- Não consegui ler os READMEs do FinGPT nem do TradingAgents-CN; o conteúdo do FinGPT (modelos fine-tuned para sentimento) não foi verificado nesta sessão.
- Stars do Y-Research-SBU/QuantAgent não recolhidas.

---

## Q2. Que componentes concretos têm estes projetos que um bot Ollama de modelo único não tem?

### Takeaway
Os mecanismos recorrentes são: (1) debate estruturado bull/bear com rondas configuráveis; (2) agente/camada de risco com poder de veto, de preferência em código determinístico e não em prompt; (3) memória com "settlement" — guardar a decisão, ir buscar o retorno realizado N dias depois, calcular alpha vs. benchmark e gerar uma reflexão curta que é reinjetada; (4) saída estruturada com schema + fallback e um estado sentinela "REVIEW/WAIT" em vez de degradar silenciosamente para HOLD; (5) ensembles/votação entre modelos ou entre estratégias por timeframe; (6) dois escalões de modelo (rápido para análise, lento para decisão final).

### Cited Findings

**Debate e hierarquia de agentes**
- TradingAgents: Analyst Team em paralelo (Fundamentals, Sentiment, News, Technical com MACD/RSI); Researcher Team com "bullish and bearish researchers conduct structured debates"; Trader Agent decide timing e magnitude; Risk Management team avalia volatilidade/liquidez e passa ao Portfolio Manager para aprovação final — [README](https://raw.githubusercontent.com/TauricResearch/TradingAgents/main/README.md)
- Defaults: `"max_debate_rounds": 1`, `"max_risk_discuss_rounds": 1`, `"max_recur_limit": 100`; data vendors `core_stock_apis: yfinance`, `fundamental_data: sec_edgar,yfinance`, `macro_data: fred`, `prediction_markets: polymarket` — [default_config.py](https://raw.githubusercontent.com/TauricResearch/TradingAgents/main/tradingagents/default_config.py)
- Dois escalões de modelo: `quick_think_llm` para analistas, researchers, debaters e trader; `deep_think_llm` para research manager e portfolio manager; cada escalão pode usar provider diferente (env `TRADINGAGENTS_DEEP_THINK_PROVIDER` / `TRADINGAGENTS_QUICK_THINK_PROVIDER`) — [README](https://raw.githubusercontent.com/TauricResearch/TradingAgents/main/README.md)
- Diretório `risk_mgmt/` separado de `managers/` (research_manager, risk_manager) no código — [tree](https://github.com/TauricResearch/TradingAgents/tree/main/tradingagents/agents)
- FinCon (NeurIPS 2024, arXiv 2407.06567): hierarquia manager-analyst; "risk-control component ... episodically initiating a self-critiquing mechanism to update systematic investment beliefs", com as crenças conceptualizadas a servir de "verbal reinforcement" propagado seletivamente só ao nó que precisa — [pesquisa web, NeurIPS proceedings](https://proceedings.neurips.cc/paper/2024/hash/f7ae4fe91d96f50abc2211f09b6a7e49-Abstract-Conference.html)
- ALTA: avaliações "locked, sequential" (cada revisor compromete-se antes de ver os outros), moderador que só expõe desacordos a partir de registos guardados, ranking comparativo feito em software e não por voto adicional de agente — [README](https://raw.githubusercontent.com/kyky2347/ALTA/main/README.md)

**Veto de risco em código (não em prompt)**
- astra-quant-agent: "Python code maintains absolute veto authority over LLM proposals". Limites: máx. 6 posições concorrentes; máx. 4 na mesma direção; 30% do equity por ativo; circuit breaker diário = min(150 USDT, 5% do equity); alavancagem 2.0x–5.0x; reward-to-risk mínimo ≥ 2.0; timeout de posição 4 h; stop-loss 100% do lado da exchange ("cloud-side"); brackets OCO nativos — [README](https://raw.githubusercontent.com/0xethanq/astra-quant-agent/main/README.md)
- astra-quant-agent "Paranoid Veto": qualquer anomalia num dos seats rebaixa a decisão para WAIT — [README](https://raw.githubusercontent.com/0xethanq/astra-quant-agent/main/README.md)
- ALTA: execução só com autorização explícita (verificar conta → escolher destino → ligar provider + revisão de conta); apenas long USD stocks/ETFs, ações inteiras, ordens DAY limit; "One active plan per account"; aviso de que parar o ALTA não fecha posições e que as saídas são geridas por software, não por ordens protetoras nativas do broker — [README](https://raw.githubusercontent.com/kyky2347/ALTA/main/README.md)
- Vibe-Trading: "pre-trade gates", "position limits, exposure caps" — [README](https://raw.githubusercontent.com/HKUDS/Vibe-Trading/main/README.md)
- ai-hedge-fund: módulo `risk/` ("position and exposure limits") separado de `portfolio/` ("view blending and target weights") e de `signals/` (alpha models e personas) — [tree](https://github.com/virattt/ai-hedge-fund/tree/main/hedge_fund)

**Memória, settlement e reflexão**
- TradingAgents: cada corrida acrescenta a decisão a `~/.tradingagents/memory/trading_memory.md`; em corridas seguintes o framework "settles" decisões anteriores — vai buscar retornos realizados (brutos e alpha vs. benchmark regional) e gera reflexões de um parágrafo; o Portfolio Manager recebe lições recentes do mesmo ticker e insights cross-ticker — [README](https://raw.githubusercontent.com/TauricResearch/TradingAgents/main/README.md)
- settlement.py: janela de holding por defeito 5 dias de negociação; closes do Yahoo Finance; se faltarem preços adia para a próxima corrida; alpha = retorno bruto − retorno do benchmark; benchmark por sufixo de bolsa, com SPY por defeito para tickers US; o log fica com raw return, alpha, holding days, texto de reflexão e data de resolução; o ficheiro de memória é bloqueado durante a passagem para evitar duplicados — [settlement.py](https://raw.githubusercontent.com/TauricResearch/TradingAgents/main/tradingagents/memory/settlement.py)
- reflection.py: `reflect_on_final_decision` recebe decisão final, retorno bruto, alpha vs. benchmark, holding period e nome do benchmark; pede "exactly 2-4 sentences" cobrindo (a) se o alpha valida a tese direcional, admitindo se a janela é curta, (b) que partes da tese o resultado apoia/contradiz, (c) "a single actionable lesson"; prompt diz "Your output will be stored verbatim in a memory log and re-read by future analysts, so every word must earn its place" — [reflection.py](https://raw.githubusercontent.com/TauricResearch/TradingAgents/main/tradingagents/memory/reflection.py)
- FinMem: memória em camadas (working memory + short/mid/long-term) com "importance scoring, recency decay, and access counters" e promoção de informação crítica para camadas superiores; módulo de perfil com "Risk Preference" configurável e "Self-Adaptive Risk" (ajuste dinâmico durante o trading); fases distintas train (povoar memória) e test (decidir); checkpoints — [README](https://raw.githubusercontent.com/pipiku915/FinMem-LLM-StockTrading/main/README.md)
- FinAgent (arXiv 2402.18485): combina texto, números e gráficos; "uses memory and reflection to learn from mistakes"; incorpora conhecimento de especialistas e ferramentas de trading — [pesquisa web, llmquant.substack](https://llmquant.substack.com/p/finagent-a-new-era-of-ai-powered)
- QuantAgent (IDEA, arXiv 2402.03755): loop de duas camadas — interior refina respostas a partir de uma knowledge base; exterior testa em cenários reais e enriquece automaticamente a knowledge base — [pesquisa web, arXiv 2402.03755](https://www.arxiv.org/abs/2402.03755)
- LangAlpha: ficheiro `agent.md` de notas que sobrevive entre sessões dentro do workspace do sandbox — [README](https://raw.githubusercontent.com/ginlix-ai/LangAlpha/main/README.md)

**Saída estruturada e sentinelas**
- TradingAgents `structured.py`: schemas Pydantic ligados com `with_structured_output(schema)` ("binds exactly one tool (the schema itself)"); `bind_structured()` apanha a exceção quando o provider (ex.: Ollama) não suporta e avisa "falling back to free-text generation"; `invoke_structured_or_freetext()` tenta estruturado e, em falha (JSON malformado, erro transitório, resposta vazia), "retrying once as free text"; agentes só-schema recebem "Use only the evidence provided in this prompt. Do not call external tools or search the web." — [structured.py](https://raw.githubusercontent.com/TauricResearch/TradingAgents/main/tradingagents/agents/structured.py)
- TradingAgents `rating.py`: escala de 5 níveis Buy / Overweight / Hold / Underweight / Sell partilhada por Research Manager, Portfolio Manager e log de memória; `parse_rating()` devolve o sentinela `REVIEW` ("a non-tradeable sentinel") quando não há rating reconhecível, para forçar "human/re-run rather than silently degrading to Hold" — [rating.py](https://raw.githubusercontent.com/TauricResearch/TradingAgents/main/tradingagents/agents/rating.py)
- TradingAgents: output final inclui rating de 5 níveis, direção e magnitude, rationale, risk assessment, impacto no portfólio; relatórios em HTML e markdown — [README](https://raw.githubusercontent.com/TauricResearch/TradingAgents/main/README.md)
- AlpacaTradingAgent: "structured output fallback mechanisms" e resume por checkpoint — [README](https://raw.githubusercontent.com/huygiatrng/AlpacaTradingAgent/main/README.md)

**Ensembles, votação e multi-timeframe**
- astra-quant-agent: seats independentes por perspetiva (macro, momentum técnico, microestrutura, contrarian) "to reduce single-model bias"; consenso por "weighted majority voting" ou "alpha momentum weighting" — [README](https://raw.githubusercontent.com/0xethanq/astra-quant-agent/main/README.md)
- ai-hedge-fund-crypto: timeframes configuráveis ("5m, 15m, 30m, 1h, 4h, 1d"), um nó de processamento por timeframe; estratégias (MACD, RSI, Bollinger) carregadas dinamicamente, cada uma emite sinal com nível de confiança; "weighted aggregation of multiple technical strategies (trend following, mean reversion, momentum, volatility, and statistical arbitrage)"; Risk Management avalia "position limits and exposure" e Portfolio Management decide com LLM sobre os sinais agregados — [README](https://raw.githubusercontent.com/51bitquant/ai-hedge-fund-crypto/main/README.md)
- "Can Blindfolded LLMs Still Trade?" (ICLR 2026, arXiv 2603.17692): quatro agentes (Momentum, News-Event, Mean-Reversion, Risk-Regime) avaliam independentemente e produzem raciocínio — [pesquisa web, iclr.cc](https://iclr.cc/virtual/2026/10016478)
- QuantHarness/QuantAgent (2509.09995): IndicatorAgent, PatternAgent, TrendAgent, RiskAgent só com OHLC — [pesquisa web](https://arxiv.org/pdf/2509.09995)

**Ferramentas de dados**
- TradingAgents: yfinance (point-in-time), SEC EDGAR (fundamentais US com filed-date), FRED (macro), StockTwits/Reddit (sentimento), Yahoo search para notícias, Polymarket, Alpha Vantage, insider trades — [README](https://raw.githubusercontent.com/TauricResearch/TradingAgents/main/README.md)
- Vibe-Trading: 74 ferramentas MCP (market data, backtesting, análise de portfólio, execução) — [README](https://raw.githubusercontent.com/HKUDS/Vibe-Trading/main/README.md)
- LangAlpha: fallback em três níveis de fornecedor de dados (ginlix-data WebSocket → FMP → Yahoo Finance gratuito) — [README](https://raw.githubusercontent.com/ginlix-ai/LangAlpha/main/README.md)

### Inferences (mapeamento para o bot Ollama / ib_async)
- **Settlement + reflexão (TradingAgents)** é o componente mais barato e mais alinhado com a "retrospectiva diária" já existente: basta uma tabela SQLite `decisions(ts, symbol, action, confidence, rationale, entry_px)` e um job que, N barras/dias depois, preenche `realized_ret`, `alpha_vs_SPY` e pede ao LLM 2–4 frases de lição. Substitui "reescrever lições" por lições ancoradas em P&L realizado, não em impressões.
- **Veto determinístico (astra-quant-agent / ALTA)**: portar como camada Python antes de `placeOrder`: máx. trades/dia, máx. posições, perda diária máxima (circuit breaker), R:R mínimo coerente com o bracket (o bracket 2%/5% já dá R:R 2,5), cooldown após stop, e um estado WAIT/REVIEW quando o JSON do LLM é inválido ou a confiança é baixa. Zero custo de inferência.
- **Debate bull/bear**: num bot de 1 minuto, 2–3 chamadas extra por decisão com Llama3/Qwen2.5 local é pesado; alternativa portável é fazer o debate só em "confluência" (RSI+SMA/EMA alinhados) ou numa cadência mais lenta (5–15 min), e usar `max_debate_rounds=1` como o próprio TradingAgents faz por defeito.
- **Dois escalões de modelo**: mapeável para Ollama como modelo pequeno (ex.: Qwen2.5 7B) para triagem por barra e modelo maior/raciocínio só quando a triagem passa — o padrão `quick_think_llm`/`deep_think_llm`.
- **Ensemble**: com Ollama é viável correr o mesmo prompt em 2–3 modelos (Llama3, Qwen2.5, Mistral) e exigir maioria; o DXRG (Q3) mostra que a consistência entre corridas varia muito entre modelos, o que justifica votar.
- **Memória FinMem** pode ser reduzida a: cada lição/evento com `importance` (0–1 atribuída pelo LLM ou pelo |P&L|), `created_at`, `access_count`; score de recuperação = f(recência, importância, acessos); top-k injetado no prompt. Não precisa de embeddings da OpenAI.
- **Sentinela REVIEW** (rating.py) é diretamente aplicável: nunca converter parse falhado em HOLD silencioso; registar e não negociar.

### Gaps
- Não consegui ler `risk_manager.py` nem os debaters (`risk_mgmt/aggresive_debator.py` etc.) do TradingAgents (caminhos mudaram; 404), logo o prompt exato do veto de risco não está citado.
- Não consegui ler `config.toml` do FinMem (404 no caminho tentado) nem o paper (bloqueado), por isso a fórmula exata do score de memória e as taxas de decaimento por camada não estão confirmadas.
- ai-hedge-fund: a lógica numérica de limites de posição (`hedge_fund/risk/`) não foi lida; o README atual não a descreve.
- Não encontrei nenhum projeto que implemente explicitamente "position sizing by confidence" com fórmula publicada; o mais próximo é a "Trade direction and magnitude" do TradingAgents e os limites fixos do astra-quant-agent.

---

## Q3. O que reportam os papers/READMEs sobre desempenho e sobre modos de falha?

### Takeaway
Os números-título são altos (TradingAgents: ≥23,21% cumulativo e Sharpe ≥5,60 em 3 ações; FinMem: 61,78% em TSLA com Sharpe 2,68) mas há críticas publicadas e replicações em 2026 que apontam baselines errados, janelas curtas, ausência de custos de transação e contaminação temporal; o único estudo de produção em escala (DXRG) encontrou ausência de edge direcional, alavancagem cega à volatilidade e saídas mecânicas simples a bater a discrição do LLM.

### Cited Findings

**Resultados reportados**
- TradingAgents: "at least a 23.21% cumulative return and a 24.90% annual return on the three sampled stocks" (AAPL, GOOGL, AMZN), "surpassing the best-performing baselines by a margin of 6.1%"; Sharpe "5.60 or higher", superando o segundo melhor em ≥2,07; baselines Buy&Hold, MACD, KDJ&RSI, ZMR, SMA — [pesquisa web, arXiv 2412.20138](https://arxiv.org/pdf/2412.20138)
- FinMem (backbone GPT-4-Turbo): TSLA retorno cumulativo 61,7758% e Sharpe 2,6789 vs. Buy&Hold −18,6312% / Sharpe −0,5410; cinco ações (TSLA, NFLX, AMZN, MSFT, COIN), teste out. 2022–abr. 2023; TSLA e NFLX com Sharpe >2 e retorno >35%; superou DRL (A2C, PPO) e outros LLMs — [pesquisa web, arXiv 2311.13743](https://arxiv.org/pdf/2311.13743); aceite em AAAI Spring Symposium — [ojs.aaai.org](https://ojs.aaai.org/index.php/AAAI-SS/article/view/31290)
- FinAgent: "significantly outperforms 12 state-of-the-art baselines in terms of 6 financial metrics with over 36% average improvement on profit"; 92,27% de retorno num dataset — [pesquisa web, llmquant.substack](https://llmquant.substack.com/p/finagent-a-new-era-of-ai-powered)
- FinCon: "substantially higher cumulative returns and Sharpe Ratios, alongside lower maximum drawdowns" vs. DRL e outros LLM, em single-asset e portfólio — [NeurIPS 2024](https://proceedings.neurips.cc/paper/2024/hash/f7ae4fe91d96f50abc2211f09b6a7e49-Abstract-Conference.html)
- Blindfolded LLMs (2603.17692): Sharpe 1,40 ± 0,22 em 20 seeds, 2025 YTD até 2025-08-01; em 2024–2025 mostra dependência de regime ("excelling in volatile conditions but showing reduced alpha in trending bull markets") — [pesquisa web, iclr.cc](https://iclr.cc/virtual/2026/10016478)
- QuantHarness: "superior performance in both predictive accuracy and cumulative return over 4-hour trading intervals" em 10 instrumentos, zero-shot — [pesquisa web](https://arxiv.org/pdf/2509.09995)

**Críticas e replicações**
- Crítica ao TradingAgents (dev.to, "The most-starred LLM trading paper claims buy-and-hold lost 5.23%; it actually gained 9.12%"): o paper reporta 26,62% em AAPL vs. Buy&Hold −5,23% no período 2024-06-19 a 2024-11-19; recomputado com closes ajustados a dividendos do Yahoo, AAPL subiu ≈9,12% (erro de ~14 pontos percentuais e de sinal); Sharpe 8,21 em ~105 dias com max drawdown 0,91% "suggests an evaluation artifact rather than genuine alpha"; o repositório tem issue reportado de look-ahead bias; custos de transação não modelados — [pesquisa web, dev.to](https://dev.to/trow126/the-most-starred-llm-trading-paper-claims-buy-and-hold-lost-523-it-actually-gained-912-1jj6) (página bloqueada; números vêm do snippet de pesquisa)
- O próprio README do TradingAgents: "designed for research purposes... not intended as financial, investment, or trading advice"; não-determinismo por amostragem (modelos de raciocínio variam mais); fontes live (notícias, social) refletem "now" independentemente da data de análise; resultados "not guaranteed to match published performance figures" — [README](https://raw.githubusercontent.com/TauricResearch/TradingAgents/main/README.md)
- "The Alpha Illusion: Reported Alpha from LLM Trading Agents Should Not Be Treated as Deployment Evidence" (arXiv 2605.16895, 16 mai. 2026, Yuxuan Ye, Jun Han, Ao Hu; Fudan, SUFE, SWUFE, Northeastern, Imperial, Peng Cheng Lab): audita FinCon, FinMem, TradingAgents, FinAgent, QuantAgent e FLAG-Trader; três causas — "temporal contamination, unmodeled real-world friction, and statistical uncertainty stemming from short evaluation windows"; replicação mostra que retornos "gross" se tornam perdas "net" ou subdesempenho vs. buy-and-hold quando se aplicam custos reais — [pesquisa web, alphaxiv](https://www.alphaxiv.org/abs/2605.16895) (página bloqueada; resumo via snippet)
- Blindfolded LLMs: o problema que ataca é "memorization bias from ticker-specific pre-training, and survivorship bias from flawed backtesting"; anonimização "AAPL" → "STOCK 0026"; ataque de desanonimização com 10 LLMs de fronteira em 200 probes recuperou no máximo 10,2% de top-5 do ticker e 1,5% de sucesso conjunto — [pesquisa web, iclr.cc](https://iclr.cc/virtual/2026/10016478)
- Contaminação temporal em LLMs é mais subtil do que look-ahead clássico: os pesos podem ter absorvido notícias e revisões de mercado posteriores aos eventos — [pesquisa web, substack](https://nazymaltbridge.substack.com/p/your-ai-backtesting-can-cheat-here)
- LLMs exibem enviesamentos humanos — otimismo, overconfidence, extrapolação, framing — apesar de "saberem" os conceitos de finanças comportamentais (Chen et al., 2025, "ChatGPT's Stock Return Biases") — [pesquisa web, Princeton BCF PDF](https://bcf.princeton.edu/wp-content/uploads/2026/01/2026.01.08-MA-Clifton-Green.pdf)
- Em mercados experimentais, agentes LLM mostraram comportamento "textbook-rational", preço perto do valor fundamental e tendência "muted" para bolhas (arXiv 2502.15800) — [pesquisa web, arXiv](https://arxiv.org/pdf/2502.15800v3)

**Comportamento em produção (DXRG, "What LLM Trading Agents Actually Do in Production")**
- Setup: 3 505 vaults financiados por utilizadores em Base (fev.–mar. 2026) e 500–599 agentes em perpétuos Hyperliquid (jun.–ago. 2026) → 231 638 turnos finalizados e 14 596 trades a preços live; modelos Qwen3-235B via SGLang e qwen3.7-plus via OpenRouter — [README](https://raw.githubusercontent.com/ProjectDXAI/continuous-record-llm-trading-agents/main/README.md)
- "Operating layer dominance": um leaderboard de 9 linhas renderizado no prompt explicou 46,5% das entradas, com descontinuidade na fronteira de renderização (efeito 1,75x) — [idem](https://raw.githubusercontent.com/ProjectDXAI/continuous-record-llm-trading-agents/main/README.md)
- "Volatility-blind positioning": alavancagem mediana constante de 5,0x em todos os sextis de volatilidade, com retornos realizados a degradar-se 9x no mesmo intervalo — [idem](https://raw.githubusercontent.com/ProjectDXAI/continuous-record-llm-trading-agents/main/README.md)
- "Profit capture failure": 43,2% das posições atingiram +300 bp de excursão favorável em 24 h, mas 49,3% fecharam em perda; captura mediana 2,0% do potencial; "simple 2%/4% bracket exits outperforming discretionary strategies by 39 basis points per position" — [idem](https://raw.githubusercontent.com/ProjectDXAI/continuous-record-llm-trading-agents/main/README.md)
- Uma configuração de alta alavancagem com 11% das posições gerou 62% das liquidações (OR 22,37); sem edge direcional ao nível da frota; qualidade de decisão comparável entre modelos de fronteira mas consistência muito díspar (taxas de "flip" de 35% vs. 90%+ em cenários idênticos); recomendações: controlos robustos de sizing, regras mecânicas simples de saída, avaliar defaults da camada operacional antes de dar mais autonomia — [idem](https://raw.githubusercontent.com/ProjectDXAI/continuous-record-llm-trading-agents/main/README.md)

**Outros avisos**
- ALTA: "not investment advice, a production OMS or evidence of profitable Alpha"; nenhum pedido de alpha out-of-sample sustentado — [README](https://raw.githubusercontent.com/kyky2347/ALTA/main/README.md)
- ai-hedge-fund: "Not intended for real trading or investment" — [README](https://raw.githubusercontent.com/virattt/ai-hedge-fund/main/README.md)
- astra-quant-agent: "Historical backtests do not guarantee future performance"; exige validação extensa em paper antes de capital real — [README](https://raw.githubusercontent.com/0xethanq/astra-quant-agent/main/README.md)
- Vibe-Trading: nenhuma alegação de desempenho; reporta métricas pós-execução (heatmaps mensais, rolling Sharpe, trade logs) — [README](https://raw.githubusercontent.com/HKUDS/Vibe-Trading/main/README.md)

### Inferences
- A evidência empírica mais relevante para um bot intradiário com brackets fixos é o DXRG: saídas mecânicas (bracket 2%/4%) venceram a discrição do LLM, e o LLM não ajusta exposição à volatilidade. Para o bot, isto sugere manter o bracket em código (já existe) e acrescentar sizing por volatilidade (ATR) em código, não deixar o LLM decidir tamanho.
- O achado de que a UI/prompt layout explica 46,5% das entradas implica que a forma como o bot apresenta indicadores no prompt (ordem, destaque, listas) pode enviesar sistematicamente BUY/SELL; vale testar permutações do prompt e medir a taxa de flip no mesmo cenário.
- Dado o padrão "gross alpha → net loss" (Alpha Illusion) e o Sharpe 8,21 com DD 0,91% como artefacto, qualquer retrospectiva interna do bot deve medir P&L líquido de comissões IBKR e slippage, com intervalo de confiança, e nunca em janelas de dias.
- A contaminação temporal/memorização é menos grave para um bot que só vê barras de 1 min de hoje do que para backtests históricos com tickers nomeados; mas se se fizer backtest do prompt com dados de 2023–2024 em Llama3/Qwen2.5, convém anonimizar o ticker como no paper Blindfolded.

### Gaps
- Não consegui aceder ao texto integral dos papers TradingAgents, FinMem, FinCon, FinAgent, Alpha Illusion e Blindfolded (domínios bloqueados); os números vêm de snippets de pesquisa e podem omitir contexto (ex.: custos assumidos, períodos exatos por ação).
- Não encontrei o PDF de 40 páginas do LLM-Trading-Lab nem os seus resultados numéricos vs. Russell 2000.
- Não encontrei replicações independentes de FinMem ou FinCon com modelos locais (Llama/Qwen) com números.

---

## Q4. Quais correm com Ollama / endpoints OpenAI-compatíveis locais?

### Takeaway
Os principais frameworks ativos (TradingAgents, AlpacaTradingAgent, Vibe-Trading, ALTA, FinRobot, ai-hedge-fund-crypto) suportam Ollama ou um `base_url` OpenAI-compatível, mas com aviso explícito no TradingAgents de que a saída estruturada cai para texto livre com Ollama; FinMem precisa de embeddings OpenAI mesmo com LLM open-source via TGI; o ai-hedge-fund atual, LangAlpha e astra-quant-agent não documentam Ollama.

### Cited Findings
- TradingAgents: "Ollama for local models; any OpenAI-compatible endpoint via `backend_url` parameter (vLLM, LM Studio, llama.cpp)", além de OpenAI, Google, Anthropic, xAI, DeepSeek, Qwen, GLM, MiniMax, Mistral, Kimi, Groq, NVIDIA NIM, Bedrock, Azure, OpenRouter — [README](https://raw.githubusercontent.com/TauricResearch/TradingAgents/main/README.md); `"backend_url": None` por defeito — [default_config.py](https://raw.githubusercontent.com/TauricResearch/TradingAgents/main/tradingagents/default_config.py)
- TradingAgents com Ollama: `bind_structured()` apanha a exceção de `with_structured_output` e faz "falling back to free-text generation" — [structured.py](https://raw.githubusercontent.com/TauricResearch/TradingAgents/main/tradingagents/agents/structured.py)
- AlpacaTradingAgent: "Supports OpenAI, local OpenAI-compatible endpoints, Google Gemini, Anthropic Claude, xAI, MiniMax, DeepSeek, Qwen, GLM, OpenRouter, Ollama, and Azure"; local via `OPENAI_USE_LOCAL` e `OPENAI_BASE_URL` — [README](https://raw.githubusercontent.com/huygiatrng/AlpacaTradingAgent/main/README.md)
- Vibe-Trading: OpenAI, Anthropic, OpenRouter (default), "Ollama and OpenAI-compatible endpoints", GitHub Copilot, Novita AI — [README](https://raw.githubusercontent.com/HKUDS/Vibe-Trading/main/README.md)
- ALTA: OpenAI, Anthropic, Ollama e endpoints OpenAI-compatíveis, com atribuição de modelo por papel (role) — [README](https://raw.githubusercontent.com/kyky2347/ALTA/main/README.md)
- FinRobot: "OpenAI-compatible endpoints; local models supported" — [README](https://raw.githubusercontent.com/AI4Finance-Foundation/FinRobot/master/README.md)
- ai-hedge-fund-crypto: "OpenAI, Groq, OpenRouter, Gemini, Anthropic, and Ollama" — [README](https://raw.githubusercontent.com/51bitquant/ai-hedge-fund-crypto/main/README.md)
- FinMem: GPT-4 via API ou modelos HuggingFace via Text Generation Inference (`model = "tgi"`, `end_point = ...`); "Users must set OPENAI_API_KEY for embedding services regardless of the backbone LLM choice"; embedding `text-embedding-ada-002` exclusivamente; Docker Python 3.10 — [README](https://raw.githubusercontent.com/pipiku915/FinMem-LLM-StockTrading/main/README.md), [página do repo](https://github.com/pipiku915/FinMem-LLM-StockTrading)
- ai-hedge-fund (README atual): chaves aceites de "Anthropic, OpenAI, DeepSeek, Google, xAI, Kimi, TypeSafe (Jev)"; "No mention of Ollama or local LLMs appears in the provided README content" — [README](https://raw.githubusercontent.com/virattt/ai-hedge-fund/main/README.md), [página do repo](https://github.com/virattt/ai-hedge-fund)
- astra-quant-agent: seats configuráveis com DeepSeek-V3/R1, Claude 3.5 Sonnet, GPT-4o, Qwen (sem menção a Ollama no resumo do README) — [README](https://raw.githubusercontent.com/0xethanq/astra-quant-agent/main/README.md)
- LangAlpha: "No mention of local/Ollama support" — [README](https://raw.githubusercontent.com/ginlix-ai/LangAlpha/main/README.md)
- StockAgent: GPT-3.5 e Gemini (default) — [README](https://raw.githubusercontent.com/MingyuJ666/Stockagent/main/README.md)
- ALTA pesa: frontend em três línguas, App Server baseado num snapshot do OpenAI Codex (Apache-2.0), 916 testes — [README](https://raw.githubusercontent.com/kyky2347/ALTA/main/README.md); Vibe-Trading exige Python 3.11+, FastAPI, React 19 — [README](https://raw.githubusercontent.com/HKUDS/Vibe-Trading/main/README.md)

### Inferences
- Peso local estimado por chamadas LLM por decisão (inferido da arquitetura descrita): TradingAgents com defaults = 4 analistas + 2 researchers × 1 ronda + research manager + trader + 3 debaters de risco + risk/portfolio manager ≈ 12+ chamadas, várias com contexto longo — impraticável por barra de 1 min num GPU doméstico; viável em cadência de 15–60 min ou só em confluência. ai-hedge-fund-crypto (sinais técnicos em código + 1 chamada LLM final) e astra-quant-agent (N seats em paralelo + risco em código) são os desenhos mais leves.
- Para Ollama, o ponto frágil comum é a saída estruturada; o Ollama suporta `format: json`/JSON schema nativamente, pelo que o bot deve manter validação Pydantic própria com fallback para REVIEW em vez de depender de `with_structured_output` do LangChain.
- Nenhum dos frameworks grandes é "drop-in" para ib_async; o que se porta são padrões (settlement, veto, sentinela, dois escalões, votação), não pacotes.

### Gaps
- Não verifiquei se versões anteriores do ai-hedge-fund tinham flag `--ollama` (o README atual não o menciona); não confirmar sem ler o histórico do repo.
- Não há medições publicadas de latência/throughput destes frameworks com modelos 7B–14B em Ollama.
- Não verifiquei se o Vibe-Trading usa structured output ou tool calling que modelos pequenos Ollama falhem.

---

## Q5. Licenças relevantes para reutilização

### Takeaway
Quase tudo o que interessa é Apache-2.0 ou MIT (TradingAgents, FinRobot, ALTA, AlpacaTradingAgent, LangAlpha: Apache-2.0; ai-hedge-fund, FinMem, FinGPT, FinRL, Vibe-Trading, ai-hedge-fund-crypto, llm-agent-trader: MIT); as exceções são o astra-quant-agent (AGPL-3.0 + Commons Clause — copiar código exige cuidado), TradingAgents-CN e TradingAgents-AShare (licença "Other"/NOASSERTION), e TradingAgents-MCPmode (sem licença).

### Cited Findings
- Apache-2.0: TradingAgents — [GitHub API](https://github.com/TauricResearch/TradingAgents); FinRobot — [GitHub API](https://github.com/AI4Finance-Foundation/FinRobot); ALTA — [README](https://raw.githubusercontent.com/kyky2347/ALTA/main/README.md); AlpacaTradingAgent — [GitHub API](https://github.com/huygiatrng/AlpacaTradingAgent); LangAlpha — [README](https://raw.githubusercontent.com/ginlix-ai/LangAlpha/main/README.md); TradingAgents-astock — [GitHub API](https://github.com/simonlin1212/TradingAgents-astock)
- MIT: ai-hedge-fund — [README](https://raw.githubusercontent.com/virattt/ai-hedge-fund/main/README.md); FinMem — [README](https://raw.githubusercontent.com/pipiku915/FinMem-LLM-StockTrading/main/README.md); FinGPT e FinRL — [GitHub API](https://github.com/AI4Finance-Foundation/FinGPT), [FinRL](https://github.com/AI4Finance-Foundation/FinRL); Vibe-Trading — [README](https://raw.githubusercontent.com/HKUDS/Vibe-Trading/main/README.md); ai-hedge-fund-crypto — [README](https://raw.githubusercontent.com/51bitquant/ai-hedge-fund-crypto/main/README.md); llm-agent-trader — [GitHub API](https://github.com/jason8745/llm-agent-trader)
- MIT-0 (sem atribuição): moss-site/moss-trade-bot-skills (388 stars, push 2026-09-09) — [GitHub API](https://github.com/moss-site/moss-trade-bot-skills)
- AGPL-3.0 + Commons Clause, "prohibiting commercial resale, closed-source packaging, or paid signal services without explicit permission": astra-quant-agent — [README](https://raw.githubusercontent.com/0xethanq/astra-quant-agent/main/README.md)
- Licença não-standard ("Other"/NOASSERTION): TradingAgents-CN — [GitHub API](https://github.com/hsliuping/TradingAgents-CN); TradingAgents-AShare — [GitHub API](https://github.com/KylinMountain/TradingAgents-AShare); ElegantRL — [GitHub API](https://github.com/AI4Finance-Foundation/ElegantRL)
- Sem ficheiro de licença detetado pela API: TradingAgents-MCPmode — [GitHub API](https://github.com/guangxiangdebizi/TradingAgents-MCPmode); LLM-Trading-Lab (README não indica) — [README](https://raw.githubusercontent.com/LuckyOne7777/LLM-Trading-Lab/main/README.md)
- ALTA: operadores "remain responsible for provider terms, exchange entitlements, and data redistribution rights" — [README](https://raw.githubusercontent.com/kyky2347/ALTA/main/README.md)

### Inferences
- Para um bot pessoal/desktop, copiar padrões e até funções do TradingAgents (Apache-2.0: manter NOTICE/atribuição) e do ai-hedge-fund/FinMem (MIT) é seguro; do astra-quant-agent convém portar apenas a *ideia* dos limites (os números são parametrização trivial), não o código, por causa do AGPL + Commons Clause.
- Reutilizar dados: a licença do código não cobre dados de mercado; IBKR/yfinance/SEC têm termos próprios (ALTA lembra-o explicitamente).

### Gaps
- Licenças de freqtrade, jesse, nautilus_trader, StockAgent e Y-Research-SBU/QuantAgent não foram recolhidas nesta sessão.
- Não li os ficheiros LICENSE diretamente; os valores vêm do campo `license.spdx_id` da API GitHub e de READMEs.
