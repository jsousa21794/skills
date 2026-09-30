"use client";

import { useMemo } from "react";
import { computeMetrics, type TextMetrics } from "@/lib/metrics";
import { findPatterns, summarizePatterns, type PatternSummary } from "@/lib/patterns";
import { computeReadability } from "@/lib/readability";
import { splitParagraphs } from "@/lib/text";
import type { Language, ParagraphCheck } from "@/lib/types";
import { variantProfile } from "@/lib/variant";

interface Props {
  original: string;
  revised: string;
  language: Language;
  checks: ParagraphCheck[];
  meaningCheck: "ok" | "unavailable" | "off" | null;
}

function Stat({ label, a, b, hint }: { label: string; a: number | string; b: number | string | null; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5 text-sm" title={hint}>
      <span className="text-muted">{label}</span>
      <span className="tabular-nums">
        {a}
        {b !== null && (
          <>
            <span className="mx-1.5 text-subtle">›</span>
            <span className="font-medium">{b}</span>
          </>
        )}
      </span>
    </div>
  );
}

function LengthBars({ lengths, max }: { lengths: number[]; max: number }) {
  if (lengths.length === 0) return <p className="text-xs text-subtle">Sem frases detetadas.</p>;
  return (
    <div className="flex h-10 items-end gap-px" aria-label="Extensão de cada frase, por ordem">
      {lengths.slice(0, 120).map((n, i) => (
        <div key={i} className="flex-1 rounded-t-sm bg-accent/70" style={{ height: `${Math.max(6, (n / Math.max(1, max)) * 100)}%` }} title={`Frase ${i + 1}: ${n} palavras`} />
      ))}
    </div>
  );
}

function Repetitions({ metrics }: { metrics: TextMetrics }) {
  const seen = new Set<string>();
  const items = [
    ...metrics.repeatedOpeners.map((f) => ({ ...f, kind: "início de frase" })),
    ...metrics.repeatedBigrams.map((f) => ({ ...f, kind: "expressão" })),
    ...metrics.repeatedWords.map((f) => ({ ...f, kind: "palavra" })),
  ]
    .filter((item) => (seen.has(item.term) ? false : (seen.add(item.term), true)))
    .slice(0, 8);
  if (items.length === 0) return <p className="text-xs text-subtle">Sem repetições salientes.</p>;
  return (
    <ul className="flex flex-wrap gap-1.5">
      {items.map((item) => (
        <li key={`${item.kind}-${item.term}`} className="chip" title={item.kind}>
          <span className="max-w-[10rem] truncate">{item.term}</span>
          <span className="rounded-full bg-elevated px-1.5 text-[10px] tabular-nums text-muted">×{item.count}</span>
        </li>
      ))}
    </ul>
  );
}

const CATEGORY_LABEL: Record<string, string> = {
  abertura: "aberturas vazias",
  fecho: "fechos redundantes",
  transição: "transições mecânicas",
  vocabulário: "vocabulário inflacionado",
  ênfase: "ênfase gratuita",
  meta: "comentários meta",
  estrutura: "construções em molde",
};

function Patterns({ summary }: { summary: PatternSummary }) {
  if (summary.total === 0) return <p className="text-xs text-subtle">Nenhum padrão da lista encontrado.</p>;
  return (
    <ul className="flex flex-col gap-1.5 text-xs">
      {summary.byCategory.map((c) => (
        <li key={c.category} className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="font-medium">
            {CATEGORY_LABEL[c.category] ?? c.category} <span className="tabular-nums text-muted">×{c.count}</span>
          </span>
          <span className="text-muted">{c.examples.map((e) => `«${e}»`).join(", ")}</span>
        </li>
      ))}
    </ul>
  );
}

