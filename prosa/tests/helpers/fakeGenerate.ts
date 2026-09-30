import type { z } from "zod";
import type { Generate, StructuredCall } from "@/lib/ai/client";

export interface RecordedCall {
  system: string;
  user: string;
}

/**
 * Gerador simulado para testes: não contacta nenhum fornecedor.
 * Recebe uma função que decide a resposta a partir do prompt.
 */
export function fakeGenerate(respond: (call: RecordedCall, index: number) => unknown): { generate: Generate; calls: RecordedCall[] } {
  const calls: RecordedCall[] = [];
  const generate: Generate = async <T extends z.ZodType>(call: StructuredCall<T>) => {
    const recorded = { system: call.system, user: call.user };
    calls.push(recorded);
    const raw = respond(recorded, calls.length - 1);
    return call.schema.parse(raw) as z.infer<T>;
  };
  return { generate, calls };
}

/** Extrai o conteúdo do bloco <texto> do prompt de utilizador. */
export function extractText(user: string): string {
  const match = user.match(/<texto>\n([\s\S]*?)\n<\/texto>/);
  return match ? match[1] : "";
}
