import { countWords, splitParagraphs, splitSentences, words } from "./text";

export interface Frequency {
  term: string;
  count: number;
}

export interface TextMetrics {
  words: number;
  characters: number;
  sentences: number;
  paragraphs: number;
  averageSentenceLength: number;
  longestSentence: number;
  shortestSentence: number;
  sentenceLengths: number[];
  /** Palavras com conteúdo repetidas mais vezes. */
  repeatedWords: Frequency[];
  /** Pares de palavras repetidos. */
  repeatedBigrams: Frequency[];
  /** Palavras com que as frases começam, quando se repetem. */
  repeatedOpeners: Frequency[];
}

const STOPWORDS = new Set(
  (
    "a o as os um uma uns umas de do da dos das em no na nos nas por para com sem sob sobre e ou mas que se não sim ao à aos às pelo pela pelos pelas este esta estes estas esse essa esses essas aquele aquela isto isso aquilo eu tu ele ela nós vós eles elas me te lhe nos vos lhes meu minha teu tua seu sua nosso nossa é são foi foram ser está estão estava ter tem têm há como mais menos muito muita muitos muitas já também só ainda quando onde qual quais quem cujo cuja porque então assim até entre num numa nuns numas dum duma " +
    "the a an of to in on at for from by with without and or but nor so yet that this these those it its is are was were be been being have has had do does did as if then than too very can could will would shall should may might must not no yes he she they we you i me him her them us my your his their our who whom whose which what when where why how there here into onto over under about after before between through during each such only also just"
  ).split(/\s+/),
);

function topFrequencies(map: Map<string, number>, minCount: number, limit: number): Frequency[] {
  return Array.from(map.entries())
    .filter(([, count]) => count >= minCount)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
    .map(([term, count]) => ({ term, count }));
}

export function computeMetrics(text: string): TextMetrics {
  const sentences = splitSentences(text);
  const sentenceLengths = sentences.map((s) => countWords(s)).filter((n) => n > 0);
  const totalWords = countWords(text);
  const tokens = words(text).map((w) => w.toLowerCase());

  const wordCounts = new Map<string, number>();
  const bigramCounts = new Map<string, number>();
  for (let i = 0; i < tokens.length; i += 1) {
    const w = tokens[i];
    if (w.length > 3 && !STOPWORDS.has(w) && !/^\d+$/.test(w)) {
      wordCounts.set(w, (wordCounts.get(w) ?? 0) + 1);
    }
    if (i + 1 < tokens.length) {
      const next = tokens[i + 1];
      if (!STOPWORDS.has(w) && !STOPWORDS.has(next) && w.length > 2 && next.length > 2) {
        const key = `${w} ${next}`;
        bigramCounts.set(key, (bigramCounts.get(key) ?? 0) + 1);
      }
    }
  }

  const openerCounts = new Map<string, number>();
  for (const sentence of sentences) {
    const first = words(sentence).slice(0, 2).join(" ").toLowerCase();
    if (first) openerCounts.set(first, (openerCounts.get(first) ?? 0) + 1);
  }

  return {
    words: totalWords,
    characters: text.length,
    sentences: sentenceLengths.length,
    paragraphs: splitParagraphs(text).length,
    averageSentenceLength: sentenceLengths.length ? Math.round((totalWords / sentenceLengths.length) * 10) / 10 : 0,
    longestSentence: sentenceLengths.length ? Math.max(...sentenceLengths) : 0,
    shortestSentence: sentenceLengths.length ? Math.min(...sentenceLengths) : 0,
    sentenceLengths,
    repeatedWords: topFrequencies(wordCounts, 3, 8),
    repeatedBigrams: topFrequencies(bigramCounts, 2, 6),
    repeatedOpeners: topFrequencies(openerCounts, 2, 6),
  };
}
