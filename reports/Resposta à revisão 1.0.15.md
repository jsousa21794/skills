# Resposta à revisão da versão 1.0.15

Documento revisto: *Revisão técnica do Ollama IBKR Trader — Versão 1.0.15* (7 de outubro de 2026).
Versão corrigida: **1.0.16** (branch `claude/trading-system-ollama-ibkr-3qzwm3`, release `trader-v1.0.16`).

## Veracidade da revisão

Os 3 achados (AG01–AG03) foram verificados contra o código da tag `trader-v1.0.15`. **Os 3 achados confirmam-se.**
Os ensaios do auditor estão no repositório com as asserções invertidas (`tests/test_review_115.py`, 4 testes: os 3
originais mais a variante do fecho pendente). O estado gravado pela 1.0.14 (cobertura inferida sem ciclo de vida;
rótulo finalizado com o trade aberto) é reproduzido na própria base antes da atualização, sem checkouts anteriores, e
os testes verificam também a idempotência da reabertura. A suite completa passa (268 testes). O ensaio HTTP/MCP e o
smoke da interface foram repetidos. Continua sem haver ensaio com TWS, ordens reais ou inferência Ollama.

## Estado por achado

| ID | Prioridade | Veredicto | Correção na 1.0.16 | Teste |
|----|-----------|-----------|--------------------|-------|
| AG01 | P1 | Confirmado | Em `_on_fill`, a entrada pendente só é incrementada/libertada quando o grupo validado pela identidade da execução é o grupo da própria entrada (`_event_targets`, a mesma regra do AF01); a execução antiga (permId 888) completa o trade antigo e deixa intactas a entrada nova (permId 999) e a sua reserva de 2000; a execução da própria entrada continua a libertá-la. A mesma regra aplica-se ao ramo dos fechos pendentes (`pending_close.group_id`). | AG01 ×2 |
| AG02 | P1 | Confirmado | Reparação identificada por versão (`Database.COVERAGE_MIGRATION`, marcador `migration:coverage`): todas as linhas de `order_coverage` são reavaliadas pelo histórico — a ordem tem de ter sido colocada depois de o trade existir (sequência do histórico) e antes de ele fechar/ser reconciliado (instante); as relações inferidas pela 1.0.14 sem essa prova são removidas, as criadas diretamente pelas ordens são preservadas. No cenário do auditor a execução de 120 passa a 100/0/20 também na sequência 1.0.12 → 1.0.14 → 1.0.16. Sem prova a execução fica pendente com discrepância (invariante anterior). | AG02 |
| AG03 | P2 | Confirmado | Reparação explícita (sempre executada, só onde há inconsistência): decisões com `label_final=1` e fonte `ledger:*` cujo trade continua `OPEN` voltam a `label_final=0`, com o rótulo provisório anterior recuperado de `provisional_correct`, regressam a `provisional_decisions` e `labels_changed_at` invalida a calibração. O fecho posterior finaliza pela sequência real. | AG03 |

## Estado dos achados anteriores marcados como parciais

- **AF01**: completado pelo AG01.
- **AF02**: completado pelo AG02.
- **AF03**: completado pelo AG03.

## Fora do âmbito desta versão

- Ensaios integrados com TWS paper e a ligação efetiva do conector ao ChatGPT: só no computador do utilizador.
- Serviço contínuo de supervisão por API: fora, como nas versões anteriores.
