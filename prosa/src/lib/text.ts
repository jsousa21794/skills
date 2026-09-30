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
 * Abreviaturas que não terminam frase. O Intl.Segmenter não aplica as supressões do CLDR,
 * por isso os segmentos que terminam numa destas são fundidos com o seguinte.
 */
const ABBREVIATIONS = new Set(
  (
    "sr sra srs sras dr dra drs dras prof profa profs eng engª exmo exma exmos exmas ex il ilmo ilma art arts n nº no nos núm num pág págs p pp séc sécs cap caps vol vols ed eds fig figs tel tlm av avª r lg pç pcs ltda lda s.a cia cª vs cf ex " +
    "mr mrs ms dr prof sr jr st mt ave blvd rd inc ltd co corp dept est fig figs vol vols no nos ch pp pg pgs ed eds approx govt jan feb mar apr jun jul aug sep sept oct nov dec mon tue wed thu fri sat sun e.g i.e al vs cf"
  ).split(/\s+/),
);

function endsWithAbbreviation(segment: string): boolean {
  const trimmed = segment.trimEnd();
  if (!trimmed.endsWith(".")) return false;
  const lastWord = trimmed.slice(0, -1).split(/[\s(«“"']+/).pop()?.toLowerCase() ?? "";
  if (!lastWord) return false;
  // Iniciais isoladas («J. Silva») e abreviaturas conhecidas.
  if (lastWord.length === 1 && /\p{L}/u.test(lastWord)) return true;
  return ABBREVIATIONS.has(lastWord) || ABBREVIATIONS.has(lastWord.replace(/\./g, ""));
}

function segmenterFor(locale: string): Intl.Segmenter | null {
  try {
    if (typeof Intl === "undefined" || typeof Intl.Segmenter !== "function") return null;
    return new Intl.Segmenter(locale, { granularity: "sentence" });
  } catch {
    return null;
  }
}

/**
 * Divide o texto em frases. Usa o Intl.Segmenter quando existe, com correção
 * de abreviaturas, e um separador por pontuação como alternativa.
 */
export function splitSentences(text: string, locale = "pt"): string[] {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (!normalized) return [];
  const segmenter = segmenterFor(locale);
  let raw: string[];
  if (segmenter) {
    raw = Array.from(segmenter.segment(normalized), (s) => s.segment);
  } else {
    raw = normalized.split(/(?<=[.!?…]["”’)\]]?)\s+(?=[\p{Lu}\p{N}"“‘(\[])/u);
  }
  const merged: string[] = [];
  for (const piece of raw) {
    if (merged.length > 0 && endsWithAbbreviation(merged[merged.length - 1])) {
      merged[merged.length - 1] += piece;
    } else {
      merged.push(piece);
    }
  }
  return merged.map((s) => s.trim()).filter(Boolean);
}

export type TextToken = { kind: "sentence"; text: string } | { kind: "break" };

/**
 * Converte o texto numa sequência de frases e quebras de parágrafo, para
 * comparação frase a frase e reconstrução fiel do texto.
 */
export function tokenizeSentences(text: string, locale = "pt"): TextToken[] {
  const tokens: TextToken[] = [];
  const paragraphs = splitParagraphs(text);
  paragraphs.forEach((paragraph, index) => {
    if (index > 0) tokens.push({ kind: "break" });
    for (const sentence of splitSentences(paragraph, locale)) tokens.push({ kind: "sentence", text: sentence });
  });
  return tokens;
}

export function joinTokens(tokens: TextToken[]): string {
  let out = "";
  let previous: TextToken | null = null;
  for (const token of tokens) {
    if (token.kind === "break") {
      out = out.trimEnd() + "\n\n";
    } else {
      if (previous && previous.kind === "sentence") out += " ";
      out += token.text;
    }
    previous = token;
  }
  return out.trim();
}

export function localeFor(language: string): string {
  return language.startsWith("en") ? "en" : "pt";
}
