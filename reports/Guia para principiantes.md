# Ollama × IBKR Trader — Guia passo a passo para quem nunca fez nada disto

Versão do programa: **1.0.18** (Windows 10/11, 64 bits). Data de referência: outubro de 2026.

Este guia assume que **não sabes nada** sobre corretoras, APIs ou inteligência artificial. Cada passo diz onde
clicar e o que deves ver. Se já conheces alguma parte, salta-a. Os preços indicados são os que a Interactive
Brokers publicava à data; **confirma sempre no teu Portal do Cliente**, porque podem mudar.

---

## 0. Antes de começar

### 0.1 O que o programa faz (em 8 linhas)

1. Liga-se à tua conta na corretora **Interactive Brokers (IBKR)** através do programa dela, a **TWS**.
2. Recebe os preços das ações que escolheres (por exemplo AAPL, MSFT) em velas de 1 minuto.
3. De 15 em 15 minutos pergunta a um modelo de inteligência artificial **local** (o **Ollama**, a correr no teu PC,
   sem enviar nada para a internet) se vê um sinal de compra ou venda.
4. Uma **camada de risco escrita em código** decide se esse sinal pode ser executado: tamanho da posição, custos,
   horário, limites diários. O modelo só propõe; o código decide.
5. Se executar, envia uma ordem com **stop** (perda máxima) e **take-profit** (objetivo de ganho) já anexados.
6. Aprende com o resultado das decisões (base de dados local) e calibra a confiança do modelo ao longo do tempo.
7. Pode mostrar o estado ao **ChatGPT**, se quiseres, através de uma ligação chamada **MCP** (opcional).
8. Para sozinho se o dia chegar a **−20 %** (kill-switch).

O que **não** faz: não garante lucros, não funciona sem a TWS aberta, não negoceia fora do horário regular da bolsa
dos EUA, não toca em posições que abriste à mão (a menos que o autorizes) e não deixa o ChatGPT enviar ordens.

### 0.2 O que precisas

| Item | Obrigatório? | Custo aproximado |
|---|---|---|
| PC com Windows 10/11 64 bits, **16 GB de RAM** (8 GB é o mínimo), 20 GB livres em disco, internet estável | Sim | — |
| Conta na Interactive Brokers, **aprovada e com dinheiro** | Sim | Sem mensalidade de conta |
| Subscrição de dados de mercado em tempo real na IBKR | Sim, para o programa abrir posições (ver 2.4) | ~4,50 USD/mês (3 feeds a 1,50 USD) |
| Ollama (modelo de IA local) | Sim | Grátis |
| Programa `OllamaIBKRTrader.exe` | Sim | Grátis |
| Chave do Finnhub (dados de resultados e notícias) | Não | Grátis |
| Conta ChatGPT paga + túnel `cloudflared` | Não (só para a integração com o ChatGPT) | ChatGPT Plus ~20 USD/mês; cloudflared grátis |

Tempo total: cerca de **2 horas** de trabalho, mais **1 a 3 dias úteis** de espera pela aprovação da conta IBKR e
pelo depósito.

### 0.3 Palavras que vais encontrar

- **Corretora**: a empresa que guarda o teu dinheiro e executa as compras e vendas. Aqui é a IBKR.
- **TWS (Trader Workstation)**: o programa da IBKR que instalas no PC. O nosso programa fala com ele.
- **IB Gateway**: versão da TWS sem gráficos, mais leve. Faz o mesmo para o nosso programa.
- **API**: a "porta" por onde um programa fala com outro. No caso da IBKR não há chave nem senha: autorizas a
  ligação dentro da TWS.
- **Conta Paper**: conta de **simulação** com dinheiro fictício. Igual à real, mas sem risco. Começa sempre aqui.
- **Conta Real (live)**: a tua conta com dinheiro verdadeiro.
- **Ticker**: o código de uma ação na bolsa (Apple = `AAPL`, Microsoft = `MSFT`).
- **Equity (capital)**: o valor total da conta (dinheiro + posições).
- **Stop (stop-loss)**: ordem que fecha a posição automaticamente se o preço for contra ti até certo ponto.
- **Take-profit (TP)**: ordem que fecha a posição automaticamente quando atinge o objetivo de ganho.
- **Bracket**: a entrada + o stop + o take-profit, enviados juntos. É assim que o programa entra sempre.
- **ATR**: medida de quanto o preço costuma oscilar. O programa usa-a para calcular a distância do stop.
- **Short (venda a descoberto)**: vender primeiro para comprar mais barato depois. Está permitido por defeito.
- **PDT (Pattern Day Trader)**: regra dos EUA; contas abaixo de 25 000 USD só podem fazer 3 operações
  intradiárias em 5 dias úteis. O programa respeita-a sozinho.
- **Dados atrasados**: preços com 15 minutos de atraso (grátis). **Dados em tempo real**: preços ao segundo (pagos).
- **MCP**: o protocolo que permite ao ChatGPT "ver" o programa. **Túnel**: a ponte segura entre a internet e o teu PC.

