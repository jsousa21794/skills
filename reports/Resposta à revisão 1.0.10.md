# Resposta à revisão da versão 1.0.10

Documento revisto: *Revisão técnica do Ollama IBKR Trader — Versão 1.0.10* (7 de outubro de 2026).
Versão corrigida: **1.0.11** (branch `claude/trading-system-ollama-ibkr-3qzwm3`, release `trader-v1.0.11`).

## Veracidade da revisão

Os 6 achados (AB01–AB06) foram verificados contra o código da tag `trader-v1.0.10`. **Os 6 achados confirmam-se.**
Os ensaios do auditor estão no repositório com as asserções invertidas (`tests/test_review_110.py`, 13 testes: os 6
originais mais 7 adjacentes — identidade ambígua pendente e reconciliada pelo `permId` revelado, relatório de comissão
após alocação total, mesmas execuções na ordem natural, reabertura invalida o rótulo, migração de fecho parcialmente
corrigido, saída mista nas duas ordens de chegada e em trade aberto). A suite completa passa (236 testes). O ensaio
HTTP/MCP e o smoke da interface foram repetidos. Continua sem haver ensaio com TWS, ordens reais ou inferência Ollama.

## Estado por achado

| ID | Prioridade | Veredicto | Correção na 1.0.11 | Teste |
|----|-----------|-----------|--------------------|-------|
| AB01 | P1 | Confirmado | `group_for_order` devolve primeiro um grupo com correspondência EXATA de `permId`; com `permId` na execução e vários grupos candidatos sem `permId` conhecido devolve `None` (identidade ambígua). O motor deixa essa execução pendente, não consome nenhum trade e bloqueia o ativo; quando o callback de estado revela o `permId` da ordem viva, a execução pendente é reconciliada para o grupo certo. `trades_for_order` aplica a mesma preferência (exato > desconhecido). Sem `permId` na execução mantém-se o grupo mais recente. | AB01 ×2 |
| AB02 | P1 | Confirmado | `_on_commission` calcula a comissão devida a cada alocação como quota da quantidade TOTAL da execução (`fills.shares`), reescala a alocação (`set_allocation_commission`) e aplica ao trade só a diferença; a parte por alocar conserva a sua quota para a recuperação. Cenário do auditor: 4,00 em vez de 5,60; P&L líquido −204. | AB02 ×2 |
| AB03 | P1 | Confirmado | `record_entry_fill` reprecifica as saídas já contabilizadas quando o custo médio muda (`gross_pnl −= Δmédia × exit_qty × direção`). Cenário do auditor: −280 em vez de −264; a ordem natural dá o mesmo valor. | AB03 ×2 |
| AB04 | P1 | Confirmado | `record_entry_fill` reabre (`OPEN`) um trade fechado quando a quantidade comprovada de entrada excede as saídas e devolve o estado; o motor marca discrepância no ativo (reconciliar com a corretora antes de continuar) e passa a decisão associada a rótulo provisório (`unfinalize_label`, com `labels_changed_at`). `_own_qty` volta a contar o saldo (80). | AB04 ×2 |
| AB05 | P1 | Confirmado | Migração explícita em `_migrate`: fechos com `exit_reason='RECONCILED'` ou saída incompleta sem `reconciled_ts` recebem `reconciled_ts = exit_ts`; os que já tinham correções parciais ficam com `exit_ts` reconstruído pela execução de saída mais recente. Testado com uma base no esquema da 1.0.9 (coluna removida) aberta pela versão nova. | AB05 ×2 |
| AB06 | P1 | Confirmado | As alocações guardam a razão (`fill_allocations.reason`); `refresh_exit_summary` deriva `exit_reason`, `exit_ts` e `exit_price` do conjunto ordenado por instante de EXECUÇÃO (razões distintas unidas por `+`, ex.: `TP+SL`). Regra explícita para saídas mistas: o primeiro toque decide o rótulo (TP → 1, SL → 0), a fonte guarda a sequência (`ledger:TP+SL`), e a calibração só é invalidada quando a conclusão muda. As duas ordens de chegada dão o mesmo lucro, data, razão e rótulo. | AB06 ×3 |

## Estado dos achados anteriores marcados como parciais

- **AA02**: completado pelo AB01.
- **AA03**: completado pelo AB02, AB03 e AB04.
- **AA04**: completado pelo AB06.
- **AA06**: completado pelo AB05.

## Fora do âmbito desta versão

- Ensaios integrados com TWS paper (sequências de callbacks fora de ordem em conta real) e a ligação efetiva do
  conector ao ChatGPT: só no computador do utilizador. Os cenários determinísticos acima estão cobertos em simulação.
- Serviço contínuo de supervisão por API: fora, como nas versões anteriores.
