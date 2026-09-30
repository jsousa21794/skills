/** Utilitários puros de texto partilhados entre cliente e servidor. */

const WORD_RE = /[\p{L}\p{N}]+(?:['’\-][\p{L}\p{N}]+)*/gu;

export function countWords(text: string): number {
  const matches = text.match(WORD_RE);
  return matches ? matches.length : 0;
}

export function words(text: string): string[] {
  return text.match(WORD_RE) ?? [];
}

/** Divide o texto em parágrafos, preservando o texto de cada um. Linhas em branco separam parágrafos. */
export function splitParagraphs(text: string): string[] {
  return text
    .replace(/\r\n?/g, "\n")
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter((p) => p.length > 0);
}

export function joinParagraphs(paragraphs: string[]): string {
  return paragraphs.join("\n\n");
}

/**
 * Divide o texto em frases de forma aproximada. Não pretende ser perfeito:
 * serve para métricas e para dividir parágrafos demasiado longos.
 */
export function splitSentences(text: string): string[] {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (!normalized) return [];
  const parts = normalized.split(/(?<=[.!?…]["”’)\]]?)\s+(?=[\p{Lu}\p{N}"“‘(\[])/u);
  return parts.map((s) => s.trim()).filter(Boolean);
}