---

## 1. Criar e preparar a conta na Interactive Brokers

### 1.1 Abrir a conta

1. Vai a https://www.interactivebrokers.com e clica em **Open Account** (Abrir conta).
2. Escolhe **Individual**. Preenche os dados pessoais, fiscais (NIF) e financeiros. Vai pedir uma fotografia do
   cartão de cidadão/passaporte e um comprovativo de morada.
3. Quando perguntar o **plano de preços**, escolhe **IBKR Pro**. O programa vem configurado para as comissões do
   plano Pro (0,005 USD por ação, mínimo 1 USD por ordem) e a IBKR anuncia o acesso à API e à TWS para este plano.
   Se já tiveres IBKR Lite, podes mudar para Pro nas definições da conta a qualquer momento.
4. Nas **permissões de negociação** (Trading Permissions) marca **Stocks → United States**. Se quiseres que o
   programa possa fazer vendas a descoberto, a conta tem de ser do tipo **Margin** (não Cash); caso contrário
   desliga depois a opção *Permitir vendas a descoberto* no programa.
5. Submete. A aprovação demora normalmente **1 a 3 dias úteis**. Vais receber um e-mail.

### 1.2 Depositar dinheiro

1. Entra no **Portal do Cliente** (Client Portal) em https://www.interactivebrokers.com → **Log In**.
2. Menu **Transfer & Pay → Transfer Funds → Make a Deposit**. Escolhe **Bank Wire** (transferência bancária) ou
   SEPA, em EUR, e segue as instruções (dão-te o IBAN deles e uma referência).
3. O dinheiro aparece em 1 a 2 dias úteis. **Sem dinheiro na conta a IBKR não deixa subscrever dados de mercado.**
4. O programa pensa em USD mas mostra os valores na moeda que escolheres (há uma opção para isso). A conversão de
   EUR para USD para comprar ações é feita pela IBKR; se a conta for Margin, a IBKR empresta USD
   automaticamente contra os EUR. Em conta Cash, converte primeiro EUR → USD na TWS (não é preciso para a Paper).

### 1.3 Criar a conta Paper (simulação)

1. Portal do Cliente → ícone do **boneco (cabeça e ombros)** no canto superior direito → **Settings**.
2. Em **Account Settings** procura **Paper Trading Account** e clica no ícone de **engrenagem**.
3. Define o nome de utilizador e a palavra-passe da conta paper (são diferentes dos da conta real) e grava. A
   conta paper fica disponível normalmente no dia seguinte e começa com 1 000 000 USD fictícios (podes pedir para
   igualar ao saldo real nessa mesma página, com **Reset Paper Trading Account**).
4. Na mesma página há uma opção com o nome aproximado **Share real-time market data subscriptions with paper
   trading account** (partilhar dados em tempo real com a conta paper). Liga-a. Sem isto a conta paper só recebe
   dados atrasados, mesmo depois de subscreveres dados na conta real.

### 1.4 Declarar que não és profissional

As bolsas cobram muito mais a "profissionais" (quem negoceia para uma empresa ou está registado como tal). Um
particular que negoceia o seu próprio dinheiro é **não profissional** (Non-Professional). Quando subscreveres o
primeiro feed de dados (passo 1.5) aparece um questionário **Market Data Subscriber Status**: responde com
sinceridade; as respostas de um particular normal levam ao estatuto não profissional, com os preços indicados abaixo.

### 1.5 Subscrever os dados de mercado em tempo real ("fazer as subscrições")

Porque é que isto é obrigatório para o programa abrir posições: sem subscrição a IBKR dá preços com **15 minutos
de atraso**. O programa só decide com velas com menos de **4 minutos** de idade e recusa enviar qualquer ordem com
cotação mais velha do que isso. Com dados atrasados ele **liga-se, supervisiona posições e mostra tudo, mas não
abre nenhuma posição nova** (na barra lateral aparece *Dados: atrasados 15 min*).

Passos:

1. Portal do Cliente → boneco → **Settings** → **User Settings**.
2. Na secção **Trading Platform** clica em **Market Data Subscriptions** (Subscrições de dados de mercado) →
   ícone de **engrenagem** / **Add or Edit** (adicionar ou editar).
3. Abre a região **North America** e marca, em **Level I**:
   - **NASDAQ (Network C/UTP)** — ações cotadas no Nasdaq (AAPL, MSFT, NVDA, TSLA, AMZN…): **1,50 USD/mês**.
   - **NYSE (Network A/CTA)** — ações cotadas na NYSE (JPM, KO, XOM…): **1,50 USD/mês**.
   - **NYSE American, BATS, ARCA, IEX, and Regional Exchanges (Network B)** — ETFs como SPY e QQQ e as
     restantes bolsas: **1,50 USD/mês**. Recomendado, porque o programa usa o **SPY** como referência.
   Total: **4,50 USD/mês**, cobrados à conta. Se só negociares ações do Nasdaq, 1,50 USD chega.
