# Ollama × IBKR Trader — Guia passo a passo (instalação, ligações e utilização)

Versão de referência: **1.0.17** (Windows). O programa é um único executável; não precisa de Python nem de
instalação. Precisa de três coisas externas: a **TWS** (ou IB Gateway) da Interactive Brokers com a API ligada, o
**Ollama** com um modelo descarregado e, opcionalmente, um **túnel HTTPS** para ligar o ChatGPT por MCP.

---

## Parte 1 — Instalar o programa

1. **Juntar as três partes do executável** (descarregadas da conversa) numa pasta, por exemplo `C:\Trader\`.
   Abre a *Linha de comandos* nessa pasta e executa:
   ```
   copy /b OllamaIBKRTrader-1.0.17.part0+OllamaIBKRTrader-1.0.17.part1+OllamaIBKRTrader-1.0.17.part2 OllamaIBKRTrader.exe
   ```
   Em alternativa, descarrega o ficheiro inteiro da release:
   https://github.com/jsousa21794/skills/releases/tag/trader-v1.0.17 → `OllamaIBKRTrader-windows-x64.exe`.
2. **Verificar a integridade** (opcional, recomendado):
   ```
   certutil -hashfile OllamaIBKRTrader.exe SHA256
   ```
   O resultado tem de ser `12c3b54ddb7b83ffa8e91d54a0feacbff05111ab512dd26091f01bfd6db74853`.
3. **Primeira execução**: duplo clique em `OllamaIBKRTrader.exe`. O Windows SmartScreen pode avisar (executável sem
   assinatura): *Mais informações* → *Executar mesmo assim*. Se o antivírus bloquear, adiciona a pasta às exceções.
4. **Onde ficam os dados** (nunca dentro da pasta do exe): `C:\Users\<o teu utilizador>\.ollamaibkrtrader\`
   - `config.json` — todas as opções (também editável à mão com o programa fechado);
   - `trader.log` — registo completo (é isto que deves enviar quando algo correr mal);
   - `trader_live_<conta>.sqlite3` / `trader_paper_<conta>.sqlite3` — base de dados por conta (decisões, ordens,
     trades, execuções, lições, calibração). **Faz cópia desta pasta** antes de atualizar de versão.
5. **Atualizar de versão**: fecha o programa, substitui o `.exe` pelo novo e abre. A base de dados é migrada
   automaticamente (as migrações são idempotentes e identificadas por versão). Não apagues a pasta de dados.

---

## Parte 2 — Interactive Brokers: ativar a API ("ir buscar o API")

A IBKR não usa chaves de API: o programa liga-se por socket à TWS ou ao IB Gateway que está a correr no teu PC com a
tua sessão iniciada. O que tens de fazer é **autorizar essa ligação**.

### 2.1 Instalar e iniciar sessão
1. Descarrega a **Trader Workstation (TWS)** ou o **IB Gateway** (mais leve, sem gráficos) em interactivebrokers.com
   → *Trading* → *Platforms*. A versão *Latest* ou *Stable* servem.
2. Inicia sessão com a tua conta. Para começar usa a **conta Paper** (simulada): no ecrã de login escolhe
   *Paper Trading*. A conta paper tem um utilizador próprio (cria-o em *Account Management* → *Settings* →
   *Paper Trading Account* se ainda não tiveres).

### 2.2 Ativar a API na TWS
1. Na TWS: *File* → *Global Configuration* → *API* → *Settings*.
2. Marca **Enable ActiveX and Socket Clients**.
3. **Desmarca** *Read-Only API* (senão o programa não consegue enviar ordens).
4. Confirma a **porta**: `7496` para conta real, `7497` para paper (no IB Gateway: `4001` real, `4002` paper).
   O programa usa 7496/7497 por defeito; se usares Gateway muda em *Configurações → Ligação à IBKR*.
5. Em **Trusted IPs** adiciona `127.0.0.1` (o programa corre no mesmo PC). Se não adicionares, a TWS mostra um
   pop-up a pedir autorização em cada ligação.
6. Opcional mas recomendado: *Master API client ID* em branco; **Download open orders on connection** marcado
   (para o programa ver ordens que ficaram abertas de sessões anteriores).
7. *File* → *Global Configuration* → *Lock and Exit*: define **Auto restart** para a TWS reiniciar sozinha de
   madrugada em vez de encerrar (a TWS fecha diariamente por defeito).
8. Clica *OK*. A TWS tem de ficar **aberta e com sessão iniciada** enquanto o programa corre.

### 2.3 Dados de mercado
- Sem subscrição paga, a IBKR fornece dados **atrasados 15 min**. O programa vem configurado para isso
  (*Configurações → Apresentação → Dados de mercado → Atrasados 15 min*). Funciona, mas as decisões baseiam-se em
  velas atrasadas.
- Para tempo real, subscreve em *Account Management → Settings → Market Data Subscriptions* (para ações dos EUA
  normalmente "US Securities Snapshot and Futures Value Bundle" + "NASDAQ/NYSE") e muda a opção para
  *Tempo real (subscrição)*. A conta paper partilha as subscrições da conta real.

### 2.4 Regras da corretora que o programa respeita
- **PDT** (Pattern Day Trader): contas com menos de 25 000 USD só podem fazer 3 day trades em 5 dias úteis. O
  programa conta e bloqueia a quarta (opção `pdt_guard_enabled` no `config.json`, ligada por defeito).
- O programa só toca em ordens com a etiqueta `OllamaIBKRTrader` e nunca gere posições que não abriu (a menos que
  ativares *Adotar posições não abertas pelo bot*).

---

## Parte 3 — Ollama: o modelo local

1. Instala o **Ollama** para Windows em https://ollama.com/download e abre-o (fica a correr em segundo plano em
   `http://localhost:11434`).
