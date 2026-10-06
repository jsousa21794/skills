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

> O modo predefinido é a conta **real** (base de dados própria,
> `trader_live.sqlite3`). Risco por trade até **10%** do equity (limitado pelos
> fundos disponíveis da corretora), **entradas ilimitadas enquanto o dia está
> em lucro** (só a regra PDT e os fundos disponíveis limitam) e **kill-switch a
> -20% no dia, que para o ciclo automaticamente e persiste a reinícios**. Em dia
> de perda aplicam-se o StoplossGuard, o travão de perdas seguidas e o cooldown
> após saída em perda. Tudo ajustável em `config.json` (validado ao carregar).
>
> Versão 1.0.10: resposta à revisão da 1.0.9 (6 achados, 1 P0), ver
> `../reports/Resposta à revisão 1.0.9.md`; antes, as respostas às revisões
> anteriores e à auditoria da 1.0.2 na mesma pasta.

## Invariantes de segurança (1.0.10)

- **Arranque = supervisão**: no primeiro contacto após um período desligado,
  cada ativo gerido com posição passa pela MESMA classificação da supervisão
  periódica (inversão de sinal, quantidade própria zero, redução, excesso,
  lado errado) antes de qualquer reposição de proteção; uma inversão ocorrida
  com o programa desligado cancela as saídas antigas, regista o conflito e não
  coloca nenhuma ordem. A reposição de proteção recusa, por si só, uma posição
  com sinal contrário ao do ledger.
- **Identidade permanente até à alocação**: a distribuição de uma execução de
  saída exclui grupos cujo `permId` conhecido para essa ordem difere do da
  execução (orderId reutilizado entre sessões) e inclui sempre o grupo validado
  no callback.
- **Saldo por alocar é recuperável**: uma execução parcialmente alocada (saída
  recebida antes do resto da entrada) fica na fila de recuperação com o saldo
  `shares − alocado`; o resto da entrada recupera-a automaticamente, de forma
  idempotente; a discrepância só sai quando o total está explicado.
- **Correção tardia atualiza o rótulo**: quando uma execução tardia muda o
  resultado de um trade já encerrado, a decisão associada (se já avaliada)
  recebe o rótulo final do ledger na mesma operação e `labels_changed_at`
  invalida a calibração dependente.
- **Contexto opaco no comando remoto**: `get_status` devolve `context_id`
  (conta completa, modo, geração e base de dados); `pause_new_entries` exige-o
  e recusa qualquer divergência — a máscara da conta é só apresentação e pode
  colidir entre contas.
- **Data de saída = execução comprovada**: um trade reconciliado guarda
  `reconciled_ts` à parte; a correção tardia põe em `exit_ts` a execução mais
  recente que completa a saída (nunca a data da reconciliação), para que
  contadores de stops recentes e relatórios usem a cronologia real.

## Invariantes de segurança herdados (1.0.9)

- **Conflito externo persistente**: uma redução ou inversão fora do bot fica
  gravada na base de dados (`conflict:<ativo>`) e sobrevive a reinícios; quando
  toda a quantidade própria está explicada por ajustes provisórios, o que resta
  na corretora NÃO é do bot: em nenhum ciclo (nem após reinício) se colocam
  stops, TP ou fechos sobre essa posição, as saídas próprias que sobrem são
  canceladas e as decisões no ativo ficam bloqueadas até as execuções
  explicarem a diferença. Titularidade, direção e níveis são revalidados em
  cada passagem do supervisor.
- **A pausa revoga entradas em preparação**: o token de autorização que a
  corretora reavalia imediatamente antes de `placeOrder` inclui a pausa de
  novas entradas (local ou remota) e a discrepância do ativo; uma entrada que
  estava a ser qualificada quando a pausa foi confirmada não sai.
- **Pausa = só novas entradas**: a intenção é classificada antes de aplicar a
  pausa; fechos por sinal contrário e proteções continuam sujeitos apenas às
  suas próprias validações.
- **Execuções pela identidade da ordem**: uma execução de saída só é
  distribuída pelos trades que a ordem cobre (grupo da ordem, pernas
  arquivadas e `exit_coverage` dos fechos por sinal), nunca por trades novos do
  mesmo ativo; a execução tardia de um grupo já reconciliado corrige esse trade
  (quantidade e P&L) sem o reabrir; sem saldo coberto fica por alocar.