4. **Não** confundas com o **US Securities Snapshot and Futures Value Bundle** (10 USD/mês, gratuito com 30 USD
   de comissões): esse pacote dá *snapshots* (fotografias pontuais) e **não serve** para o fluxo contínuo de velas
   que o programa precisa.
5. Responde ao questionário de estatuto (1.4), aceita os contratos das bolsas e confirma. A ativação costuma ser
   imediata ou até ao dia seguinte. Os feeds podem ser cancelados a qualquer momento na mesma página.
6. Confirma que a opção de partilha com a conta paper (1.3, ponto 4) está ligada.

Regra prática: se tiveres a sessão real e a paper abertas ao mesmo tempo, têm de estar **no mesmo PC** para a paper
receber os dados em tempo real.

---

## 2. Instalar a TWS e autorizar a ligação do programa ("ir buscar a API da IBKR")

### 2.1 Instalar a TWS

1. https://www.interactivebrokers.com → **Trading → Platforms → Trader Workstation** → **Download** (Windows,
   64 bits). Qualquer das versões *Latest* ou *Stable* serve. Em alternativa descarrega o **IB Gateway**
   (mais leve, só para programas) na mesma página.
2. Instala com as opções por defeito.
3. Abre a TWS. No ecrã de entrada, escreve o utilizador e a palavra-passe **da conta paper** (criados em 1.3) e
   confirma que aparece a indicação **Paper Trading** (o ecrã de login tem um interruptor Live/Paper). Em paper o
   fundo da TWS fica com um aviso e as cores mudam; é normal.
4. Pode pedir a autenticação de dois fatores (app **IBKR Mobile**). Na conta paper normalmente não pede.

### 2.2 Ativar a API

1. Na TWS: menu **File → Global Configuration…** (no IB Gateway: **Configure → Settings**).
2. À esquerda abre **API → Settings** e deixa assim:
   - ☑ **Enable ActiveX and Socket Clients** (ligado).
   - ☐ **Read-Only API** (**desligado**; se ficar ligado, o programa vê tudo mas não pode enviar ordens).
   - **Socket port**: `7497` na TWS paper, `7496` na TWS real (IB Gateway: `4002` paper, `4001` real). O programa já
     vem com 7496/7497; se usares o Gateway, muda no programa (secção 5.3).
   - **Trusted IPs**: clica **Create** e escreve `127.0.0.1`. Isto diz à TWS que pode confiar em programas que
     correm no mesmo PC. Sem isto, a TWS mostra uma janela a pedir autorização de cada vez.
   - ☑ **Download open orders on connection** (ligado), para o programa ver ordens que ficaram abertas.
   - **Master API client ID**: deixa em branco.
3. À esquerda abre **API → Precautions** e marca **Bypass Order Precautions for API Orders**. Sem isto, a TWS
   pode abrir janelas de aviso ("a ordem excede o tamanho habitual", etc.) que bloqueiam as ordens do programa
   até alguém clicar.
4. À esquerda abre **Lock and Exit**. Em **Auto restart** escolhe uma hora de madrugada (por exemplo 02:00) e
   **Auto restart** em vez de *Auto logoff*. A TWS reinicia sozinha todos os dias em vez de fechar; o programa volta
   a ligar-se sozinho.
5. Clica **OK**. Repete estes passos mais tarde na TWS **real** (os ajustes são por utilizador).
6. A TWS tem de ficar **aberta e com sessão iniciada** enquanto o programa corre. Pode estar minimizada.

### 2.3 Como saber se a API está a funcionar

Quando, mais à frente, clicares **▶ Iniciar trading** no programa, a barra lateral passa de *IBKR: desligado* para
*IBKR: ligado* em poucos segundos e, na TWS, aparece em baixo um pequeno indicador de ligação API. Se aparecer
uma janela na TWS a perguntar se aceita a ligação, clica **Yes** e volta a 2.2 para adicionar o Trusted IP.

---

## 3. Instalar o Ollama (a inteligência artificial local)

1. Vai a https://ollama.com/download → **Download for Windows** → abre o instalador e aceita tudo. Fica um ícone
   de lama (o logótipo) na barra de tarefas; o Ollama corre em segundo plano em `http://localhost:11434`.
2. Abre a **Linha de comandos**: tecla **Windows + R**, escreve `cmd`, **Enter**.
3. Descarrega o modelo (cerca de 4,7 GB, demora alguns minutos):
   ```
   ollama pull llama3
   ```
   Este é o modelo que o programa usa por defeito. Alternativas boas: `qwen2.5:7b` (mais fiável em JSON) ou, com
   16 GB de RAM ou mais, `qwen2.5:14b`. Podes ter vários; escolhes no programa.
4. Confirma:
   ```
   ollama list
   ```
   Deve mostrar `llama3:latest`. Se escreveres `curl http://localhost:11434/api/tags` também deve responder com
   o nome do modelo.
5. O Ollama arranca com o Windows. Se um dia o ícone não estiver na barra de tarefas, abre-o pelo menu Iniciar.

