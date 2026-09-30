export const LANGUAGES = ["pt-PT", "pt-BR", "en-GB", "en-US"] as const;
export type Language = (typeof LANGUAGES)[number];

export const MODES = ["profissional", "academico", "informal", "literario", "personalizado"] as const;
export type Mode = (typeof MODES)[number];

export const INTENSITIES = ["ligeira", "moderada", "profunda"] as const;
export type Intensity = (typeof INTENSITIES)[number];

export const LENGTH_OPTIONS = ["manter", "encurtar", "desenvolver"] as const;
export type LengthOption = (typeof LENGTH_OPTIONS)[number];

export interface VoiceMetrics {
  sampleWords: number;
  averageSentenceLength: number;
  sentenceLengthStdDev: number;
  longSentenceShare: number;
  shortSentenceShare: number;
  averageParagraphWords: number;
  commasPer100Words: number;
  semicolonsPer100Words: number;
  colonsPer100Words: number;
  dashesPer100Words: number;
  parenthesesPer100Words: number;
  exclamationShare: number;
  questionShare: number;
  firstPersonPer100Words: number;
  typeTokenRatio: number;
  connectors: { term: string; count: number }[];
  /** Tratamento do leitor em português: tu, você, misto ou impessoal. */
  address: "tu" | "você" | "misto" | "impessoal" | null;
  enclisisPer100Words: number | null;
  contractionsPer100Words: number | null;
}

export interface PreferredTerm {
  from: string;
  to: string;
}

export interface VoiceProfile {
  /** Nome curto do perfil, definido pelo utilizador. */
  name: string;
  /** Vocabulário e expressões que o utilizador tende a usar. */
  vocabulary: string;
  /** Ritmo e extensão típica das frases. */
  rhythm: string;
  /** Nível de formalidade habitual. */
  formality: string;
  /** Construção frásica: ordem, subordinação, uso de listas, pontuação. */
  sentenceStructure: string;
  /** Coisas a evitar. */
  avoid: string;
  /** Métricas objetivas calculadas a partir das amostras (opcional). */
  metrics?: VoiceMetrics | null;
  /** Guia de estilo: palavras que nunca devem aparecer. */
  avoidWords?: string[];
  /** Guia de estilo: substituições preferidas. */
  preferredTerms?: PreferredTerm[];
}

export interface RewriteOptions {
  language: Language;
  mode: Mode;
  intensity: Intensity;
  length: LengthOption;
  /** Instruções livres, usadas sobretudo no modo personalizado. */
  customInstructions?: string;
  /** Termos ou passagens que não podem ser alterados. */
  lockedTerms?: string[];
  profile?: VoiceProfile | null;
}

export interface RewriteRequest extends RewriteOptions {
  text: string;
  /**
   * Contexto opcional quando se revê apenas um parágrafo:
   * texto imediatamente antes e depois, só para leitura.
   */
  context?: { before?: string; after?: string };
}

export interface Ambiguity {
  excerpt: string;
  note: string;
}

export interface TerminologyEntry {
  source: string;
  target: string;
}

export interface ParagraphCheck {
  /** Índice do parágrafo dentro do texto revisto. */
  index: number;
  /** Semelhança de sentido com o parágrafo original correspondente (cosseno), quando calculável. */
  similarity: number | null;
  /** Número de padrões típicos de IA encontrados. */
  patterns: number;
  /** Marcas de outra variante linguística encontradas. */
  foreignVariant: string[];
  /** Verdadeiro quando o parágrafo foi automaticamente re-revisto. */
  polished: boolean;
}

export interface SectionResult {
  index: number;
  original: string;
  rewritten: string;
  ambiguities: Ambiguity[];
  terminology: TerminologyEntry[];
  warnings: string[];
  checks: ParagraphCheck[];
}

export type MeaningCheckStatus = "ok" | "unavailable" | "off";

export interface RewriteResult {
  rewritten: string;
  ambiguities: Ambiguity[];
  warnings: string[];
  checks: ParagraphCheck[];
  meaningCheck: MeaningCheckStatus;
  polishedParagraphs: number;
  sections: number;
  model: string;
}

/** Eventos emitidos pelo servidor em NDJSON durante uma reescrita. */
export type RewriteEvent =
  | { type: "start"; sections: number; model: string }
  | { type: "progress"; completed: number; total: number }
  | { type: "status"; message: string }
  | { type: "section"; section: SectionResult }
  | { type: "done"; result: RewriteResult }
  | { type: "error"; message: string; code?: string };

export interface ConfigStatus {
  provider: "ollama" | "openai";
  /** Verdadeiro quando a configuração está completa e o fornecedor respondeu à verificação. */
  ready: boolean;
  /** Mensagem a mostrar ao utilizador quando algo falta ou não responde. */
  problem: string | null;
  model: string;
  baseURL: string;
  sectionWords: number;
  maxInputChars: number;
  /** Contexto pedido ao Ollama por pedido (tokens), ou null noutros fornecedores. */
  contextLength: number | null;
  /** Contexto máximo do modelo, quando o fornecedor o expõe. */
  modelContextLength: number | null;
  /** Modelo de embeddings usado na verificação de sentido, ou null se desativada. */
  embedModel: string | null;
  /** Verdadeiro quando há um servidor LanguageTool configurado. */
  languageTool: boolean;
  autoPolish: boolean;
}

export interface LanguageToolIssue {
  message: string;
  shortMessage: string;
  offset: number;
  length: number;
  category: string;
  ruleId: string;
  replacements: string[];
  context: string;
}