- **Ajustes ligados à evidência**: cada ajuste provisório guarda as saídas
  vivas no instante da discrepância; só uma execução dessas ordens o consome.
  Saídas colocadas depois fecham a quantidade viva e nunca explicam a redução
  anterior. Um trade reconciliado com execuções por receber fica assinalado
  (P&L indeterminado) até elas chegarem.
- **Modelo inválido é retirado**: se um reajuste necessário (rótulos
  corrigidos/finalizados ou prazo) falha por falta de amostras finais ou de
  diversidade de classes, o modelo anterior é removido da memória e do
  armazenamento e volta-se à heurística; no arranque, um modelo anterior à
  última correção de rótulos não é carregado.
- **Diagnóstico MCP honesto**: `compare_ledger_with_broker` distingue
  `consistent`, `inconsistent` e `unknown` (desligado ou por reconciliar nunca
  conclui positivamente), inclui ativos que só têm ordens vivas e deteta saídas
  sem posição, excesso, lado errado, execuções por alocar e conflitos.
- **Comando remoto com identidade exata**: a pausa transporta conta completa,
  modo, geração e base de dados validados até ao loop do motor, onde são
  reavaliados imediatamente antes da escrita; uma troca de conta/modo ou um
  reinício entretanto recusa o pedido. A máscara da conta é só apresentação.

## Invariantes de segurança herdados (1.0.8)

- **Reconciliação única**: arranque e supervisão periódica usam a mesma rotina;
  um ativo fica gerido enquanto houver ordens próprias vivas, mesmo sem trade
  aberto; as saídas são canceladas e confirmadas ANTES de fechar o vínculo, e
  sem confirmação o conflito persiste.
- **Entradas ≠ saídas**: uma entrada ainda por executar (e os seus filhos)
  nunca é tratada como posição desaparecida; só o prazo da entrada a cancela.
- **Nenhuma execução inventada**: uma redução ou inversão da posição fora do
  bot gera um ajuste PROVISÓRIO de quantidade (P&L indeterminado), consumido
  pela execução real quando chega, nunca somado duas vezes; a inversão de sinal
  é um conflito explícito e persistente com todas as saídas próprias
  canceladas e sem proteção nova.
- **Identidade durável na deduplicação**: `permId` conhecido e diferente nunca
  é duplicado; coincidência parcial é conflito assinalado, nunca descarte.
- **Só rótulos finais aprendem**: calibração, lições e métricas ignoram
  rótulos provisórios; finalizar um rótulo invalida os modelos anteriores.
- **Saídas agregadas distribuídas**: uma proteção que cobre vários trades fica
  associada a todos e a sua execução é repartida pelos saldos, sem `exit_qty`
  acima de `filled_qty`.

## Invariantes de segurança herdados (1.0.7)

- **Nenhuma saída excede a posição reconciliada**: o supervisor compara a
  posição líquida com a quantidade própria do ledger em ambos os sentidos. Se
  a posição DIMINUIU fora do bot, o ledger regista uma saída `EXTERNAL`, as
  decisões no ativo ficam bloqueadas e todas as saídas do bot são canceladas
  com confirmação terminal e repostas com a quantidade existente; cobertura
  excessiva é um estado distinto de cobertura suficiente. Uma posição que
  desapareceu tem as suas saídas canceladas antes de o trade ser reconciliado.
- **Identidade original dos fills persistida**: conta, `clientId`, `permId`,
  `orderRef` e `conId` ficam gravados em cada execução; o reprocessamento usa
  essa identidade e limita-se à ordem em causa, pelo que uma identidade
  incompatível nunca é convertida em desconhecida.
- **Falha de migração em ficheiro-marcador**: `migration_failed.flag` na pasta
  de dados bloqueia entradas independentemente da base aberta, também quando
  a falha ocorre sem destino prévio; apaga-se depois de resolver.
- **Deduplicação por identidade completa**: (símbolo, conta, contrato, id da
  ordem) e (símbolo, conta, direção, instante e quantidade da entrada); um
  número de ordem sozinho nunca é prova de duplicado.
