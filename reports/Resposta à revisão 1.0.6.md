# Resposta à revisão da versão 1.0.6

Documento revisto: *Verificação da versão 1.0.6* (6 de outubro de 2026).
Versão corrigida: **1.0.7** (branch `claude/trading-system-ollama-ibkr-3qzwm3`, release `trader-v1.0.7`).

## Veracidade da revisão

Os 6 achados (X01–X06) e os 6 ensaios (C01–C06) foram verificados contra o código da tag `trader-v1.0.6`.
**Os 6 achados confirmam-se.** Os ensaios estão agora no repositório com as asserções invertidas
(`tests/test_review_106.py`, 11 testes, incluindo casos adjacentes). A suite completa passa
(184 testes). Continua sem haver ensaio com TWS, ordens reais ou inferência Ollama nesta verificação.

## Estado por achado

| ID | Prioridade | Veredicto | Correção na 1.0.7 | Teste |
|----|-----------|-----------|-------------------|-------|
| X01 | P0 | Confirmado | O supervisor compara, em cada ciclo, a posição líquida com a quantidade própria do ledger em AMBOS os sentidos. Posição menor do que a própria: o ledger regista uma saída `EXTERNAL` ao preço de mercado, o ativo entra em `_discrepancies` (bloqueia decisões), e `resize_exits` cancela todas as saídas do bot, espera a confirmação terminal e só então repõe um par com a quantidade existente (sem confirmação não coloca nada e repete no ciclo seguinte). `has_excess_exits` distingue cobertura suficiente de cobertura EXCESSIVA (maior saída do bot, stop ou TP, acima da posição ou da parcela própria). Posição desaparecida: as saídas vivas do bot são canceladas antes de o trade ser reconciliado. | C05, X01 ×3 |
| X02 | P1 | Confirmado | A identidade original de cada execução (conta, `clientId`, `permId`, `orderRef`, `conId`) fica persistida na tabela `fills`; `_reconcile_unallocated_fills` reprocessa com essa identidade e só as execuções da ordem cujo estado chegou. Um fill com `permId` incompatível continua incompatível; só um `permId` antes desconhecido pode passar a corresponder. | C01 |
| X03 | P1 | Confirmado | Ficheiro-marcador `migration_failed.flag` na pasta de dados, lido pelo motor independentemente da base aberta, além do `kv` em todas as bases envolvidas (por modo e destino efetivo). Cobre também a falha no percurso sem destino prévio. Resolução explícita: apagar o ficheiro e limpar o `kv`. | C02, X03 |
| X04 | P1 | Confirmado | Deduplicação por identidade completa: (símbolo, conta, contrato, id da ordem) com tolerância só para campos desconhecidos, e (símbolo, conta, direção, instante e quantidade da entrada). Ordens de contratos ou contas diferentes com o mesmo número são operações distintas e são importadas. | C03 |
| X05 | P1 | Confirmado | `_restore_pending_orders` e `has_pending_entry` usam `is_live_order` (não terminal): uma entrada em `PendingCancel` é restaurada com a reserva, marcada como cancelamento já pedido (o supervisor não repete) e só a confirmação terminal a liberta. | C04 |
| X06 | P1 | Confirmado | Objetivo único: resultado final da operação pelo ledger. No horizonte, uma operação ainda aberta recebe um rótulo PROVISÓRIO (`label_final=0`, pelos níveis absolutos); quando o trade fecha, `finalize_provisional` substitui-o pelo rótulo do ledger e guarda o provisório em `provisional_correct`. O rótulo final é o mesmo com o avaliador a correr aos 31 ou só aos 60 minutos. | C06, X06 |

## Dos 7 achados anteriores que ficaram "parciais"

W03, W04 e W06 são encerrados pelas entradas X02, X03/X04 e X06, respetivamente.

## Proposta de integração com ChatGPT / supervisão técnica

Não foi implementada nesta versão, de acordo com a própria sequência recomendada na revisão (corrigir e voltar a
verificar X01–X06 primeiro). Fica como decisão do utilizador. Se avançar, a ordem proposta (consultas e
diagnósticos com dados simulados → TWS paper → pausa de novas entradas em paper → ligação do plugin → serviço
contínuo) é compatível com a arquitetura atual: o motor já expõe estado, reconciliação, discrepâncias e pausas por
`UIBus`/`kv`, e a camada de risco é local e independente de qualquer modelo.

## Fora do âmbito desta versão

- Ensaios integrados com TWS paper (alteração externa de quantidade, cancelamento com fill tardio, reinício,
  falha de migração, reconciliação com extrato): só no computador do utilizador.
- Replay sem filtros externos nem lições, e recuperação de execuções offline limitada às que a sessão da TWS
  conhece: limitações já declaradas.
