import { countWords, splitSentences, words } from "./text";

/**
 * Legibilidade. Para português usa a adaptação de Martins et al. (1996) da fórmula
 * de Flesch: o valor original mais 42 pontos, porque as palavras portuguesas têm
 * mais sílabas. Para inglês usa a Flesch Reading Ease clássica. A contagem de
 * sílabas é heurística; os valores servem como indicador, não como nota.
 */

const PT_VOWELS = "aeiouáéíóúâêôãõàü";

export function countSyllablesPt(word: string): number {
  const w = word.toLowerCase().replace(/[^a-záéíóúâêôãõàüç]/g, "");
  if (!w) return 0;
  let count = 0;
  let inVowel = false;
  for (const ch of w) {
    const isVowel = PT_VOWELS.includes(ch);
    if (isVowel && !inVowel) count += 1;
    inVowel = isVowel;
  }
  // Hiatos frequentes que a contagem de grupos vocálicos junta indevidamente.
  const hiatus = w.match(/(?<![qg])u[ae]|[aeo][aeo]|[aeo][íú]|[íú][aeo]|i[aeo](?=\w)|(?<![qg])u[io]/g);
  if (hiatus) count += hiatus.length;
  return Math.max(1, count);
}

export function countSyllablesEn(word: string): number {
  let w = word.toLowerCase().replace(/[^a-z]/g, "");
  if (!w) return 0;
  if (w.length <= 3) return 1;
  w = w.replace(/(?:[^laeiouy]es|ed|[^laeiouy]e)$/, "").replace(/^y/, "");
  const groups = w.match(/[aeiouy]{1,2}/g);
  return Math.max(1, groups ? groups.length : 1);
}

export interface Readability {
  /** Índice de facilidade de leitura (0 a 100, maior é mais fácil). */
  flesch: number;
  /** Etiqueta em português para o índice. */
  label: string;
  syllablesPerWord: number;
  wordsPerSentence: number;
  /** Desvio-padrão do comprimento das frases: variação de ritmo. */
  sentenceLengthStdDev: number;
  /** Fração de frases com mais de 30 palavras. */
  longSentenceShare: number;
  /** Diversidade lexical: tipos únicos a dividir por total de palavras. */
  typeTokenRatio: number;
  formula: string;
}

function label(score: number): string {
  if (score >= 75) return "muito fácil";
  if (score >= 50) return "fácil";
  if (score >= 25) return "difícil";
  return "muito difícil";
}

export function computeReadability(text: string, language: string): Readability | null {
  const isEnglish = language.startsWith("en");
  const sentences = splitSentences(text, isEnglish ? "en" : "pt");
  const tokens = words(text);
  const totalWords = tokens.length;
  if (totalWords === 0 || sentences.length === 0) return null;

  const syllables = tokens.reduce((acc, w) => acc + (isEnglish ? countSyllablesEn(w) : countSyllablesPt(w)), 0);
  const asw = syllables / totalWords;
  const asl = totalWords / sentences.length;
  const base = 206.835 - 1.015 * asl - 84.6 * asw;
  const flesch = Math.max(0, Math.min(100, isEnglish ? base : base + 42));

  const lengths = sentences.map((s) => countWords(s)).filter((n) => n > 0);
  const mean = lengths.reduce((a, b) => a + b, 0) / Math.max(1, lengths.length);
  const variance = lengths.reduce((a, b) => a + (b - mean) ** 2, 0) / Math.max(1, lengths.length);
  const unique = new Set(tokens.map((w) => w.toLowerCase())).size;

  return {
    flesch: Math.round(flesch),
    label: label(flesch),
    syllablesPerWord: Math.round(asw * 100) / 100,
    wordsPerSentence: Math.round(asl * 10) / 10,
    sentenceLengthStdDev: Math.round(Math.sqrt(variance) * 10) / 10,
    longSentenceShare: Math.round((lengths.filter((n) => n > 30).length / Math.max(1, lengths.length)) * 100) / 100,
    typeTokenRatio: Math.round((unique / totalWords) * 100) / 100,
    formula: isEnglish ? "Flesch Reading Ease" : "Flesch adaptado ao português (Martins et al., 1996)",
  };
}
