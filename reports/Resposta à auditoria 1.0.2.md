# Resposta à auditoria da versão 1.0.2

Release corrigida: **trader-v1.0.3**. Os 40 achados foram verificados contra o commit auditado (`c4987a9`). Confirmaram-se 38 como reais e 2 como parcialmente corretos (F20 e F31, em que o comportamento existia mas a política pretendida não estava definida). Cada correção tem um teste de regressão em `ollama-ibkr-trader/tests/test_audit.py` (31 cenários) ou em `tests/test_engine.py`; a suite completa tem 110 testes.

Não foram enviadas ordens à IBKR nem usada inferência real nesta verificação; os cenários usam clientes e respostas simulados, como na auditoria.

## P0

| # | Achado | Estado | Correção |
|---|---|---|---|
| F01 | Parar/mudar de conta não invalida a decisão em curso | Confirmado, corrigido | Contador de geração no motor; `stop_trading`, `set_mode`, `set_model`, `set_symbols`, desconexão e kill-switch incrementam-no; `_execute` e `_execute_entry` revalidam geração, `trading_enabled`, ligação, conta, fundos e gates imediatamente antes de qualquer ordem. `set_mode` espera pelo lock de decisão. |
| F02 | Fecho pode inverter a posição por corrida com o stop | Confirmado, corrigido | `close_position` cancela os filhos do bot, espera estados terminais (até 5 s), relê a posição, não envia nada se já fechou e envia só a quantidade remanescente; falhas devolvem `needs_protection` e o motor repõe cobertura. |
| F03 | Estado de fecho incorreto em parciais e cancelamentos | Confirmado, corrigido | `_pending_close` guarda ordem, quantidade e executado; só é libertado quando a quantidade está toda executada ou a ordem termina em Cancelled/Inactive (com reposição de cobertura). |
| F04 | Stop inadequado aceite como proteção | Confirmado, corrigido | `protective_coverage` soma apenas stops ativos (PreSubmitted/Submitted/PendingSubmit/ApiPending), do lado oposto à posição, na conta e contrato do bot; `has_protective_orders` exige cobertura ≥ posição. |
| F05 | Rejeição/perda de proteção sem recuperação | Confirmado, corrigido | Supervisor a cada ciclo (`_supervise`) repõe cobertura em falta; `_on_order_status` dispara `_reprotect` quando um filho TP/SL termina cancelado/rejeitado com posição aberta. |

## P1

| # | Achado | Estado | Correção |
|---|---|---|---|
| F06 | Execuções offline não reconciliadas | Confirmado, corrigido em parte | Ao ligar importam-se `ib.fills()` pelo mesmo percurso idempotente (`execId`) antes da reconciliação; órfãos sem execução conhecida continuam a ser fechados como `RECONCILED` sem P&L inventado, com aviso para verificar o extrato. |
| F07 | Proteções reconstruídas sem associação na DB | Confirmado, corrigido | `_protect_if_naked` persiste grupo (`tp/sl_order_id`) e cria/atualiza o trade com a quantidade e preço médio da corretora; os fills das saídas fecham o trade certo. |
| F08 | Sem isolamento por conta/contrato/ordens do bot | Confirmado, corrigido em parte | `ib_account` e `order_ref` em todas as ordens; posições, execuções e ordens filtradas por conta, `secType=STK` e `conId`; `close_position` só cancela ordens com o `orderRef` do bot; posições fora dos ativos não são geridas por omissão. Subcontas de advisor não foram testadas. |
| F09 | Benchmark excluído da proteção | Confirmado, corrigido | A reconciliação e o supervisor protegem todos os ativos geridos (configurados ou com trade aberto), incluindo o benchmark. |
| F10 | Frações truncadas | Confirmado, corrigido | `_order_qty` recusa quantidade inteira zero (sem ordem inválida) e avisa explicitamente do resíduo. |
| F11 | Fundos/vagas reutilizados no ciclo | Confirmado, corrigido | Estado relido por decisão; entradas pendentes reservam notional e contam para `max_open_positions`. |
| F12 | Preço/saldo/gates desatualizados na execução | Confirmado, corrigido | `decision_max_age_seconds` e revalidação de gates, equity e fundos em `_execute`; `now` é relido por ativo. |
| F13 | Slippage não limitado na entrada | Confirmado, corrigido | Entrada em limit marketable a `ref × (1 ± max_entry_slippage_pct)`; entradas não executadas são canceladas após `entry_timeout_seconds`. Os níveis TP/SL continuam fixados a partir do preço de referência (desvio máximo igual à tolerância). |
| F14 | Fundos negativos/desconhecidos não bloqueiam | Confirmado, corrigido | Fundos `None`, não finitos ou ≤ 0 recusam a entrada; sizer devolve quantidade zero. |
| F15 | Moeda da conta confundida | Confirmado, corrigido | Valores lidos por moeda, moeda base detetada, conversão para USD via `ExchangeRate`; interface mostra a moeda. |
| F16 | Paper e real partilham histórico | Confirmado, corrigido | `trader_live.sqlite3` / `trader_paper.sqlite3`; `set_mode` troca a base de dados e recarrega pausas, calibração e lições. |
| F17 | Drawdown intradiário perdido | Confirmado, corrigido | Pico-vale sobre todos os snapshots da janela. |
| F18 | Pausas e kill-switch não restaurados | Confirmado, corrigido | `RiskGate.restore` carrega pausas ativas e o kill-switch (persistido até ao fim do dia UTC). |
| F21 | Configuração sem validação | Confirmado, corrigido | Coerção por tipo, números finitos, intervalos e relações; avisos em `Settings.load_warnings`; gravação atómica. |
| F22 | Amostras falhadas inflacionam o consenso | Confirmado, corrigido | Acordo e margem sobre as amostras pedidas; quórum mínimo (`llm_min_valid_fraction`); modelo do ensemble sem respostas válidas → REVIEW. |
| F23 | Parser aceita negações e confiança não finita | Confirmado, corrigido | Ação só por correspondência exata; negações rejeitadas; NaN/Inf → `invalid_confidence`. |
| F24 | JSON pode inverter a análise | Confirmado, corrigido | Divergência entre etapas invalida a amostra (`stage_mismatch`). |
| F28 | Settlement fora do horizonte e instante desalinhado | Confirmado, corrigido | `market_ts` persistido e usado como base; `price_at` com limite de `settlement_max_gap_minutes`. |
| F29 | Probabilidade mede evento diferente do bracket | Confirmado, corrigido | Rótulo por primeiro toque (TP antes do stop) sobre o percurso de velas com os níveis `stop_pct/tp_pct` guardados por decisão; direcional só como fallback. |
| F30 | Calibração e gates partilhados entre modelos | Confirmado, corrigido | Platt e gates por modelo (`platt_model:v2:<modelo>`), ajuste só com decisões desse modelo. Prompt e A/B não segmentam ainda. |
| F32 | Backtest com política diferente | Confirmado, corrigido | `trader/policy.py` partilhado; o replay usa `RiskGate`, calibração, custos/EV, kill-switch, cooldown, fecho por sinal contrário e short. |
| F33 | Replay sem custos no P&L por trade | Confirmado, corrigido | Ledger único: comissões em `record_entry_fill/record_exit_fill`, slippage só no preço; equity = soma do P&L líquido dos trades. |
| F34 | Stops a preços impossíveis em gaps | Confirmado, corrigido | Execução à abertura quando a vela abre além do nível, com slippage adverso. |
| F36 | Rede síncrona no loop | Confirmado, corrigido | `EventData.prefetch` em executor no início do ciclo; gates leem só a cache (`cached_only`). |
| F38 | Comissão de fecho atribuída ao trade errado | Confirmado, corrigido | Tabela `fill_allocations`; o delta da comissão real é repartido pelos trades que a execução tocou. |