Nada do que o programa envia ao modelo sai do teu computador. Por defeito o programa até **esconde** o nome da ação
e os preços ao modelo, para ele não "adivinhar" pelo nome.

---

## 4. Instalar o programa

### 4.1 Juntar e verificar o ficheiro

1. Cria a pasta `C:\Trader\`.
2. Copia para lá os três ficheiros recebidos (`OllamaIBKRTrader-<versão>.part0`, `.part1`, `.part2`) **ou** descarrega
   o ficheiro inteiro `OllamaIBKRTrader-windows-x64.exe` da release mais recente em
   https://github.com/jsousa21794/skills/releases e renomeia-o para `OllamaIBKRTrader.exe`.
3. Se tiveres as três partes: abre a Linha de comandos (Windows + R → `cmd`), escreve `cd C:\Trader` e depois:
   ```
   copy /b OllamaIBKRTrader-1.0.18.part0+OllamaIBKRTrader-1.0.18.part1+OllamaIBKRTrader-1.0.18.part2 OllamaIBKRTrader.exe
   ```
   (substitui `1.0.18` pelo número que aparece no nome dos ficheiros que recebeste.)
4. Verifica que o ficheiro não está corrompido:
   ```
   certutil -hashfile OllamaIBKRTrader.exe SHA256
   ```
   A linha longa de letras e números tem de ser exatamente a indicada na mensagem que acompanha os ficheiros (cada
   versão tem a sua; a da 1.0.17 é `12c3b54ddb7b83ffa8e91d54a0feacbff05111ab512dd26091f01bfd6db74853`). Se for
   diferente, volta a descarregar; não executes.

### 4.2 Primeira abertura

1. Duplo clique em `OllamaIBKRTrader.exe`. O Windows pode mostrar o aviso azul do **SmartScreen** (o programa não
   tem assinatura paga): clica **Mais informações → Executar mesmo assim**. Se o antivírus o apagar, adiciona a
   pasta `C:\Trader` às exceções e volta a copiar o ficheiro.
2. A primeira abertura demora 10–20 segundos. Abre uma janela escura com uma **barra lateral** à esquerda (estado
   da IBKR, Ollama, modo, capital, P&L do dia e botões) e **separadores** em cima: *Visão geral*, *Decisões*,
   *Risco*, *Consola*, *Configurações*.
3. **Ainda não cliques em ▶ Iniciar trading.** O programa vem por defeito em **conta REAL** (barra lateral: *CONTA
   REAL*, porta 7496). Enquanto não iniciares, não se liga a nada.
4. Os teus dados ficam em `C:\Users\<o teu nome>\.ollamaibkrtrader\` (pasta oculta; escreve o caminho na barra do
   Explorador). Lá dentro: `config.json` (opções), `trader.log` (registo; é o que envias quando pedes ajuda) e um
   ficheiro `.sqlite3` por conta (a memória do programa). **Nunca apagues esta pasta**; faz uma cópia de vez em
   quando.

---

## 5. Configurar o programa (separador Configurações)

Clica no separador **Configurações** (ou no botão **⚙ Configurações** da barra lateral). Em cima há o botão
**💾 Guardar e aplicar**; tudo o que alterares nas secções em caixas só é gravado quando clicares nele. A secção
*Conta e modelo* aplica-se logo. Aqui estão as opções de uso corrente; as avançadas (stops fixos, PDT, drawdown,
Finnhub, módulos opcionais) só existem em `config.json` (a secção 8 explica como editar).

### 5.1 Conta e modelo (aplica-se de imediato)

1. **Conta**: clica em **Paper**. A barra lateral passa a *PAPER*, porta 7497. Vais trabalhar em simulação
   durante as primeiras semanas.
2. **Modelo Ollama**: clica no botão **↻** à direita do menu. A lista passa a mostrar os modelos que descarregaste
   (3.3). Escolhe `llama3:latest` (ou o que preferires). Cada modelo tem a sua própria calibração.
3. **Ativos negociados**: escreve os tickers separados por vírgula, por exemplo `AAPL, MSFT, NVDA, AMZN`, e clica
   **OK**. Começa com 2 a 5 ações grandes dos EUA (muito líquidas). Não uses ETFs alavancados nem ações abaixo de
   10 USD.

### 5.2 Ligação à IBKR

| Opção (como aparece no programa) | Valor para começar |
|---|---|
| Host da TWS / Gateway | `127.0.0.1` (não mudes) |
| Porta conta REAL | `7496` (TWS) ou `4001` (IB Gateway) |
| Porta conta Paper | `7497` (TWS) ou `4002` (IB Gateway) |
| Client ID da API | `17` (qualquer número exceto 0; se tiveres outro programa ligado à TWS, usa um diferente) |
| Conta (vazio = primeira conta gerida) | vazio |
| Adotar posições não abertas pelo bot (proteger e gerir) | **desligado** |
| Ao fechar por sinal, cancelar stops/limits MANUAIS (senão o fecho é bloqueado) | **desligado** |

### 5.3 Ollama

| Opção | Valor para começar | O que é |
|---|---|---|
| URL do Ollama | `http://localhost:11434` | onde o Ollama atende |
| Consultar o modelo a cada (min) | `15` | com que frequência pergunta por ação |
| Amostras por decisão | `5` | pergunta 5 vezes e vê se o modelo concorda consigo próprio |
| Acordo mínimo entre amostras (%) | `60` | abaixo disto não há sinal |
| Duas etapas (raciocínio livre → JSON) | ligado | respostas mais estáveis |
| Ocultar ticker e níveis de preço ao modelo | ligado | evita "chutes" pelo nome da ação |

