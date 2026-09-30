import type { Language, LanguageToolIssue } from "./types";

/**
 * Cliente para um servidor LanguageTool auto-alojado (LGPL), via POST /v2/check.
 * Cobre pt-PT, pt-BR, en-GB e en-US com regras próprias de cada variante.
 */

interface LtMatch {
  message: string;
  shortMessage?: string;
  offset: number;
  length: number;
  replacements?: { value: string }[];
  rule?: { id?: string; category?: { id?: string; name?: string }; issueType?: string };
  context?: { text?: string; offset?: number; length?: number };
}

const MAX_CHUNK = 12000;

/** Divide por parágrafos em blocos abaixo do limite, guardando o desvio de cada bloco. */
function chunk(text: string): { text: string; offset: number }[] {
  if (text.length <= MAX_CHUNK) return [{ text, offset: 0 }];
  const chunks: { text: string; offset: number }[] = [];
  let start = 0;
  while (start < text.length) {
    let end = Math.min(text.length, start + MAX_CHUNK);
    if (end < text.length) {
      const breakAt = text.lastIndexOf("\n\n", end);
      if (breakAt > start + MAX_CHUNK / 2) end = breakAt;
    }
    chunks.push({ text: text.slice(start, end), offset: start });
    start = end;
  }
  return chunks;
}

export async function checkWithLanguageTool(url: string, text: string, language: Language, signal?: AbortSignal): Promise<LanguageToolIssue[]> {
  const issues: LanguageToolIssue[] = [];
  for (const part of chunk(text)) {
    const body = new URLSearchParams({ text: part.text, language, level: "default" });
    const response = await fetch(`${url}/v2/check`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
      body,
      signal,
      cache: "no-store",
    });
    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      throw new Error(`O LanguageTool respondeu com o estado ${response.status}${detail ? `: ${detail.slice(0, 200)}` : "."}`);
    }
    const data = (await response.json()) as { matches?: LtMatch[] };
    for (const match of data.matches ?? []) {
      issues.push({
        message: match.message,
        shortMessage: match.shortMessage ?? "",
        offset: match.offset + part.offset,
        length: match.length,
        category: match.rule?.category?.name ?? match.rule?.category?.id ?? "outro",
        ruleId: match.rule?.id ?? "",
        replacements: (match.replacements ?? []).slice(0, 5).map((r) => r.value),
        context: match.context?.text ?? "",
      });
    }
  }
  return issues;
}
