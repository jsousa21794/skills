# Resposta à revisão da versão 1.0.14

Documento revisto: *Revisão técnica do Ollama IBKR Trader — Versão 1.0.14* (7 de outubro de 2026).
Versão corrigida: **1.0.15** (branch `claude/trading-system-ollama-ibkr-3qzwm3`, release `trader-v1.0.15`).

## Veracidade da revisão

Os 3 achados (AF01–AF03) foram verificados contra o código da tag `trader-v1.0.14`. **Os 3 achados confirmam-se.**
Os ensaios do auditor estão no repositório com as asserções invertidas (`tests/test_review_114.py`, 4 testes: os 3
originais mais o controlo de que o cancelamento da própria entrada pendente continua a libertá-la). Os cenários das
versões anteriores (AF02 com o estado acumulado pela 1.0.12; AF03 com uma decisão provisória da 1.0.13) são
reproduzidos sem checkouts anteriores, recriando o mesmo estado da base. A suite completa passa (264 testes). O
ensaio HTTP/MCP e o smoke da interface foram repetidos. Continua sem haver ensaio com TWS, ordens reais ou inferência
Ollama.

## Estado por achado

| ID | Prioridade | Veredicto | Correção na 1.0.15 | Teste |
|----|-----------|-----------|--------------------|-------|
| AF01 | P1 | Confirmado | As entradas pendentes (criadas ou restauradas) e os fechos pendentes guardam o `group_id` a que pertencem. No ramo terminal de `_on_order_status`, o evento só liberta a entrada/fecho pendente se o grupo validado pela identidade for o MESMO (`_event_targets`); um grupo histórico validado (permId 888) não autoriza alterações à entrada do grupo atual (permId 999): a ordem viva, a reserva de 2000 e o trade mantêm-se. O cancelamento com a identidade da própria entrada continua a libertá-la. | AF01 ×2 |
| AF02 | P1 | Confirmado | A migração da cobertura legada acrescenta à condição "depois de o trade existir" a condição "antes de o trade fechar/ser reconciliado" (`order_history.ts < COALESCE(reconciled_ts, exit_ts)`): uma ordem colocada no instante do fecho ou depois nunca cobriu o trade. No cenário acumulado da 1.0.12 (proteção 1+2, depois 1+3) a ordem final cobre apenas 1 e 3 e a execução de 120 aloca 100/0/20. Sem prova suficiente a execução fica pendente com discrepância (invariante anterior). | AF02 |
| AF03 | P2 | Confirmado | `relabel_from_ledger` (usado pela migração e pelo motor) só finaliza rótulos de trades `CLOSED`; um trade aberto com saída parcial mantém `label_final=0` e continua em `provisional_decisions`, mesmo com o resumo das saídas parciais atualizado. O fecho posterior finaliza pela sequência real (`TP+SL`, rótulo 1). | AF03 |

## Estado dos achados anteriores marcados como parciais

- **AE01**: completado pelo AF01.
- **AE03**: completado pelo AF02.

## Fora do âmbito desta versão

- Ensaios integrados com TWS paper e a ligação efetiva do conector ao ChatGPT: só no computador do utilizador.
- Serviço contínuo de supervisão por API: fora, como nas versões anteriores.