### 5.4 Trading e risco (os valores por defeito são os pedidos pelo dono do programa)

| Opção | Valor por defeito | Significado |
|---|---|---|
| Máximo de posições abertas | `10` | o verdadeiro limite é o dinheiro disponível na conta |
| Risco por operação (% do equity) | `10` | até 10 % do capital em risco por operação, limitado pelos fundos disponíveis; nas primeiras semanas o programa reduz isto sozinho até ter estatística suficiente |
| Kill-switch: perda diária máxima (%) | `20` | a −20 % no dia para tudo até ao dia seguinte; não há botão para contornar |
| Entradas ilimitadas enquanto o dia está em lucro | ligado | em dia positivo não há limite de operações |
| Permitir vendas a descoberto (short) | ligado | desliga se a conta for Cash |
| Slippage máximo na entrada (%) | `0.3` | a ordem de entrada é limite a até 0,3 % do preço de referência |
| Stop = k × ATR (k) | `2` | distância do stop |
| Take-profit = R × distância do stop (R) | `2` | o objetivo é o dobro do risco |
| Sinal tem de repetir-se N consultas seguidas | `2` | só entra se o modelo repetir a opinião 15 min depois |

Se és iniciante e queres começar mais devagar, baixa o *Risco por operação* para `2` ou `3` e o *Kill-switch* para
`5`. Podes voltar a subir quando confiares.

### 5.5 Custos

| Opção | Valor por defeito |
|---|---|
| Comissão por ação (USD) | `0.005` (IBKR Pro) |
| Comissão mínima por ordem (USD) | `1` |
| Custo ida+volta máximo (% do ganho no TP) | `20` |
| Ganho líquido no TP ≥ N × custo | `3` |

Quer dizer: uma operação só é feita se, atingindo o take-profit, o ganho **depois** das comissões for pelo menos
3 vezes o custo, e as comissões não passarem de 20 % do ganho bruto. Com contas pequenas (abaixo de ~3 000 USD)
muitas decisões vão aparecer como "não executado: custo"; é o programa a proteger-te.

### 5.6 Integração ChatGPT (MCP)

Deixa **desligado** por agora; a secção 7 explica.

### 5.7 Apresentação

- **Moeda em que os valores são mostrados**: `EUR` se a tua conta for em euros (a taxa vem da IBKR).
- **Dados de mercado**: escolhe **Tempo real (subscrição)** depois de concluir o passo 1.5. Enquanto não tiveres
  subscrição deixa **Atrasados 15 min (sem subscrição)**; o programa funciona mas não abre posições.
- **Janela sempre visível (always-on-top)**: a gosto.

### 5.8 Gravar

Clica **💾 Guardar e aplicar**. Por baixo aparece uma linha verde a confirmar. Se aparecer amarelo com "Rejeitado",
algum valor estava fora dos limites e foi ignorado; corrige e grava outra vez.

---

## 6. Primeiro dia (em Paper) — o que fazer e o que esperar

1. **Ordem de arranque**, todos os dias: (1) TWS aberta com sessão iniciada (paper, por agora); (2) Ollama a correr
   (ícone na barra de tarefas); (3) `OllamaIBKRTrader.exe`.
2. Clica **▶ Iniciar trading**. Em 5–10 segundos o cabeçalho mostra *IBKR: ligado*, o estado do Ollama, o valor da
   carteira e *Dados: tempo real* (ou *Dados: atrasados 15 min*); a barra lateral mostra o modo, a porta e a conta.
3. O programa faz primeiro a **reconciliação**: compara o que a corretora tem com o que ele tem registado,
   importa execuções que aconteceram enquanto estava fechado e classifica cada ação. Enquanto no separador **Risco**
   aparecer «⏳ a reconciliar o estado da corretora», não sai nenhuma ordem nova. Demora segundos.
4. **Horário**: só há decisões no horário regular da bolsa de Nova Iorque, **09:30–16:00 em NY = 14:30–21:00 em
   Lisboa** (a diferença é de 5 horas quase todo o ano), e nunca nos primeiros 15 nem nos últimos 10 minutos. Fora
   disso, o separador *Decisões* fica vazio e isso é normal.
