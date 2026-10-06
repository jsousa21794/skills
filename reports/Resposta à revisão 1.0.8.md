# Resposta à revisão da versão 1.0.8

Documento revisto: *Verificação da versão 1.0.8* (6 de outubro de 2026).
Versão corrigida: **1.0.9** (branch `claude/trading-system-ollama-ibkr-3qzwm3`, release `trader-v1.0.9`).

## Veracidade da revisão

Os 8 achados (Z01–Z08) e os 8 ensaios de diagnóstico (E01–E08) foram verificados contra o código da tag
`trader-v1.0.8`. **Os 8 achados confirmam-se.** Os ensaios estão no repositório com as asserções invertidas
(`tests/test_review_108.py`, 16 testes: os 8 do auditor mais 8 casos adjacentes — reinício após inversão,
quantidade própria reduzida a zero por ajustes, pausa durante um fecho, execução sem trade coberto, execução antiga
que chega primeiro, modelo antigo no arranque, comparação consistente, mudança de geração). O ensaio positivo
HTTP/MCP foi repetido localmente (servidor uvicorn + cliente MCP 2.3.0): 401 sem token, 9 ferramentas, pausa
aplicada, persistida e idempotente, auditoria gravada. A suite completa passa (213 testes). Continua sem haver
ensaio com TWS, ordens reais ou inferência Ollama nesta verificação.

Um teste da 1.0.7 (D07) codificava a semântica errada que o Z05 denuncia — a saída nova consumia o ajuste
antigo — e foi corrigido para a semântica nova (o ajuste mantém-se até à execução que o explica).

## Estado por achado

| ID | Prioridade | Veredicto | Correção na 1.0.9 | Teste |
|----|-----------|-----------|-------------------|-------|
| Z01 | P0 | Confirmado | A natureza do conflito (redução, inversão, execuções por receber) é PERSISTIDA em `conflict:<ativo>` e recarregada no arranque. Quando a quantidade própria é zero e a corretora ainda mostra posição (tudo explicado por ajustes), o supervisor entra em `_hold_external_conflict`: cancela as saídas próprias que sobrem, não coloca nenhuma ordem e mantém o bloqueio; `_protect_if_naked` recusa proteger uma posição com trades abertos mas quantidade própria zero. Titularidade, direção e níveis são revalidados em cada ciclo. Testado em 4 ciclos consecutivos e após reinício (novo motor sobre a mesma base). | E01, Z01 ×2 |
| Z02 | P1 | Confirmado | O token de autorização reavaliado pela corretora imediatamente antes de `placeOrder` passa a saber se protege uma ENTRADA (`entry=True`): a pausa de novas entradas (local ou MCP) e a discrepância do ativo revogam a entrada em preparação. Fechos e proteções usam tokens sem essa condição. | E02, Z02 |
| Z03 | P1 | Confirmado | A verificação da pausa saiu da fase global e passou para DEPOIS da classificação do plano: só planos de ENTRADA são travados; um plano CLOSE segue para `_execute_close` com as suas validações próprias. | E03 |
| Z04 | P1 | Confirmado | Nova tabela `exit_coverage` (fecho por sinal → trades que cobre) e `trades_for_order` (grupo da ordem, pernas arquivadas, cobertura explícita). `_allocate_exit` distribui SÓ por esses trades; um trade coberto já fechado por reconciliação recebe a execução tardia como correção (`record_late_exit_fill`: quantidade, preço, P&L, sem reabrir); sem saldo coberto a execução fica por alocar e assinalada. Fechos gravados por versões anteriores (sem cobertura) cobrem por cronologia só os trades abertos antes da ordem. | E04, Z04 |
| Z05 | P1 | Confirmado | Cada ajuste provisório guarda as saídas vivas no instante da discrepância (`order_ids`); `consume_adjustment` só aceita execuções dessas ordens (ajustes antigos sem lista: ordens anteriores ao ajuste). A saída nova fecha a quantidade viva e deixa o ajuste intacto; o trade reconciliado com execuções por receber fica assinalado como indeterminado e a execução antiga, quando chega, corrige-o e consome o ajuste. No cenário do auditor o resultado passa de −40 para −280 (100 ações, as duas execuções comprovadas). | E05, Z05, D07 (corrigido) |
| Z06 | P1 | Confirmado | `fit_from_db` que devolve `None` com `needs_refit()` verdadeiro chama `invalidate()`: modelo removido da memória e do kv (heurística). `reload()` descarta um modelo guardado anterior a `labels_changed_at`. | E06, Z06 |
| Z07 | P1 | Confirmado | `compare_ledger_with_broker` percorre também os ativos que só têm ordens próprias vivas; deteta saídas sem posição, excesso de saídas, saídas do lado errado, execuções por alocar e conflitos persistidos; devolve `status` ∈ {`consistent`, `inconsistent`, `unknown`} e `ok` só em `consistent` (ligado + reconciliado + sem problemas). | E07, Z07 |
| Z08 | P1 | Confirmado | `RemoteSupervisor.pause_entries` captura a conta EXATA, o modo, a geração e a base de dados no momento da validação e passa-os em `expected` à coroutine; `TradingEngine.pause_entries` reavalia tudo no loop do motor imediatamente antes da escrita e recusa qualquer divergência (`applied=False`, sem escrita). A confirmação exige ainda persistência na mesma base e conta. A máscara serve só para apresentação. | E08, Z08 |

## Estado dos achados anteriores marcados como parciais

- **Y03** (ajustes provisórios): completado pelo Z05 — a ordem de chegada dos callbacks deixa de alterar quantidade e P&L.
- **Y05** (rótulos finais): completado pelo Z06 — o modelo invalidado é efetivamente retirado.
- **Y06** (inversão): completado pelo Z01 — nenhum ciclo posterior nem reinício repõe ordens sobre a posição externa.
- **Y07** (alocação multi-trade): completado pelo Z04 — a distribuição respeita a cobertura persistida da ordem.

## Fora do âmbito desta versão

- Ensaios integrados com TWS paper (sequências com vários ciclos, callbacks tardios e mudanças de conta na
  corretora real): só no computador do utilizador, agora com os cenários acima já cobertos em simulação.
- Ligação efetiva do conector ao ChatGPT (túnel HTTPS + conector): depende do PC do utilizador; instruções no README.
- Serviço contínuo de supervisão por API: continua fora, como na 1.0.8.
