import "server-only";

export type Provider = "ollama" | "openai";

export interface ServerConfig {
  provider: Provider;
  /** Chave da API (só relevante para a OpenAI ou serviços compatíveis que a exijam). */
  apiKey: string | undefined;
  /** Endereço base compatível com a API da OpenAI (termina em /v1). */
  baseURL: string;
  model: string;
  temperature: number;
  sectionWords: number;
  maxInputChars: number;
  maxOutputTokens: number;
}

function intFromEnv(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function floatFromEnv(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const value = Number.parseFloat(raw);
  return Number.isFinite(value) && value >= 0 && value <= 2 ? value : fallback;
}

function trimSlash(url: string): string {
  return url.replace(/\/+$/, "");
}

/** Lê a configuração do servidor a partir das variáveis de ambiente. Nunca expõe a chave. */
export function getServerConfig(): ServerConfig {
  const provider: Provider = process.env.PROSA_PROVIDER === "openai" ? "openai" : "ollama";
  const isOllama = provider === "ollama";
  return {
    provider,
    apiKey: isOllama ? process.env.OLLAMA_API_KEY || "ollama" : process.env.OPENAI_API_KEY || undefined,
    baseURL: trimSlash(
      isOllama ? process.env.OLLAMA_BASE_URL || "http://localhost:11434/v1" : process.env.OPENAI_BASE_URL || "https://api.openai.com/v1",
    ),
    model: (isOllama ? process.env.OLLAMA_MODEL : process.env.OPENAI_MODEL) || (isOllama ? "llama3.1" : "gpt-4.1"),
    temperature: floatFromEnv("PROSA_TEMPERATURE", 0.4),
    // Modelos locais costumam ter janelas de contexto mais curtas; secções mais pequenas por defeito.
    sectionWords: intFromEnv("PROSA_SECTION_WORDS", isOllama ? 400 : 650),
    maxInputChars: intFromEnv("PROSA_MAX_INPUT_CHARS", 80000),
    maxOutputTokens: intFromEnv("PROSA_MAX_OUTPUT_TOKENS", 4000),
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
}

/**
 * Verifica se o fornecedor responde e, quando a lista de modelos está disponível,
 * se o modelo configurado existe. Usado apenas para informar a interface.
 */
export async function probeProvider(config: ServerConfig, timeoutMs = 2500): Promise<ProviderStatus> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${config.baseURL}/models`, {
      headers: config.apiKey ? { Authorization: `Bearer ${config.apiKey}` } : {},
      signal: controller.signal,
      cache: "no-store",
    });
    if (!response.ok) {
      if (config.provider === "openai" && (response.status === 401 || response.status === 403)) {
        return { reachable: true, modelAvailable: null, problem: "A OpenAI rejeitou a chave em OPENAI_API_KEY. Confirma o valor e reinicia o servidor." };
      }
      return { reachable: true, modelAvailable: null, problem: null };
    }
    const body = (await response.json()) as { data?: Array<{ id?: string }> };
    const ids = (body.data ?? []).map((m) => m.id ?? "").filter(Boolean);
    if (ids.length === 0) return { reachable: true, modelAvailable: null, problem: null };
    const wanted = config.model;
    const available = ids.some((id) => id === wanted || id === `${wanted}:latest` || id.split(":")[0] === wanted.split(":")[0]);
    if (!available && config.provider === "ollama") {
      return {
        reachable: true,
        modelAvailable: false,
        problem: `O Ollama está a correr mas não tem o modelo «${wanted}». Executa «ollama pull ${wanted}» ou altera OLLAMA_MODEL. Modelos disponíveis: ${ids.slice(0, 6).join(", ")}.`,
      };
    }
    return { reachable: true, modelAvailable: available, problem: null };
  } catch {
    if (config.provider === "ollama") {
      return {
        reachable: false,
        modelAvailable: null,
        problem: `Não foi possível contactar o Ollama em ${config.baseURL}. Inicia-o (a aplicação Ollama ou «ollama serve») e confirma OLLAMA_BASE_URL.`,
      };
    }
    return { reachable: false, modelAvailable: null, problem: `Não foi possível contactar ${config.baseURL}. Verifica a ligação de rede e OPENAI_BASE_URL.` };
  } finally {
    clearTimeout(timer);
  }
}
