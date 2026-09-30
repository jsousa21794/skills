import "server-only";
import Anthropic from "@anthropic-ai/sdk";
import { betaZodOutputFormat } from "@anthropic-ai/sdk/helpers/beta/zod";
import type { z } from "zod";
import { getServerConfig, type ServerConfig } from "../config";
import { AiError } from "./errors";

let cached: { key: string; client: Anthropic } | null = null;

function getClient(config: ServerConfig): Anthropic {
  if (!config.apiKey) {
    throw new AiError(
      "not_configured",
      "Falta a chave da API. Define ANTHROPIC_API_KEY no ficheiro .env.local (vê o .env.example) e reinicia o servidor.",
      503,
    );
  }
  if (!cached || cached.key !== config.apiKey) {
    cached = { key: config.apiKey, client: new Anthropic({ apiKey: config.apiKey, maxRetries: 2, timeout: 10 * 60 * 1000 }) };
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

export function createGenerate(config: ServerConfig = getServerConfig()): Generate {
  return async function generate<T extends z.ZodType>(call: StructuredCall<T>): Promise<z.infer<T>> {
    const client = getClient(config);
    try {
      const response = await client.beta.messages.parse(
        {
          model: config.model,
          max_tokens: call.maxTokens ?? config.maxOutputTokens,
          system: [{ type: "text", text: call.system, cache_control: { type: "ephemeral" } }],
          messages: [{ role: "user", content: call.user }],
          output_config: {
            effort: config.effort,
            format: betaZodOutputFormat(call.schema),
          },
          // Se um classificador de segurança recusar o pedido, a API repete-o noutro modelo
          // adequado dentro da mesma chamada, em vez de devolver uma recusa ao utilizador.
          ...(config.fallbacks ? { betas: ["server-side-fallback-2026-07-01"], fallbacks: "default" as const } : {}),
        },
        { signal: call.signal },
      );

      if (response.stop_reason === "refusal") {
        const explanation = response.stop_details?.explanation;
        throw new AiError(
          "refusal",
          `O modelo recusou processar este texto${explanation ? `: ${explanation}` : "."} Nenhuma reescrita foi produzida.`,
          422,
        );
      }
      if (response.stop_reason === "max_tokens") {
        throw new AiError(
          "too_long",
          "A resposta excedeu o limite de tokens antes de terminar. Reduz PROSA_SECTION_WORDS ou aumenta PROSA_MAX_OUTPUT_TOKENS.",
          502,
        );
      }
      const parsed = response.parsed_output;
      if (!parsed) {
        throw new AiError("parse", "O modelo devolveu uma resposta que não foi possível interpretar. Tenta novamente.", 502);
      }
      return parsed as z.infer<T>;
    } catch (error) {
      if (error instanceof AiError) throw error;
      if (error instanceof Anthropic.AuthenticationError) {
        throw new AiError("auth", "A chave da API foi rejeitada. Confirma o valor de ANTHROPIC_API_KEY.", 401);
      }
      if (error instanceof Anthropic.PermissionDeniedError) {
        throw new AiError("auth", "A chave da API não tem permissão para usar este modelo. Confirma PROSA_MODEL e o plano da conta.", 403);
      }
      if (error instanceof Anthropic.RateLimitError) {
        throw new AiError("rate_limit", "Limite de pedidos atingido no fornecedor de IA. Espera um pouco e tenta de novo.", 429);
      }
      if (error instanceof Anthropic.BadRequestError) {
        throw new AiError(
          "bad_request",
          `O fornecedor de IA rejeitou o pedido: ${error.message}. Verifica se PROSA_MODEL e PROSA_EFFORT são válidos para o modelo escolhido.`,
          400,
        );
      }
      if (error instanceof Anthropic.NotFoundError) {
        throw new AiError("bad_request", `O modelo «${config.model}» não foi encontrado. Corrige PROSA_MODEL.`, 400);
      }
      if (error instanceof Anthropic.InternalServerError) {
        throw new AiError("overloaded", "O fornecedor de IA está temporariamente indisponível. Tenta novamente daqui a pouco.", 503);
      }
      if (error instanceof Anthropic.APIConnectionError) {
        throw new AiError("api", "Não foi possível ligar ao fornecedor de IA. Verifica a ligação de rede do servidor.", 502);
      }
      if (error instanceof Anthropic.APIError) {
        throw new AiError("api", `Erro do fornecedor de IA (${error.status ?? "?"}): ${error.message}`, 502);
      }
      if (error instanceof Error && error.name === "AbortError") {
        throw new AiError("aborted", "A operação foi cancelada.", 499);
      }
      throw error;
    }
  };
}
