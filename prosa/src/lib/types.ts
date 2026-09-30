export const LANGUAGES = ["pt-PT", "pt-BR", "en-GB", "en-US"] as const;
export type Language = (typeof LANGUAGES)[number];

export const MODES = ["profissional", "academico", "informal", "literario", "personalizado"] as const;
export type Mode = (typeof MODES)[number];

export const INTENSITIES = ["ligeira", "moderada", "profunda"] as const;
export type Intensity = (typeof INTENSITIES)[number];

export const LENGTH_OPTIONS = ["manter", "encurtar", "desenvolver"] as const;
export type LengthOption = (typeof LENGTH_OPTIONS)[number];

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

export interface SectionResult {
  index: number;
  original: string;
  rewritten: string;
  ambiguities: Ambiguity[];
  terminology: TerminologyEntry[];
  warnings: string[];
}

export interface RewriteResult {
  rewritten: string;
  ambiguities: Ambiguity[];
  warnings: string[];
  sections: number;
  model: string;
}

/** Eventos emitidos pelo servidor em NDJSON durante uma reescrita. */
export type RewriteEvent =
  | { type: "start"; sections: number; model: string }
  | { type: "progress"; completed: number; total: number }
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
}
