# Resposta à revisão da versão 1.0.9

Documento revisto: *Revisão técnica do Ollama IBKR Trader — Versão 1.0.9* (6 de outubro de 2026).
Versão corrigida: **1.0.10** (branch `claude/trading-system-ollama-ibkr-3qzwm3`, release `trader-v1.0.10`).

## Veracidade da revisão

Os 6 achados (AA01–AA06) e os 6 ensaios de diagnóstico (T01–T06) foram verificados contra o código da tag
`trader-v1.0.9`. **Os 6 achados confirmam-se.** Os ensaios estão no repositório com as asserções invertidas
(`tests/test_review_109.py`, 10 testes: os 6 do auditor mais 4 casos adjacentes — redução ocorrida com o programa
desligado, recusa de proteção com sinal contrário, `context_id` sensível a modo/geração, várias execuções tardias).
O ensaio positivo HTTP/MCP foi repetido localmente com o novo parâmetro `context_id`. A suite completa passa
(223 testes). Continua sem haver ensaio com TWS, ordens reais ou inferência Ollama nesta verificação.

## Estado por achado

| ID | Prioridade | Veredicto | Correção na 1.0.10 | Teste |
|----|-----------|-----------|--------------------|-------|
| AA01 | P0 | Confirmado | A lógica por ativo da supervisão foi extraída para `_supervise_symbol` (inversão, quantidade própria zero, redução, excesso, lado errado, só depois cobertura) e o arranque (`_reconcile`) passa a usá-la em vez de chamar a reposição de proteção diretamente. No cenário do auditor (ledger +100, corretora −20, sem marcador anterior): zero ordens, vendas antigas canceladas, conflito persistido, discrepância e `_reconciled=True`. Defesa adicional: `_protect_if_naked` recusa qualquer posição com sinal contrário ao do ledger e regista o conflito. | T01, AA01 ×2 |
| AA02 | P1 | Confirmado | `trades_for_order` recebe o `permId` da execução e o grupo validado no callback: grupos cujo `permId` conhecido para essa ordem difere ficam excluídos; o grupo validado é sempre incluído. Com orderId reutilizado (permId 888 vs 999), a execução antiga corrige só o trade antigo (100) e a nova só o novo (20). | T02 |
| AA03 | P1 | Confirmado | `unallocated_fills` devolve o saldo `shares − Σ alocações` (`remaining`), incluindo execuções parcialmente alocadas; `_on_fill` de uma execução já registada processa apenas o saldo (idempotente, comissão proporcional); o resto da entrada (perna PARENT) dispara a recuperação do saldo pendente do ativo. A discrepância só sai quando o total está explicado. | T03 |
| AA04 | P1 | Confirmado | `_relabel_decision`: quando uma execução tardia muda o resultado de um trade encerrado e a decisão já estava avaliada, o rótulo final é recalculado pela mesma regra do ledger (`Settler._ledger_label`) na mesma operação lógica e `finalize_label` grava `labels_changed_at` (invalida a calibração). Avaliações posteriores mantêm o rótulo corrigido. | T04 |
| AA05 | P1 | Confirmado | `get_status` devolve `context_id` (hash opaco de conta completa, modo, geração e base de dados); `pause_new_entries` exige-o e recusa divergência com instrução para repetir `get_status`. Duas contas com a mesma máscara (`U1***67`) deixam de ser confundíveis. A revalidação exata no loop do motor (Z08) mantém-se. **Alteração de interface**: o comando tem agora 4 parâmetros (`account`, `mode`, `reason`, `context_id`). | T05, AA05 |
| AA06 | P1 | Confirmado | Nova coluna `reconciled_ts`; `close_trade_reconciled` grava-a; `record_late_exit_fill` define `exit_ts` como a execução comprovada mais recente que completa a saída (substitui a data da reconciliação; nunca recua perante uma execução mais antiga que chegue depois). `recent_stop_count` deixa de contar um stop de há dois dias como recente. | T06, AA06 |

## Estado dos achados anteriores marcados como parciais

- **Z01**: completado pelo AA01 (primeiro arranque perante inversão).
- **Z04**: completado pelo AA02 (identidade permanente) e AA03 (alocação parcial).
- **Z05**: completado pelo AA04 (rótulo) e AA06 (data da saída).
- **Z08**: completado pelo AA05 (contexto opaco).

## Fora do âmbito desta versão

- Ensaios integrados com TWS paper e a ligação efetiva do conector ao ChatGPT (túnel HTTPS): só no computador do
  utilizador. Nota: um conector já configurado continua a funcionar; só o comando de pausa passa a pedir o
  `context_id` que `get_status` devolve.
- Serviço contínuo de supervisão por API: fora, como nas versões anteriores.