5. No separador **Decisões** vês cada consulta ao modelo: ação proposta, confiança, acordo entre amostras, e o
   que a camada de risco fez: `EXECUTADA · …` ou `não executada · motivo`, com o motivo exato (custo, horário, sinal
   ainda não repetido, PDT, kill-switch, dados atrasados…). **Ler esta coluna é a melhor forma de perceber o programa.**
6. Quando uma ordem é enviada, na TWS aparece um **bracket** com três linhas (entrada, stop, take-profit) e a
   etiqueta `OllamaIBKRTrader`. Se a entrada não executar em 120 segundos, o programa cancela-a. O stop e o TP
   ficam **na corretora** (GTC): mesmo que o PC desligue, a posição está protegida.
7. Botões da barra lateral:
   - **■ Parar**: para de pedir sinais novos. **Não** fecha posições nem cancela stops; continua a vigiar o que está
     aberto.
   - **🧠 Retrospetiva agora**: avalia as decisões passadas e atualiza lições e calibração (acontece sozinho às
     21:30 todos os dias).
   - **📊 Relatório estatístico**: mostra na *Consola* as métricas (acerto, expectativa, risco de ruína) e se os
     "gates" estatísticos já permitem usar o risco total.
   - **▶ Retomar novas entradas**: só fica ativo quando há uma pausa (local ou pedida pelo ChatGPT).
8. **Primeiras semanas**: o programa começa "em modo aprendiz": a confiança do modelo ainda não está calibrada e o
   risco por operação é reduzido até haver dezenas de decisões avaliadas e 100 operações fechadas. Isto é
   intencional. Deixa-o correr em paper **pelo menos 3 a 4 semanas** e lê o relatório estatístico à sexta-feira
   (é gerado automaticamente).
9. **Fim do dia**: podes deixar tudo aberto (a TWS reinicia de madrugada e o programa volta a ligar-se) ou fechar o
   programa; os stops ficam na corretora e ao reabrir ele reconcilia o que aconteceu.

### 6.1 Avisos que precisam de ti (separador Risco e Consola)

- **Posição externa / mista**: tens ações do mesmo ticker compradas à mão. O programa não lhes toca e bloqueia
  sinais nessa ação. Ou fechas a tua parte na TWS, ou ligas *Adotar posições não abertas pelo bot*.
- **Discrepância / conflito**: a corretora e o registo do programa não batem certo (alguém mexeu na posição fora
  do programa, ou há uma execução ainda por identificar). Não envia ordens nessa ação até perceber. Vê na TWS
  (**Account → Trade Log**) e, se foste tu, espera; resolve-se sozinho quando as execuções chegam.
- **Kill-switch ativado**: o dia chegou a −20 %. Para até ao dia seguinte. Não há como contornar.
- **Pausa**: uma proteção (3 stops em 2 horas, 5 perdas seguidas, drawdown de 6 % em 5 dias, VIX acima de 35)
  parou as entradas novas por um tempo. O separador *Risco* diz até quando. Podes retomar com **▶ Retomar novas
  entradas** se a pausa for manual; as automáticas levantam-se sozinhas.
- **`migration_failed.flag` na pasta de dados**: a base de dados não conseguiu atualizar. Faz cópia da pasta,
  envia o `trader.log` e não apagues o ficheiro sem perceber o problema.

---

## 7. Passar para a conta REAL

Só quando o paper estiver a correr há semanas sem surpresas e perceberes cada linha do separador *Decisões*.

1. Garante que concluíste 1.2 (dinheiro na conta) e 1.5 (dados em tempo real).
2. Abre a TWS com o utilizador da **conta real**, e repete a secção 2.2 nessa sessão (API, Trusted IP, Precautions,
   Auto restart). A porta real é `7496`.
3. No programa: **■ Parar**, depois **Configurações → Conta → Real**. Abre uma janela vermelha *"Ativar trading com
   DINHEIRO REAL"*. Escreve **REAL** (em maiúsculas) e clica **Ativar conta real**. Esta confirmação é pedida uma
   única vez e fica guardada. A barra lateral passa a *CONTA REAL*, porta 7496.
4. A conta real tem a sua **própria base de dados** (ficheiro `trader_live_<conta>.sqlite3`): a calibração e as
   estatísticas recomeçam do zero e o programa volta ao "modo aprendiz" com risco reduzido. É assim de propósito.
5. Começa com **Risco por operação 2–3 %** e 2 ou 3 ações. Sobe só quando o relatório estatístico mostrar que os
   gates passaram.
6. **▶ Iniciar trading**. Verifica na TWS real que as ordens aparecem com a etiqueta `OllamaIBKRTrader`.

---

## 8. APIs opcionais de dados (resultados, notícias)

Por defeito o programa usa o **yfinance** (grátis, sem chave) para saber datas de resultados, o VIX e notícias. Se
falhar, continua sem esses filtros e regista no log. O **Finnhub** é mais fiável e também é grátis:

