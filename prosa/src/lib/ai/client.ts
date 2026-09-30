import "server-only";
import OpenAI from "openai";
import { z } from "zod";
import { getServerConfig, setupProblem, type ServerConfig } from "../config";
import { AiError } from "./errors";
import { ollamaChat, type RawCompletion } from "./ollama";

let cached: { key: string; client: OpenAI } | null = null;

export function getOpenAIClient(config: ServerConfig): OpenAI {
  const problem = setupProblem(config);
  if (problem) throw new AiError("not_configured", problem, 503);
  const key = `${config.provider}|${config.baseURL}|${config.apiKey ?? ""}`;
  if (!cached || cached.key !== key) {
    cached = {
      key,
      client: new OpenAI({
        apiKey: config.apiKey ?? "",
        baseURL: config.baseURL,
        maxRetries: 2,
        // Modelos locais em CPU podem demorar vários minutos por secção.
        timeout: 10 * 60 * 1000,
      }),
    };
  }
  return cached.client;
}

export interface StructuredCall<T extends z.ZodType> {
  system: string;
  user: string;
  schema: T;
  maxTokens?: number;
  signal?: AbortSignal;
}

/**
 * Função de geração estruturada. É a única porta de saída para o fornecedor de IA.
 * Assinatura simples para poder ser substituída nos testes.
 */
export type Generate = <T extends z.ZodType>(call: StructuredCall<T>) => Promise<z.infer<T>>;

/** Extrai um objeto JSON de uma resposta que pode vir com cercas de código ou texto à volta. */
export function extractJson(raw: string): unknown {
  const trimmed = raw.trim();
  const fenced = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const candidate = fenced ? fenced[1].trim() : trimmed;
  try {
    return JSON.parse(candidate);
  } catch {
    const start = candidate.indexOf("{");
    const end = candidate.lastIndexOf("}");
    if (start === -1 || end <= start) throw new SyntaxError("Sem objeto JSON na resposta.");
    return JSON.parse(candidate.slice(start, end + 1));
  }
}

export function jsonSchemaFor(schema: z.ZodType): Record<string, unknown> {
  const json = z.toJSONSchema(schema) as Record<string, unknown>;
  delete json.$schema;
  return json;
}

export function mapOpenAIError(error: unknown, config: ServerConfig): AiError {
  if (error instanceof AiError) return error;
  if (error instanceof OpenAI.APIUserAbortError) return new AiError("aborted", "A operação foi cancelada.", 499);
  if (error instanceof OpenAI.AuthenticationError) return new AiError("auth", "A chave da API foi rejeitada. Confirma o valor de OPENAI_API_KEY.", 401);
  if (error instanceof OpenAI.PermissionDeniedError) {
    return new AiError("auth", `Sem permissão para usar o modelo «${config.model}». Confirma o modelo e o plano da conta.`, 403);
  }
  if (error instanceof OpenAI.RateLimitError) {
    return new AiError("rate_limit", "Limite de pedidos ou de quota atingido no fornecedor de IA. Espera um pouco e tenta de novo.", 429);
  }
  if (error instanceof OpenAI.NotFoundError) return new AiError("bad_request", `O modelo «${config.model}» não foi encontrado. Corrige OPENAI_MODEL.`, 400);
  if (error instanceof OpenAI.BadRequestError) {
    return new AiError("bad_request", `O fornecedor de IA rejeitou o pedido: ${error.message}. Verifica o modelo configurado e os limites de contexto.`, 400);
  }
  if (error instanceof OpenAI.InternalServerError) {
    return new AiError("overloaded", "O fornecedor de IA devolveu um erro interno ou está sobrecarregado. Tenta novamente daqui a pouco.", 503);
  }
  if (error instanceof OpenAI.APIConnectionError) {
    return new AiError("api", `Não foi possível ligar a ${config.baseURL}. Verifica a ligação de rede do servidor.`, 502);
  }
  if (error instanceof OpenAI.APIError) return new AiError("api", `Erro do fornecedor de IA (${error.status ?? "?"}): ${error.message}`, 502);
  if (error instanceof Error && error.name === "AbortError") return new AiError("aborted", "A operação foi cancelada.", 499);
  return new AiError("api", "Ocorreu um erro inesperado ao contactar o fornecedor de IA.", 500);
}

