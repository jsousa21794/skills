# Prosa

Aplicação web de revisão e reescrita de texto. Transforma textos rígidos, artificiais ou genéricos em textos naturais e fluidos, adequados à voz de quem escreve, preservando o significado e o rigor do original.

Construída com Next.js (App Router), TypeScript e Tailwind CSS. As chamadas ao fornecedor de IA acontecem exclusivamente no servidor. Funciona com **Ollama** (modelos locais, sem chave nem envio de texto para terceiros) ou com a **API da OpenAI** e serviços compatíveis, através do formato de chat completions da OpenAI.

## Funcionalidades

- **Editor com dois painéis**: original à esquerda, versão revista à direita. Em telemóvel, alterna-se por separadores.
- **Idiomas**: português de Portugal, português do Brasil, inglês britânico e inglês americano, com regras explícitas para não misturar variantes.
- **Modos**: profissional, académico, informal, literário e personalizado (com instruções livres de estilo).
- **Intensidades**: ligeira (corrige e melhora a fluidez), moderada (reformula e reduz repetições), profunda (reorganiza expressão e ritmo, mantendo as ideias).
- **Extensão**: manter, encurtar ou desenvolver, sem inventar informação.
- **Rever apenas um parágrafo**: passa o rato sobre um parágrafo revisto e escolhe «Rever parágrafo». Os parágrafos vizinhos são enviados só como contexto.
- **Termos bloqueados**: lista de nomes, siglas ou passagens que não podem mudar. Podem ser acrescentados por seleção no painel original. O servidor verifica se se mantiveram e repete a revisão uma vez quando não se mantêm; se ainda assim falhar, avisa.
- **Re-revisão automática**: depois de cada secção, os parágrafos com padrões típicos de IA (aberturas vazias, transições mecânicas, vocabulário inflacionado, construções em molde) ou com marcas de outra variante linguística são re-revistos uma vez, com essas expressões explicitamente proibidas. A nova versão só é aceite se tiver menos ocorrências e mantiver os termos bloqueados. É o equivalente, ao nível do parágrafo, aos «samplers» anti-slop, que não funcionam sobre Ollama nem OpenAI porque nenhum devolve probabilidades por token.
- **Verificação de sentido**: cada parágrafo revisto é comparado com o original por embeddings (`bge-m3` no Ollama, `text-embedding-3-small` na OpenAI). Abaixo do limiar configurado, o parágrafo recebe aviso e etiqueta.
- **Alterações frase a frase**: vista «Alterações» com cada frase substituída, inserida ou removida, e botão «Manter original» por alteração. As decisões aplicam-se ao texto revisto e criam uma versão.
- **Exportação .docx com alterações registadas** (tracked changes), para rever no Word ou no LibreOffice.
- **Histórico de versões**: cada reescrita cria uma versão. É possível restaurar, comparar, renomear, marcar (as versões marcadas não são descartadas quando o histórico enche), eliminar e ativar, opcionalmente, a persistência no browser. Um botão elimina todos os dados guardados.
- **Comparação**: diferenças ao nível da palavra ou da frase entre o revisto e o original (ou outra versão do histórico).
- **Copiar, descarregar (.txt) e restaurar o original.**
- **Perfil de voz**: cola exemplos da tua escrita e a aplicação extrai um perfil editável (vocabulário, ritmo, formalidade, construção frásica, o que evitar), acompanhado de métricas medidas nas amostras: comprimento e variação das frases, pontuação por 100 palavras, tratamento tu/você, ênclise, contrações, conectores preferidos. As métricas entram no prompt como restrições, porque exemplos em few-shot por si só não chegam para imitar estilo. Inclui um guia de estilo com palavras proibidas e substituições preferidas. O perfil é aplicado sem copiar passagens dos exemplos; os exemplos não são guardados.
- **Indicadores transparentes**: palavras, frases, parágrafos, média e variação do comprimento das frases, frases longas, diversidade lexical, legibilidade (Flesch adaptado ao português por Martins et al., 1996; Flesch Reading Ease em inglês), repetições, padrões típicos de IA por categoria (listas próprias para português e inglês, mais construções independentes da língua) e marcas da variante contrária. Não há percentagens de «escrita humana» nem promessas sobre detetores de IA.
- **Verificação linguística opcional com LanguageTool** auto-alojado (LGPL): gramática, ortografia e regras próprias de pt-PT, pt-BR, en-GB e en-US, para o original e para a revisão.
- **Documentos longos**: divididos por parágrafos em secções (por defeito até 400 palavras com Ollama e 650 com OpenAI) e processados em sequência. Cada secção recebe o final da secção anterior já revista e um glossário de decisões terminológicas acumulado, para manter consistência. A segmentação de frases usa `Intl.Segmenter` com correção de abreviaturas portuguesas e inglesas.
- **Contexto do Ollama sob controlo**: o endpoint compatível com OpenAI do Ollama ignora a janela de contexto por pedido, por isso a Prosa usa a API nativa (`/api/chat`) com `num_ctx` e esquema JSON por pedido, mostra o contexto pedido e o máximo do modelo, e avisa quando uma secção pode não caber.
- **Notas da revisão**: ambiguidades que o modelo não conseguiu resolver com segurança, e avisos automáticos quando números, datas ou citações do original não aparecem iguais na revisão.
- **Tema claro e escuro**, contagem de palavras, barra de progresso por secção, cancelamento e mensagens de erro úteis.
- **Privacidade**: nenhum texto é guardado no servidor. As preferências de interface e o perfil ficam no `localStorage`; o histórico de textos só é guardado se o utilizador o ativar.

