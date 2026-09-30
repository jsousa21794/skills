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
- **Histórico de versões**: cada reescrita cria uma versão. É possível restaurar, comparar com a versão atual e ativar, opcionalmente, a persistência no browser. Um botão elimina todos os dados guardados.
- **Comparação**: diferenças ao nível da palavra entre o revisto e o original (ou outra versão do histórico).
- **Copiar, descarregar (.txt) e restaurar o original.**
- **Perfil de voz**: cola exemplos da tua escrita e a aplicação extrai um perfil editável (vocabulário, ritmo, formalidade, construção frásica, o que evitar). O perfil é aplicado sem copiar passagens dos exemplos; os exemplos não são guardados.
- **Indicadores transparentes**: palavras, frases, parágrafos, média e máximo de palavras por frase, ritmo frase a frase e repetições salientes. Não há percentagens de «escrita humana» nem promessas sobre detetores de IA.
- **Documentos longos**: divididos por parágrafos em secções (por defeito até 400 palavras com Ollama e 650 com OpenAI) e processados em sequência. Cada secção recebe o final da secção anterior já revista e um glossário de decisões terminológicas acumulado, para manter consistência.
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

O Ollama corre por defeito em `http://localhost:11434`. Modelos maiores (por exemplo `qwen2.5:14b` ou `gemma3:12b`) produzem reescritas mais fiéis do que modelos pequenos, sobretudo em português. Se o modelo tiver uma janela de contexto curta, mantém `PROSA_SECTION_WORDS` baixo.

### Opção B: OpenAI

1. Cria uma chave em https://platform.openai.com/api-keys.
2. Em `.env.local`, define `PROSA_PROVIDER=openai`, `OPENAI_API_KEY` e, se quiseres, `OPENAI_MODEL`.
3. `npm run dev`.

Serviços compatíveis com a API da OpenAI (Azure OpenAI, Groq, Together, LM Studio, etc.) funcionam definindo `OPENAI_BASE_URL`.

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
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Endereço da API compatível com OpenAI do Ollama. |
| `OLLAMA_MODEL` | `llama3.1` | Modelo local a usar (tem de estar instalado). |
| `OLLAMA_API_KEY` | — | Só se o Ollama estiver atrás de um proxy que exija chave. |
| `OPENAI_API_KEY` | — | Obrigatória com `PROSA_PROVIDER=openai`. |
| `OPENAI_MODEL` | `gpt-4.1` | Modelo da OpenAI. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Para serviços compatíveis. |
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
5. O servidor verifica termos bloqueados e a preservação de números, datas e citações, e devolve avisos.
6. A resposta chega ao browser em NDJSON, com eventos de progresso por secção.

Ficheiros principais:

```
src/lib/prompt.ts        regras e construção dos prompts
src/lib/segment.ts       divisão de documentos longos
src/lib/ai/rewrite.ts    pipeline por secções, glossário, verificação de bloqueios
src/lib/ai/client.ts     única ligação ao fornecedor de IA (servidor, formato OpenAI)
src/lib/guard.ts         verificações de preservação
src/lib/metrics.ts       indicadores do texto
src/app/api/rewrite      rota de reescrita (streaming NDJSON)
src/app/api/profile      rota de criação de perfil de voz
src/app/api/config       estado da configuração (sem segredos)
src/components/          interface
tests/                   testes (Vitest)
```

## Testes

```bash
npm test          # testes unitários e das rotas, com fornecedor de IA simulado
npm run lint
npm run typecheck
```

Os testes cobrem: segmentação de documentos, contagem e métricas, verificação de termos bloqueados e de preservação, construção de prompts (incluindo as regras por variante e a proteção contra instruções embutidas no texto), o pipeline por secções com contexto e glossário, e as rotas `/api/rewrite` e `/api/profile` (validação, ausência de chave, fluxo NDJSON, mapeamento de erros). Não fazem chamadas reais à API.

## Limites conhecidos

- A qualidade da reescrita depende do modelo. Modelos locais pequenos podem ignorar regras subtis (variante linguística, termos bloqueados); os avisos automáticos ajudam a detetar isso, mas convém rever o resultado.

- A divisão em frases é heurística e serve apenas para métricas e para partir parágrafos muito longos.
- As verificações de preservação são conservadoras: um número escrito por extenso na revisão gera um aviso para confirmação manual.
- A persistência local usa `localStorage`; históricos muito grandes podem exceder a quota do browser, caso em que a aplicação continua a funcionar em memória.