- **Entradas não terminais restauradas**: uma entrada em `PendingCancel`
  mantém a reserva e o bloqueio de nova entrada até à confirmação terminal.
- **Rótulos provisórios e finais**: uma operação ainda aberta no horizonte
  recebe um rótulo provisório (`label_final=0`); quando fecha, o ledger
  finaliza o rótulo e guarda o provisório à parte. O resultado não depende da
  cadência do avaliador.

## Invariantes de segurança herdados (1.0.6)

- **Ciclo de vida até ao estado terminal**: toda a ordem não terminal
  (`PendingCancel` incluído) conta como executável. Um fecho cancela, espera a
  confirmação terminal, reconcilia de novo todas as saídas do contrato e volta
  a verificar imediatamente antes do envio; qualquer saída viva, manual ou do
  bot, bloqueia a venda. A reparação de cobertura espera pelos stops em
  transição antes de recalcular o residual e nunca coloca cobertura nova por
  cima de um stop ainda não terminado.
- **Identidade completa em todos os callbacks**: estados de ordem só alteram
  histórico, reservas ou fechos se conta, `clientId`, `orderRef` e contrato
  coincidirem e a ordem pertencer a um grupo do bot; o `permId` é gravado por
  grupo e corrigível; execuções registadas sem alocação são reprocessadas
  quando a identidade fica conhecida.
- **Migração para a base efetiva**: a base ≤ 1.0.2 é importada para a base da
  conta já em uso (não só para a base por modo), numa única transação e com
  deduplicação pela identidade das ordens e da entrada; uma falha bloqueia
  entradas até ser resolvida.
- **Validação por experiência persistida**: o estado dos gates é guardado e
  lido pela chave (modelo, versão do prompt); um prompt novo nunca herda a
  validação do anterior.
- **Rótulos pelo ledger**: operações executadas são rotuladas pela saída real
  (TP = 1, stop = 0; outras saídas ficam censuradas com a razão explícita) e,
  enquanto abertas, pelos níveis absolutos da ordem no intervalo em que a
  posição esteve aberta, sem usar a vela da entrada; dentro do horizonte da
  operação espera-se pelo desfecho real.
- **Cobertura própria em posições mistas**: só os stops do próprio bot cobrem
  a parcela própria; um stop manual pertence à parcela manual.

## Invariantes de segurança herdados (1.0.5)

- **Nenhuma saída concorrente**: um fecho por sinal reconcilia primeiro todas
  as ordens de saída do contrato na conta; com um stop ou limit MANUAL ativo o
  fecho é bloqueado e assinalado (`cancel_external_exits_on_close` permite
  cancelá-lo antes de enviar). Posições MISTAS (ações do bot + ações manuais no
  mesmo contrato) só são protegidas e fechadas na quantidade própria, e os
  sinais nesse ativo ficam bloqueados até adoção explícita.
- **Identidade persistida das ordens**: no reinício, TP/SL autónomos são
  reconhecidos pela perna registada em `order_history` e mantidos; só pernas
  PARENT são entradas/fechos pendentes; um par incompleto (stop sem TP) é
  reposto. Execuções só tocam num trade com o `clientId` do bot e, quando
  conhecido, o mesmo `permId`; o cliente 0 (TWS manual) é uma identidade como
  outra qualquer.
- **Proveniência das bases**: `order_history` é preenchida para grupos
  herdados; a base por modo fica atribuída à primeira conta que a copiou e
  nunca é copiada para outra, nem quando contém registos de outra conta; uma
  base ≤ 1.0.2 que coexista com a da 1.0.3 vê as suas proteções ativas e
  trades abertos importados (histórico fechado preservado à parte).
- **Experiência única**: retrospetiva, calibração, relatórios e gates usam o
  calibrador atual e particionam por (modelo, versão do prompt), com a curva
  de equity reconstruída dos trades dessa experiência; gates de outra
  experiência nunca validam o modelo atual; no A/B o estado de validação é o
  do modelo que respondeu. A autorização final reavalia a idade da decisão
  (relógio e prazo monotónico) e a cotação face ao limite depois de cada
  `await`. Os rótulos do bracket usam a entrada real quando a decisão foi
  executada, o fim da inferência caso contrário, e as mesmas regras de
  abertura do replay.