## Requisitos

- Node.js 20.9 ou superior (testado com Node 22)
- Um de: [Ollama](https://ollama.com) instalado com um modelo descarregado, ou uma chave da API da OpenAI

## Instalação

```bash
cd prosa
npm install
cp .env.example .env.local
```

### Opção A: Ollama (local)

1. Instala o Ollama e descarrega um modelo, por exemplo:

   ```bash
   ollama pull llama3.1
   ```

2. Em `.env.local`, deixa `PROSA_PROVIDER=ollama` e define `OLLAMA_MODEL` com o modelo descarregado.
3. Arranca a aplicação:

   ```bash
   npm run dev
   ```

O Ollama corre por defeito em `http://localhost:11434`. Para a verificação de sentido, instala também o modelo de embeddings:

```bash
ollama pull bge-m3
```

#### Modelos recomendados

A maioria dos modelos deriva para português do Brasil mesmo quando se pede português europeu. O benchmark P3B3 (ACL 2026) identificou AMALIA, EuroLLM e Gemma como os que se mantêm em pt-PT de forma consistente. Sugestões, por ordem de fidelidade a pt-PT:

| Modelo | Licença | Contexto | Instalação | Notas |
| --- | --- | --- | --- | --- |
| AMALIA-9B-0626-DPO | Apache 2.0 | 32k | `ollama create amalia-pt -f ollama/Modelfile.amalia` | Continuação do EuroLLM-9B com dados pt-PT (Arquivo.pt). GGUF comunitário; usa template ChatML, vê as notas no Modelfile. |
| Gervásio 8B PT-PT (PORTULAN) | MIT | herda Llama 3.1 | `ollama pull hf.co/PORTULAN/gervasio-8b-portuguese-ptpt-decoder-quantized-4bit` | Base Llama 3.1, afinado em pt-PT. |
| EuroLLM-22B-Instruct-2512 | Apache 2.0 | 32k | `ollama pull hf.co/bartowski/utter-project_EuroLLM-22B-Instruct-2512-GGUF:Q4_K_M` | Precisa de cerca de 14 GB. Evita o EuroLLM-9B para textos longos: só tem 4k de contexto. |
| Gemma 3 12B / 27B | Gemma Terms | 128k | `ollama pull gemma3:12b` | Generalista para as quatro variantes, com bom controlo de pt-PT. Predefinição no `.env.example`. |
| Qwen 3, Mistral Small 3.2 | Apache 2.0 | 32k a 128k | `ollama pull qwen3:14b` | Alternativas generalistas. Sem evidência específica de pt-PT. |

Os nomes de repositório e as versões podem mudar; confirma no Hugging Face antes de instalar. Modelos pequenos (menos de 7B) ignoram regras subtis e o formato JSON com mais frequência.

#### Janela de contexto

Desde a versão 0.15.5, o Ollama usa por defeito 4096 tokens em máquinas com menos de 24 GiB de VRAM. Como o endpoint `/v1` ignora `num_ctx`, a Prosa envia os pedidos pela API nativa com o valor de `OLLAMA_NUM_CTX` (8192 por defeito). Podes também fixar o contexto no servidor com `OLLAMA_CONTEXT_LENGTH=16384 ollama serve` ou num Modelfile (`PARAMETER num_ctx`). A interface mostra o contexto pedido e o máximo do modelo, e avisa quando uma secção pode não caber. Confirma com `ollama ps`.

### Opção B: OpenAI

1. Cria uma chave em https://platform.openai.com/api-keys.
2. Em `.env.local`, define `PROSA_PROVIDER=openai`, `OPENAI_API_KEY` e, se quiseres, `OPENAI_MODEL`.
3. `npm run dev`.

Serviços compatíveis com a API da OpenAI funcionam definindo `OPENAI_BASE_URL`: Azure OpenAI, Groq, Together, LM Studio, llama.cpp server ou a Maritaca (Sabiá, forte em pt-BR; confirma o endereço e o nome do modelo na documentação deles). A verificação de sentido usa o endpoint de embeddings do mesmo serviço; se não existir, define `PROSA_MEANING_CHECK=off`.

### Opção C: LanguageTool (opcional, com qualquer fornecedor)

```bash
docker run -d -p 8010:8010 erikvl87/languagetool
```

Define `LANGUAGETOOL_URL=http://localhost:8010`. O painel «Verificação linguística» passa a comparar o original e a revisão com as regras de pt-PT, pt-BR, en-GB ou en-US. Não existem dados de n-gramas para português, por isso as regras de confusão de palavras ficam de fora.

Abre http://localhost:3000. Para produção:

```bash
npm run build
npm start
```

## Configuração

Todas as variáveis estão descritas em `.env.example`:

| Variável | Predefinição | Descrição |
| --- | --- | --- |
| `PROSA_PROVIDER` | `ollama` | `ollama` ou `openai`. |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Endereço do Ollama. A API nativa é derivada retirando `/v1`. |
| `OLLAMA_MODEL` | `gemma3:12b` | Modelo local a usar (tem de estar instalado). |
| `OLLAMA_NUM_CTX` | `8192` | Janela de contexto pedida em cada pedido. |
| `OLLAMA_KEEP_ALIVE` | `10m` | Tempo que o modelo fica carregado entre pedidos. |
| `OLLAMA_API_KEY` | — | Só se o Ollama estiver atrás de um proxy que exija chave. |
| `OPENAI_API_KEY` | — | Obrigatória com `PROSA_PROVIDER=openai`. |
| `OPENAI_MODEL` | `gpt-4.1` | Modelo da OpenAI. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Para serviços compatíveis. |
| `PROSA_MEANING_CHECK` | `on` | Verificação de sentido por embeddings. |
| `PROSA_EMBED_MODEL` | `bge-m3` / `text-embedding-3-small` | Modelo de embeddings. |
| `PROSA_SIMILARITY_MIN` | `0.72` | Limiar de semelhança abaixo do qual há aviso. Calibra com `npm run calibrate`. |
| `PROSA_AUTO_POLISH` | `on` | Re-revisão automática de parágrafos com problemas detetáveis. |
| `PROSA_MAX_POLISH` | `3` | Máximo de parágrafos re-revistos por secção. |
| `LANGUAGETOOL_URL` | — | Servidor LanguageTool auto-alojado. |
| `PROSA_TEMPERATURE` | `0.4` | Temperatura da geração. |
| `PROSA_SECTION_WORDS` | `400` (Ollama) / `650` (OpenAI) | Dimensão máxima de cada secção enviada ao modelo. |
| `PROSA_MAX_INPUT_CHARS` | `80000` | Limite de caracteres por pedido. |
| `PROSA_MAX_OUTPUT_TOKENS` | `4000` | Máximo de tokens gerados por secção. |

### Se algo faltar

A interface mostra um aviso no topo quando a chave está em falta, quando o Ollama não responde ou quando o modelo configurado não está instalado, com a instrução concreta para resolver (por exemplo `ollama pull llama3.1`). Podes confirmar o estado em `GET /api/config`, que devolve `provider`, `ready`, `problem` e `model` sem nunca expor a chave. Nunca são apresentados resultados simulados.

## Como funciona a reescrita

1. O pedido é validado no servidor (`zod`) e o texto é segmentado por parágrafos.
2. Para cada secção, é construído um prompt de sistema com: variante linguística, modo, intensidade, extensão, regras invioláveis (preservar factos, números, datas, citações; não inventar; evitar frases feitas e sinónimos mecânicos; não introduzir erros; assinalar ambiguidades), perfil de voz, termos bloqueados e instruções do utilizador.
3. O texto do utilizador entra sempre dentro de blocos `<texto>` e o prompt indica que esse conteúdo é material a editar e nunca instruções. O mesmo se aplica ao contexto, aos exemplos e ao perfil.
4. O modelo responde num formato estruturado (`rewritten`, `ambiguities`, `terminology`), pedido através de `response_format` com esquema JSON e validado com `zod`. Se um modelo local ignorar o formato, o servidor repete o pedido uma vez com instrução explícita antes de devolver erro.
5. Os parágrafos com padrões típicos de IA ou marcas de outra variante são re-revistos uma vez, com essas expressões proibidas; a nova versão só é aceite se melhorar e mantiver os termos bloqueados.
6. Cada parágrafo é comparado com o original por embeddings; abaixo do limiar há aviso.
7. O servidor verifica termos bloqueados e a preservação de números, datas e citações, e devolve avisos.
8. A resposta chega ao browser em NDJSON, com eventos de progresso e de estado por secção.

Ficheiros principais:

```
src/lib/prompt.ts        regras e construção dos prompts
src/lib/segment.ts       divisão de documentos longos
src/lib/ai/rewrite.ts    pipeline por secções, glossário, re-revisão automática, verificação de sentido
src/lib/ai/client.ts     geração estruturada (OpenAI SDK ou API nativa do Ollama)
src/lib/ai/ollama.ts     cliente da API nativa do Ollama (chat com num_ctx e esquema, embeddings)
src/lib/ai/embeddings.ts embeddings para os dois fornecedores
src/lib/patterns.ts      listas de padrões típicos de IA (pt e en) e construções em molde
src/lib/variant.ts       marcadores de variante (pt-PT/pt-BR, en-GB/en-US)
src/lib/readability.ts   Flesch adaptado ao português e Flesch Reading Ease
src/lib/voice.ts         métricas objetivas do perfil de voz
src/lib/changes.ts       alterações frase a frase com aceitar/rejeitar
src/lib/languagetool.ts  cliente do LanguageTool
src/lib/client/docx.ts   exportação .docx com alterações registadas
src/lib/guard.ts         verificações de preservação
src/lib/metrics.ts       indicadores do texto
ollama/                  Modelfiles de exemplo (AMALIA pt-PT, Gemma)
scripts/                 calibração do limiar de semelhança com pares anotados (ASSIN)
src/app/api/rewrite      rota de reescrita (streaming NDJSON)
src/app/api/profile      rota de criação de perfil de voz
src/app/api/config       estado da configuração e do fornecedor (sem segredos)
src/app/api/check        verificação linguística via LanguageTool
src/components/          interface
tests/                   testes (Vitest)
```

## Testes

```bash
npm test          # testes unitários e das rotas, com fornecedor de IA simulado
npm run lint
npm run typecheck
```

Os testes cobrem: segmentação de documentos e de frases, contagem, métricas e legibilidade, deteção de variante e de padrões de IA, métricas de voz, alterações frase a frase, verificação de termos bloqueados e de preservação, construção de prompts (incluindo as regras por variante, o perfil com métricas e a proteção contra instruções embutidas no texto), o pipeline por secções com contexto, glossário, re-revisão automática e verificação de sentido, o cliente LanguageTool, a configuração e as rotas `/api/rewrite` e `/api/profile`. Não fazem chamadas reais à API.

Para calibrar o limiar de semelhança com um conjunto anotado (por exemplo o ASSIN, com pares pt-PT e pt-BR):

```bash
npm run calibrate -- pares.tsv --model bge-m3 --positive 4
```

## Decisões deliberadas

- **Sem detetores de IA.** Existem modelos abertos que classificam texto como «gerado» (por exemplo detetores BERT para português ou métodos zero-shot como o Binoculars). Não foram integrados: a literatura mostra que falham sistematicamente em texto parafraseado ou polido, e mostrar uma percentagem criaria uma falsa garantia. Os indicadores da Prosa medem propriedades do texto, não «humanidade».
- **Sem troca de sinónimos automática.** Ferramentas de «humanização» que substituem palavras por sinónimos ou traduzem em cadeia degradam o texto e alteram factos. A Prosa reescreve pelo sentido e verifica a preservação.
- **Listas próprias de padrões.** Não existe nenhuma lista publicada de expressões típicas de IA em português; as listas incluídas são curadas à mão e podem ser alargadas em `src/lib/patterns.ts`.

## Limites conhecidos

- A qualidade da reescrita depende do modelo. Modelos locais pequenos podem ignorar regras subtis (variante linguística, termos bloqueados); os avisos automáticos ajudam a detetar isso, mas convém rever o resultado.

- A divisão em frases usa o `Intl.Segmenter` com uma lista de abreviaturas; casos raros podem ficar mal segmentados. A contagem de sílabas para a legibilidade é aproximada.
- A deteção de variante usa listas de marcadores quase exclusivos de cada variante; não substitui um classificador treinado (como o `liaad/PtVId`), mas não exige Python nem modelos adicionais.
- A verificação de sentido por embeddings apanha desvios grandes, não contradições subtis. O limiar predefinido (0,72) é um ponto de partida; calibra-o com o script incluído.
- As verificações de preservação são conservadoras: um número escrito por extenso na revisão gera um aviso para confirmação manual.
- A persistência local usa `localStorage`; históricos muito grandes podem exceder a quota do browser, caso em que a aplicação continua a funcionar em memória.
