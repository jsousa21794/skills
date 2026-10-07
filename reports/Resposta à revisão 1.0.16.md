# Resposta à revisão da versão 1.0.16

Documento revisto: *Revisão técnica do Ollama IBKR Trader — Versão 1.0.16* (7 de outubro de 2026).
Versão corrigida: **1.0.17** (branch `claude/trading-system-ollama-ibkr-3qzwm3`, release `trader-v1.0.17`).

## Veracidade da revisão

Os 2 achados (AH01, AH02) e a observação sobre a falha intermitente do Windows foram verificados contra o código da tag
`trader-v1.0.16`. **Os 2 achados confirmam-se e a causa da intermitência é a mesma do AH01** (empates de relógio). Os
ensaios do auditor estão no repositório com as asserções invertidas (`tests/test_review_116.py`, 4 testes: AH01 com
delta 0 e 1 µs, AH01 para cobertura inferida, AH02), todos com relógio controlado (`trader.database.utc_now` substituído),
e os dois testes de migração sequencial da 1.0.15/1.0.16 passaram a usar o mesmo relógio controlado. A suite completa
passa (272 testes). O ensaio HTTP/MCP e o smoke da interface foram repetidos. Continua sem haver ensaio com TWS,
ordens reais ou inferência Ollama.

## Estado por achado

| ID | Prioridade | Veredicto | Correção na 1.0.17 | Teste |
|----|-----------|-----------|--------------------|-------|
| AH01 | P1 | Confirmado | Proveniência na cobertura por ordem (`order_coverage.source`): `placed` para relações gravadas na colocação (motor), `migrated` para relações inferidas pela migração da cobertura legada. A reparação identificada por versão (`COVERAGE_MIGRATION = 1.0.17`) nunca remove relações `placed`; relações `migrated` exigem prova estrita (colocação antes do fecho; um empate temporal não prova e a relação é removida, ficando a execução pendente com discrepância); relações de origem desconhecida (versões anteriores) só caem com prova estrita do contrário (colocação DEPOIS do fecho), nunca por empate. Com relógio idêntico na colocação e na reconciliação a cobertura explícita de 100+20 é preservada e a execução de 120 aloca 100/20. | AH01 ×3 |
| AH02 | P1 | Confirmado | Ao invalidar uma relação, a reparação reverte as alocações de execuções dessa ordem a esse trade (`_reverse_allocation`): quantidade saída, P&L bruto/líquido e comissão repostos, resumo recalculado (ou estado reconciliado reposto quando não restam saídas), rótulo reavaliado; o saldo volta a `unallocated_fills` e a reconciliação de arranque (`_reconcile`) recupera-o com a cobertura reparada e a identidade original. No cenário do auditor as alocações passam de (t1 100, t2 20) para (t1 100, t3 20) e `exit_qty` de 100/20/0 para 100/0/20. | AH02 |
| CI Windows | — | Confirmado | Os dois testes que falharam na execução de push (`test_af02`, `test_ag02`) dependiam da ordem de instantes gerados pelo relógio real; com resolução baixa, fecho e colocação coincidiam e o trade 3 saía da cobertura. Os testes passaram a controlar o relógio explicitamente; a regra do código para relações de origem desconhecida deixou de invalidar por empate. | AF02, AG02 (atualizados) |

## Estado dos achados anteriores marcados como parciais

- **AG02**: completado pelo AH01 e AH02.

## Fora do âmbito desta versão

- Ensaios integrados com TWS paper e a ligação efetiva do conector ao ChatGPT: só no computador do utilizador.
- Serviço contínuo de supervisão por API: fora, como nas versões anteriores.
