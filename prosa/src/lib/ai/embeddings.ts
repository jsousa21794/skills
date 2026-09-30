import "server-only";
import type { ServerConfig } from "../config";
import { getOpenAIClient, mapOpenAIError } from "./client";
import { ollamaEmbed } from "./ollama";

/** Função de embeddings: recebe textos, devolve um vetor por texto. Substituível nos testes. */
export type Embed = (texts: string[], signal?: AbortSignal) => Promise<number[][]>;

export function createEmbed(config: ServerConfig): Embed | null {
  const model = config.embedModel;
  if (!model) return null;
  if (config.provider === "ollama") {
    return (texts, signal) => ollamaEmbed(config, model, texts, signal);
  }
  return async (texts, signal) => {
    try {
      const client = getOpenAIClient(config);
      const response = await client.embeddings.create({ model, input: texts }, { signal });
      return response.data.sort((a, b) => a.index - b.index).map((d) => d.embedding);
    } catch (error) {
      throw mapOpenAIError(error, config);
    }
  };
}

export function cosine(a: number[], b: number[]): number {
  let dot = 0;
  let na = 0;
  let nb = 0;
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i += 1) {
    dot += a[i] * b[i];
    na += a[i] * a[i];
    nb += b[i] * b[i];
  }
  if (na === 0 || nb === 0) return 0;
  return dot / (Math.sqrt(na) * Math.sqrt(nb));
}
