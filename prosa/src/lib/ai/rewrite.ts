import { checkPreservation } from "../guard";
import { normalizeLockedTerms, verifyLocks } from "../locks";
import { findPatterns, termsToAvoid } from "../patterns";
import { buildPolishPrompt, buildSectionPrompt, buildSystemPrompt } from "../prompt";
import { sectionOutputSchema } from "../schemas";
import { segmentDocument, tail } from "../segment";
import { splitParagraphs } from "../text";
import type { Language, MeaningCheckStatus, ParagraphCheck, RewriteEvent, RewriteRequest, SectionResult, TerminologyEntry } from "../types";
import { detectForeignVariant } from "../variant";
import type { Generate } from "./client";

export type EmbedFn = (texts: string[], signal?: AbortSignal) => Promise<number[][]>;

export interface RewriteRuntime {
  generate: Generate;
  model: string;
  sectionWords: number;
  maxOutputTokens?: number;
  signal?: AbortSignal;
  /** Embeddings para a verificação de sentido; ausente desativa a verificação. */
  embed?: EmbedFn | null;
  similarityMin?: number;
  /** Re-revisão automática de parágrafos com padrões de IA ou marcas de outra variante. */
  autoPolish?: boolean;
  maxPolishPerSection?: number;
}

const CONTEXT_TAIL_WORDS = 120;
const MAX_TERMINOLOGY = 60;

function mergeTerminology(current: TerminologyEntry[], incoming: TerminologyEntry[]): TerminologyEntry[] {
  const map = new Map(current.map((t) => [t.source.toLowerCase(), t]));
  for (const entry of incoming) {
    const source = entry.source.trim();
    const target = entry.target.trim();
    if (!source || !target || source.toLowerCase() === target.toLowerCase()) continue;
    if (!map.has(source.toLowerCase())) map.set(source.toLowerCase(), { source, target });
  }
  return Array.from(map.values()).slice(0, MAX_TERMINOLOGY);
}

function cosine(a: number[], b: number[]): number {
  let dot = 0;
  let na = 0;
  let nb = 0;
  for (let i = 0; i < Math.min(a.length, b.length); i += 1) {
    dot += a[i] * b[i];
    na += a[i] * a[i];
    nb += b[i] * b[i];
  }
  return na && nb ? dot / (Math.sqrt(na) * Math.sqrt(nb)) : 0;
}

interface ParagraphIssues {
  patterns: number;
  avoid: string[];
  foreign: string[];
  needsPolish: boolean;
}

function analyseParagraphs(paragraphs: string[], language: Language): ParagraphIssues[] {
  const hits = findPatterns(paragraphs.join("\n\n"), language);
  return paragraphs.map((paragraph, index) => {
    const own = hits.filter((h) => h.paragraph === index);
    const structural = own.some((h) => h.category === "estrutura");
    const foreign = Array.from(new Set(detectForeignVariant(paragraph, language).map((m) => m.term)));
    return {
      patterns: own.length,
      avoid: termsToAvoid(hits, index),
      foreign,
      needsPolish: foreign.length > 0 || own.length >= 2 || structural,
    };
  });
}

/**
 * Reescreve um documento por secções, em sequência, emitindo eventos de progresso.
 * A consistência entre secções é mantida através do final da secção anterior já
 * revista e de um glossário de decisões terminológicas acumulado. Depois de cada
 * secção, os parágrafos com padrões típicos de IA ou marcas de outra variante são
 * re-revistos uma vez, e o sentido é comparado com o original por embeddings.
 */