async function openaiRequest(config: ServerConfig, system: string, user: string, schema: Record<string, unknown>, maxTokens: number, signal?: AbortSignal): Promise<RawCompletion> {
  const client = getOpenAIClient(config);
  try {
    const response = await client.chat.completions.create(
      {
        model: config.model,
        temperature: config.temperature,
        max_tokens: maxTokens,
        messages: [
          { role: "system", content: system },
          { role: "user", content: user },
        ],
        response_format: { type: "json_schema", json_schema: { name: "resposta", schema, strict: true } },
      },
      { signal },
    );
    const choice = response.choices[0];
    if (!choice) throw new AiError("parse", "O fornecedor de IA devolveu uma resposta vazia.", 502);
    if (choice.message.refusal) throw new AiError("refusal", `O modelo recusou processar este texto: ${choice.message.refusal}`, 422);
    if (choice.finish_reason === "content_filter") {
      throw new AiError("refusal", "O fornecedor de IA bloqueou a resposta por filtro de conteúdo. Nenhuma reescrita foi produzida.", 422);
    }
    return {
      content: choice.message.content ?? "",
      finish: choice.finish_reason === "length" ? "length" : "stop",
      promptTokens: response.usage?.prompt_tokens,
    };
  } catch (error) {
    throw mapOpenAIError(error, config);
  }
}

function safeParse<T extends z.ZodType>(schema: T, content: string): { success: true; data: z.infer<T> } | { success: false; reason: string } {
  let raw: unknown;
  try {
    raw = extractJson(content);
  } catch (error) {
    return { success: false, reason: error instanceof Error ? error.message : "JSON inválido" };
  }
  const result = schema.safeParse(raw);
  if (!result.success) return { success: false, reason: result.error.issues[0]?.message ?? "esquema não respeitado" };
  return { success: true, data: result.data as z.infer<T> };
}

export function createGenerate(config: ServerConfig = getServerConfig()): Generate {
  const problem = setupProblem(config);
  return async function generate<T extends z.ZodType>(call: StructuredCall<T>): Promise<z.infer<T>> {
    if (problem) throw new AiError("not_configured", problem, 503);
    const schema = jsonSchemaFor(call.schema);
    const maxTokens = call.maxTokens ?? config.maxOutputTokens;

    const request = async (extraInstruction?: string): Promise<string> => {
      const user = extraInstruction ? `${call.user}\n\n${extraInstruction}` : call.user;
      const raw =
        config.provider === "ollama"
          ? await ollamaChat(config, { system: call.system, user, schema, maxTokens, signal: call.signal })
          : await openaiRequest(config, call.system, user, schema, maxTokens, call.signal);
      if (raw.finish === "length") {
        throw new AiError(
          "too_long",
          "A resposta foi cortada por atingir o limite de tokens. Reduz PROSA_SECTION_WORDS ou aumenta PROSA_MAX_OUTPUT_TOKENS (e OLLAMA_NUM_CTX no Ollama).",
          502,
        );
      }
      return raw.content;
    };

    let content = await request();
    let parsed = safeParse(call.schema, content);
    if (!parsed.success) {
      // Alguns modelos (sobretudo locais) ignoram o formato à primeira. Uma segunda tentativa explícita.
      content = await request(
        "A resposta anterior não respeitou o formato. Responde apenas com um objeto JSON válido que siga exatamente o esquema pedido, sem texto antes ou depois.",
      );
      parsed = safeParse(call.schema, content);
    }
    if (!parsed.success) {
      throw new AiError("parse", `O modelo devolveu uma resposta que não foi possível interpretar (${parsed.reason}). Tenta novamente ou experimenta outro modelo.`, 502);
    }
    return parsed.data;
  };
}
