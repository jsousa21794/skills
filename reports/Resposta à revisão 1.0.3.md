# Resposta à revisão da versão 1.0.3

Documento revisto: *Verificação da versão 1.0.3 — comparação com a auditoria da 1.0.2* (6 de outubro de 2026).
Versão corrigida: **1.0.4** (branch `claude/trading-system-ollama-ibkr-3qzwm3`, release `trader-v1.0.4`).

## Veracidade da revisão

Cada uma das 18 entradas foi verificada contra o código da tag `trader-v1.0.3` antes de qualquer alteração.
**As 18 entradas confirmam-se.** Não encontrei nenhuma afirmação incorreta; em duas (N09 "bases por conta" e
N18 "lock integral") a revisão descreve uma exigência de desenho que a 1.0.3 não tinha, e não um defeito
observado, mas ambas foram implementadas. A conclusão da revisão, de que a 1.0.3 não estava pronta para
operação autónoma em conta real, estava certa: N01 a N04 permitiam, em cenários de corrida, uma ordem depois
de Parar, um TP/SL novo a coexistir com uma venda de fecho, cobertura em excesso e cobertura falsa de outra conta.

Os 21 cenários R01–R21 foram reescritos como testes de regressão **dentro do repositório**
(`tests/test_review_103.py`, 29 testes), agora com asserções do comportamento corrigido. A suite completa passa
(139 testes). Continua a não haver ensaio com TWS, ordens reais ou inferência Ollama nesta verificação; o ponto 5
da prioridade de correção (ensaios com TWS paper: parciais, rejeições, cancelamentos atrasados, reinício com
ordens abertas, reconciliação com o extrato) só pode ser feito no computador do utilizador.

## Estado por entrada

