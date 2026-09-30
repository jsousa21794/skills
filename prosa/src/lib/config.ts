import "server-only";

export type Provider = "ollama" | "openai";

export interface ServerConfig {
  provider: Provider;
  /** Chave da API (só relevante para a OpenAI ou serviços compatíveis que a exijam). */
  apiKey: string | undefined;
  /** Endereço base compatível com a API da OpenAI (termina em /v1). */
  baseURL: string;
  /** Raiz do servidor Ollama (sem /v1), para a API nativa. */
  ollamaRoot: string;
  /** Janela de contexto pedida ao Ollama em cada pedido. */
  ollamaNumCtx: number;
  ollamaKeepAlive: string;
  model: string;
  temperature: number;
  sectionWords: number;
  maxInputChars: number;
  maxOutputTokens: number;
  /** Modelo de embeddings para a verificação de sentido; null desativa. */
  embedModel: string | null;
  similarityMin: number;
  autoPolish: boolean;
  maxPolishPerSection: number;
  languageToolUrl: string | null;
}

function intFromEnv(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function floatFromEnv(name: string, fallback: number, min = 0, max = 2): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const value = Number.parseFloat(raw);
  return Number.isFinite(value) && value >= min && value <= max ? value : fallback;
}

function trimSlash(url: string): string {
  return url.replace(/\/+$/, "");
}

/** Lê a configuração do servidor a partir das variáveis de ambiente. Nunca expõe a chave. */
export function getServerConfig(): ServerConfig {
  const provider: Provider = process.env.PROSA_PROVIDER === "openai" ? "openai" : "ollama";
  const isOllama = provider === "ollama";
  const ollamaBase = trimSlash(process.env.OLLAMA_BASE_URL || "http://localhost:11434/v1");
  const meaningCheckOff = (process.env.PROSA_MEANING_CHECK ?? "on") === "off";
  const embedDefault = isOllama ? "bge-m3" : "text-embedding-3-small";
  return {
    provider,
    apiKey: isOllama ? process.env.OLLAMA_API_KEY || "ollama" : process.env.OPENAI_API_KEY || undefined,
    baseURL: isOllama ? ollamaBase : trimSlash(process.env.OPENAI_BASE_URL || "https://api.openai.com/v1"),
    ollamaRoot: ollamaBase.replace(/\/v1$/, ""),
    ollamaNumCtx: intFromEnv("OLLAMA_NUM_CTX", 8192),
    ollamaKeepAlive: process.env.OLLAMA_KEEP_ALIVE || "10m",
    model: (isOllama ? process.env.OLLAMA_MODEL : process.env.OPENAI_MODEL) || (isOllama ? "llama3.1" : "gpt-4.1"),
    temperature: floatFromEnv("PROSA_TEMPERATURE", 0.4),
    // Modelos locais costumam ter janelas de contexto mais curtas; secções mais pequenas por defeito.
    sectionWords: intFromEnv("PROSA_SECTION_WORDS", isOllama ? 400 : 650),
    maxInputChars: intFromEnv("PROSA_MAX_INPUT_CHARS", 80000),
    maxOutputTokens: intFromEnv("PROSA_MAX_OUTPUT_TOKENS", 4000),
    embedModel: meaningCheckOff ? null : process.env.PROSA_EMBED_MODEL || embedDefault,
    similarityMin: floatFromEnv("PROSA_SIMILARITY_MIN", 0.72, 0, 1),
    autoPolish: (process.env.PROSA_AUTO_POLISH ?? "on") !== "off",
    maxPolishPerSection: intFromEnv("PROSA_MAX_POLISH", 3),
    languageToolUrl: process.env.LANGUAGETOOL_URL ? trimSlash(process.env.LANGUAGETOOL_URL) : null,
  };
}

/**
 * Devolve uma mensagem quando falta algo na configuração estática (sem contactar a rede),
 * ou null quando está tudo definido.
 */