1. Vai a https://finnhub.io → **Get free API key** → regista-te com e-mail.
2. Depois de entrar, no **Dashboard** aparece a tua **API Key** (uma sequência de letras e números). Copia-a.
3. **Fecha o programa.** Abre a pasta `C:\Users\<o teu nome>\.ollamaibkrtrader\` e abre `config.json` com o
   **Bloco de notas**.
4. Procura a linha `"news_source": "yfinance",` e muda para `"news_source": "finnhub",`. Procura
   `"finnhub_api_key": "",` e cola a chave entre as aspas: `"finnhub_api_key": "abc123…",`. Mantém as vírgulas e
   as aspas exatamente como estão nas outras linhas.
5. Grava, abre o programa. Se tiveres estragado o JSON, o programa avisa na *Consola* e usa os valores anteriores.

As opções `sentiment_enabled` (FinBERT) e `volmodel_enabled` (Chronos) **não** estão incluídas no executável; deixa-as
desligadas.

---

## 9. Ligar o ChatGPT ao programa (MCP) — opcional

O que isto dá: numa conversa no ChatGPT podes perguntar "como está o bot?", "há discrepâncias?", "compara o
registo com a corretora", "mostra as últimas decisões" e o ChatGPT vai buscar as respostas ao programa no teu PC.
O ChatGPT **só consegue consultar** e, no máximo, **pausar novas entradas**. Não pode enviar ou cancelar ordens,
retomar entradas nem mudar o risco.

O que precisas: uma conta ChatGPT **paga** (Plus, Pro, Team ou Business; o modo de programador não existe no plano
gratuito) e um túnel HTTPS, porque o ChatGPT só alcança endereços públicos.

### 9.1 Ativar o servidor no programa

1. **Configurações → Integração ChatGPT (MCP)**: liga **Ativar servidor MCP local (aplica-se ao reiniciar o
   programa)**; *Endereço de escuta* `127.0.0.1`; *Porta* `8765`. **💾 Guardar e aplicar**.
2. **Fecha e volta a abrir o programa.** Na secção **Ligação do ChatGPT** (em baixo nas Configurações) aparece
   `Conector: http://127.0.0.1:8765/t/<token>/mcp`. Esse `<token>` (letras e números) é a tua **senha**: quem o
   tiver vê o estado da tua conta. Não o partilhes.

### 9.2 Criar o túnel com o cloudflared (grátis)

1. Vai a https://github.com/cloudflare/cloudflared/releases → descarrega `cloudflared-windows-amd64.exe` → move
   para `C:\Trader\` e renomeia para `cloudflared.exe`.
2. Linha de comandos → `cd C:\Trader` → executa:
   ```
   cloudflared tunnel --url http://127.0.0.1:8765
   ```
3. Passados segundos aparece uma caixa com um endereço do tipo `https://palavra-palavra-palavra.trycloudflare.com`.
   Copia-o. **Deixa esta janela aberta** enquanto quiseres usar o ChatGPT (fechar a janela fecha o túnel).
