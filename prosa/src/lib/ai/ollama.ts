import "server-only";
import type { ServerConfig } from "../config";
import { AiError } from "./errors";

/**
 * Cliente mínimo para a API nativa do Ollama. O endpoint compatível com OpenAI
 * ignora `num_ctx`, por isso as chamadas de geração usam /api/chat, que aceita
 * a janela de contexto e o esquema JSON por pedido.
 */

interface OllamaChatResponse {
  message?: { content?: string };
  done_reason?: string;
  prompt_eval_count?: number;
  eval_count?: number;
  error?: string;
}

function headers(config: ServerConfig): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (config.apiKey && config.apiKey !== "ollama") h.Authorization = `Bearer ${config.apiKey}`;
  return h;
}

async function post<T>(config: ServerConfig, path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${config.ollamaRoot}${path}`, {
      method: "POST",
      headers: headers(config),
      body: JSON.stringify(body),
      signal,
      cache: "no-store",
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw new AiError("aborted", "A operação foi cancelada.", 499);
    throw new AiError(
      "api",
      `Não foi possível ligar ao Ollama em ${config.ollamaRoot}. Confirma que está a correr («ollama serve») e o valor de OLLAMA_BASE_URL.`,
      502,
    );
  }
  if (!response.ok) {
    let detail = "";
    try {
      const parsed = (await response.json()) as { error?: string };
      detail = parsed.error ?? "";
    } catch {
      // sem corpo JSON
    }
    if (response.status === 404 || /not found/i.test(detail)) {
      const model = (body as { model?: string }).model ?? config.model;
      throw new AiError("bad_request", `O modelo «${model}» não está instalado no Ollama. Executa «ollama pull ${model}» ou altera a configuração.`, 400);
    }
    if (response.status === 401 || response.status === 403) {
      throw new AiError("auth", "O servidor Ollama rejeitou a autenticação. Confirma OLLAMA_API_KEY, se o tiveres protegido.", response.status);
    }
    if (response.status === 429) throw new AiError("rate_limit", "O servidor Ollama está ocupado. Tenta novamente daqui a pouco.", 429);
    if (response.status >= 500) {
      throw new AiError("overloaded", `O Ollama devolveu um erro interno${detail ? `: ${detail}` : "."} Verifica a memória disponível e o valor de OLLAMA_NUM_CTX.`, 503);
    }
    throw new AiError("bad_request", `O Ollama rejeitou o pedido${detail ? `: ${detail}` : "."}`, 400);
  }
  return (await response.json()) as T;
}

export interface OllamaChatCall {
  system: string;
  user: string;
  schema: Record<string, unknown>;
  maxTokens: number;
  signal?: AbortSignal;
}

export interface RawCompletion {
  content: string;
  finish: "stop" | "length" | "other";
  promptTokens?: number;
}

export async function ollamaChat(config: ServerConfig, call: OllamaChatCall): Promise<RawCompletion> {
  const response = await post<OllamaChatResponse>(
    config,
    "/api/chat",
    {
      model: config.model,
      stream: false,
      messages: [
        { role: "system", content: call.system },
        { role: "user", content: call.user },
      ],
      format: call.schema,
      keep_alive: config.ollamaKeepAlive,
      options: {
        num_ctx: config.ollamaNumCtx,
        num_predict: call.maxTokens,
        temperature: config.temperature,
      },
    },
    call.signal,
  );
  if (response.error) throw new AiError("api", `Erro do Ollama: ${response.error}`, 502);
  return {
    content: response.message?.content ?? "",
    finish: response.done_reason === "length" ? "length" : response.done_reason === "stop" || !response.done_reason ? "stop" : "other",
    promptTokens: response.prompt_eval_count,
  };
}

export async function ollamaEmbed(config: ServerConfig, model: string, input: string[], signal?: AbortSignal): Promise<number[][]> {
  const response = await post<{ embeddings?: number[][]; error?: string }>(config, "/api/embed", { model, input, keep_alive: config.ollamaKeepAlive }, signal);
  if (!response.embeddings) throw new AiError("api", `O Ollama não devolveu embeddings para «${model}».`, 502);
  return response.embeddings;
}