## P2

| # | Achado | Estado | Correção |
|---|---|---|---|
| F19 | Perdas consecutivas bloqueiam todos os dias | Confirmado, corrigido | Contagem só desde a última pausa desse tipo. |
| F20 | Lucro não liberta pausas | Parcial, corrigido | Política definida: em lucro no dia libertam-se as pausas de StoplossGuard e de perdas seguidas; a de drawdown multi-dia e o kill-switch mantêm-se. |
| F25 | Logprob da ação minoritária | Confirmado, corrigido | Logprob por amostra; agregado só das amostras da ação vencedora. |
| F26 | Variações de 5/30 min medem 25/150 min | Confirmado, corrigido | Horizontes convertidos em número de velas (`bar_minutes`). |
| F27 | Vela agregada incompleta no contexto | Confirmado, corrigido | `aggregate_bars(drop_incomplete=True)` só aceita o último bucket se a sua última vela for a última do intervalo. |
| F31 | Multiplicador de aprendizagem não aplicado | Parcial, corrigido | `learning_risk_multiplier` (defeito 1,0, conforme os 10% configurados) é agora aplicado de facto ao sizing e refletido no estado. |
| F35 | Relógio do replay mistura datas | Confirmado, corrigido | Decisões, snapshots e fills datados com o relógio da simulação; janela do relatório = intervalo do dataset; execução marcada só após abertura simulada. |
| F37 | Mínimo de notícias fabricado | Confirmado, corrigido | `sentiment_n` persistido e passado ao veto. |
| F39 | Build sem teste real do binário, sem lock | Confirmado, corrigido | `OLLAMA_TRADER_SMOKE` arranca GUI e motor e encerra; o workflow executa o binário em Windows, macOS (ecrã) e Linux (Xvfb) e exige "Motor iniciado"/"Motor de trading parado" no log; dependências fixadas por versão. |
| F40 | Versão e documentação contraditórias | Confirmado, corrigido | `__version__ = 1.0.3`; docstring de config e README atualizados. |

## O que fica por fazer (não bloqueante, assinalado)

- F06/F08: execuções de sessões anteriores ao dia (não carregadas pelo `ib_async`) e subcontas de advisor não foram testadas contra a corretora real; a reconciliação continua a não inventar P&L para órfãos.
- F13: os níveis TP/SL não são recalculados a partir do preço efetivo de execução (o desvio está limitado pela tolerância de 0,3%).
- F30: a calibração é segmentada por modelo, não por versão de prompt nem por grupo A/B.
- Nenhum destes pontos foi provocado na IBKR real; a primeira sessão em conta real deve ser acompanhada, com a consola visível.
