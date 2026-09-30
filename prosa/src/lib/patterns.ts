import type { Language } from "./types";
import { splitParagraphs, splitSentences } from "./text";

/**
 * Padrões típicos de texto gerado por modelos de linguagem: aberturas vazias,
 * fechos redundantes, transições mecânicas, vocabulário inflacionado e
 * construções estruturais. Listas próprias, curadas para português e inglês.
 * São mostradas como indicadores transparentes, nunca como «nota humana».
 */

export type PatternCategory = "abertura" | "fecho" | "transição" | "vocabulário" | "ênfase" | "meta" | "estrutura";

export interface PatternHit {
  term: string;
  category: PatternCategory;
  paragraph: number;
  index: number;
}

interface PatternList {
  openers: string[];
  closers: string[];
  transitions: string[];
  vocabulary: string[];
  emphasis: string[];
  meta: string[];
}

const PT: PatternList = {
  openers: [
    "no mundo atual", "no mundo de hoje", "nos dias de hoje", "na sociedade atual", "no cenário atual", "no contexto atual",
    "num mundo cada vez mais", "em um mundo cada vez mais", "na era digital", "na era da informação", "é importante referir",
    "é importante destacar", "é importante ressaltar", "é importante salientar", "é importante notar", "é importante mencionar",
    "é importante lembrar", "vale a pena referir", "vale a pena destacar", "vale ressaltar", "vale destacar", "vale lembrar",
    "é de salientar", "é de referir", "é de notar", "cabe destacar", "cabe ressaltar", "cumpre salientar", "importa referir",
    "importa salientar", "importa notar", "note-se que", "de notar que", "é fundamental compreender", "é essencial compreender",
    "ao longo da história", "desde tempos imemoriais", "hoje em dia",
  ],
  closers: ["em suma", "em conclusão", "para concluir", "concluindo", "resumindo", "em síntese", "em resumo", "em última análise", "no final do dia", "no fim do dia", "no fim de contas"],
  transitions: [
    "além disso", "para além disso", "por outro lado", "adicionalmente", "ademais", "outrossim", "por conseguinte", "nesse sentido",
    "neste sentido", "dessa forma", "desta forma", "dessa maneira", "desta maneira", "sendo assim", "posto isto", "posto isso",
    "com efeito", "assim sendo", "por sua vez", "não obstante", "em contrapartida", "paralelamente", "por fim", "primeiramente",
  ],
  vocabulary: [
    "crucial", "cruciais", "fundamental para", "essencial para", "vital para", "imprescindível", "robusto", "robusta", "abrangente",
    "holístico", "holística", "dinâmico", "dinâmica", "inovador", "inovadora", "transformador", "transformadora", "disruptivo",
    "disruptiva", "envolvente", "meticuloso", "meticulosa", "meticulosamente", "impactante", "sinergia", "sinergias", "alavancar",
    "potenciar", "impulsionar", "mergulhar em", "mergulho profundo", "desvendar", "tapeçaria", "testemunho de", "um testemunho",
    "panorama", "paisagem digital", "jornada", "navegar por", "navegar pelos", "navegar pelas", "revolucionar", "elevar", "desbloquear",
    "otimizar", "fomentar", "sublinhar", "sublinha a importância", "ressoar", "vibrante", "cativante", "sem precedentes", "de ponta",
    "última geração", "em constante evolução", "de forma eficaz", "de forma eficiente", "de forma significativa", "de forma abrangente",
    "no âmbito de", "um papel fundamental", "um papel crucial", "um papel essencial", "um papel vital", "desempenha um papel",
    "desempenham um papel", "insights", "insight", "game changer", "fator-chave", "fatores-chave", "a chave para", "peça-chave",
    "multifacetado", "multifacetada", "abordagem holística", "soluções inovadoras", "ecossistema", "paradigma", "mudança de paradigma",
    "de uma forma geral", "de um modo geral", "uma vasta gama", "uma ampla gama", "um leque de", "uma miríade de", "inúmeros", "inúmeras",
    "capacitar", "empoderar", "otimização", "maximizar", "impulsionando", "alavancando", "primordial", "incontornável", "ímpar",
  ],
  emphasis: ["sem dúvida", "certamente", "claramente", "obviamente", "inegavelmente", "é inegável que", "não há dúvida de que", "verdadeiramente", "genuinamente", "extremamente", "incrivelmente", "significativamente", "notavelmente", "absolutamente"],
  meta: ["como mencionado anteriormente", "como referido anteriormente", "como já foi dito", "como já referido", "conforme mencionado", "espero que ajude", "como modelo de linguagem", "como assistente"],
};