2. Descarrega um modelo. Na *Linha de comandos*:
   ```
   ollama pull llama3
   ```
   Alternativas que funcionam bem com saída JSON: `qwen2.5:7b`, `llama3.1:8b`, `mistral`. Com 8 GB de RAM usa
   modelos de 7–8 B; com 16 GB podes usar `qwen2.5:14b`.
3. Confirma que responde:
   ```
   curl http://localhost:11434/api/tags
   ```
   Deve listar o modelo. O programa lê esta lista para preencher o menu *Modelo Ollama*.
4. No programa, *Configurações → Conta e modelo → Modelo Ollama*: clica **↻** para atualizar a lista e escolhe o
   modelo. A mudança é imediata e reinicia a calibração desse modelo (cada modelo tem a sua).

Dicas: `Consultar o modelo a cada (min)` = 15 por defeito (não vale a pena menos: as velas são de 5 min);
`Amostras por decisão` = 5 (o modelo é perguntado 5 vezes e só se avança com acordo ≥ 60 %); *Duas etapas* ligado
(raciocínio livre e depois JSON com esquema) dá respostas mais estáveis; *Ocultar ticker e níveis* evita que o
modelo "reconheça" a ação e invente.

---

## Parte 4 — APIs opcionais de dados (resultados, notícias, VIX)

Nada disto é obrigatório. Por defeito o programa usa **yfinance** (sem chave) para datas de resultados, VIX e
notícias; se falhar, continua sem esses filtros (*fail-soft*) e regista no log.

- **Finnhub** (gratuito, mais fiável para resultados e notícias):
  1. Cria conta em https://finnhub.io → *Dashboard* → copia a *API Key*.
  2. Fecha o programa e edita `config.json`: `"news_source": "finnhub"`, `"finnhub_api_key": "a_tua_chave"`.
- **Sentimento FinBERT** (`sentiment_enabled`) e **previsão de volatilidade Chronos** (`volmodel_enabled`) são
  módulos opcionais que exigem bibliotecas pesadas; **não estão incluídos no executável**. Deixa-os desligados.

