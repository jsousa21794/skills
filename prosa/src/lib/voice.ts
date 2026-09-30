import { computeReadability } from "./readability";
import { countWords, splitParagraphs, splitSentences } from "./text";
import type { Language, VoiceMetrics } from "./types";

/**
 * Métricas objetivas extraídas das amostras de escrita do utilizador.
 * Complementam a descrição feita pelo modelo com restrições mensuráveis.
 */

const CONNECTORS_PT = ["mas", "porém", "contudo", "todavia", "no entanto", "porque", "pois", "já que", "embora", "apesar de", "ou seja", "isto é", "por isso", "portanto", "logo", "então", "aliás", "de resto", "afinal", "entretanto", "assim", "ainda assim", "mesmo assim", "além disso", "por outro lado", "de facto", "de fato", "na verdade", "na prática", "por exemplo"];
const CONNECTORS_EN = ["but", "however", "yet", "because", "since", "although", "though", "so", "therefore", "then", "actually", "in fact", "for example", "for instance", "still", "instead", "also", "besides", "meanwhile", "anyway", "after all", "in practice", "that is", "in other words", "moreover", "furthermore"];

function per100(count: number, totalWords: number): number {
  return totalWords > 0 ? Math.round((count / totalWords) * 100 * 10) / 10 : 0;
}

function countRe(text: string, re: RegExp): number {
  return (text.match(re) ?? []).length;
}

export function computeVoiceMetrics(samples: string[], language: Language): VoiceMetrics {
  const text = samples.join("\n\n");
  const isEnglish = language.startsWith("en");
  const locale = isEnglish ? "en" : "pt";
  const totalWords = countWords(text);
  const sentences = splitSentences(text, locale);
  const lengths = sentences.map((s) => countWords(s)).filter((n) => n > 0);
  const mean = lengths.reduce((a, b) => a + b, 0) / Math.max(1, lengths.length);
  const variance = lengths.reduce((a, b) => a + (b - mean) ** 2, 0) / Math.max(1, lengths.length);
  const paragraphs = splitParagraphs(text);
  const lower = text.toLowerCase();

  const connectors = (isEnglish ? CONNECTORS_EN : CONNECTORS_PT)
    .map((c) => ({ term: c, count: countRe(lower, new RegExp(`(?<![\\p{L}-])${c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\p{L}-])`, "gu")) }))
    .filter((c) => c.count > 0)
    .sort((a, b) => b.count - a.count)
    .slice(0, 6);

  const readability = computeReadability(text, language);

  const metrics: VoiceMetrics = {
    sampleWords: totalWords,
    averageSentenceLength: Math.round(mean * 10) / 10,
    sentenceLengthStdDev: Math.round(Math.sqrt(variance) * 10) / 10,
    longSentenceShare: Math.round((lengths.filter((n) => n > 30).length / Math.max(1, lengths.length)) * 100) / 100,
    shortSentenceShare: Math.round((lengths.filter((n) => n <= 8).length / Math.max(1, lengths.length)) * 100) / 100,
    averageParagraphWords: Math.round(totalWords / Math.max(1, paragraphs.length)),
    commasPer100Words: per100(countRe(text, /,/g), totalWords),
    semicolonsPer100Words: per100(countRe(text, /;/g), totalWords),
    colonsPer100Words: per100(countRe(text, /:/g), totalWords),
    dashesPer100Words: per100(countRe(text, /[—–]| - /g), totalWords),
    parenthesesPer100Words: per100(countRe(text, /\(/g), totalWords),
    exclamationShare: Math.round((countRe(text, /!/g) / Math.max(1, sentences.length)) * 100) / 100,
    questionShare: Math.round((countRe(text, /\?/g) / Math.max(1, sentences.length)) * 100) / 100,
    firstPersonPer100Words: per100(countRe(lower, isEnglish ? /\b(?:i|we|my|our|me|us)\b/g : /(?<!\p{L})(?:eu|nós|meu|minha|meus|minhas|nosso|nossa|nossos|nossas|me|nos)(?!\p{L})/gu), totalWords),
    typeTokenRatio: readability?.typeTokenRatio ?? 0,
    connectors,
    address: null,
    enclisisPer100Words: null,
    contractionsPer100Words: null,
  };

  if (!isEnglish) {
    const tu = countRe(lower, /(?<!\p{L})(?:tu|te|ti|teu|tua|teus|tuas|contigo)(?!\p{L})/gu);
    const voce = countRe(lower, /(?<!\p{L})(?:você|vocês|o senhor|a senhora|seu|sua)(?!\p{L})/gu);
    metrics.address = tu === 0 && voce === 0 ? "impessoal" : tu >= voce * 1.5 ? "tu" : voce >= tu * 1.5 ? "você" : "misto";
    metrics.enclisisPer100Words = per100(countRe(lower, /\p{L}+-(?:me|te|se|lhe|nos|vos|lhes|o|a|os|as|lo|la|los|las|no|na|nos|nas)(?!\p{L})/gu), totalWords);
  } else {
    metrics.contractionsPer100Words = per100(countRe(lower, /\b\w+(?:'s|'t|'re|'ll|'ve|'d|'m)\b/g), totalWords);
  }

  return metrics;
}

/** Descrição em texto das métricas, para injetar no prompt como restrições. */
export function describeVoiceMetrics(m: VoiceMetrics): string {
  const rhythm =
    m.sentenceLengthStdDev >= 9 ? "grande variação de ritmo" : m.sentenceLengthStdDev >= 5 ? "variação moderada de ritmo" : "ritmo bastante regular";
  const parts = [
    `frases com cerca de ${m.averageSentenceLength} palavras em média (${rhythm}; ${Math.round(m.longSentenceShare * 100)}% acima de 30 palavras, ${Math.round(m.shortSentenceShare * 100)}% com 8 ou menos)`,
    `parágrafos de cerca de ${m.averageParagraphWords} palavras`,
    `pontuação por 100 palavras: ${m.commasPer100Words} vírgulas, ${m.semicolonsPer100Words} pontos e vírgulas, ${m.colonsPer100Words} dois pontos, ${m.dashesPer100Words} travessões, ${m.parenthesesPer100Words} parênteses`,
    m.questionShare > 0.05 ? `usa perguntas (${Math.round(m.questionShare * 100)}% das frases)` : "raramente usa perguntas",
    m.exclamationShare > 0.05 ? `usa exclamações (${Math.round(m.exclamationShare * 100)}% das frases)` : "praticamente não usa exclamações",
    m.firstPersonPer100Words >= 2 ? `escreve na primeira pessoa (${m.firstPersonPer100Words} marcas por 100 palavras)` : "evita a primeira pessoa",
  ];
  if (m.address) parts.push(`tratamento do leitor: ${m.address}`);
  if (m.enclisisPer100Words !== null) parts.push(`ênclise pronominal: ${m.enclisisPer100Words} por 100 palavras`);
  if (m.contractionsPer100Words !== null) parts.push(`contrações: ${m.contractionsPer100Words} por 100 palavras`);
  if (m.connectors.length > 0) parts.push(`conectores mais usados: ${m.connectors.map((c) => `«${c.term}»`).join(", ")}`);
  return parts.join("; ") + ".";
}

export function metricsAreMeaningful(m: VoiceMetrics | null | undefined): m is VoiceMetrics {
  return Boolean(m && m.sampleWords >= 60);
}
