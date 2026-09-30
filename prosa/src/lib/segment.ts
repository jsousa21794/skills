import { countWords, splitParagraphs, splitSentences } from "./text";

export interface Segment {
  index: number;
  text: string;
  words: number;
}

/**
 * Divide um documento em secções para processamento sequencial.
 * Junta parágrafos consecutivos até ao limite de palavras; um parágrafo
 * que por si só exceda o limite é partido por frases.
 */
export function segmentDocument(text: string, maxWords: number): Segment[] {
  const limit = Math.max(50, maxWords);
  const paragraphs = splitParagraphs(text);
  const segments: Segment[] = [];
  let current: string[] = [];
  let currentWords = 0;

  const flush = () => {
    if (current.length === 0) return;
    const joined = current.join("\n\n");
    segments.push({ index: segments.length, text: joined, words: currentWords });
    current = [];
    currentWords = 0;
  };

  for (const paragraph of paragraphs) {
    const pWords = countWords(paragraph);
    if (pWords > limit) {
      flush();
      for (const chunk of splitLongParagraph(paragraph, limit)) {
        segments.push({ index: segments.length, text: chunk, words: countWords(chunk) });
      }
      continue;
    }
    if (currentWords + pWords > limit && current.length > 0) {
      flush();
    }
    current.push(paragraph);
    currentWords += pWords;
  }
  flush();
  return segments;
}

function splitLongParagraph(paragraph: string, limit: number): string[] {
  const sentences = splitSentences(paragraph);
  if (sentences.length <= 1) return [paragraph];
  const chunks: string[] = [];
  let current: string[] = [];
  let currentWords = 0;
  for (const sentence of sentences) {
    const sWords = countWords(sentence);
    if (currentWords + sWords > limit && current.length > 0) {
      chunks.push(current.join(" "));
      current = [];
      currentWords = 0;
    }
    current.push(sentence);
    currentWords += sWords;
  }
  if (current.length > 0) chunks.push(current.join(" "));
  return chunks;
}

/** Devolve as últimas `n` palavras de um texto, para dar contexto à secção seguinte. */
export function tail(text: string, n: number): string {
  const tokens = text.trim().split(/\s+/);
  if (tokens.length <= n) return text.trim();
  return "… " + tokens.slice(-n).join(" ");
}