---

## Parte 5 — Primeira configuração dentro do programa

Abre o programa e vai ao separador **Configurações** (ou botão ⚙ na barra lateral). Tudo o que é opção está aqui.

1. **Conta e modelo**
   - *Conta*: **Paper** para começar. *Real* pede confirmação (escrever `REAL`) uma única vez e fica guardada.
   - *Modelo Ollama*: escolhe o modelo descarregado.
   - *Ativos negociados*: tickers separados por vírgula (ex.: `AAPL, MSFT, NVDA`) e **OK**. Ações dos EUA via
     SMART em USD.
2. **Ligação à IBKR**: host `127.0.0.1`, portas 7496/7497 (ou 4001/4002 no Gateway), *Client ID* 17 (qualquer número
   livre; não uses 0). Deixa *Adotar posições não abertas pelo bot* **desligado** a menos que queiras que o programa
   proteja e gira posições que abriste à mão.
3. **Trading e risco** (os valores que pediste já são os de defeito):
   - *Risco por operação*: até 10 % do equity (limitado pelos fundos disponíveis);
   - *Kill-switch*: −20 % no dia → o ciclo para sozinho e fica parado até ao dia seguinte;
   - *Entradas ilimitadas enquanto o dia está em lucro*: ligado;
   - *Stop = k × ATR* (2,0) e *Take-profit = R × stop* (2,0): define a geometria do bracket;
   - *Sinal tem de repetir-se N consultas*: 2 (evita entradas por um único palpite do modelo).
4. **Custos**: comissão por ação 0,005 USD, mínimo 1 USD (tarifário IBKR Pro). O programa só entra quando o ganho
   líquido no TP é ≥ 3× o custo ida-e-volta e o custo ≤ 20 % do ganho bruto.
5. **Apresentação**: moeda em que vês os valores (a conta pode ser EUR; a taxa vem da IBKR), dados de mercado,
   janela sempre visível.
6. Clica **💾 Guardar e aplicar**. Alterações à ligação IBKR reconectam; o servidor MCP só muda ao reiniciar.

---

## Parte 6 — Ligar o ChatGPT por MCP ("instalar MCPs")

O programa inclui um servidor **MCP** (Model Context Protocol) local, desligado por defeito. Dá ao ChatGPT um conjunto
fechado de ferramentas: ver estado, posições (ledger vs IBKR), ordens, log, decisões, comparar ledger com a corretora,
correr diagnósticos e **um só comando**: pausar novas entradas. Não há ferramentas para enviar/cancelar ordens,
retomar entradas ou mudar risco; retomar faz-se na barra lateral.

### 6.1 Ativar o servidor
1. *Configurações → Integração ChatGPT (MCP)*: liga **Ativar servidor MCP local**, endereço `127.0.0.1`, porta
   `8765`. **Guardar e aplicar** e **reinicia o programa**.
2. Em *Ligação do ChatGPT* aparece o endereço do conector, com o **token** gerado automaticamente:
   `http://127.0.0.1:8765/t/<token>/mcp`. O token fica em `config.json` (`mcp_token`). Trata-o como uma senha.

### 6.2 Criar um túnel HTTPS (o ChatGPT só alcança endereços públicos HTTPS)
Opção A — **cloudflared** (gratuito, sem conta para URLs temporárias):
1. Descarrega `cloudflared-windows-amd64.exe` em https://github.com/cloudflare/cloudflared/releases e renomeia para
   `cloudflared.exe`.
2. Executa:
   ```
   cloudflared tunnel --url http://127.0.0.1:8765
   ```
   Aparece uma URL do tipo `https://xxxx-yyyy.trycloudflare.com`. Deixa a janela aberta.

Opção B — **ngrok** (precisa de conta gratuita): `ngrok config add-authtoken <token>` e depois `ngrok http 8765`.

A URL temporária muda sempre que reinicias o túnel. Para uma URL fixa usa um túnel nomeado do Cloudflare (conta
gratuita, domínio próprio) ou um domínio reservado do ngrok (plano pago).

