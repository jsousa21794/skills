import { z } from "zod";
import { INTENSITIES, LANGUAGES, LENGTH_OPTIONS, MODES } from "./types";

export const voiceMetricsSchema = z.object({
  sampleWords: z.number(),
  averageSentenceLength: z.number(),
  sentenceLengthStdDev: z.number(),
  longSentenceShare: z.number(),
  shortSentenceShare: z.number(),
  averageParagraphWords: z.number(),
  commasPer100Words: z.number(),
  semicolonsPer100Words: z.number(),
  colonsPer100Words: z.number(),
  dashesPer100Words: z.number(),
  parenthesesPer100Words: z.number(),
  exclamationShare: z.number(),
  questionShare: z.number(),
  firstPersonPer100Words: z.number(),
  typeTokenRatio: z.number(),
  connectors: z.array(z.object({ term: z.string(), count: z.number() })).max(10),
  address: z.enum(["tu", "você", "misto", "impessoal"]).nullable(),
  enclisisPer100Words: z.number().nullable(),
  contractionsPer100Words: z.number().nullable(),
});

export const voiceProfileSchema = z.object({
  name: z.string().max(80).default("O meu perfil"),
  vocabulary: z.string().max(2000).default(""),
  rhythm: z.string().max(2000).default(""),
  formality: z.string().max(1000).default(""),
  sentenceStructure: z.string().max(2000).default(""),
  avoid: z.string().max(2000).default(""),
  metrics: voiceMetricsSchema.nullable().optional(),
  avoidWords: z.array(z.string().min(1).max(80)).max(200).optional(),
  preferredTerms: z.array(z.object({ from: z.string().min(1).max(80), to: z.string().min(1).max(80) })).max(200).optional(),
});

export const rewriteRequestSchema = z.object({
  text: z.string().min(1, "O texto está vazio."),
  language: z.enum(LANGUAGES),
  mode: z.enum(MODES),
  intensity: z.enum(INTENSITIES),
  length: z.enum(LENGTH_OPTIONS),
  customInstructions: z.string().max(4000).optional(),
  lockedTerms: z.array(z.string().min(1).max(500)).max(100).optional(),
  profile: voiceProfileSchema.nullable().optional(),
  context: z
    .object({
      before: z.string().max(4000).optional(),
      after: z.string().max(4000).optional(),
    })
    .optional(),
});

export type RewriteRequestInput = z.input<typeof rewriteRequestSchema>;

export const profileRequestSchema = z.object({
  examples: z.array(z.string().min(20, "Cada exemplo precisa de pelo menos 20 caracteres.")).min(1).max(10),
  language: z.enum(LANGUAGES),
});

export const checkRequestSchema = z.object({
  text: z.string().min(1).max(200000),
  language: z.enum(LANGUAGES),
});

/** Formato estruturado devolvido pelo modelo para cada secção reescrita. */
export const sectionOutputSchema = z.object({
  rewritten: z.string(),
  ambiguities: z.array(
    z.object({
      excerpt: z.string(),
      note: z.string(),
    }),
  ),
  terminology: z.array(
    z.object({
      source: z.string(),
      target: z.string(),
    }),
  ),
});

export type SectionOutput = z.infer<typeof sectionOutputSchema>;

/** Formato estruturado devolvido pelo modelo ao criar um perfil de voz. */
export const profileOutputSchema = z.object({
  vocabulary: z.string(),
  rhythm: z.string(),
  formality: z.string(),
  sentenceStructure: z.string(),
  avoid: z.string(),
});