export function setupProblem(config: ServerConfig = getServerConfig()): string | null {
  if (config.provider === "openai" && !config.apiKey) {
    return "Falta a chave da API. Define OPENAI_API_KEY em .env.local (vê o .env.example) e reinicia o servidor. Sem ela não há reescrita real.";
  }
  return null;
}

export function isConfigured(config: ServerConfig = getServerConfig()): boolean {
  return setupProblem(config) === null;
}

export interface ProviderStatus {
  reachable: boolean;
  modelAvailable: boolean | null;
  problem: string | null;
  modelContextLength: number | null;
}

function matchesModel(ids: string[], wanted: string): boolean {
  return ids.some((id) => id === wanted || id === `${wanted}:latest` || id.split(":")[0] === wanted.split(":")[0]);
}

/**
 * Verifica se o fornecedor responde e, quando a lista de modelos está disponível,
 * se o modelo configurado existe. No Ollama lê também o contexto máximo do modelo.
 */
export async function probeProvider(config: ServerConfig, timeoutMs = 2500): Promise<ProviderStatus> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const none: ProviderStatus = { reachable: true, modelAvailable: null, problem: null, modelContextLength: null };
  try {
    const response = await fetch(`${config.baseURL}/models`, {
      headers: config.apiKey ? { Authorization: `Bearer ${config.apiKey}` } : {},
      signal: controller.signal,
      cache: "no-store",
    });
    if (!response.ok) {
      if (config.provider === "openai" && (response.status === 401 || response.status === 403)) {
        return { ...none, problem: "A OpenAI rejeitou a chave em OPENAI_API_KEY. Confirma o valor e reinicia o servidor." };
      }
      return none;
    }
    const body = (await response.json()) as { data?: Array<{ id?: string }> };
    const ids = (body.data ?? []).map((m) => m.id ?? "").filter(Boolean);
    if (ids.length === 0) return none;
    const available = matchesModel(ids, config.model);
    if (!available && config.provider === "ollama") {
      return {
        ...none,
        modelAvailable: false,
        problem: `O Ollama está a correr mas não tem o modelo «${config.model}». Executa «ollama pull ${config.model}» ou altera OLLAMA_MODEL. Modelos disponíveis: ${ids.slice(0, 6).join(", ")}.`,
      };
    }
    let modelContextLength: number | null = null;
    if (config.provider === "ollama") {
      modelContextLength = await ollamaModelContext(config, controller.signal);
    }
    return { reachable: true, modelAvailable: available, problem: null, modelContextLength };
  } catch {
    if (config.provider === "ollama") {
      return {
        reachable: false,
        modelAvailable: null,
        modelContextLength: null,
        problem: `Não foi possível contactar o Ollama em ${config.baseURL}. Inicia-o (a aplicação Ollama ou «ollama serve») e confirma OLLAMA_BASE_URL.`,
      };
    }
    return { reachable: false, modelAvailable: null, modelContextLength: null, problem: `Não foi possível contactar ${config.baseURL}. Verifica a ligação de rede e OPENAI_BASE_URL.` };
  } finally {
    clearTimeout(timer);
  }
}

/** Lê o contexto máximo do modelo através de POST /api/show do Ollama. */
export async function ollamaModelContext(config: ServerConfig, signal?: AbortSignal): Promise<number | null> {
  try {
    const response = await fetch(`${config.ollamaRoot}/api/show`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(config.apiKey && config.apiKey !== "ollama" ? { Authorization: `Bearer ${config.apiKey}` } : {}) },
      body: JSON.stringify({ model: config.model }),
      signal,
      cache: "no-store",
    });
    if (!response.ok) return null;
    const body = (await response.json()) as { model_info?: Record<string, unknown> };
    const info = body.model_info ?? {};
    for (const [key, value] of Object.entries(info)) {
      if (key.endsWith(".context_length") && typeof value === "number") return value;
    }
    return null;
  } catch {
    return null;
  }
}

/** Verifica se o servidor LanguageTool responde. */
export async function probeLanguageTool(url: string, timeoutMs = 2000): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${url}/v2/languages`, { signal: controller.signal, cache: "no-store" });
    return response.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}