3. Cola a URL pública em *Configurações → Integração ChatGPT (MCP) → URL pública do túnel* e guarda: o endereço do
   conector passa a mostrar `https://<túnel>/t/<token>/mcp`.

### 6.3 Criar o conector no ChatGPT
1. No ChatGPT (web ou app): **Definições → Conectores → Criar** (nalgumas contas: *Apps e conectores* → *Avançado* →
   *Modo de programador* tem de estar ativo para conectores MCP personalizados).
2. Nome: `Ollama IBKR Trader`. **URL do servidor MCP**: o endereço completo mostrado no programa
   (`https://<túnel>/t/<token>/mcp`). **Autenticação: nenhuma** (o token vai no caminho e é validado pelo servidor;
   sem token válido a resposta é 401).
3. Cria e, numa conversa nova, ativa o conector. Pede, por exemplo: *"Chama get_status e diz-me se há
   discrepâncias"* ou *"Compara o ledger com a corretora"*.

### 6.4 Como o ChatGPT pausa as entradas (e porque pede o contexto)
- `get_status` devolve `account` (mascarada, ex. `U1***67`), `mode` e um **`context_id`** opaco.
- `pause_new_entries` exige **conta, modo, razão e esse `context_id`**. Se a conta, o modo, a geração do ciclo ou a
  base de dados mudarem entre a consulta e o comando, a pausa é recusada com a indicação para repetir `get_status`.
- A pausa é persistente e idempotente, não cancela entradas já enviadas nem remove stops. **Retomar** só na barra
  lateral do programa (▶ *Retomar novas entradas*).
- Todas as chamadas ficam registadas (ferramenta `get_remote_commands` e tabela `remote_commands`).

### 6.5 Segurança
- Nunca partilhes a URL com o token. Se suspeitares que fugiu, apaga `mcp_token` do `config.json` com o programa
  fechado: é gerado um novo ao arrancar (e tens de atualizar o conector).
- Fecha o túnel quando não estiveres a usar o ChatGPT. O servidor escuta só em `127.0.0.1`; sem túnel ninguém de fora
  chega lá.

---

## Parte 7 — Usar o programa no dia a dia

### 7.1 Arranque diário
1. Abre a TWS e inicia sessão (ou confirma que o auto-restart a manteve ligada).
2. Confirma que o Ollama está a correr (ícone na barra de tarefas) e, se usares o ChatGPT, arranca o túnel.
3. Abre `OllamaIBKRTrader.exe`. Na barra lateral vês: estado da IBKR, Ollama, modo (PAPER / CONTA REAL · porta),
   equity, P&L do dia.
4. Ao ligar, o programa **reconcilia** antes de qualquer decisão: importa execuções que ocorreram enquanto estava
   desligado, restaura entradas/fechos pendentes, cancela saídas órfãs com confirmação, verifica a cobertura das
   posições e classifica cada ativo (próprio, misto, externo, invertido). Enquanto `Reconciliado` não aparecer no
   separador *Risco*, nenhuma ordem nova sai.
5. Clica **▶ Iniciar trading**. Em conta real a primeira vez pede que escrevas `REAL`.

### 7.2 Separadores
- **Visão geral**: valor da carteira (48 h), posições com P&L, última proposta do modelo.
- **Decisões**: cada consulta ao modelo (ação, confiança, acordo, probabilidade calibrada) e o que a camada de risco
  fez com ela: `executado`, ou o motivo exato do bloqueio (custo, horário, PDT, kill-switch, discrepância, pausa…).
- **Risco**: *Proteções e kill-switch* (pausas ativas e até quando), *Gates estatísticos* (quantas decisões já
  avaliadas, se o risco validado está ativo), *Calibração*, *Lições ativas*, estado da reconciliação, posições
  externas, discrepâncias e a pausa remota.