4. Este endereço muda de cada vez que arrancas o cloudflared; nesse caso tens de o atualizar no programa (9.3) e no
   ChatGPT (9.4). Para um endereço fixo é preciso uma conta Cloudflare gratuita com domínio próprio ("túnel
   nomeado"), fora do âmbito deste guia.

### 9.3 Dizer ao programa qual é o endereço público

**Configurações → Integração ChatGPT (MCP) → URL pública do túnel HTTPS**: cola o endereço do cloudflared
(sem barra no fim) e **💾 Guardar e aplicar**. A linha *Conector* passa a mostrar
`https://…trycloudflare.com/t/<token>/mcp`. Copia esta linha inteira.

### 9.4 Criar o conector no ChatGPT

1. No ChatGPT (web): foto de perfil → **Settings** (Definições) → **Connectors** (Conectores) ou **Apps &
   Connectors** → **Advanced settings** → liga **Developer mode** (modo de programador).
2. Volta a **Connectors** → **Create** (Criar).
3. **Name**: `Ollama IBKR Trader`. **MCP Server URL**: cola o endereço completo copiado em 9.3.
   **Authentication**: **No authentication** (o token já vai no endereço). Marca que confias na aplicação e cria.
4. Numa conversa nova, no botão **+** → **More** (ou o ícone de ferramentas de *Developer mode*) ativa o conector
   `Ollama IBKR Trader`. Pergunta: *"Chama get_status e resume-me o estado do bot."*
5. Ferramentas disponíveis: `get_status`, `get_positions`, `get_open_orders`, `get_recent_decisions`,
   `get_recent_log`, `compare_ledger_with_broker`, `run_diagnostics`, `get_remote_commands` e
   **`pause_new_entries`**.

### 9.5 Como funciona a pausa pedida pelo ChatGPT

- `get_status` devolve a conta (mascarada, ex. `U1***67`), o modo (paper/real) e um **`context_id`**.
- Para pausar, o ChatGPT tem de chamar `pause_new_entries` com conta, modo, razão e esse `context_id`. Se
  entretanto mudaste de conta ou de modo, ou reiniciaste o ciclo, a pausa é recusada e o ChatGPT tem de voltar a
  chamar `get_status`. Isto evita pausar a conta errada.
- Para **retomar**, só no programa: botão **▶ Retomar novas entradas**. Todos os pedidos ficam registados.
- Nos planos Plus/Pro o ChatGPT pode tratar conectores personalizados como **só de leitura**; nesse caso a pausa
  por ChatGPT não funciona e usas o programa. As consultas funcionam sempre.

### 9.6 Segurança

- O servidor só escuta no teu PC (`127.0.0.1`). Sem o túnel ninguém de fora chega lá.
- Fecha a janela do cloudflared quando não estiveres a usar o ChatGPT.
- Se achares que o token fugiu: fecha o programa, abre `config.json`, apaga a linha `"mcp_token": "...",` e volta a
  abrir; é gerado um novo (e tens de refazer 9.3 e 9.4).

---

## 10. Atualizar o programa

1. Fecha o programa (não é preciso fechar a TWS nem o Ollama).
2. Faz uma cópia da pasta `C:\Users\<o teu nome>\.ollamaibkrtrader\`.
3. Substitui `C:\Trader\OllamaIBKRTrader.exe` pelo novo ficheiro (junta as partes e verifica o SHA-256 como em 4.1).
4. Abre. A base de dados é atualizada sozinha na primeira abertura (pode demorar alguns segundos). A versão aparece
   no título da janela.

---

## 11. Se algo correr mal

| O que vês | Causa mais provável | O que fazer |
|---|---|---|
| *IBKR: desligado* depois de Iniciar | TWS fechada ou não iniciou sessão; API desligada; porta errada; modo (Paper/Real) não corresponde à TWS aberta | Abre a TWS certa; secção 2.2; confirma a porta na secção *Ligação* da barra lateral |
| Janela na TWS a pedir autorização a cada ligação | `127.0.0.1` não está em *Trusted IPs* | Secção 2.2, passo 2 |
| Ordens não aparecem e a TWS mostra um aviso | *Read-Only API* ligado ou *Precautions* a bloquear | Secção 2.2, passos 2 e 3 |
| *Ollama: indisponível* | Ollama não está a correr | Abre o Ollama pelo menu Iniciar; `ollama list` na linha de comandos |
| Lista de modelos vazia | Nenhum modelo descarregado | `ollama pull llama3` e clica **↻** |
| *Dados: atrasados 15 min* e nenhuma entrada | Sem subscrição, ou partilha com a paper desligada, ou opção *Dados de mercado* em *Atrasados* | Secções 1.3 (ponto 4), 1.5 e 5.7 |
| Decisões sempre "não executado: custo" | Conta pequena face à comissão mínima | Normal; sobe o capital ou aceita menos operações |
| Nada acontece durante o dia | Fora do horário 14:30–21:00 (Lisboa) ou primeiros 15 min | Espera; vê o separador *Decisões* |
| "Posição externa" numa ação | Compraste à mão o mesmo ticker | Fecha na TWS ou liga *Adotar posições não abertas pelo bot* |
| ChatGPT diz que o conector não responde | Janela do cloudflared fechada; endereço mudou; token diferente | Reinicia o cloudflared, atualiza 9.3 e o conector em 9.4 |
| `pause_new_entries` recusada | `context_id` antigo | Pede ao ChatGPT para chamar `get_status` e repetir |
| Linha amarela "Rejeitado" ao gravar | Valor fora dos limites (ex.: percentagem > 100) | Corrige o campo indicado |

Quando pedires ajuda, envia o ficheiro `trader.log` (pasta de dados) e diz a versão (título da janela) e a hora
aproximada do problema.

---

## 12. Resumo em 12 passos

1. Abrir conta IBKR **Pro**, Individual, Margin se quiseres shorts → esperar aprovação.
2. Depositar dinheiro.
3. Criar conta Paper e ligar a partilha de dados com a paper.
4. Subscrever **NASDAQ (Network C/UTP)**, **NYSE (Network A/CTA)** e **Network B** como não profissional
   (~4,50 USD/mês).
5. Instalar a TWS; **API → Settings**: Enable ActiveX and Socket Clients, Read-Only desligado, Trusted IP
   `127.0.0.1`; **API → Precautions**: Bypass; **Lock and Exit**: Auto restart.
6. Instalar o Ollama e `ollama pull llama3`.
7. Juntar e verificar o `OllamaIBKRTrader.exe`; abrir.
8. Configurações: **Paper**, modelo, tickers, *Dados de mercado: Tempo real*, moeda; **Guardar e aplicar**.
9. TWS paper aberta → **▶ Iniciar trading** → observar o separador *Decisões* durante 3–4 semanas.
10. (Opcional) Finnhub no `config.json`; (opcional) MCP + cloudflared + conector no ChatGPT.
11. Passar para **Real** com risco baixo; subir só com o relatório estatístico a favor.
12. Todos os dias: TWS → Ollama → programa → Iniciar. Ler *Decisões* e *Risco*. Fazer cópia da pasta de dados.