## Invariantes de segurança herdados (1.0.4)

- **Token de autorização até ao `placeOrder`**: a geração e o estado do ciclo
  são reavaliados depois de cada `await` (qualificação, cancelamentos) e
  imediatamente antes de enviar qualquer ordem; Parar durante uma operação em
  curso impede a ordem. Só a reposição de cobertura (redução de risco) continua
  permitida depois de Parar.
- **Fecho serializado por ativo**: o estado `CLOSING` é instalado ANTES de
  cancelar os filhos; fecho e reproteção partilham um lock por ativo;
  cancelamentos intencionais são distinguidos de rejeições; um timeout parcial
  declara a cobertura em falta para ser reposta.
- **Cobertura por quantidade residual**: a reparação cancela filhos órfãos
  (TP sem stop ou vice-versa) e coloca só o que falta cobrir; frações abaixo de
  uma ação ficam assinaladas uma vez, sem ordens repetidas. O filtro de conta é
  obrigatório e independente do filtro de propriedade (`orderRef`).
- **Identidade das execuções**: um fill só toca num trade se conta, contrato
  (`conId`), símbolo e `clientId` coincidirem; os filhos substituídos ficam em
  `order_history`, por isso um fill tardio de um TP antigo fecha o trade certo.
- **Estado restaurado antes de decidir**: ao ligar, as entradas (MKT e LMT) e
  fechos ainda vivos na corretora são reconstruídos; o ciclo de decisão não
  corre até a reconciliação terminar. O supervisor (prazos monotónicos de
  entradas, cobertura) corre numa tarefa própria, independente da inferência.
- **Uma base de dados por conta**: ao conhecer a conta, o bot passa para
  `trader_<modo>_<conta>.sqlite3`; uma base associada a outra conta nunca é
  usada. A base única das versões ≤ 1.0.2 é migrada uma vez (original
  preservado). Posições não abertas pelo bot não são geridas, fechadas nem
  protegidas sem `manage_external_positions`, e bloqueiam sinais nesse ativo.
- **Cotação fresca**: antes de enviar, o último preço é comparado com a
  referência da decisão; além do limite ou com desvio excessivo, não entra.
  Tolerância de slippage zero significa limit AO preço de referência; só
  `entry_order_type="market"` dispensa o limite.
- **Rótulos honestos**: o instante de mercado é o fecho da vela agregada; o
  percurso do settlement começa depois da decisão; sem toque em TP nem stop a
  decisão fica censurada. Calibração, relatórios e gates são por experiência
  (modelo + versão do prompt); o A/B usa o calibrador do modelo que respondeu.

## Invariantes de segurança herdados (1.0.3)

- **Geração de decisões**: parar, mudar de conta, de modelo ou de ativos
  invalida qualquer decisão em curso; nenhuma ordem sai de uma decisão antiga.
- **Revalidação antes de cada ordem**: ligação, conta, fundos, gates e idade da
  decisão são relidos imediatamente antes de `placeOrder`.
- **Fechos seguros**: cancelam-se os filhos, espera-se o estado terminal,
  relê-se a posição e só então se envia a quantidade remanescente; fechos e
  entradas pendentes têm máquina de estados, e entradas não executadas são
  canceladas após `entry_timeout_seconds`.
- **Cobertura verificada a cada ciclo**: stops ativos do lado certo, com
  quantidade suficiente e estado confirmado; se faltar, é reposta e persistida.
- **Isolamento**: só ordens com o `orderRef` do bot, na conta configurada, em
  contratos `STK` identificados por `conId`; posições externas não são tocadas
  (`manage_external_positions=False`).
- **Moeda**: valores de conta na moeda base com conversão explícita para USD
  no dimensionamento; a interface mostra a moeda da conta.
- **Política única**: `trader/policy.py` é usada pelo live e pelo replay.

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
  trading_engine.py          loop asyncio numa thread dedicada; ciclo, supervisão e execução
  policy.py                  política de execução partilhada (live e replay)
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
tests/                       110 testes offline (IBKR e Ollama simulados; 31 regressões da auditoria)
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
  no 5.º percentil (pysystemtrade); risco até 10% do equity, com o notional
  limitado aos fundos disponíveis reportados pela IBKR (`AvailableFunds`).
  Stop = 2×ATR, TP = 2× o stop.