| ID | Prioridade | Veredicto | Correção na 1.0.4 | Teste |
|----|-----------|-----------|-------------------|-------|
| N01 | P0 | Confirmado | `place_bracket` e `close_position` recebem um token `authorize` (geração + ciclo ativo + ligação + kill-switch) reavaliado depois de cada `await` e imediatamente antes de `placeOrder`. Política explícita: depois de Parar, nenhuma ordem de entrada ou de fecho por sinal é enviada; só a **reposição de cobertura** (redução de risco) continua permitida. | R01, R21 |
| N02 | P0 | Confirmado | O estado `CLOSING` é instalado **antes** de cancelar os filhos; fecho e reproteção partilham um lock por ativo; cancelamentos pedidos pelo bot ficam registados (`cancel_trade`/`is_intentional_cancel`) e não disparam reproteção; um timeout parcial devolve `needs_protection` verdadeiro quando a cobertura ficou incompleta. | R18, R21 |
| N03 | P0 | Confirmado | `ensure_protection` cancela filhos órfãos (TP sem stop ou stop sem TP), espera, recalcula a cobertura e coloca **só o residual**; `has_protective_orders` considera coberta a parte inteira negociável; a fração restante é assinalada uma vez e não gera ordens repetidas. | R03, R09, N03 |
| N04 | P0 | Confirmado | O filtro de conta (`_same_account`) é obrigatório em `open_trades_for`, independente de `ours_only`; um stop de outra conta nunca é cobertura. Um stop manual da mesma conta continua a contar. | R02 |
| N05 | P1 | Confirmado | `has_pending_entry` reconhece pais MKT **e LMT**; `_restore_pending_orders` reconstrói entradas e fechos pendentes a partir das ordens vivas na corretora (prazo monotónico restante, reserva de fundos, trade associado); ordens do bot sem grupo na base são canceladas; o ciclo de decisão **não corre até `_reconciled`**. | R05, N05 |
| N06 | P1 | Confirmado | Fills de outro `clientId` ou com outro `orderRef` são ignorados; `group_for_order` valida símbolo, `conId` e conta além do `orderId`. | R06 |
| N07 | P1 | Confirmado | Nova tabela `order_history` (pai/TP/SL por grupo, com `replaced_ts`); `update_group_orders` arquiva em vez de apagar; `order_leg` identifica a perna de um filho antigo; um fill tardio do TP substituído fecha o trade certo com a razão certa. | R04 |
| N08 | P1 | Confirmado | `_migrate_legacy_db`: `trader.sqlite3` (≤ 1.0.2) é copiado uma vez para a base do modo configurado (o mesmo `trading_mode` do `config.json` com que foi usado), o original fica como `.migrated-1.0.2`; se a base nova já existir, nada é tocado e é emitido aviso. A reconciliação ao ligar reconstrói o estado antes de decidir. | R07 |
| N09 | P1 | Confirmado | `base_currency`, conta(s), `data_delayed` e cancelamentos são limpos ao desligar e redetetados ao ligar. **Base de dados por conta**: ao conhecer a conta, o motor passa para `trader_<modo>_<conta>.sqlite3` (copiando o estado pré-ligação só quando ainda não está associado a nenhuma conta) e grava `bound_account`; uma base associada a outra conta é recusada. Posições sem trade do bot são **externas**: não são geridas, fechadas nem protegidas, e bloqueiam sinais nesse ativo, salvo `manage_external_positions`. | R08, R17, N09 |
| N10 | P1 | Confirmado | `bar_time`/`market_ts` passa a ser o **fecho** da vela agregada; a idade da decisão deixa de somar a duração do bucket; o percurso do settlement só usa velas posteriores à decisão; sem toque em TP nem stop a decisão fica **censurada** (`correct` NULL) e não entra na calibração. | R10, R11 |
| N11 | P1 | Confirmado | `Analytics.build_report(model=…)` filtra decisões e trades (join por `decision_id`) do modelo; gates e relatório manual/retrospetiva usam o modelo atual; um calibrador por modelo (`_calibrator_for`) e a amostra A/B usa o do modelo que respondeu; a calibração particiona por (modelo, versão do prompt) e a versão do prompt só avança quando o texto muda. | R12, N11 |
| N12 | P1 | Confirmado | Equity do replay marcada a mercado (perda aberta entra nos gates); `decide_execution` recebe `learning_risk_multiplier` e `gates_passed=False`; `_check_exit` resolve primeiro os eventos observáveis na abertura (gap pelo stop ou pelo TP) e só depois a ambiguidade intrabar. Filtros externos (resultados, VIX, sentimento, lições) continuam fora do replay, por serem dependentes de rede e de data (ver "Fora do âmbito"). | R13, R20 |
| N13 | P1 | Confirmado | Tolerância zero = limit **ao preço de referência**; nova opção explícita `entry_order_type="market"` é a única forma de entrar sem limite (validada). | R15 |
| N14 | P1 | Confirmado | `_supervisor_loop` é uma tarefa própria (2–5 s), independente do ciclo de inferência; prazos das entradas com `time.monotonic()`; o cancelamento só é pedido uma vez. | N14 |
| N15 | P1 | Confirmado | Antes de enviar, `last_price` (vela em formação) é comparado com a referência: além do limite ou desvio > 2× a tolerância, não entra; `recent_news(cached_only=True)` no loop nunca faz rede (a rede fica no `prefetch` em executor). | N15 ×2 |
| N16 | P2 | Confirmado | `Settings.load` valida que a raiz é um objeto; caso contrário aviso, cópia `config.json.invalid` e valores por defeito; `apply` ignora raízes não-dict sem exceção. | R16 |
| N17 | P2 | Confirmado | Com `drop_incomplete`, **qualquer** bucket a que falte um minuto (interno ou final) é descartado; velas duplicadas do mesmo minuto colapsam na última. Política documentada em `aggregate_bars`. | R14 |
| N18 | P2 | Confirmado | Gates do relatório usam `learning_risk_multiplier` e o risco efetivo do motor; `protection_events` recebem o relógio de quem chama (histórico no replay); `requirements.lock` com todas as dependências transitivas, PyInstaller e pytest em versões exatas, usado pelo workflow. | R19 |

## Estado dos 40 achados da 1.0.2 que a revisão deixou "parciais"

F01, F02, F03, F04, F05, F06*, F07, F08, F10, F11, F12, F13, F15, F16, F21, F27, F28, F29, F30, F31, F32, F34,
F35, F36, F39 — todos tratados pelas entradas acima (ver coluna "Rastreabilidade" da revisão).

\* F06: as execuções importadas continuam a ser as que a sessão da TWS conhece (`ib.fills()` do dia); execuções
mais antigas sem posição continuam a fechar como `RECONCILED` sem P&L. Isto é uma limitação da API, não é
resolúvel sem o extrato da corretora (Flex Query), e fica assinalado no log.

## Fora do âmbito desta versão (explícito)

- Ensaios com TWS paper (prioridade 5 da revisão) e validação contra o extrato: só no computador do utilizador.
- Replay com resultados, VIX, notícias/sentimento e lições: continuam fora, por dependerem de rede e de datas.
- A base de dados por conta separa contas; não separa subcontas de um mesmo *advisor* com o mesmo identificador.

## Outras alterações na 1.0.4

- Interface: todas as opções (conta, modelo, ativos, ligação, Ollama, risco, custos, apresentação) passaram para
  o separador **Configurações**, com validação, gravação atómica e aplicação no motor; nova opção de **moeda de
  apresentação** convertida com a taxa `ExchangeRate` da IBKR; o separador *Risco* mostra reconciliação, posições
  externas não geridas, base de dados da conta e experiência em curso.
