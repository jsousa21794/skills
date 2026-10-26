# Conselho de verificação da versão 1.0.17

Pedido: "Reúne o conselho e verifica tudo. Se estiver tudo conforme, escreve um step-by-step para alguém que não perceba nada."
Versão verificada: **1.0.17** (tag `trader-v1.0.17`). Versão corrigida: **1.0.18** (branch `claude/trading-system-ollama-ibkr-3qzwm3`,
release `trader-v1.0.18`).

## Como foi feita a verificação

Três revisores independentes, cada um com acesso só de leitura ao código e instruções para **reproduzir** qualquer defeito antes de o
reportar (um teste que passa = defeito confirmado), em paralelo com a suite completa, o ensaio HTTP/MCP e o smoke da interface:

| Revisor | Âmbito | Resultado |
|---|---|---|
| A — ciclo de ordens | identidade permId/orderId, alocação de execuções, reconciliação, ajustes provisórios, migrações, fechos/proteção | 1 P1 + 3 P2 reproduzidos; núcleo (alocação, identidade, migrações, fechos) sem defeito |
| B — risco, custos, aprendizagem | requisitos do dono (real por defeito, 10 %, entradas ilimitadas em lucro, kill-switch −20 %, custos, LLM só propõe, idade das decisões, rótulos finais) | 1 **P0** + 1 P1 + 2 P2 reproduzidos; todos os requisitos do dono confirmados no código |
| C — MCP, interface, build, documentação | autenticação, fugas de informação, consistência GUI/config, empacotamento, exatidão dos textos | 4 P1 + 10 P2 (código e texto); smoke MCP OK, `test_mcp` OK |

Veredicto: **não estava tudo conforme**. Nenhum achado afeta o envio de ordens nem a proteção de posições abertas pelo bot, mas o P0
parava silenciosamente o ciclo de decisão e dois P1 ligavam-se a dinheiro (regra PDT) e a bloqueios permanentes de um ativo. Tudo o que
foi reproduzido foi corrigido na 1.0.18, com os diagnósticos dos revisores invertidos em regressões (`tests/test_review_117.py`, 13
testes) e o teste F05 atualizado à regra de propriedade. Suite: **286 testes**. Smokes repetidos (interface; interface com MCP ligado,
confirmando que o token não vai para o log; HTTP/MCP).

## Achados e correções