- Protections: kill-switch −20%/dia (para o ciclo; posições mantêm TP/SL),
  MaxDrawdown multi-dia (6% em 5 dias → pausa 1 sessão; 3% → tamanho a
  metade), regra PDT para contas < 25k USD (3 day trades/5 dias). Só em dia
  de perda: StoplossGuard (3 stops/2 h → pausa 1 h), 5 perdas seguidas →
  pausa até ao dia seguinte, cooldown de 30 min após saída em perda. Em dia
  de lucro as entradas são ilimitadas.
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

- **`data/seed_lessons.json`**: 55 lições iniciais em três camadas, todas
  com o campo `source`: (1) 23 derivadas da literatura e de experiências de
  LLMs ao vivo, com citação; (2) 32 de conhecimento geral de trading e
  microestrutura escritas pelo assistente (hora do dia, eventos macro,
  volatilidade, correlação com o índice, stops não garantidos, spread, SSR e
  halts, regimes de RSI, VWAP, volume, processo), marcadas como não medidas.
  As lições com chave `hora:`, `rsi:` ou `regime:` só entram no prompt quando
  o contexto bate certo. Importadas uma vez no arranque (`seed_lessons_file`),
  têm importância baixa e são ultrapassadas pelas lições medidas nas tuas
  decisões assim que houver suporte estatístico.
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
  repete o valor para ficar visível na barra de tarefas.
- **Barra lateral** só com controlo (Iniciar/Parar), estado da ligação e
  análise (retrospetiva, relatório estatístico, atalho para Configurações).
- **Separador Configurações**: TODAS as opções do programa vivem aqui, por
  secções: *Conta e modelo* (Paper/Real com confirmação, modelo Ollama com
  lista de `/api/tags`, ativos; aplicação imediata), *Ligação à IBKR* (host,
  portas, client ID, conta, adoção de posições externas), *Ollama* (URL,
  cadência, amostras, acordo mínimo, duas etapas, anonimização), *Trading e
  risco* (posições, risco por operação, kill-switch diário, entradas
  ilimitadas em lucro, short, slippage, stop ATR, R:R, persistência do sinal),
  *Custos* (comissões e gate de custos) e *Apresentação* (**moeda em que os
  valores são mostrados**, dados de mercado, janela sempre visível).
  "Guardar e aplicar" valida com as mesmas regras do `config.json` (valores
  inválidos são rejeitados e assinalados), grava e aplica no motor; alterações
  de ligação reiniciam a ligação à IBKR.
- **Moeda de apresentação**: "Conta (auto)" mostra a moeda base da conta; uma
  moeda explícita (EUR, GBP, …) converte todos os montantes de conta com a
  taxa `ExchangeRate` publicada pela própria IBKR. Sem taxa disponível, a
  interface diz que está a mostrar a moeda da conta. Preços e quantidades das
  posições ficam sempre na moeda do ativo.
- **Conta**: o modo predefinido é **Real** (porta 7496, TWS). Na primeira
  vez que carregas em *Iniciar trading* é pedida uma confirmação única
  (escrever `REAL`), que fica guardada no `config.json` (`live_confirmed`).
  O seletor *Paper* / *Real* em Configurações permite mudar para paper (7497)
  se a tiveres. Ao ligar, o bot confirma o tipo de conta pelo identificador
  (`DU…` = paper): em modo paper com conta real desliga-se por segurança; em
  modo real com conta paper avisa. A camada de risco, o kill-switch e os
  brackets funcionam igual em ambos os modos.
- **Outros separadores**: *Visão geral* (cartões, gráfico do valor da carteira
  nas últimas 48 h, posições com stop e take-profit), *Decisões* (cada proposta
  do LLM com confiança verbal, acordo, probabilidade calibrada e o veredicto da
  camada de risco), *Risco* (proteções ativas, reconciliação, posições externas
  não geridas, base de dados da conta, gates, calibração, lições) e *Consola*.

