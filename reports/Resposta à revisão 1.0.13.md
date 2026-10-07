# Resposta à revisão da versão 1.0.13

Documento revisto: *Revisão técnica do Ollama IBKR Trader — Versão 1.0.13* (7 de outubro de 2026).
Versão corrigida: **1.0.14** (branch `claude/trading-system-ollama-ibkr-3qzwm3`, release `trader-v1.0.14`).

## Veracidade da revisão

Os 4 achados (AE01–AE04) foram verificados contra o código da tag `trader-v1.0.13`. **Os 4 achados confirmam-se.**
Os ensaios do auditor estão no repositório com as asserções invertidas (`tests/test_review_113.py`, 5 testes: os 4
originais mais o controlo de que um cancelamento com a identidade correta continua a libertar a entrada e a reserva).
Os ensaios AE03 e AE04 reproduzem o estado deixado pela 1.0.12 sem checkouts anteriores (cobertura ampla por grupo
em `exit_coverage`; razões preenchidas com resumo e rótulo desatualizados) e incluem a idempotência. A suite completa
passa (260 testes). O ensaio HTTP/MCP e o smoke da interface foram repetidos. Continua sem haver ensaio com TWS,
ordens reais ou inferência Ollama.

## Estado por achado

| ID | Prioridade | Veredicto | Correção na 1.0.14 | Teste |
|----|-----------|-----------|--------------------|-------|
| AE01 | P1 | Confirmado | `_on_order_status` termina IMEDIATAMENTE quando a identidade foi rejeitada (existem grupos do bot com o `orderId`, nenhum validado pelo `permId` do estado): nenhum estado terminal toca em `_pending_entries`, `_pending_close`, reservas ou trades. A entrada viva (permId 999) mantém-se com a reserva intacta perante um `Cancelled` com permId 888; o cancelamento com permId 999 continua a libertar a entrada, a reserva e a marcar o trade como `CANCELLED`. | AE01 ×2 |
| AE02 | P1 | Confirmado | `trades_for_order` lê `order_coverage` apenas para os grupos previamente validados pelo `permId` (`order_id = ? AND group_id IN (validados)`); a correspondência exata feita antes da consulta deixa de ser anulada pela união de coberturas. A execução antiga (888) cobre só o trade antigo (100) e a nova (999) só o novo (20). | AE02 |
| AE03 | P1 | Confirmado | Migração explícita das linhas legadas de `exit_coverage` (1.0.12): cada (grupo, trade) converte-se em cobertura por ordem apenas para as ordens de saída do grupo colocadas DEPOIS de o trade existir (instante do grupo do trade, independente do instante de execução da entrada); ordens anteriores nunca passam a cobrir trades novos; linhas do próprio grupo do trade são redundantes; as linhas legadas são removidas. Idempotente. Na base gerada com o estado da 1.0.12 o stop antigo vai todo para o trade antigo. | AE03 |
| AE04 | P1 | Confirmado | Migração identificada por VERSÃO (`Database.SUMMARY_MIGRATION`, marcador `migration:summaries` em kv): quando o marcador não corresponde, todos os trades com alocações de saída têm o resumo recalculado (`refresh_exit_summary`) e o rótulo final da decisão reavaliado pela regra única do ledger, com `labels_changed_at` quando algum muda; depois grava o marcador. Já não depende de haver razões por preencher: o caminho 1.0.11 → 1.0.12 → 1.0.14 dá o mesmo resultado que a migração direta (`TP+SL`, 100,40, rótulo 1). | AE04 |

## Estado dos achados anteriores marcados como parciais

- **AD01**: completado pelo AE01.
- **AD03**: completado pelo AE02 e AE03.
- **AD04**: completado pelo AE04.

## Fora do âmbito desta versão

- Ensaios integrados com TWS paper (estados atrasados reais, `orderId` reutilizado entre sessões) e a ligação
  efetiva do conector ao ChatGPT: só no computador do utilizador.
- Serviço contínuo de supervisão por API: fora, como nas versões anteriores.
