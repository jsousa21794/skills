import "server-only";
import { getServerConfig } from "../config";
import { createGenerate } from "./client";
import type { RewriteRuntime } from "./rewrite";

/** Constrói o runtime de reescrita a partir da configuração do servidor. */
export function createRuntime(signal?: AbortSignal): RewriteRuntime {
  const config = getServerConfig();
  return {
    generate: createGenerate(config),
    model: config.model,
    sectionWords: config.sectionWords,
    maxOutputTokens: config.maxOutputTokens,
    signal,
  };
}