## Integração com o ChatGPT (MCP): supervisão técnica

O programa inclui um servidor MCP (Model Context Protocol) local, desligado
por defeito, que expõe ao ChatGPT um conjunto FECHADO de ferramentas: consultar
estado, posições (ledger vs IBKR), ordens vivas, log, decisões, comparar o
ledger com a corretora, correr diagnósticos sem enviar ordens, ver o registo
auditado dos pedidos, e **um único comando**: pausar novas entradas (persistente
e idempotente; não cancela entradas já enviadas, não remove proteções nem
desliga a supervisão). Retomar entradas, reiniciar o motor, alterar risco ou
tocar em ordens **não** existem como ferramentas; retomar faz-se na barra
lateral. A camada de risco continua local e independente da IA.

1. Em Configurações → *Integração ChatGPT (MCP)* ativa o servidor (porta 8765
   por defeito) e reinicia o programa. O token é gerado uma vez e fica no
   `config.json`; o endereço do conector aparece em *Ligação do ChatGPT*.
2. O ChatGPT só alcança servidores HTTPS públicos: cria um túnel para o teu PC,
   por exemplo `cloudflared tunnel --url http://127.0.0.1:8765` (ou
   `ngrok http 8765`), e põe a URL pública no campo *URL pública do túnel*.
3. No ChatGPT: Definições → Conectores → Criar; URL do servidor MCP =
   `https://<túnel>/t/<token>/mcp`; autenticação: nenhuma (o token viaja no
   caminho e é validado pelo servidor; sem token válido a resposta é 401).
   Clientes que suportem cabeçalhos podem usar `Authorization: Bearer <token>`.
4. Cada chamada fica registada em `remote_commands` (autor, pedido, instante,
   conta mascarada, modo, resultado). A pausa exige a conta (como mostrada em
   `get_status`), o modo atuais, o `context_id` devolvido por `get_status` e
   uma razão; a identidade exata validada
   segue com o comando e é reavaliada no motor antes de escrever; só é
   confirmada depois de aplicada e persistida na base da conta validada.
5. `compare_ledger_with_broker` devolve `status` = `consistent` |
   `inconsistent` | `unknown`; `ok` só é verdadeiro com ligação, reconciliação
   concluída e zero problemas.

Supervisão contínua (um serviço separado a vigiar eventos e a chamar o modelo
pela API) não está incluída: a conversa com o ChatGPT só supervisiona enquanto
está aberta.

## Instalação

```bash
cd ollama-ibkr-trader
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt          # ou requirements.lock para as versões exatas dos binários
python main.py
```

Requisitos:
- Python 3.10+ (exigido pelo `ib_async`) com Tk (`sudo apt install python3-tk` no Linux).
- **TWS ou IB Gateway** com API ativa: *Configure → API → Settings*:
  ✔ Enable ActiveX and Socket Clients, ✘ Read-Only API, porta **7496**
  (real, predefinido) ou **7497** (paper), 127.0.0.1 nos Trusted IPs. Sem subscrição, o paper usa dados com
  15 min de atraso; o bot avisa e as métricas de qualidade do LLM não são válidas.
- **Ollama** (`ollama serve`) com pelo menos um modelo; o dropdown lista `/api/tags`.

Configuração em `~/.ollamaibkrtrader/config.json`; base de dados, log e
`relatorio_estatistico.md` na mesma pasta (`OLLAMA_TRADER_HOME` muda-a).

## Executável único (PyInstaller)

**Transferir**: os executáveis compilados pelo GitHub Actions estão na página de
Releases do repositório (`OllamaIBKRTrader-windows-x64.exe`,
`OllamaIBKRTrader-macos`, `OllamaIBKRTrader-linux-x64`). Para gerar uma nova
versão, corre o workflow "Build Ollama IBKR Trader" em *Actions → Run
workflow* com um `release_tag` (ex.: `trader-v1.0.1`); cada push ao branch
também compila e guarda os binários como artefactos durante 90 dias.

No Windows o SmartScreen pode avisar por o executável não estar assinado:
*Mais informações → Executar mesmo assim*. Os dados ficam em
`%USERPROFILE%\.ollamaibkrtrader\`.

**Compilar localmente**:

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
