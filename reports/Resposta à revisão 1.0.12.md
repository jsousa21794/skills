# Resposta à revisão da versão 1.0.12

Documento revisto: *Revisão técnica do Ollama IBKR Trader — Versão 1.0.12* (7 de outubro de 2026).
Versão corrigida: **1.0.13** (branch `claude/trading-system-ollama-ibkr-3qzwm3`, release `trader-v1.0.13`).

## Veracidade da revisão

Os 5 achados (AD01–AD05) foram verificados contra o código da tag `trader-v1.0.12`. **Os 5 achados confirmam-se.**
Os ensaios do auditor estão no repositório com as asserções invertidas (`tests/test_review_112.py`, 9 testes: os 6
originais — AD02 parametrizado em dois percursos — mais 3 adjacentes: reparação com prova da ordem viva, ajustes
provisórios a seguir a cobertura por ordem, empate no primeiro instante continua censurado). O AD04 foi reproduzido
sem o checkout da 1.0.11 (o estado legado é gerado anulando a razão da alocação antes da segunda execução, como no
script do auditor) e inclui a verificação de idempotência. A suite completa passa (255 testes). O ensaio HTTP/MCP e o
smoke da interface foram repetidos. Continua sem haver ensaio com TWS, ordens reais ou inferência Ollama.

## Estado por achado

| ID | Prioridade | Veredicto | Correção na 1.0.13 | Teste |
|----|-----------|-----------|--------------------|-------|
| AD01 | P1 | Confirmado | Um candidato único com `permId` conhecido e incompatível só é corrigido quando existe PROVA adicional: a ordem viva na corretora (nossa, mesmo `orderId` e símbolo) tem exatamente o `permId` do estado (`_live_order_proof`). Sem prova a identidade é preservada, o conflito registado em log, e a execução seguinte com o `permId` incompatível fica pendente e bloqueia o ativo (nunca fecha o trade novo). O caso W03 (associação herdada errada) continua a funcionar, agora com a prova da ordem viva. | AD01 ×2, W03 (atualizado) |
| AD02 | P1 | Confirmado | Ponto ÚNICO de desbloqueio (`_release_block`): a discrepância só sai se não houver execuções próprias por alocar no ativo; `_resolve_conflict`, `_resize_exits_locked`, `_reconcile_vanished_position`, `_supervise_symbol` e `_allocate_exit` passam todos por ele. Testado nos dois percursos do auditor (redimensionamento e posição desaparecida), em dois ciclos cada. | AD02 ×2 |
| AD03 | P1 | Confirmado | Nova tabela `order_coverage(order_id, group_id, trade_id)`: a cobertura é por ORDEM concreta e congelada na colocação (proteção reposta, proteção agregada e fecho por sinal gravam os seus `order_id`). `trades_for_order` usa-a; a tabela por grupo fica só para linhas legadas. A ordem antiga continua a cobrir apenas o seu trade: o fill de 100 do stop antigo vai todo para o trade antigo (reconciliado) e nada para o novo. `exit_order_ids_for_trade` (evidência dos ajustes) segue a mesma cobertura por ordem. | AD03 ×2 |
| AD04 | P1 | Confirmado | A migração recolhe os trades cujas alocações receberam razão e, na mesma operação, recalcula `refresh_exit_summary` e o rótulo final da decisão pela regra única `Database.ledger_label` (partilhada com o `Settler`), gravando `labels_changed_at` quando algo muda. Idempotente (só alocações preenchidas nessa execução). Cenário do auditor: `TP+SL`, preço médio 100,40, rótulo 1 sem nova execução. | AD04 |
| AD05 | P2 | Confirmado | `refresh_exit_summary` agrupa as execuções por instante e só o PRIMEIRO instante decide a ambiguidade: razões diferentes nesse instante → `SL|TP` (censurado); um empate posterior não apaga um primeiro toque comprovado → `TP+SL`, rótulo 1. | AD05 ×2 |

## Estado dos achados anteriores marcados como parciais

- **AC01**: completado pelo AD01 e AD03.
- **AC02**: completado pelo AD02.
- **AC04**: completado pelo AD04.
- **AC05**: completado pelo AD05.

## Fora do âmbito desta versão

- Ensaios integrados com TWS paper (estados atrasados reais, `orderId` reutilizado entre sessões) e a ligação
  efetiva do conector ao ChatGPT: só no computador do utilizador.
- Serviço contínuo de supervisão por API: fora, como nas versões anteriores.
