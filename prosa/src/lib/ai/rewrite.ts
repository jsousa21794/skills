import { checkPreservation } from "../guard";
import { normalizeLockedTerms, verifyLocks } from "../locks";
import { buildSectionPrompt, buildSystemPrompt } from "../prompt";
import { sectionOutputSchema } from "../schemas";
import { segmentDocument, tail } from "../segment";
import type { RewriteEvent, RewriteRequest, SectionResult, TerminologyEntry } from "../types";
import type { Generate } from "./client";

export interface RewriteRuntime {
  generate: Generate;
  model: string;
  sectionWords: number;
  maxOutputTokens?: number;
  signal?: AbortSignal;
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

/**
 * Reescreve um documento por secções, em sequência, emitindo eventos de progresso.
 * A consistência entre secções é mantida através do final da secção anterior já
 * revista e de um glossário de decisões terminológicas acumulado.
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

    let output = await runtime.generate({
      system,
      user,
      schema: sectionOutputSchema,
      maxTokens: runtime.maxOutputTokens,
      signal: runtime.signal,
    });

    let lockProblems = verifyLocks(segment.text, output.rewritten, lockedTerms);
    if (lockProblems.length > 0) {
      // Segunda tentativa com instrução reforçada antes de desistir e avisar.
      const retryUser =
        user +
        `\n\nAtenção: na tentativa anterior os seguintes termos bloqueados não ficaram intactos: ${lockProblems
          .map((p) => `«${p.term}»`)
          .join(", ")}. Repete a revisão garantindo que cada um aparece exatamente como no original.`;
      output = await runtime.generate({
        system,
        user: retryUser,
        schema: sectionOutputSchema,
        maxTokens: runtime.maxOutputTokens,
        signal: runtime.signal,
      });
      lockProblems = verifyLocks(segment.text, output.rewritten, lockedTerms);
    }

    const warnings = [
      ...lockProblems.map(
        (p) => `O termo bloqueado «${p.term}» aparece ${p.found} vez(es) na revisão mas ${p.expected} no original. Confirma manualmente.`,
      ),
      ...checkPreservation(segment.text, output.rewritten),
    ];

    const section: SectionResult = {
      index: segment.index,
      original: segment.text,
      rewritten: output.rewritten.trim(),
      ambiguities: output.ambiguities,
      terminology: output.terminology,
      warnings,
    };
    results.push(section);
    terminology = mergeTerminology(terminology, output.terminology);
    previousTail = tail(section.rewritten, CONTEXT_TAIL_WORDS);

    yield { type: "section", section };
    yield { type: "progress", completed: results.length, total: segments.length };
  }

  yield {
    type: "done",
    result: {
      rewritten: results.map((r) => r.rewritten).join("\n\n"),
      ambiguities: results.flatMap((r) => r.ambiguities),
      warnings: results.flatMap((r) => r.warnings),
      sections: results.length,
      model: runtime.model,
    },
  };
}