const EN: PatternList = {
  openers: [
    "in today's fast-paced world", "in today's world", "in today's digital age", "in the modern era", "in an ever-changing", "in the ever-evolving",
    "in this day and age", "it is important to note", "it's important to note", "it is worth noting", "it's worth noting", "it should be noted",
    "needless to say", "as we all know", "in recent years", "since the dawn of time", "throughout history", "when it comes to", "let's dive in",
    "let's delve into", "look no further", "whether you're a", "have you ever wondered", "picture this", "imagine a world",
  ],
  closers: ["in conclusion", "to sum up", "in summary", "all in all", "at the end of the day", "to conclude", "in essence", "ultimately,", "the bottom line is", "in a nutshell"],
  transitions: [
    "moreover", "furthermore", "additionally", "in addition", "on the other hand", "that being said", "with that said", "as such",
    "consequently", "therefore", "thus", "nevertheless", "nonetheless", "in light of this", "with this in mind", "first and foremost", "last but not least",
  ],
  vocabulary: [
    "delve", "delves", "delving", "tapestry", "testament to", "a testament", "underscore", "underscores", "underscoring", "landscape",
    "realm", "embark", "embarking", "navigate", "navigating", "foster", "fostering", "leverage", "leveraging", "robust", "seamless",
    "seamlessly", "cutting-edge", "state-of-the-art", "game-changer", "game changer", "unlock", "unlocking", "elevate", "harness",
    "harnessing", "multifaceted", "holistic", "pivotal", "crucial", "vital", "paramount", "meticulous", "meticulously", "intricate",
    "intricacies", "nuanced", "vibrant", "bustling", "ever-evolving", "unprecedented", "transformative", "revolutionize", "revolutionary",
    "synergy", "synergies", "paradigm", "paradigm shift", "streamline", "empower", "empowering", "journey", "resonate", "resonates",
    "dive into", "deep dive", "insights", "actionable insights", "in the realm of", "plays a crucial role", "plays a vital role",
    "plays a pivotal role", "plays a key role", "a wide range of", "a myriad of", "myriad", "plethora", "utilize", "utilizing", "facilitate",
    "comprehensive", "innovative", "dynamic", "key takeaways", "shed light on", "at its core", "it goes without saying", "aims to",
    "serves as", "stands as", "beacon", "cornerstone", "groundbreaking", "enhance", "enhancing", "optimize", "optimizing", "elevate your",
    "unleash", "captivating", "compelling", "invaluable", "indelible", "ever-changing", "fast-paced", "treasure trove", "rich tapestry",
    "in a world where", "a world of possibilities", "the power of", "the importance of", "the significance of", "profound", "remarkable",
  ],
  emphasis: ["undoubtedly", "certainly", "absolutely", "truly", "genuinely", "incredibly", "remarkably", "significantly", "notably", "importantly", "crucially", "essentially"],
  meta: ["as mentioned earlier", "as previously mentioned", "as an ai language model", "i hope this helps", "great question", "certainly!", "of course!", "as discussed above"],
};