- **Consola**: log em tempo real com categorias (ordem, decisão, risco, erro).
- **Configurações**: tudo o que é opção (Parte 5 e 6).

### 7.3 Botões da barra lateral
- **▶ Iniciar trading / ■ Parar**: liga e desliga o ciclo de decisão. Parar **não** cancela stops nem fecha posições:
  a supervisão das posições existentes continua (cobertura, prazos de entradas, fechos pendentes).
- **🧠 Retrospetiva agora**: avalia decisões passadas (settlement), recalcula lições e calibração.
- **📊 Relatório estatístico**: Brier, expectancy, Monte Carlo, gates; mostra se o risco validado pode subir.
- **▶ Retomar novas entradas**: só aparece quando há pausa (local ou pedida pelo ChatGPT).

### 7.4 O que esperar nas primeiras semanas
- O programa começa **sem calibração** (heurística) e com risco por trade limitado pelo multiplicador de aprendizagem
  até os *gates estatísticos* passarem (precisa de dezenas de decisões avaliadas). Isto é intencional.
- Só decide em horário regular de NY (15:30–22:00 Lisboa, hora de verão), fora dos primeiros 15 e últimos 10 minutos.
- Cada entrada é um **bracket**: ordem limite marketable + take-profit limite + stop (OCA, GTC). Se a entrada não
  executar em 120 s é cancelada.

### 7.5 Avisos que exigem a tua atenção (separador Risco e Consola)
- **Posição externa / mista**: tens ações do mesmo ticker abertas à mão. O programa só gere a parte dele e bloqueia
  sinais nesse ativo. Ou fechas a tua parte na TWS, ou ligas *Adotar posições não abertas pelo bot*.
- **Discrepância / conflito**: a posição na corretora não bate certo com o registo do programa (redução ou inversão
  fora do bot, execução por identificar). O programa não envia ordens nesse ativo até as execuções explicarem a
  diferença. Confirma na TWS (*Account → Trade Log*) e, se foste tu que mexeste, aguarda a reconciliação.
- **Kill-switch**: −20 % no dia. Fica parado até ao próximo dia; não há botão para contornar.
- **Migração da base de dados por resolver**: ficheiro `migration_failed.flag` na pasta de dados; lê o log, faz
  cópia da pasta e apaga o flag só depois de perceber o problema.

### 7.6 Fecho do dia
Podes deixar o programa aberto (a TWS reinicia de madrugada e o programa reconecta sozinho) ou fechá-lo: as posições
ficam protegidas pelos stops GTC na corretora, e ao reabrir a reconciliação importa o que aconteceu entretanto.

---

## Parte 8 — Resolução de problemas rápida

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| "IBKR: desligado" | TWS fechada, API não ativada, porta errada, Trusted IP em falta | Parte 2.2; confirma a porta no rodapé da barra lateral |
| Pop-up na TWS a cada ligação | `127.0.0.1` não está em *Trusted IPs* | Adiciona e marca *Enable ActiveX and Socket Clients* |
| "Ollama: indisponível" | Ollama fechado ou URL errada | Abre o Ollama; `curl http://localhost:11434/api/tags` |
| Lista de modelos vazia | Nenhum modelo descarregado | `ollama pull llama3` e clica ↻ |
| Decisões sempre "não executado: custo" | Posição pequena vs comissão mínima | Sobe o equity disponível ou ajusta *Custos* |
| Nada acontece de manhã | Fora do horário regular / primeiros 15 min | Espera; vê *Decisões* |
| ChatGPT não liga ao conector | Túnel fechado, URL do túnel mudou, token diferente | Reinicia `cloudflared`, atualiza *URL pública* e o conector |
| `pause_new_entries` recusada | `context_id` antigo | Pede ao ChatGPT para chamar `get_status` e repetir |
| Dados "atrasados" no estado | Sem subscrição de dados | Normal; ver 2.3 |

Quando pedires ajuda, envia `trader.log` (pasta de dados) e indica a versão (título da janela).