export function Indicators({ original, revised, language, checks, meaningCheck }: Props) {
  const a = useMemo(() => computeMetrics(original), [original]);
  const b = useMemo(() => (revised ? computeMetrics(revised) : null), [revised]);
  const ra = useMemo(() => computeReadability(original, language), [original, language]);
  const rb = useMemo(() => (revised ? computeReadability(revised, language) : null), [revised, language]);
  const pa = useMemo(() => summarizePatterns(findPatterns(original, language), a.words, a.paragraphs), [original, language, a.words, a.paragraphs]);
  const pb = useMemo(() => (revised ? summarizePatterns(findPatterns(revised, language), b?.words ?? 0, b?.paragraphs ?? 0) : null), [revised, language, b]);
  const va = useMemo(() => variantProfile(original, language), [original, language]);
  const vb = useMemo(() => (revised ? variantProfile(revised, language) : null), [revised, language]);
  const max = Math.max(a.longestSentence, b?.longestSentence ?? 0, 1);
  const revisedParagraphs = useMemo(() => splitParagraphs(revised).length, [revised]);
  const similarities = checks.map((c) => c.similarity).filter((s): s is number => s !== null);
  const minSimilarity = similarities.length ? Math.min(...similarities) : null;
  const polished = checks.filter((c) => c.polished).length;

  return (
    <section className="surface p-4" aria-labelledby="indicators-title">
      <div className="mb-2 flex items-baseline justify-between">
        <h2 id="indicators-title" className="text-sm font-semibold">
          Indicadores
        </h2>
        <span className="text-[11px] text-subtle">original {b ? "› revisto" : ""}</span>
      </div>
      <div className="divide-y divide-line">
        <Stat label="Palavras" a={a.words} b={b?.words ?? null} />
        <Stat label="Frases" a={a.sentences} b={b?.sentences ?? null} />
        <Stat label="Parágrafos" a={a.paragraphs} b={b?.paragraphs ?? null} />
        <Stat label="Média de palavras por frase" a={a.averageSentenceLength} b={b?.averageSentenceLength ?? null} />
        <Stat label="Variação do comprimento das frases" a={ra?.sentenceLengthStdDev ?? 0} b={rb ? rb.sentenceLengthStdDev : null} hint="Desvio-padrão do número de palavras por frase. Valores maiores indicam ritmo mais variado." />
        <Stat label="Frase mais longa" a={a.longestSentence} b={b?.longestSentence ?? null} />
        <Stat label="Frases com mais de 30 palavras" a={`${Math.round((ra?.longSentenceShare ?? 0) * 100)}%`} b={rb ? `${Math.round(rb.longSentenceShare * 100)}%` : null} />
        <Stat label="Diversidade lexical" a={ra?.typeTokenRatio ?? 0} b={rb ? rb.typeTokenRatio : null} hint="Palavras distintas a dividir pelo total. Depende do tamanho do texto." />
        <Stat label={`Legibilidade (${ra?.formula ?? "Flesch"})`} a={ra ? `${ra.flesch} · ${ra.label}` : "—"} b={rb ? `${rb.flesch} · ${rb.label}` : null} hint="0 a 100; valores maiores são mais fáceis de ler. A contagem de sílabas é aproximada." />
        <Stat label="Padrões típicos de IA por 1000 palavras" a={pa.per1000Words} b={pb ? pb.per1000Words : null} />
        <Stat label={`Marcas de outra variante (${language})`} a={va.foreign} b={vb ? vb.foreign : null} />
        {meaningCheck === "ok" && minSimilarity !== null && (
          <Stat label="Semelhança de sentido mínima por parágrafo" a="—" b={minSimilarity} hint="Cosseno entre embeddings do parágrafo original e do revisto. Abaixo do limiar configurado gera um aviso." />
        )}
        {polished > 0 && <Stat label="Parágrafos re-revistos automaticamente" a="—" b={`${polished} de ${revisedParagraphs}`} />}
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-3">
          <div>
            <p className="label">Ritmo · original</p>
            <LengthBars lengths={a.sentenceLengths} max={max} />
          </div>
          <div>
            <p className="label">Repetições · original</p>
            <Repetitions metrics={a} />
          </div>
          <div>
            <p className="label">Padrões típicos de IA · original</p>
            <Patterns summary={pa} />
          </div>
          {va.foreignTerms.length > 0 && (
            <div>
              <p className="label">Marcas de outra variante · original</p>
              <p className="text-xs text-muted">{va.foreignTerms.join(", ")}</p>
            </div>
          )}
        </div>
        {b && pb && vb && (
          <div className="flex flex-col gap-3">
            <div>
              <p className="label">Ritmo · revisto</p>
              <LengthBars lengths={b.sentenceLengths} max={max} />
            </div>
            <div>
              <p className="label">Repetições · revisto</p>
              <Repetitions metrics={b} />
            </div>
            <div>
              <p className="label">Padrões típicos de IA · revisto</p>
              <Patterns summary={pb} />
            </div>
            {vb.foreignTerms.length > 0 && (
              <div>
                <p className="label">Marcas de outra variante · revisto</p>
                <p className="text-xs text-warning-fg">{vb.foreignTerms.join(", ")}</p>
              </div>
            )}
          </div>
        )}
      </div>
      <p className="mt-4 text-[11px] leading-relaxed text-subtle">
        Estes indicadores medem apenas o texto: extensão, ritmo, repetições, legibilidade, expressões de listas curadas e marcas de variante. Não há percentagens de «escrita humana» nem promessas sobre detetores de IA, porque essas medidas não são fiáveis.
        {meaningCheck === "unavailable" && " A verificação de sentido por embeddings não ficou disponível nesta reescrita."}
        {meaningCheck === "off" && " A verificação de sentido por embeddings está desativada na configuração."}
      </p>
    </section>
  );
}