/** Padrões estruturais independentes da língua. */
const STRUCTURAL: { re: RegExp; label: string }[] = [
  { re: /(?<!\p{L})(?:não\s+(?:só|apenas|somente)|not\s+only)(?!\p{L})[^.!?\n]{2,80}?(?<!\p{L})(?:mas\s+também|mas\s+sim|but\s+also)(?!\p{L})/giu, label: "«não só X, mas também Y»" },
  { re: /(?<!\p{L})(?:não\s+é\s+(?:apenas|só)|isn't\s+just|is\s+not\s+just|isn't\s+(?:about|merely)|não\s+se\s+trata\s+(?:apenas|só)\s+de)(?!\p{L})[^.!?\n]{2,80}?[,;:]\s*(?:é|it's|it\s+is|mas|but)(?!\p{L})/giu, label: "«não é apenas X: é Y»" },
  { re: /[^.!?\n]*—[^.!?\n—]*—[^.!?\n]*/gu, label: "travessões em série" },
  { re: /(?<!\p{L})(?:from|de)\s+\p{L}+\s+(?:to|a)\s+\p{L}+\s*,\s*(?:from|de)\s+\p{L}+\s+(?:to|a)\s+\p{L}+/giu, label: "«de X a Y, de Z a W»" },
  { re: /^(?:[^\n]{1,60}):\s+\p{Lu}[^\n]{20,}$/gmu, label: "parágrafo com rótulo seguido de dois pontos" },
];

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function listFor(language: Language): PatternList {
  return language.startsWith("en") ? EN : PT;
}

function scan(paragraph: string, paragraphIndex: number, terms: string[], category: PatternCategory, sentenceInitialOnly: boolean, locale: string): PatternHit[] {
  const hits: PatternHit[] = [];
  const sentences = sentenceInitialOnly ? splitSentences(paragraph, locale) : [];
  for (const term of terms) {
    const re = new RegExp(`(?<![\\p{L}\\p{N}-])${escapeRe(term)}(?![\\p{L}\\p{N}-])`, "giu");
    if (sentenceInitialOnly) {
      for (const sentence of sentences) {
        const head = sentence.slice(0, term.length + 2).toLowerCase();
        if (head.startsWith(term.toLowerCase())) {
          hits.push({ term, category, paragraph: paragraphIndex, index: paragraph.indexOf(sentence) });
        }
      }
    } else {
      for (const match of paragraph.matchAll(re)) {
        hits.push({ term: match[0], category, paragraph: paragraphIndex, index: match.index ?? 0 });
      }
    }
  }
  return hits;
}

/** Procura padrões típicos de IA. As transições e fechos só contam em início de frase. */
export function findPatterns(text: string, language: Language): PatternHit[] {
  const list = listFor(language);
  const locale = language.startsWith("en") ? "en" : "pt";
  const hits: PatternHit[] = [];
  splitParagraphs(text).forEach((paragraph, p) => {
    hits.push(
      ...scan(paragraph, p, list.openers, "abertura", false, locale),
      ...scan(paragraph, p, list.closers, "fecho", true, locale),
      ...scan(paragraph, p, list.transitions, "transição", true, locale),
      ...scan(paragraph, p, list.vocabulary, "vocabulário", false, locale),
      ...scan(paragraph, p, list.emphasis, "ênfase", false, locale),
      ...scan(paragraph, p, list.meta, "meta", false, locale),
    );
    for (const rule of STRUCTURAL) {
      for (const match of paragraph.matchAll(rule.re)) {
        hits.push({ term: `${rule.label}: «${match[0].trim().slice(0, 70)}${match[0].length > 70 ? "…" : ""}»`, category: "estrutura", paragraph: p, index: match.index ?? 0 });
      }
    }
  });
  return hits.sort((a, b) => a.paragraph - b.paragraph || a.index - b.index);
}

export interface PatternSummary {
  total: number;
  per1000Words: number;
  byCategory: { category: PatternCategory; count: number; examples: string[] }[];
  byParagraph: number[];
}

export function summarizePatterns(hits: PatternHit[], wordCount: number, paragraphCount: number): PatternSummary {
  const categories = new Map<PatternCategory, { count: number; examples: Set<string> }>();
  const byParagraph = new Array<number>(paragraphCount).fill(0);
  for (const hit of hits) {
    const entry = categories.get(hit.category) ?? { count: 0, examples: new Set<string>() };
    entry.count += 1;
    if (entry.examples.size < 5) entry.examples.add(hit.term);
    categories.set(hit.category, entry);
    if (hit.paragraph < byParagraph.length) byParagraph[hit.paragraph] += 1;
  }
  return {
    total: hits.length,
    per1000Words: wordCount > 0 ? Math.round((hits.length / wordCount) * 1000 * 10) / 10 : 0,
    byCategory: Array.from(categories.entries())
      .map(([category, v]) => ({ category, count: v.count, examples: Array.from(v.examples) }))
      .sort((a, b) => b.count - a.count),
    byParagraph,
  };
}

/** Termos únicos apanhados num parágrafo, para pedir ao modelo que os evite. */
export function termsToAvoid(hits: PatternHit[], paragraph: number): string[] {
  return Array.from(new Set(hits.filter((h) => h.paragraph === paragraph && h.category !== "estrutura").map((h) => h.term.toLowerCase())));
}