export async function* rewriteDocument(request: RewriteRequest, runtime: RewriteRuntime): AsyncGenerator<RewriteEvent> {
  const lockedTerms = normalizeLockedTerms(request.lockedTerms);
  const options = { ...request, lockedTerms };
  const system = buildSystemPrompt(options);
  const segments = segmentDocument(request.text, runtime.sectionWords);

  if (segments.length === 0) {
    yield { type: "error", message: "O texto está vazio.", code: "bad_request" };
    return;
  }

  yield { type: "start", sections: segments.length, model: runtime.model };
  yield { type: "progress", completed: 0, total: segments.length };

  const results: SectionResult[] = [];
  let terminology: TerminologyEntry[] = [];
  let previousTail: string | undefined;
  let meaningCheck: MeaningCheckStatus = runtime.embed ? "ok" : "off";
  let embedWarned = false;
  let polishedTotal = 0;
  let paragraphOffset = 0;

  for (const segment of segments) {
    runtime.signal?.throwIfAborted();
    const isSingle = segments.length === 1;
    const user = buildSectionPrompt({
      text: segment.text,
      sectionIndex: segment.index,
      sectionCount: segments.length,
      previousTail,
      contextBefore: isSingle ? request.context?.before : undefined,
      contextAfter: isSingle ? request.context?.after : undefined,
      terminology,
    });

    let output = await runtime.generate({ system, user, schema: sectionOutputSchema, maxTokens: runtime.maxOutputTokens, signal: runtime.signal });

    let lockProblems = verifyLocks(segment.text, output.rewritten, lockedTerms);
    if (lockProblems.length > 0) {
      // Segunda tentativa com instrução reforçada antes de desistir e avisar.
      const retryUser =
        user +
        `\n\nAtenção: na tentativa anterior os seguintes termos bloqueados não ficaram intactos: ${lockProblems.map((p) => `«${p.term}»`).join(", ")}. Repete a revisão garantindo que cada um aparece exatamente como no original.`;
      output = await runtime.generate({ system, user: retryUser, schema: sectionOutputSchema, maxTokens: runtime.maxOutputTokens, signal: runtime.signal });
      lockProblems = verifyLocks(segment.text, output.rewritten, lockedTerms);
    }

    const warnings: string[] = [];
    let paragraphs = splitParagraphs(output.rewritten);
    let issues = analyseParagraphs(paragraphs, request.language);
    const polished = new Set<number>();

    // Re-revisão automática, parágrafo a parágrafo, dos casos com problemas detetáveis.
    if (runtime.autoPolish !== false) {
      const budget = runtime.maxPolishPerSection ?? 3;
      const candidates = issues.map((issue, i) => ({ issue, i })).filter(({ issue }) => issue.needsPolish).slice(0, budget);
      for (const { issue, i } of candidates) {
        runtime.signal?.throwIfAborted();
        yield { type: "status", message: `A polir o parágrafo ${paragraphOffset + i + 1}…` };
        const polishUser = buildPolishPrompt({
          paragraph: paragraphs[i],
          before: paragraphs[i - 1],
          after: paragraphs[i + 1],
          avoidTerms: issue.avoid,
          foreignVariantTerms: issue.foreign,
          language: request.language,
        });
        try {
          const polishedOutput = await runtime.generate({ system, user: polishUser, schema: sectionOutputSchema, maxTokens: runtime.maxOutputTokens, signal: runtime.signal });
          const candidate = polishedOutput.rewritten.trim();
          if (!candidate) continue;
          const candidateIssues = analyseParagraphs([candidate], request.language)[0];
          const before = issue.patterns + issue.foreign.length;
          const after = candidateIssues.patterns + candidateIssues.foreign.length;
          const locksOk = verifyLocks(paragraphs[i], candidate, lockedTerms).length === 0;
          const sizeOk = candidate.length >= paragraphs[i].length * 0.5 && candidate.length <= paragraphs[i].length * 1.6;
          if (after < before && locksOk && sizeOk) {
            paragraphs = paragraphs.map((p, j) => (j === i ? candidate : p));
            polished.add(i);
            polishedTotal += 1;
          }
        } catch (error) {
          // O polimento é uma melhoria opcional: uma falha não invalida a secção.
          if (error instanceof Error && error.name === "AbortError") throw error;
          warnings.push(`Não foi possível re-rever automaticamente o parágrafo ${paragraphOffset + i + 1}.`);
          break;
        }
      }
      issues = analyseParagraphs(paragraphs, request.language);
    }

    const rewritten = paragraphs.join("\n\n");
    lockProblems = verifyLocks(segment.text, rewritten, lockedTerms);

    // Verificação de sentido por embeddings, parágrafo a parágrafo quando a contagem coincide.
    const similarities: (number | null)[] = paragraphs.map(() => null);
    if (runtime.embed && meaningCheck === "ok") {
      const originals = splitParagraphs(segment.text);
      try {
        const pairs = originals.length === paragraphs.length ? originals.map((o, i) => [o, paragraphs[i]] as const) : [[segment.text, rewritten] as const];
        const vectors = await runtime.embed(pairs.flatMap(([a, b]) => [a, b]), runtime.signal);
        const min = runtime.similarityMin ?? 0.72;
        pairs.forEach((_, i) => {
          const score = Math.round(cosine(vectors[i * 2], vectors[i * 2 + 1]) * 100) / 100;
          if (pairs.length === paragraphs.length) similarities[i] = score;
          else similarities.fill(score);
          if (score < min) {
            warnings.push(
              pairs.length === paragraphs.length
                ? `O parágrafo ${paragraphOffset + i + 1} afastou-se do sentido do original (semelhança ${score}). Compara-o com atenção.`
                : `Esta secção afastou-se do sentido do original (semelhança ${score}). Compara-a com atenção.`,
            );
          }
        });
      } catch (error) {
        if (error instanceof Error && error.name === "AbortError") throw error;
        meaningCheck = "unavailable";
        if (!embedWarned) {
          embedWarned = true;
          const detail = error instanceof Error ? error.message : "";
          warnings.push(`A verificação de sentido por embeddings não está disponível${detail ? `: ${detail}` : "."}`);
        }
      }
    }

    warnings.push(
      ...lockProblems.map((p) => `O termo bloqueado «${p.term}» aparece ${p.found} vez(es) na revisão mas ${p.expected} no original. Confirma manualmente.`),
      ...checkPreservation(segment.text, rewritten),
    );
    const remainingForeign = issues.flatMap((i) => i.foreign);
    if (remainingForeign.length > 0) {
      warnings.push(`A revisão mantém marcas de outra variante linguística: ${Array.from(new Set(remainingForeign)).slice(0, 6).join(", ")}.`);
    }

    const checks: ParagraphCheck[] = paragraphs.map((_, i) => ({
      index: paragraphOffset + i,
      similarity: similarities[i],
      patterns: issues[i].patterns,
      foreignVariant: issues[i].foreign,
      polished: polished.has(i),
    }));

    const section: SectionResult = {
      index: segment.index,
      original: segment.text,
      rewritten,
      ambiguities: output.ambiguities,
      terminology: output.terminology,
      warnings,
      checks,
    };
    results.push(section);
    terminology = mergeTerminology(terminology, output.terminology);
    previousTail = tail(section.rewritten, CONTEXT_TAIL_WORDS);
    paragraphOffset += paragraphs.length;

    yield { type: "section", section };
    yield { type: "progress", completed: results.length, total: segments.length };
  }

  yield {
    type: "done",
    result: {
      rewritten: results.map((r) => r.rewritten).join("\n\n"),
      ambiguities: results.flatMap((r) => r.ambiguities),
      warnings: results.flatMap((r) => r.warnings),
      checks: results.flatMap((r) => r.checks),
      meaningCheck,
      polishedParagraphs: polishedTotal,
      sections: results.length,
      model: runtime.model,
    },
  };
}
