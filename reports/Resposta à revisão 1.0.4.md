# Resposta à revisão da versão 1.0.4

Documento revisto: *Verificação da versão 1.0.4* (6 de outubro de 2026).
Versão corrigida: **1.0.5** (branch `claude/trading-system-ollama-ibkr-3qzwm3`, release `trader-v1.0.5`).

## Veracidade da revisão

Os 11 achados (V01–V11) e os 13 ensaios (A01–A13) foram verificados contra o código da tag `trader-v1.0.4`.
**Os 11 achados confirmam-se**; os 13 ensaios reproduziam de facto os comportamentos descritos. Todos foram
corrigidos e os ensaios passaram a fazer parte do repositório com as asserções invertidas
(`tests/test_review_104.py`, 17 testes). A suite completa passa (156 testes).

**Uma correção à revisão.** A nota "Configuração a conferir" afirma que o requisito final era 15% de perda
diária. O pedido do utilizador foi explícito: "Se perda mais de 20% num dia, para automaticamente". O valor por
defeito mantém-se em 20% (`daily_loss_limit_pct=0.20`), ajustável em Configurações.

Continua sem haver ensaio com TWS, ordens reais ou inferência Ollama nesta verificação. A prioridade 4 da
revisão (cenários completos em TWS paper) só pode ser feita no computador do utilizador.

## Estado por achado

| ID | Prioridade | Veredicto | Correção na 1.0.5 | Teste |
|----|-----------|-----------|-------------------|-------|
| V01 | P0 | Confirmado | `close_position` reconcilia primeiro TODAS as saídas ativas do contrato/conta. Com uma saída MANUAL (não é do bot) o fecho é **bloqueado** e assinalado como conflito (log crítico com os IDs); nenhuma venda concorrente é enviada. A nova opção `cancel_external_exits_on_close` (predefinição desligada, exposta em Configurações) é a única forma de o bot cancelar ordens manuais antes do fecho. | A02, V01 |
| V02 | P1 | Confirmado | `_restore_pending_orders` classifica cada ordem viva pela identidade persistida (`order_history` + `order_leg`): pernas TP/SL autónomas são proteção e ficam; só pernas PARENT são entradas/fechos pendentes; ordens do bot sem registo continuam a ser canceladas. O supervisor também repõe pares incompletos (`has_orphan_children`), não só a quantidade de stops. | A01, V02 |
| V03 | P1 | Confirmado | `clientId` presente e diferente do do bot é sempre rejeitado (o cliente 0 é uma identidade, não ausência dela); `orderRef` diferente é rejeitado; o `permId` é gravado em `order_history` assim que chega no estado da ordem e um fill com `permId` conhecido e diferente é rejeitado. | A03 |
| V04 | P1 | Confirmado | `_migrate` faz backfill idempotente de `order_history` para os grupos herdados; `update_group_orders` insere primeiro o ID antigo quando falta no histórico, pelo que nunca se perde a identidade de um filho substituído. | A04 |
| V05 | P1 | Confirmado | Quando `trader.sqlite3` coexiste com a base da 1.0.3, `merge_open_state_from` importa para a base atual só o estado operacional: proteções ainda ativas (kill-switch/pausas) e trades abertos com grupos e pernas (IDs remapeados). O histórico fechado fica no ficheiro arquivado; paper e live não são fundidos. | A05 |
| V06 | P1 | Confirmado | A base por modo fica marcada com `bound_account` no momento da cópia (atribuição durável); uma base com registos de outra conta nunca é copiada (`accounts_in_records`). Um segundo arranque com outra conta começa com base vazia. | A12 |
| V07 | P1 | Confirmado | Quantidade própria calculada a partir dos trades abertos (`_own_qty`). Posição MISTA (corretora > própria) é tratada como externa: sinais bloqueados, proteção e fecho limitados à quantidade própria (`max_qty`), aviso crítico uma vez. Com `manage_external_positions` a posição inteira é adotada. | A13 |
| V08 | P1 | Confirmado | `_set_calibrator` reassocia a retrospetiva sempre que o calibrador muda; `_apply_gates` só aceita gates cuja experiência é (modelo, versão do prompt) atual; relatórios e gates filtram por prompt além do modelo, com a curva de equity reconstruída a partir dos trades dessa experiência; no A/B o estado de validação e o multiplicador aplicados são os do modelo que respondeu (`_gates_for`). | A06, A11, V08 |
| V09 | P1 | Confirmado | O percurso do rótulo começa no mais tardio entre o fecho da vela e o fim da inferência; para decisões executadas usa o preço e o instante da ENTRADA real; `first_touch_label` aplica as regras de abertura do replay (gap pelo stop/TP antes da ambiguidade intrabar). Nova coluna `label_source` distingue `bracket:entrada`, `bracket:vela`, `direcional` e `censurado`. | A09, A10 |
| V10 | P1 | Confirmado | O token de autorização revalida, depois de cada `await` e imediatamente antes do envio, a idade da decisão (relógio de parede e prazo monotónico) e a cotação face ao limite da entrada; o `timestamp` de `last_price` passa a ser consumido. | A07, V10 |
| V11 | P2 | Confirmado | `settings_changed` reconstrói o tracker de persistência e a fonte de eventos com os novos valores. | A08 |

## Dos 18 grupos da revisão anterior que ficaram "parciais"

N05, N06, N07, N08, N09, N10, N11 e N15 são tratados pelos achados acima (ver "Rastreabilidade" da revisão).
N12 (replay sem filtros externos nem lições) mantém-se como limitação declarada: resultados, VIX, notícias e
lições dependem de rede e de datas e ficam fora do replay por desenho.

## Fora do âmbito desta versão

- Ensaios com TWS paper (parciais, rejeições, cancelamentos atrasados, reinício com ordens abertas,
  reconciliação com o extrato): só no computador do utilizador.
- O `permId` só é conhecido depois do primeiro estado da ordem; um fill que chegue antes desse estado é
  validado por conta, contrato, símbolo, `clientId` e `orderRef`.
