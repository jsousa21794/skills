# Resposta à revisão da versão 1.0.5

Documento revisto: *Verificação da versão 1.0.5* (6 de outubro de 2026).
Versão corrigida: **1.0.6** (branch `claude/trading-system-ollama-ibkr-3qzwm3`, release `trader-v1.0.6`).

## Veracidade da revisão

Os 7 achados (W01–W07) e os 11 ensaios (B01–B11) foram verificados contra o código da tag `trader-v1.0.5`.
**Os 7 achados confirmam-se**, incluindo a causa comum dos dois P0 (um pedido de cancelamento tratado como
cancelamento confirmado). A observação sobre a V10 também está certa: o `timestamp` de `last_price` era extraído
mas não usado; passa a ser usado. Os 11 ensaios estão agora no repositório com as asserções invertidas
(`tests/test_review_105.py`, 17 testes). A suite completa passa (173 testes).

Esta versão segue o critério pedido: invariância em todos os estados relevantes, não só a inversão de cada
reprodução. Nenhuma saída concorrente sem reconciliação (verificada três vezes: antes de cancelar, depois da
confirmação terminal e imediatamente antes do envio); nenhum evento externo altera um trade próprio (identidade
completa em fills E em estados de ordem); nenhuma migração duplica a mesma posição (deduplicação por identidade
das ordens e da entrada, numa transação).

## Estado por achado

| ID | Prioridade | Veredicto | Correção na 1.0.6 | Teste |
|----|-----------|-----------|-------------------|-------|
| W01 | P0 | Confirmado | `is_live_order` (não terminal, via `Trade.isDone()` do ib_async) substitui a lista de estados ativos em `external_exit_orders`; `close_position` cancela, espera o estado terminal, **reconcilia de novo** todas as saídas (manuais e do bot) até três vezes e volta a verificar imediatamente antes do `placeOrder`; qualquer saída viva bloqueia o envio. Com `cancel_external_exits_on_close`, as manuais são canceladas e esperadas até ao estado terminal. | B01, B02, W01 |
| W02 | P0 | Confirmado | Cobertura **confirmada** (estados ativos) separada de ordens **em transição** (`PendingCancel`): `ensure_protection` espera pelas transitórias antes de recalcular o residual e, se não terminarem, adia a reparação sem criar cobertura nova; órfãos em transição não são substituídos. | B03 |
| W03 | P1 | Confirmado | `_on_order_status` valida conta, `clientId`, `orderRef`, contrato e pertença a um grupo do bot antes de tocar em histórico, reservas ou fechos (`order_is_ours`); `set_perm_id` escreve por grupo e corrige associações anteriores quando o estado é validado como nosso; execuções registadas sem alocação são reprocessadas (`_reconcile_unallocated_fills`) assim que a identidade fica conhecida. | B04, B05, W03 |
| W04 | P1 | Confirmado | A importação do legado vai para a base **efetivamente usada** (a da conta quando a base por modo já está atribuída); `merge_open_state_from` corre numa única transação com deduplicação pela identidade das ordens e da entrada (e das proteções); o estado da migração fica em `kv` e uma falha bloqueia entradas (`migration_failed`) até ser resolvida. A reconciliação ao ligar continua a confrontar as quantidades com a corretora. | B07, B08, W04 |
| W05 | P1 | Confirmado | Os gates são guardados e lidos pela chave da experiência (modelo + versão do prompt) em `_apply_gates`, `_gates_for` e `_load_gate_state`; a mudança de prompt recarrega o estado; chaves antigas só por modelo não são lidas (nunca atribuem validade a um prompt novo). | B06 |
| W06 | P1 | Confirmado | Operações executadas: rótulo pelo **ledger** (saída por TP = 1, por stop = 0; outras saídas censuradas com `ledger:<razão>`); enquanto abertas, níveis **absolutos** da ordem (`stop_price`/`tp_price`) e percurso a partir do minuto seguinte ao da entrada (a vela da entrada não é inventada); dentro do horizonte da operação espera-se pelo desfecho real. `first_touch_absolute` partilha as regras de abertura do replay. | B09, B10, W06 ×2 |
| W07 | P1 | Confirmado | Numa posição mista, a cobertura da parcela própria conta só os stops do bot (`ours_only`); um stop manual cobre a parcela manual. `ensure_protection(max_qty)` e o supervisor usam a mesma base. | B11, W07 |

## Nota sobre a V10

Corrigido o que a revisão apontou: a idade da cotação (`last_ts`) é agora verificada no pré-envio e no token de
autorização; uma cotação mais velha do que `max_bar_age_seconds` + 60 s recusa a ordem.

## Fora do âmbito desta versão

- Ensaios integrados com TWS paper (atrasos, fills durante cancelamento, reinícios, ordens manuais): só no
  computador do utilizador.
- Replay sem filtros externos nem lições, e recuperação de execuções offline limitada às que a sessão da TWS
  conhece: limitações já declaradas.
