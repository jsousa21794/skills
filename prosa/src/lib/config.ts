import "server-only";

export interface ServerConfig {
  apiKey: string | undefined;
  model: string;
  effort: "low" | "medium" | "high" | "xhigh" | "max";
  sectionWords: number;
  maxInputChars: number;
  maxOutputTokens: number;
  fallbacks: boolean;
}

const EFFORTS = new Set(["low", "medium", "high", "xhigh", "max"]);

function intFromEnv(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

/** Lê a configuração do servidor a partir das variáveis de ambiente. Nunca expõe a chave. */
export function getServerConfig(): ServerConfig {
  const effortRaw = process.env.PROSA_EFFORT ?? "medium";
  const effort = (EFFORTS.has(effortRaw) ? effortRaw : "medium") as ServerConfig["effort"];
  return {
    apiKey: process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_AUTH_TOKEN || undefined,
    model: process.env.PROSA_MODEL || "claude-opus-5-5",
    effort,
    sectionWords: intFromEnv("PROSA_SECTION_WORDS", 650),
    maxInputChars: intFromEnv("PROSA_MAX_INPUT_CHARS", 80000),
    maxOutputTokens: intFromEnv("PROSA_MAX_OUTPUT_TOKENS", 16000),
    fallbacks: (process.env.PROSA_FALLBACKS ?? "default") !== "off",
  };
}

export function isConfigured(config: ServerConfig = getServerConfig()): boolean {
  return Boolean(config.apiKey);
}
