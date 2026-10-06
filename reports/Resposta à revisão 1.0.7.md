# Resposta à revisão da versão 1.0.7

Documento revisto: *Verificação da versão 1.0.7* (6 de outubro de 2026).
Versão corrigida: **1.0.8** (branch `claude/trading-system-ollama-ibkr-3qzwm3`, release `trader-v1.0.8`).

## Veracidade da revisão

Os 7 achados (Y01–Y07) e os 7 ensaios (D01–D07) foram verificados contra o código da tag `trader-v1.0.7`.
**Os 7 achados confirmam-se.** Os ensaios estão no repositório com as asserções invertidas
(`tests/test_review_107.py`, 9 testes). A integração com o ChatGPT, pedida pelo utilizador e descrita na
revisão, foi implementada nesta versão (ver secção própria) com 4 testes dedicados e um ensaio ponta-a-ponta
real (servidor HTTP + cliente MCP). A suite completa passa (197 testes). Continua sem haver ensaio com TWS,
ordens reais ou inferência Ollama nesta verificação.

## Estado por achado

| ID | Prioridade | Veredicto | Correção na 1.0.8 | Teste |
|----|-----------|-----------|-------------------|-------|
| Y01 | P0 | Confirmado | Reconciliação de arranque UNIFICADA com a periódica: um trade sem posição passa por `_reconcile_vanished_position`, que cancela as saídas próprias, espera a confirmação terminal e só depois fecha o vínculo; sem confirmação o conflito persiste (discrepância) e o supervisor repete. `_managed_symbols` inclui os ativos com ordens próprias vivas mesmo sem trade aberto. | D01, Y01 ×2 |
| Y02 | P1 | Confirmado | A rotina de limpeza distingue entrada pendente (trade sem execução com pai vivo) de posição desaparecida; `cancel_own_exits`/`resize_exits` recebem os IDs das entradas pendentes e nunca tocam no pai nem nos seus filhos (identificados pela perna persistida). Só o prazo da entrada a cancela. | D02 |
| Y03 | P1 | Confirmado | Discrepância operacional ≠ execução contabilística: a redução fora do bot regista um AJUSTE PROVISÓRIO (`ledger_adjustments`, sem preço comprovado, P&L indeterminado) que reduz a quantidade própria; a execução real, quando chega, consome o ajuste na mesma quantidade e é a única que entra em `exit_qty`/P&L. O bloqueio de decisões persiste enquanto houver ajustes por explicar. | D03, C05 (atualizado) |
| Y04 | P1 | Confirmado | `known_order_keys` inclui o `permId`; `permId` conhecido e diferente nunca é duplicado; coincidência parcial (identidade permanente desconhecida de um lado) é importada e contada como conflito, nunca descartada; os `permId` das pernas ativas são transportados na importação. | D04 |
| Y05 | P1 | Confirmado | `settled_decisions` devolve por defeito SÓ rótulos finais (`label_final=1`); calibração, lições, analytics e retrospetiva ficam sem provisórios; finalizar um rótulo grava `labels_changed_at` e `needs_refit` invalida modelos ajustados antes disso. | D05 |
| Y06 | P0 | Confirmado | O supervisor compara quantidades COM SINAL: uma inversão é um conflito explícito e persistente — todas as saídas próprias (de qualquer lado) são canceladas com confirmação terminal, o ledger recebe um ajuste provisório pela quantidade própria, nenhuma proteção nova é colocada e o ativo fica bloqueado até as execuções explicarem a diferença. `wrong_side_exit_quantity` deteta saídas do lado errado. | D06 |
| Y07 | P1 | Confirmado | A proteção agregada fica associada a TODOS os trades abertos do ativo; `_allocate_exit` reparte cada execução de saída pelos saldos (começando pelo trade da ordem), nunca deixa `exit_qty` ultrapassar `filled_qty` e regista como discrepância qualquer excesso. | D07 |

## Integração com o ChatGPT via MCP (implementada)

Seguindo a proposta da revisão: servidor MCP local (Streamable HTTP, SDK `mcp` 2.3) opcional, desligado por
defeito, com um conjunto FECHADO de ferramentas que passam pelo motor:

- consultas: `get_status`, `get_positions` (ledger vs IBKR), `get_open_orders`, `get_recent_log`,
  `get_recent_decisions`, `compare_ledger_with_broker`, `run_diagnostics` (sem enviar ordens),
  `get_remote_commands` (registo auditado);
- um único comando: `pause_new_entries(account, mode, reason)` — persistente, idempotente, confirmado só depois
  de aplicado e persistido; exige a conta (mascarada, como em `get_status`) e o modo atuais; não cancela entradas
  já enviadas, não remove proteções nem desliga a supervisão. Retomar só existe na interface local.
- autenticação por token (gerado uma vez) no caminho `/t/<token>/mcp` ou em `Authorization: Bearer`; sem token
  válido, 401. Cada chamada fica em `remote_commands` (autor, pedido, instante, conta mascarada, modo, resultado).
- a camada de risco, stops e bloqueios continuam locais e independentes da IA; os textos de logs/notícias/decisões
  são tratados como dados, não como instruções (declarado nas `instructions` do servidor).

Não incluído, conforme a sequência da revisão: serviço contínuo de supervisão (vigilância permanente com chamadas
à API), que exige instalação e manutenção próprias. A ligação ao ChatGPT exige um túnel HTTPS para o PC
(cloudflared/ngrok); instruções no README.

## Fora do âmbito desta versão

- Ensaios integrados com TWS paper (reinício, posição zero/invertida, entrada pendente, callback tardio, perda
  de rede, pedidos repetidos, dados desatualizados): só no computador do utilizador.
- Replay sem filtros externos nem lições, e recuperação de execuções offline limitada às que a sessão da TWS
  conhece: limitações já declaradas.