| ID | Prioridade | Origem | Achado | Correção na 1.0.18 |
|----|-----------|--------|--------|--------------------|
| AI01 | **P0** | B | Entrada cancelada sem execução (timeout de 120 s ou cancel na TWS) deixava um trade `CANCELLED` com `entry_ts` nulo e a decisão `executed=1`; o settlement fazia `fromisoformat(None)`, rebentava e, por correr no mesmo `try` antes do ciclo, **o ciclo de decisão nunca mais corria** enquanto a decisão existisse (também bloqueava calibração, lições e retrospetiva) | Trade não `OPEN/CLOSED` ou sem `entry_ts` é tratado como decisão não negociada; cada decisão é avaliada num `try` próprio (`_settle_one`); o settlement e o ciclo correm em `try` separados |
| AI02 | P1 | C | Fugas pelo MCP: o endereço do conector **com o token** ia para `trader.log` (que o guia pede para enviar e que `get_recent_log` devolve); `run_diagnostics` devolvia o caminho da base com o **número de conta completo** e o utilizador do SO; `get_recent_log` devolvia linhas com a conta completa; argumentos da pausa auditados em claro | Token nunca vai para o log; `_redact` mascara a conta em linhas de log, nome do ficheiro da base e argumentos auditados; o caminho do log deixou de ser devolvido (só o nome) |
| AI03 | P1 | B | Gate PDT comparava `net_liq` na **moeda da conta** com o limiar de 25 000 **USD** (conta em JPY/GBP: regra ignorada ou aplicada indevidamente) | `check_global(equity_usd=…)`; o PDT usa o equity em USD |
| AI05 | P1 | A | `_reconcile_reduced/reversed_position` usavam a quantidade **nominal** para trades sem execução (`filled_qty or qty`): o ajuste provisório caía num trade fantasma, as saídas do trade real eram canceladas/repostas em cada ciclo e o ativo ficava bloqueado para sempre | Só `filled_qty` entra nos ajustes (mesma regra de `_own_qty`) |
| AI06 | P2 | A | Entrada que morreu com o bot desligado ficava `OPEN`/`filled 0` para sempre (nunca `CANCELLED`), mantendo o ativo gerido e alimentando AI05 | `_cancel_dead_entries` na reconciliação e no supervisor (ordem desconhecida da corretora) marca `CANCELLED`; limitação da API (execuções só do dia) documentada |
| AI07 | P2 | A | `Cancelled` seguido de execução (parcial) do pai deixava o trade `CANCELLED` com `filled_qty>0`: a posição real do bot passava a "externa" | `record_entry_fill` reabre trades `CANCELLED` (como já fazia com `CLOSED`), limpa a razão e o motor marca discrepância |
| AI08 | P2 | A | Ordem de saída própria viva sem trade aberto fazia o bot **adotar a posição manual inteira** (ex.: 300 ações do utilizador) com `manage_external_positions=False` | Sem trade e sem adoção explícita: saídas órfãs canceladas, posição marcada externa, nunca adotada; adoção só com a opção ligada (F05 atualizado) |
| AI04 | P2 | B | `sentiment_veto_threshold` positivo invertia o veto; `risk_per_trade_pct` aceitava 0,9 apesar da promessa "até 10 %"; `daily_loss_limit_pct=0` pararia o ciclo no arranque do dia; portas sem limite superior | Validação: risco ∈ (0, 0,10], kill-switch ∈ (0, 1], veto ∈ [−1, 0], portas 1–65535; `atr_floor_percentile_frac` morto removido |
| AI09 | P1 | C | O endereço do conector na secção *Ligação do ChatGPT* não era atualizado ao gravar (o guia dizia o contrário); "Todas as opções do programa" era falso (96 campos só em `config.json`) | Endereço recomposto ao gravar (`connector_text`); texto da interface, README e guias corrigidos |
| AI10 | P2 | C | Cabeçalho HTTP não-UTF-8 dava 500; websocket respondido como HTTP; `get_remote_commands` sem auditoria; iteração de dicionários do motor a partir da thread do servidor; `settings.save()` engolia erros; confirmação `REAL` repetida ao voltar de Paper; coluna "P&L não real." sem conversão de moeda | Corrigidos (latin-1 → 401; `websocket.close`; auditoria; cópia das estruturas; erro de gravação em âmbar; confirmação única; coluna convertida) |
| AI11 | P2 | C | Build: smoke do workflow não exercitava a pilha MCP congelada; `build.sh/.bat` fora do lock; README desatualizado (Python 3.10 vs lock 3.12, `relatorio_estatistico_<modo>.md`, conteúdo do `trader.spec`, botão Retomar, secções de Configurações) | Smoke com `mcp_enabled` e verificação de que o token não está no log; scripts usam `requirements.lock`; README corrigido |
| Docs | P2 | C + próprio | Guia: "Reconciliado" não existe (é «⏳ a reconciliar…»), `executado` vs `EXECUTADA ·`, botão Retomar sempre visível (fica ativo), estado IBKR/Ollama no cabeçalho e não na barra lateral, porta na secção *Ligação*; horário de Lisboa errado (**14:30–21:00**, não 15:30–22:00) | Ambos os guias corrigidos |

## Confirmado sem defeito (pelos três revisores)

Requisitos do dono: modo real por defeito (7496) com confirmação única; risco por operação até 10 % limitado pelos fundos; entradas
ilimitadas em dia de lucro; kill-switch −20 % que para o ciclo e persiste a reinícios; custos (comissão mínima/teto, slippage,
break-even, `max_cost_fraction_of_tp`, `min_net_gain_multiple`); o LLM só propõe (quórum, acordo, divergência → HOLD); decisões
expiradas ou com velas/cotações estagnadas nunca executam; só rótulos finais treinam a calibração; saídas mistas rotuladas pelo primeiro
toque ou censuradas; shorts e unidades %/fração consistentes. Ciclo de ordens: alocação por cobertura, identidade exata/ambígua,
`_event_targets`, migrações idempotentes com proveniência, fechos/proteção com confirmação terminal. MCP: autenticação por token
(path/Bearer, `compare_digest`), conjunto fechado de ferramentas, `context_id` revalidado no motor, auditoria. Empacotamento: todos os
`collect_all` importam; lock consistente; workflow com testes + PyInstaller + smoke em 3 SOs.

## Observações sem correção (registadas)

- Execuções de dias anteriores não são importadas (limitação da API da IBKR: `reqExecutions` só devolve o dia corrente). Mitigação: AI06
  marca a entrada como cancelada e a posição aparece como não pertencente ao bot, com aviso para confirmar no Trade Log.
- Com `decision_bar_minutes=5`, `decision_max_age_seconds=180` e consulta a cada 15 min, parte das decisões nasce já expirada conforme a
  fase do ciclo face à fronteira de 5 min; é seguro (nunca executa dados velhos) mas desperdiça inferências. Fica para uma versão futura
  (alinhar o ciclo com o fecho da vela).
- Taxas regulatórias dos EUA (SEC/TAF) não estão no modelo de custos (cêntimos por ordem).
- Correções de execução da IBKR (mesmo `execId` com sufixo novo) seriam contadas como execução adicional; não reproduzido em ensaio.

## Fora do âmbito

Ensaios integrados com TWS paper, ordens reais, inferência Ollama real e a ligação efetiva ao ChatGPT: só no computador do utilizador.
