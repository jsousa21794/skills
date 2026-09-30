# Prosa

Aplicação web de revisão e reescrita de texto. Transforma textos rígidos, artificiais ou genéricos em textos naturais e fluidos, adequados à voz de quem escreve, preservando o significado e o rigor do original.

Construída com Next.js (App Router), TypeScript e Tailwind CSS. As chamadas ao fornecedor de IA (API da Anthropic, SDK oficial) acontecem exclusivamente no servidor.

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
- **Documentos longos**: divididos por parágrafos em secções (por defeito até 650 palavras) e processados em sequência. Cada secção recebe o final da secção anterior já revista e um glossário de decisões terminológicas acumulado, para manter consistência.
- **Notas da revisão**: ambiguidades que o modelo não conseguiu resolver com segurança, e avisos automáticos quando números, datas ou citações do original não aparecem iguais na revisão.
- **Tema claro e escuro**, contagem de palavras, barra de progresso por secção, cancelamento e mensagens de erro úteis.
- **Privacidade**: nenhum texto é guardado no servidor. As preferências de interface e o perfil ficam no `localStorage`; o histórico de textos só é guardado se o utilizador o ativar.

## Requisitos

- Node.js 20.9 ou superior (testado com Node 22)
- Uma chave da API da Anthropic

## Instalação

```bash
cd prosa
npm install
cp .env.example .env.local
```

Edita `.env.local` e define `ANTHROPIC_API_KEY`. Depois:

```bash
npm run dev
```

Abre http://localhost:3000.

Para produção:

```bash
npm run build
npm start
```

## Configuração

Todas as variáveis estão descritas em `.env.example`:

| Variável | Predefinição | Descrição |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | — | Obrigatória. Sem ela a interface mostra um aviso e a reescrita fica desativada. Nunca são apresentados resultados simulados. |
| `PROSA_MODEL` | `claude-opus-5-5` | Modelo usado nas reescritas e na análise de perfil. |
| `PROSA_EFFORT` | `medium` | Esforço de raciocínio: `low`, `medium`, `high`, `xhigh` ou `max`. Modelos mais antigos podem não aceitar este parâmetro. |
| `PROSA_SECTION_WORDS` | `650` | Dimensão máxima de cada secção enviada ao modelo. |
| `PROSA_MAX_INPUT_CHARS` | `80000` | Limite de caracteres por pedido. |
| `PROSA_MAX_OUTPUT_TOKENS` | `16000` | Máximo de tokens gerados por secção. |
| `PROSA_FALLBACKS` | `default` | Com `default`, se um classificador de segurança recusar o pedido a API repete-o noutro modelo adequado dentro da mesma chamada (beta `server-side-fallback`). Define `off` para desativar. |

O SDK também respeita `ANTHROPIC_BASE_URL`, caso uses um proxy.

### Se faltar a credencial

1. Cria uma chave em https://console.anthropic.com/.
2. Copia `.env.example` para `.env.local` e cola a chave em `ANTHROPIC_API_KEY`.
3. Reinicia o servidor (`npm run dev`).

Podes confirmar o estado em `GET /api/config`, que devolve `{"configured": true|false, "model": ...}` sem nunca expor a chave. Se a chave for rejeitada, a aplicação mostra a mensagem correspondente (chave inválida, sem permissão para o modelo, limite de pedidos, modelo inexistente).

## Como funciona a reescrita

1. O pedido é validado no servidor (`zod`) e o texto é segmentado por parágrafos.
2. Para cada secção, é construído um prompt de sistema com: variante linguística, modo, intensidade, extensão, regras invioláveis (preservar factos, números, datas, citações; não inventar; evitar frases feitas e sinónimos mecânicos; não introduzir erros; assinalar ambiguidades), perfil de voz, termos bloqueados e instruções do utilizador.
3. O texto do utilizador entra sempre dentro de blocos `<texto>` e o prompt indica que esse conteúdo é material a editar e nunca instruções. O mesmo se aplica ao contexto, aos exemplos e ao perfil.
4. O modelo responde num formato estruturado (`rewritten`, `ambiguities`, `terminology`), validado com `zod`.
5. O servidor verifica termos bloqueados e a preservação de números, datas e citações, e devolve avisos.
6. A resposta chega ao browser em NDJSON, com eventos de progresso por secção.

Ficheiros principais:

```
src/lib/prompt.ts        regras e construção dos prompts
src/lib/segment.ts       divisão de documentos longos
src/lib/ai/rewrite.ts    pipeline por secções, glossário, verificação de bloqueios
src/lib/ai/client.ts     única ligação ao fornecedor de IA (servidor)
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

- A divisão em frases é heurística e serve apenas para métricas e para partir parágrafos muito longos.
- As verificações de preservação são conservadoras: um número escrito por extenso na revisão gera um aviso para confirmação manual.
- A persistência local usa `localStorage`; históricos muito grandes podem exceder a quota do browser, caso em que a aplicação continua a funcionar em memória.
