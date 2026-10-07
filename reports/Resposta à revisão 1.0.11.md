# Resposta à revisão da versão 1.0.11

Documento revisto: *Revisão técnica do Ollama IBKR Trader — Versão 1.0.11* (7 de outubro de 2026).
Versão corrigida: **1.0.12** (branch `claude/trading-system-ollama-ibkr-3qzwm3`, release `trader-v1.0.12`).

## Veracidade da revisão

Os 6 achados (AC01–AC06) foram verificados contra o código da tag `trader-v1.0.11`. **Os 6 achados confirmam-se.**
Os ensaios do auditor estão no repositório com as asserções invertidas (`tests/test_review_111.py`, 10 testes: os 6
originais mais 4 adjacentes — estado da ordem atribui a identidade ao grupo sem `permId`, bloqueio ao longo de ciclos e
após reinício, entradas mantêm razão nula na migração, contadores com saídas puras e indeterminadas). A suite completa
passa (246 testes). O ensaio HTTP/MCP e o smoke da interface foram repetidos. Continua sem haver ensaio com TWS, ordens
reais ou inferência Ollama.

Ao corrigir o AC01/AC02 foi detetado e eliminado um risco adjacente da 1.0.11: a proteção agregada (Y07) ligava a mesma
ordem a vários grupos, o que a nova regra de ambiguidade tornaria pendente em produção (execuções reais trazem
`permId`). Agora cada ordem pertence a um só grupo e cobre os outros trades em `exit_coverage` (teste D07 atualizado).

## Estado por achado

| ID | Prioridade | Veredicto | Correção na 1.0.12 | Teste |
|----|-----------|-----------|--------------------|-------|
| AC01 | P1 | Confirmado | `_on_order_status` resolve o grupo com o `permId` do próprio estado (exato > desconhecido; conhecido e diferente nunca é escolhido). Um `permId` conhecido só é substituído quando a ordem pertence a um ÚNICO grupo (associação herdada errada, W03); com vários grupos a identidade é preservada e o conflito registado. Entre vários grupos sem identidade, só a quantidade da ordem (igual à do grupo) resolve; sem evidência nada é gravado. | AC01 ×2, W03 (mantido) |
| AC02 | P1 | Confirmado | A supervisão mantém a discrepância enquanto houver execuções do bot por alocar no ativo (`unallocated_fills`), independentemente dos ajustes de quantidade; `_load_conflicts` recarrega esse bloqueio no arranque. Testado em 3 ciclos e após reinício; o bloqueio só sai depois de o `permId` revelado reconciliar a execução. | AC02 |
| AC03 | P1 | Confirmado | `_allocate_exit` não contabiliza saídas num trade sem execução de entrada comprovada (`filled_qty = 0`): a saída fica pendente (discrepância) e é contabilizada quando a entrada chega (recuperação automática pelo resto da entrada). Resultado −200 com `entry_price` 100. O saldo disponível passou a ser entradas − saídas − ajustes por explicar, consumindo primeiro o ajuste que a execução explica. | AC03, D07 |
| AC04 | P1 | Confirmado | `_migrate` preenche `fill_allocations.reason` nas alocações de saída existentes pela identidade histórica da ordem (perna TP/SL ativa ou arquivada, fecho por sinal → `SIGNAL`, caso contrário `OUTRO`) e pela direção do trade (entradas ficam nulas). `refresh_exit_summary` marca `+?` quando as razões não cobrem toda a saída; esse resumo é censurado. Cenário do auditor: `TP+SL`, média 100,40, rótulo 1. | AC04 ×2 |
| AC05 | P2 | Confirmado | Razões DIFERENTES no mesmo instante de execução tornam a sequência indeterminada: `exit_reason = "SL|TP"` (ordem alfabética), rótulo `None` com fonte `ledger:SL|TP`, excluído do treino binário. Instantes distintos continuam a ordenar o primeiro toque. | AC05 ×2 |
| AC06 | P1 | Confirmado | `recent_stop_count` e `pnl_summary` contam por componente (`exit_components`): um trade conta uma vez como stop se `SL` for uma das suas razões e uma vez como TP se `TP` o for, em `SL+TP`, `TP+SL` e `SL|TP`, sem duplicar o mesmo trade. StoplossGuard passa a ver o stop da saída mista. | AC06 ×2 |

## Estado dos achados anteriores marcados como parciais

- **AB01**: completado pelo AC01 e AC02.
- **AB03**: completado pelo AC03.
- **AB06**: completado pelo AC04, AC05 e AC06.

## Fora do âmbito desta versão

- Ensaios integrados com TWS paper (reutilização real de `orderId` entre sessões, estados atrasados) e a ligação
  efetiva do conector ao ChatGPT: só no computador do utilizador.
- Serviço contínuo de supervisão por API: fora, como nas versões anteriores.
